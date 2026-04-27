"""Context management endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.api.schemas.common import ApiResponse
from src.api.schemas.context import (
    AppendMessageRequest,
    AppendMessageResponse,
    ContextResponse,
    ForkContextResponse,
    GetMessagesResponse,
    MessageResponse,
    CreateContextRequest,
)
from src.utils.exceptions import (
    ContextExpiredError,
    ContextNotFoundError,
    ContextOverflowError,
    ContextSealedError,
)
from src.utils.logger import get_logger

logger = get_logger(__name__, component="context_router")

router = APIRouter(prefix="/contexts", tags=["Contexts"])


def _get_orchestrator(request: Request) -> object:
    return request.app.state.orchestrator


def _get_tenant(request: Request) -> str:
    return getattr(request.state, "tenant_id", "default")


def _get_user(request: Request) -> str:
    return getattr(request.state, "user_id", "anonymous")


@router.post(
    "/",
    response_model=ApiResponse[ContextResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a new context",
)
async def create_context(
    body: CreateContextRequest,
    request: Request,
    orchestrator: object = Depends(_get_orchestrator),
    tenant_id: str = Depends(_get_tenant),
) -> ApiResponse[ContextResponse]:
    ctx = await orchestrator.create_context(  # type: ignore[union-attr]
        session_id=body.session_id,
        tenant_id=tenant_id,
        max_tokens=body.max_tokens,
        ttl_seconds=body.ttl_seconds,
        metadata=body.metadata,
        system_prompt=body.system_prompt,
    )
    return ApiResponse.ok(
        _context_to_response(ctx, message_count=0),
        request_id=str(request.state.request_id),
    )


@router.get(
    "/{context_id}",
    response_model=ApiResponse[ContextResponse],
    summary="Get context metadata",
)
async def get_context(
    context_id: UUID,
    request: Request,
    orchestrator: object = Depends(_get_orchestrator),
    tenant_id: str = Depends(_get_tenant),
) -> ApiResponse[ContextResponse]:
    try:
        ctx = await orchestrator.get_context(context_id, tenant_id)  # type: ignore[union-attr]
    except ContextNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)
    except ContextExpiredError as exc:
        raise HTTPException(status_code=410, detail=exc.message)
    return ApiResponse.ok(_context_to_response(ctx))


@router.put(
    "/{context_id}/messages",
    response_model=ApiResponse[AppendMessageResponse],
    summary="Append a message to a context",
)
async def append_message(
    context_id: UUID,
    body: AppendMessageRequest,
    request: Request,
    orchestrator: object = Depends(_get_orchestrator),
    tenant_id: str = Depends(_get_tenant),
) -> ApiResponse[AppendMessageResponse]:
    try:
        ctx, msg = await orchestrator.append_to_context(  # type: ignore[union-attr]
            context_id=context_id,
            tenant_id=tenant_id,
            role=body.role,
            content=body.content,
            metadata=body.metadata,
        )
    except ContextNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)
    except ContextSealedError as exc:
        raise HTTPException(status_code=409, detail=exc.message)
    except ContextOverflowError as exc:
        raise HTTPException(status_code=422, detail=exc.message)

    return ApiResponse.ok(
        AppendMessageResponse(
            context_id=ctx.id,
            message_id=msg.id,
            role=msg.role.value,
            token_count=msg.token_count,
            context_token_count=ctx.token_count,
            context_remaining_tokens=ctx.remaining_tokens(),
        )
    )


@router.get(
    "/{context_id}/messages",
    response_model=ApiResponse[GetMessagesResponse],
    summary="Get messages in a context",
)
async def get_messages(
    context_id: UUID,
    request: Request,
    orchestrator: object = Depends(_get_orchestrator),
    tenant_id: str = Depends(_get_tenant),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[GetMessagesResponse]:
    try:
        messages = await orchestrator.get_context_messages(  # type: ignore[union-attr]
            context_id, tenant_id, limit=limit, offset=offset
        )
    except ContextNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)

    msg_responses = [
        MessageResponse(
            id=m.id,
            role=m.role.value if hasattr(m.role, "value") else m.role,
            content=m.get_text_content() if hasattr(m, "get_text_content") else str(m.content),
            token_count=m.token_count,
            timestamp=m.timestamp,
            metadata=m.metadata,
        )
        for m in messages
    ]
    return ApiResponse.ok(
        GetMessagesResponse(
            context_id=context_id,
            messages=msg_responses,
            total=len(msg_responses),
            page=offset // limit + 1 if limit else 1,
            page_size=limit,
        )
    )


@router.post(
    "/{context_id}/fork",
    response_model=ApiResponse[ForkContextResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Fork a context (create a branched copy)",
)
async def fork_context(
    context_id: UUID,
    request: Request,
    orchestrator: object = Depends(_get_orchestrator),
    tenant_id: str = Depends(_get_tenant),
) -> ApiResponse[ForkContextResponse]:
    try:
        child = await orchestrator.fork_context(context_id, tenant_id)  # type: ignore[union-attr]
    except ContextNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)

    return ApiResponse.ok(
        ForkContextResponse(
            parent_id=context_id,
            child_id=child.id,
            child_context=_context_to_response(child),
        )
    )


@router.post(
    "/{context_id}/seal",
    response_model=ApiResponse[ContextResponse],
    summary="Seal a context (make immutable)",
)
async def seal_context(
    context_id: UUID,
    request: Request,
    orchestrator: object = Depends(_get_orchestrator),
    tenant_id: str = Depends(_get_tenant),
) -> ApiResponse[ContextResponse]:
    try:
        ctx = await orchestrator.seal_context(context_id, tenant_id)  # type: ignore[union-attr]
    except ContextNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)
    return ApiResponse.ok(_context_to_response(ctx))


@router.delete(
    "/{context_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Expire and delete a context",
)
async def expire_context(
    context_id: UUID,
    request: Request,
    orchestrator: object = Depends(_get_orchestrator),
    tenant_id: str = Depends(_get_tenant),
) -> None:
    await orchestrator.expire_context(context_id, tenant_id)  # type: ignore[union-attr]


def _context_to_response(ctx: object, message_count: int = 0) -> ContextResponse:
    return ContextResponse(
        id=ctx.id,  # type: ignore[attr-defined]
        session_id=ctx.session_id,  # type: ignore[attr-defined]
        status=ctx.status.value if hasattr(ctx.status, "value") else ctx.status,  # type: ignore[attr-defined]
        token_count=ctx.token_count,  # type: ignore[attr-defined]
        max_tokens=ctx.max_tokens,  # type: ignore[attr-defined]
        remaining_tokens=ctx.remaining_tokens(),  # type: ignore[attr-defined]
        message_count=message_count,
        created_at=ctx.created_at,  # type: ignore[attr-defined]
        updated_at=ctx.updated_at,  # type: ignore[attr-defined]
        parent_id=ctx.parent_id,  # type: ignore[attr-defined]
        metadata=ctx.metadata,  # type: ignore[attr-defined]
    )
