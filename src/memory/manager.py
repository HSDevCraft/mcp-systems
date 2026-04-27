"""Memory Manager — routes reads and writes to the correct memory tier."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.memory.base import AbstractMemoryStore, MemoryItem, MemoryStats, MemoryTier
from src.utils.logger import get_logger
from src.utils.metrics import get_metrics

logger = get_logger(__name__, component="memory_manager")
metrics = get_metrics()


class MemoryManager:
    """Unified interface over all memory tiers.

    Routes operations to the appropriate backend based on the MemoryTier enum.
    Working memory is managed as an in-process dict on ExecutionContext and
    is not directly accessible here — this manager handles short-term and long-term.

    Args:
        short_term: ShortTermMemoryStore (Redis-backed).
        long_term: LongTermMemoryStore (Qdrant-backed).
    """

    def __init__(
        self,
        short_term: AbstractMemoryStore,
        long_term: AbstractMemoryStore,
    ) -> None:
        self._stores: dict[MemoryTier, AbstractMemoryStore] = {
            MemoryTier.SHORT_TERM: short_term,
            MemoryTier.LONG_TERM: long_term,
        }

    async def write(
        self,
        content: str,
        metadata: dict[str, Any],
        tier: MemoryTier = MemoryTier.SHORT_TERM,
        ttl: int | None = None,
    ) -> str:
        """Write a memory item to the specified tier.

        Args:
            content: Text content to persist.
            metadata: Payload metadata (tenant_id, session_id, role, etc.).
            tier: Target memory tier.
            ttl: Optional TTL override (only relevant for short-term).

        Returns:
            Memory item ID.

        Raises:
            KeyError: If tier is WORKING (not managed by this class).
            MemoryWriteError: If the write fails.
        """
        if tier == MemoryTier.WORKING:
            raise KeyError("Working memory is managed via ExecutionContext.working_memory")

        store = self._stores[tier]
        return await store.write(content=content, metadata=metadata, ttl=ttl)

    async def retrieve(
        self,
        query: str | None,
        tier: MemoryTier,
        session_id: UUID | None = None,
        context_id: UUID | None = None,
        tenant_id: str = "default",
        k: int = 10,
        score_threshold: float = 0.6,
    ) -> list[MemoryItem]:
        """Retrieve memory items from the specified tier.

        Args:
            query: Semantic search query (used by long-term tier only).
            tier: Source memory tier.
            session_id: Optional session filter.
            context_id: Optional context filter.
            tenant_id: Tenant namespace.
            k: Max results.
            score_threshold: Min similarity score for long-term (ignored by short-term).

        Returns:
            List of MemoryItems.
        """
        if tier == MemoryTier.WORKING:
            raise KeyError("Working memory is managed via ExecutionContext.working_memory")

        filters: dict[str, Any] = {"tenant_id": tenant_id}
        if session_id:
            filters["session_id"] = str(session_id)
        if context_id:
            filters["context_id"] = str(context_id)

        store = self._stores[tier]

        if tier == MemoryTier.LONG_TERM and hasattr(store, "retrieve"):
            return await store.retrieve(  # type: ignore[call-arg]
                query=query,
                k=k,
                filters=filters,
                score_threshold=score_threshold,
            )

        return await store.retrieve(query=query, k=k, filters=filters)

    async def delete(self, memory_id: str, tier: MemoryTier) -> bool:
        """Delete a memory item from the specified tier.

        Args:
            memory_id: The memory item ID.
            tier: Which tier to delete from.

        Returns:
            True if deleted, False if not found.
        """
        if tier == MemoryTier.WORKING:
            raise KeyError("Working memory cannot be deleted via MemoryManager")
        store = self._stores[tier]
        return await store.delete(memory_id)

    async def ping_short_term(self) -> bool:
        """Health check for short-term store."""
        return await self._stores[MemoryTier.SHORT_TERM].ping()

    async def ping_long_term(self) -> bool:
        """Health check for long-term store."""
        return await self._stores[MemoryTier.LONG_TERM].ping()

    async def get_stats(self) -> MemoryStats:
        """Gather statistics from all tiers.

        Returns:
            MemoryStats aggregate object.
        """
        st_stats = await self._stores[MemoryTier.SHORT_TERM].get_stats()
        lt_stats = await self._stores[MemoryTier.LONG_TERM].get_stats()

        return MemoryStats(
            short_term_memory_bytes=st_stats.get("used_memory_bytes", 0),
            long_term_vectors=lt_stats.get("vectors_count", 0),
            redis_connected=st_stats.get("connected", False),
            qdrant_connected=lt_stats.get("connected", False),
        )

    async def retrieve_and_stitch(
        self,
        query: str,
        tenant_id: str,
        session_id: UUID | None = None,
        token_budget: int = 4000,
        long_term_k: int = 5,
        short_term_k: int = 10,
    ) -> list[MemoryItem]:
        """Retrieve from both tiers and merge within token budget.

        Long-term (semantic) results get priority. Short-term (recent) fills
        remaining budget. Deduplication is by content hash.

        Args:
            query: Semantic query for long-term retrieval.
            tenant_id: Tenant namespace.
            session_id: Optional session filter.
            token_budget: Maximum total tokens to return.
            long_term_k: Long-term result count.
            short_term_k: Short-term result count.

        Returns:
            Merged, deduplicated list of MemoryItems within budget.
        """
        import asyncio

        lt_task = asyncio.create_task(
            self.retrieve(
                query=query,
                tier=MemoryTier.LONG_TERM,
                session_id=session_id,
                tenant_id=tenant_id,
                k=long_term_k,
            )
        )
        st_task = asyncio.create_task(
            self.retrieve(
                query=None,
                tier=MemoryTier.SHORT_TERM,
                session_id=session_id,
                tenant_id=tenant_id,
                k=short_term_k,
            )
        )

        lt_results, st_results = await asyncio.gather(lt_task, st_task, return_exceptions=True)

        if isinstance(lt_results, Exception):
            logger.warning("long_term_stitch_failed", error=str(lt_results))
            lt_results = []
        if isinstance(st_results, Exception):
            logger.warning("short_term_stitch_failed", error=str(st_results))
            st_results = []

        # Deduplicate by content hash
        seen: set[str] = set()
        merged: list[MemoryItem] = []
        total_tokens = 0

        for item in list(lt_results) + list(st_results):  # type: ignore[operator]
            content_key = item.content[:100]
            if content_key in seen:
                continue
            if total_tokens + item.token_count > token_budget:
                break
            seen.add(content_key)
            merged.append(item)
            total_tokens += item.token_count

        logger.debug(
            "memory_stitched",
            long_term_count=len(lt_results),  # type: ignore[arg-type]
            short_term_count=len(st_results),  # type: ignore[arg-type]
            merged_count=len(merged),
            total_tokens=total_tokens,
        )
        return merged
