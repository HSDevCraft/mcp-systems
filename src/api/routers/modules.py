"""Module execution and management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.api.schemas.common import ApiResponse
from src.api.schemas.modules import (
    ExecuteModuleRequest,
    ExecuteModuleResponse,
    ModuleHealthItem,
    ModuleHealthResponse,
    ModuleListResponse,
    ModuleSchemaResponse,
    ModuleSummary,
)
from src.utils.exceptions import ModuleNotFoundError
from src.utils.logger import get_logger

logger = get_logger(__name__, component="modules_router")

router = APIRouter(prefix="/modules", tags=["Modules"])


def _get_orchestrator(request: Request) -> object:
    return request.app.state.orchestrator


def _get_tenant(request: Request) -> str:
    return getattr(request.state, "tenant_id", "default")


def _get_user(request: Request) -> str:
    return getattr(request.state, "user_id", "anonymous")


@router.get(
    "/",
    response_model=ApiResponse[ModuleListResponse],
    summary="List all registered modules",
)
async def list_modules(
    request: Request,
    orchestrator: object = Depends(_get_orchestrator),
) -> ApiResponse[ModuleListResponse]:
    modules_data = orchestrator.list_modules()  # type: ignore[union-attr]
    summaries = [
        ModuleSummary(
            name=m["name"],
            version=m["version"],
            description=m["description"],
            tags=m["tags"],
            input_schema=m["input_schema"],
            output_schema=m["output_schema"],
        )
        for m in modules_data
    ]
    return ApiResponse.ok(
        ModuleListResponse(modules=summaries, total=len(summaries))
    )


@router.get(
    "/health",
    response_model=ApiResponse[ModuleHealthResponse],
    summary="Get health status of all modules",
)
async def module_health(
    request: Request,
    orchestrator: object = Depends(_get_orchestrator),
) -> ApiResponse[ModuleHealthResponse]:
    health_data = await orchestrator.module_health()  # type: ignore[union-attr]

    items: dict[str, ModuleHealthItem] = {}
    unhealthy = 0
    for key, val in health_data.items():
        is_healthy = val.get("healthy", False)
        items[key] = ModuleHealthItem(
            healthy=is_healthy,
            message=val.get("message", ""),
            latency_ms=val.get("latency_ms"),
        )
        if not is_healthy:
            unhealthy += 1

    overall = (
        "healthy" if unhealthy == 0
        else "degraded" if unhealthy < len(items)
        else "unhealthy"
    )

    return ApiResponse.ok(
        ModuleHealthResponse(
            overall=overall,
            modules=items,
            total=len(items),
            unhealthy_count=unhealthy,
        )
    )


@router.get(
    "/{name}",
    response_model=ApiResponse[ModuleSchemaResponse],
    summary="Get module schema and metadata",
)
async def get_module_schema(
    name: str,
    request: Request,
    orchestrator: object = Depends(_get_orchestrator),
    version: str | None = None,
) -> ApiResponse[ModuleSchemaResponse]:
    # Support name@version syntax
    if "@" in name:
        name, version = name.split("@", 1)

    try:
        schema = orchestrator.get_module_schema(name, version)  # type: ignore[union-attr]
    except ModuleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)

    return ApiResponse.ok(
        ModuleSchemaResponse(
            name=schema["name"],
            version=schema["version"],
            description=schema["description"],
            tags=schema["tags"],
            input_schema=schema["input_schema"],
            output_schema=schema["output_schema"],
        )
    )


@router.post(
    "/{name}/execute",
    response_model=ApiResponse[ExecuteModuleResponse],
    summary="Execute a module",
)
async def execute_module(
    name: str,
    body: ExecuteModuleRequest,
    request: Request,
    orchestrator: object = Depends(_get_orchestrator),
    tenant_id: str = Depends(_get_tenant),
    user_id: str = Depends(_get_user),
) -> ApiResponse[ExecuteModuleResponse]:
    # Support name@version syntax in path
    version = body.version
    if "@" in name:
        name, version = name.split("@", 1)

    try:
        result = await orchestrator.execute_module(  # type: ignore[union-attr]
            module_name=name,
            input_data=body.input,
            user_id=user_id,
            tenant_id=tenant_id,
            session_id=body.session_id,
            context_id=body.context_id,
            module_version=version,
            metadata=body.metadata,
        )
    except ModuleNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message)

    output_data = None
    if result.output is not None:
        if hasattr(result.output, "model_dump"):
            output_data = result.output.model_dump()
        else:
            output_data = result.output

    return ApiResponse.ok(
        ExecuteModuleResponse(
            module_name=result.module_name,
            module_version=result.module_version,
            output=output_data,
            latency_ms=round(result.latency_ms, 2),
            status=result.status,
            request_id=result.request_id,
            context_id=body.context_id,
            error=result.error,
        ),
        latency_ms=round(result.latency_ms, 2),
    )
