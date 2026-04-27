"""Memory management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.api.schemas.common import ApiResponse
from src.api.schemas.memory import (
    MemoryItemResponse,
    MemoryStatsResponse,
    RetrieveMemoryRequest,
    RetrieveMemoryResponse,
    StoreMemoryRequest,
    StoreMemoryResponse,
)
from src.utils.logger import get_logger
from src.utils.security import count_tokens

logger = get_logger(__name__, component="memory_router")

router = APIRouter(prefix="/memory", tags=["Memory"])


def _get_orchestrator(request: Request) -> object:
    return request.app.state.orchestrator


def _get_tenant(request: Request) -> str:
    return getattr(request.state, "tenant_id", "default")


@router.post(
    "/store",
    response_model=ApiResponse[StoreMemoryResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Store a memory item in the specified tier",
)
async def store_memory(
    body: StoreMemoryRequest,
    request: Request,
    orchestrator: object = Depends(_get_orchestrator),
    tenant_id: str = Depends(_get_tenant),
) -> ApiResponse[StoreMemoryResponse]:
    metadata = {
        "tenant_id": tenant_id,
        "role": body.role,
        "tags": body.tags,
        **body.metadata,
    }
    if body.session_id:
        metadata["session_id"] = str(body.session_id)
    if body.context_id:
        metadata["context_id"] = str(body.context_id)

    memory_id = await orchestrator.store_memory(  # type: ignore[union-attr]
        content=body.content,
        tenant_id=tenant_id,
        metadata=metadata,
        tier=body.tier,
    )

    return ApiResponse.ok(
        StoreMemoryResponse(
            memory_id=memory_id,
            tier=body.tier,
            content_preview=body.content[:100] + ("..." if len(body.content) > 100 else ""),
            token_count=count_tokens(body.content),
        )
    )


@router.post(
    "/retrieve",
    response_model=ApiResponse[RetrieveMemoryResponse],
    summary="Semantic search over memory",
)
async def retrieve_memory(
    body: RetrieveMemoryRequest,
    request: Request,
    orchestrator: object = Depends(_get_orchestrator),
    tenant_id: str = Depends(_get_tenant),
) -> ApiResponse[RetrieveMemoryResponse]:
    items = await orchestrator.retrieve_memory(  # type: ignore[union-attr]
        query=body.query,
        tenant_id=tenant_id,
        session_id=body.session_id,
        k=body.k,
    )

    results = [
        MemoryItemResponse(
            id=item.id,
            content=item.content,
            tier=item.tier.value if hasattr(item.tier, "value") else item.tier,
            role=item.role,
            timestamp=item.timestamp,
            importance_score=item.importance_score,
            token_count=item.token_count,
            tags=item.tags,
            metadata=item.metadata,
        )
        for item in items
    ]

    return ApiResponse.ok(
        RetrieveMemoryResponse(
            results=results,
            total=len(results),
            query=body.query,
            tier=body.tier,
        )
    )


@router.delete(
    "/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a memory item",
)
async def delete_memory(
    memory_id: str,
    request: Request,
    orchestrator: object = Depends(_get_orchestrator),
    tenant_id: str = Depends(_get_tenant),
    tier: str = "long_term",
) -> None:
    deleted = await orchestrator.delete_memory(memory_id, tier)  # type: ignore[union-attr]
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Memory item '{memory_id}' not found")


@router.get(
    "/stats",
    response_model=ApiResponse[MemoryStatsResponse],
    summary="Get memory tier statistics",
)
async def get_memory_stats(
    request: Request,
    orchestrator: object = Depends(_get_orchestrator),
) -> ApiResponse[MemoryStatsResponse]:
    stats = await orchestrator.get_memory_stats()  # type: ignore[union-attr]
    return ApiResponse.ok(
        MemoryStatsResponse(
            short_term_memory_bytes=stats.short_term_memory_bytes,
            long_term_vectors=stats.long_term_vectors,
            redis_connected=stats.redis_connected,
            qdrant_connected=stats.qdrant_connected,
        )
    )
