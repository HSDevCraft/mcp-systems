"""Memory Retriever module — semantic search over long-term memory."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from src.modules.base import ExecutionContext, HealthStatus, MCPModule


class MemoryRetrieverInput(BaseModel):
    query: str = Field(..., description="Semantic search query", min_length=1)
    k: int = Field(default=5, description="Number of results to return", ge=1, le=50)
    score_threshold: float = Field(
        default=0.6, description="Minimum similarity score (0-1)", ge=0.0, le=1.0
    )
    session_scoped: bool = Field(
        default=True, description="Limit search to current session"
    )


class MemoryResult(BaseModel):
    id: str
    content: str
    score: float
    role: str
    timestamp: str
    metadata: dict[str, Any] = {}


class MemoryRetrieverOutput(BaseModel):
    results: list[MemoryResult] = Field(description="Retrieved memory items")
    total_found: int = Field(description="Number of results returned")
    query: str = Field(description="Original query")


class MemoryRetrieverModule(MCPModule):
    """Performs semantic search over the long-term memory store.

    Allows agents and clients to explicitly query for relevant past
    information using natural language. The memory manager is injected
    via __init__ for testability.

    Args:
        memory_manager: MemoryManager instance (from dependency injection).
    """

    name = "memory-retriever"
    description = "Performs semantic search over long-term memory"
    version = "1.0.0"
    tags = ["memory", "retrieval", "semantic-search", "rag"]
    input_schema = MemoryRetrieverInput
    output_schema = MemoryRetrieverOutput

    def __init__(self, memory_manager: Any | None = None) -> None:
        self._memory_manager = memory_manager

    async def execute(
        self, input: MemoryRetrieverInput, ctx: ExecutionContext
    ) -> MemoryRetrieverOutput:
        ctx.logger.info(
            "memory_retriever_started",
            query_preview=input.query[:100],
            k=input.k,
        )

        if self._memory_manager is None:
            ctx.logger.warning("memory_retriever_no_manager")
            return MemoryRetrieverOutput(
                results=[], total_found=0, query=input.query
            )

        from src.memory.base import MemoryTier

        session_id = ctx.session_id if input.session_scoped else None

        items = await self._memory_manager.retrieve(
            query=input.query,
            tier=MemoryTier.LONG_TERM,
            session_id=session_id,
            tenant_id=ctx.tenant_id,
            k=input.k,
            score_threshold=input.score_threshold,
        )

        results = [
            MemoryResult(
                id=item.id,
                content=item.content,
                score=item.importance_score,
                role=item.role,
                timestamp=item.timestamp.isoformat(),
                metadata=item.metadata,
            )
            for item in items
        ]

        ctx.logger.info(
            "memory_retriever_complete",
            results_count=len(results),
        )

        return MemoryRetrieverOutput(
            results=results,
            total_found=len(results),
            query=input.query,
        )

    async def health_check(self) -> HealthStatus:
        if self._memory_manager is None:
            return HealthStatus(
                healthy=False,
                message="MemoryManager not injected",
            )
        try:
            ok = await self._memory_manager.ping_long_term()
            return HealthStatus(
                healthy=ok,
                message="Qdrant reachable" if ok else "Qdrant unreachable",
            )
        except Exception as exc:
            return HealthStatus(healthy=False, message=str(exc))
