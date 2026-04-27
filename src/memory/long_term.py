"""Long-term memory store backed by Qdrant vector database.

Provides semantic search over persistent memory items using approximate
nearest neighbour (ANN) retrieval. Writes are non-blocking (background tasks).
"""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from src.memory.base import AbstractMemoryStore, MemoryItem, MemoryTier
from src.utils.config import Settings, get_settings
from src.utils.logger import get_logger
from src.utils.metrics import get_metrics
from src.utils.security import count_tokens

logger = get_logger(__name__, component="long_term_memory")
metrics = get_metrics()


class LongTermMemoryStore(AbstractMemoryStore):
    """Qdrant-backed long-term semantic memory store.

    Each memory item is stored as a vector (embedding of content)
    with a rich payload for filtering. Supports:
      - Semantic similarity search (cosine distance)
      - Payload filtering (tenant_id, session_id, role, expired)
      - Soft deletion (expired=true flag in payload)

    Args:
        qdrant_client: Async Qdrant client instance.
        embedder: Callable (str) → list[float] for embedding content.
        settings: Application settings.
    """

    def __init__(
        self,
        qdrant_client: Any,
        embedder: Any,
        settings: Settings | None = None,
    ) -> None:
        self._client = qdrant_client
        self._embedder = embedder
        self._settings = settings or get_settings()
        self._collection = self._settings.qdrant_collection

    async def ensure_collection(self) -> None:
        """Create the Qdrant collection if it does not exist.

        Call once at application startup.
        """
        from qdrant_client.models import (
            Distance,
            HnswConfigDiff,
            OptimizersConfigDiff,
            VectorParams,
        )

        try:
            existing = await self._client.get_collections()
            names = [c.name for c in existing.collections]
            if self._collection not in names:
                await self._client.create_collection(
                    collection_name=self._collection,
                    vectors_config=VectorParams(
                        size=self._settings.qdrant_vector_size,
                        distance=Distance.COSINE,
                    ),
                    hnsw_config=HnswConfigDiff(
                        m=16,
                        ef_construct=100,
                        full_scan_threshold=10000,
                    ),
                    optimizers_config=OptimizersConfigDiff(
                        indexing_threshold=10000,
                    ),
                )
                await self._create_payload_indexes()
                logger.info(
                    "qdrant_collection_created",
                    collection=self._collection,
                )
        except Exception as exc:
            logger.error("qdrant_collection_setup_failed", error=str(exc))
            raise

    async def _create_payload_indexes(self) -> None:
        """Create payload indexes for efficient filtering."""
        from qdrant_client.models import PayloadSchemaType

        indexed_fields = ["tenant_id", "session_id", "expired", "role"]
        for field_name in indexed_fields:
            try:
                await self._client.create_payload_index(
                    collection_name=self._collection,
                    field_name=field_name,
                    field_schema=PayloadSchemaType.KEYWORD,
                )
            except Exception:
                pass  # Index may already exist

    async def write(
        self,
        content: str,
        metadata: dict[str, Any],
        ttl: int | None = None,
    ) -> str:
        """Embed content and upsert to Qdrant.

        Args:
            content: Text content to embed and store.
            metadata: Payload fields (tenant_id, session_id, role, etc.).
            ttl: Not used for long-term (items are permanent unless soft-deleted).

        Returns:
            Memory item ID (UUID string).
        """
        from qdrant_client.models import PointStruct

        memory_id = str(uuid4())
        tenant_id = metadata.get("tenant_id", "default")

        start = time.perf_counter()
        try:
            embedding = await self._embed(content)

            item = MemoryItem(
                id=memory_id,
                content=content,
                tier=MemoryTier.LONG_TERM,
                tenant_id=tenant_id,
                session_id=metadata.get("session_id"),
                context_id=metadata.get("context_id"),
                role=metadata.get("role", "assistant"),
                token_count=count_tokens(content),
                tags=metadata.get("tags", []),
                metadata={k: v for k, v in metadata.items()
                          if k not in {"tenant_id", "session_id", "context_id", "role", "tags"}},
            )

            payload = item.model_dump(mode="json")
            payload.pop("id", None)

            await self._client.upsert(
                collection_name=self._collection,
                points=[PointStruct(id=memory_id, vector=embedding, payload=payload)],
            )

            latency = time.perf_counter() - start
            metrics.record_memory_operation("write", "long_term", "success", latency)
            return memory_id

        except Exception as exc:
            latency = time.perf_counter() - start
            metrics.record_memory_operation("write", "long_term", "error", latency)
            logger.error("long_term_write_failed", error=str(exc))
            raise

    async def retrieve(
        self,
        query: str | None,
        k: int = 5,
        filters: dict[str, Any] | None = None,
        score_threshold: float = 0.6,
    ) -> list[MemoryItem]:
        """Semantic search over long-term memory.

        Args:
            query: Search query (will be embedded). If None, returns empty list.
            k: Number of results to return.
            filters: Dict with 'tenant_id', optional 'session_id'.
            score_threshold: Minimum cosine similarity score (0-1).

        Returns:
            List of MemoryItems ordered by relevance score descending.
        """
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        if query is None:
            return []

        filters = filters or {}
        tenant_id = filters.get("tenant_id", "default")
        session_id = filters.get("session_id")

        start = time.perf_counter()
        try:
            embedding = await self._embed(query)

            must_conditions = [
                FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
                FieldCondition(key="expired", match=MatchValue(value=False)),
            ]
            if session_id:
                must_conditions.append(
                    FieldCondition(key="session_id", match=MatchValue(value=str(session_id)))
                )

            search_filter = Filter(must=must_conditions)

            results = await self._client.search(
                collection_name=self._collection,
                query_vector=embedding,
                query_filter=search_filter,
                limit=k,
                score_threshold=score_threshold,
                with_payload=True,
            )

            items = []
            for hit in results:
                try:
                    payload = dict(hit.payload or {})
                    payload["id"] = str(hit.id)
                    payload["tier"] = MemoryTier.LONG_TERM
                    payload["importance_score"] = hit.score
                    items.append(MemoryItem.model_validate(payload))
                except Exception as exc:
                    logger.warning("long_term_parse_failed", error=str(exc))

            latency = time.perf_counter() - start
            metrics.record_memory_operation("read", "long_term", "success", latency)
            return items

        except Exception as exc:
            latency = time.perf_counter() - start
            metrics.record_memory_operation("read", "long_term", "error", latency)
            logger.error("long_term_retrieve_failed", error=str(exc))
            return []

    async def delete(self, memory_id: str) -> bool:
        """Soft-delete a memory item (sets expired=true in payload)."""
        try:
            from qdrant_client.models import SetPayload

            await self._client.set_payload(
                collection_name=self._collection,
                payload={"expired": True},
                points=[memory_id],
            )
            return True
        except Exception as exc:
            logger.error("long_term_delete_failed", memory_id=memory_id, error=str(exc))
            return False

    async def ping(self) -> bool:
        """Ping Qdrant to verify connectivity."""
        try:
            await self._client.get_collections()
            return True
        except Exception:
            return False

    async def get_stats(self) -> dict[str, Any]:
        """Return Qdrant collection statistics."""
        try:
            info = await self._client.get_collection(self._collection)
            return {
                "vectors_count": info.vectors_count,
                "indexed_vectors_count": info.indexed_vectors_count,
                "points_count": info.points_count,
                "status": str(info.status),
                "connected": True,
            }
        except Exception as exc:
            return {"connected": False, "error": str(exc)}

    async def _embed(self, text: str) -> list[float]:
        """Embed text using the configured embedder."""
        start = time.perf_counter()
        provider = self._settings.embedding_provider.value
        try:
            embedding = await self._embedder.embed(text)
            latency = time.perf_counter() - start
            metrics.record_embedding_request(provider, "success", latency)
            return embedding
        except Exception as exc:
            latency = time.perf_counter() - start
            metrics.record_embedding_request(provider, "error", latency)
            raise


class MockEmbedder:
    """Zero-dependency embedder for testing — returns random vectors."""

    def __init__(self, vector_size: int = 1536) -> None:
        self._size = vector_size

    async def embed(self, text: str) -> list[float]:
        import hashlib
        import struct

        seed = int(hashlib.md5(text.encode()).hexdigest()[:8], 16)  # noqa: S324
        values = []
        for i in range(self._size):
            seed = (seed * 1664525 + 1013904223) & 0xFFFFFFFF
            values.append(struct.unpack("f", struct.pack("I", seed))[0])
        norm = (sum(v * v for v in values) ** 0.5) or 1.0
        return [v / norm for v in values]
