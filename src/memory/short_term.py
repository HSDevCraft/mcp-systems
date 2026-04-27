"""Short-term memory store backed by Redis.

Provides fast (1-5ms) TTL-based storage for session context and recent
tool results. Uses Redis Hashes for structured items and Redis Sorted Sets
for score-ordered retrieval.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

import orjson

from src.memory.base import AbstractMemoryStore, MemoryItem, MemoryTier
from src.utils.config import Settings, get_settings
from src.utils.logger import get_logger
from src.utils.metrics import get_metrics
from src.utils.security import count_tokens

logger = get_logger(__name__, component="short_term_memory")
metrics = get_metrics()


class ShortTermMemoryStore(AbstractMemoryStore):
    """Redis-backed short-term memory store.

    Storage layout:
      - Item:    mcp:{tenant}:mem:st:{id}           → Redis Hash
      - Index:   mcp:{tenant}:mem:st:idx:{session}  → Redis Sorted Set (score=timestamp)
      - Global:  mcp:{tenant}:mem:st:all            → Redis Sorted Set

    TTL:
      - Each item key has TTL from REDIS_TTL_SECONDS
      - Index entries are expired lazily (ZRANGEBYSCORE with min score)

    Args:
        redis_client: Async Redis client.
        settings: Application settings.
    """

    def __init__(self, redis_client: Any, settings: Settings | None = None) -> None:
        self._redis = redis_client
        self._settings = settings or get_settings()

    async def write(
        self,
        content: str,
        metadata: dict[str, Any],
        ttl: int | None = None,
    ) -> str:
        """Store a memory item in Redis.

        Args:
            content: Text content to store.
            metadata: Arbitrary metadata (tenant_id, session_id, role, etc.).
            ttl: TTL in seconds; defaults to settings.redis_ttl_seconds.

        Returns:
            Memory item ID (content hash for idempotency).
        """
        effective_ttl = ttl or self._settings.redis_ttl_seconds
        tenant_id = metadata.get("tenant_id", "default")

        memory_id = _content_hash(content, metadata.get("session_id", ""))
        item = MemoryItem(
            id=memory_id,
            content=content,
            tier=MemoryTier.SHORT_TERM,
            tenant_id=tenant_id,
            session_id=metadata.get("session_id"),
            context_id=metadata.get("context_id"),
            role=metadata.get("role", "assistant"),
            token_count=count_tokens(content),
            tags=metadata.get("tags", []),
            metadata={k: v for k, v in metadata.items()
                      if k not in {"tenant_id", "session_id", "context_id", "role", "tags"}},
        )

        item_key = self._item_key(tenant_id, memory_id)
        score = time.time()

        start = time.perf_counter()
        try:
            pipeline = self._redis.pipeline()
            pipeline.set(item_key, orjson.dumps(item.model_dump(mode="json")), ex=effective_ttl)

            global_idx = self._global_index_key(tenant_id)
            pipeline.zadd(global_idx, {memory_id: score})
            pipeline.expire(global_idx, effective_ttl)

            if item.session_id:
                session_idx = self._session_index_key(tenant_id, item.session_id)
                pipeline.zadd(session_idx, {memory_id: score})
                pipeline.expire(session_idx, effective_ttl)

            await pipeline.execute()

            latency = time.perf_counter() - start
            metrics.record_memory_operation("write", "short_term", "success", latency)
            return memory_id

        except Exception as exc:
            latency = time.perf_counter() - start
            metrics.record_memory_operation("write", "short_term", "error", latency)
            logger.error("short_term_write_failed", error=str(exc))
            raise

    async def retrieve(
        self,
        query: str | None,
        k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[MemoryItem]:
        """Retrieve recent memory items.

        Short-term memory does NOT do semantic search — it retrieves
        the most recent k items, optionally filtered by session_id.

        Args:
            query: Ignored for short-term (recency-based only).
            k: Max items to return.
            filters: Dict that may contain 'tenant_id', 'session_id'.

        Returns:
            List of MemoryItems, newest first.
        """
        filters = filters or {}
        tenant_id = filters.get("tenant_id", "default")
        session_id = filters.get("session_id")

        start = time.perf_counter()
        try:
            if session_id:
                idx_key = self._session_index_key(tenant_id, session_id)
            else:
                idx_key = self._global_index_key(tenant_id)

            # Get top-k most recent IDs (highest score = most recent)
            memory_ids = await self._redis.zrevrange(idx_key, 0, k - 1)

            if not memory_ids:
                return []

            # Batch fetch items
            pipeline = self._redis.pipeline()
            for mid in memory_ids:
                mid_str = mid.decode() if isinstance(mid, bytes) else mid
                pipeline.get(self._item_key(tenant_id, mid_str))
            raw_items = await pipeline.execute()

            items = []
            for raw in raw_items:
                if raw is not None:
                    try:
                        item = MemoryItem.model_validate(orjson.loads(raw))
                        if not item.expired:
                            items.append(item)
                    except Exception:
                        pass

            latency = time.perf_counter() - start
            metrics.record_memory_operation("read", "short_term", "success", latency)
            return items

        except Exception as exc:
            latency = time.perf_counter() - start
            metrics.record_memory_operation("read", "short_term", "error", latency)
            logger.error("short_term_retrieve_failed", error=str(exc))
            return []

    async def delete(self, memory_id: str, tenant_id: str = "default") -> bool:
        """Delete a specific memory item."""
        item_key = self._item_key(tenant_id, memory_id)
        deleted = await self._redis.delete(item_key)
        return deleted > 0

    async def ping(self) -> bool:
        """Ping Redis to verify connectivity."""
        try:
            result = await self._redis.ping()
            return result is True or result == b"PONG"
        except Exception:
            return False

    async def get_stats(self) -> dict[str, Any]:
        """Return Redis memory statistics."""
        try:
            info = await self._redis.info("memory")
            return {
                "used_memory_bytes": info.get("used_memory", 0),
                "used_memory_human": info.get("used_memory_human", "unknown"),
                "connected": True,
            }
        except Exception as exc:
            return {"connected": False, "error": str(exc)}

    # ── Key Helpers ───────────────────────────────────────────────────────────

    def _item_key(self, tenant_id: str, memory_id: str) -> str:
        return f"mcp:{tenant_id}:mem:st:{memory_id}"

    def _global_index_key(self, tenant_id: str) -> str:
        return f"mcp:{tenant_id}:mem:st:all"

    def _session_index_key(self, tenant_id: str, session_id: str) -> str:
        return f"mcp:{tenant_id}:mem:st:idx:{session_id}"


def _content_hash(content: str, session_id: str) -> str:
    """Deterministic content hash for idempotent writes."""
    data = f"{session_id}:{content[:200]}"
    return hashlib.sha256(data.encode()).hexdigest()[:32]
