"""Health check endpoints for liveness, readiness, and deep health probes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from src.api.schemas.common import ApiResponse, HealthCheck, HealthResponse
from src.utils.config import get_settings
from src.utils.logger import get_logger

logger = get_logger(__name__, component="health_router")

router = APIRouter(prefix="/health", tags=["Health"])


@router.get(
    "/live",
    response_model=dict,
    summary="Liveness probe (always returns 200 if process is running)",
)
async def liveness() -> dict:
    return {"status": "alive"}


@router.get(
    "/ready",
    response_model=dict,
    summary="Readiness probe (checks if dependencies are ready)",
)
async def readiness(request: Request) -> dict:
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        return {"status": "not_ready", "reason": "orchestrator not initialized"}

    try:
        redis_ok = await orchestrator.health_check()
        return {"status": "ready"}
    except Exception as exc:
        return {"status": "not_ready", "reason": str(exc)}


@router.get(
    "/",
    response_model=ApiResponse[HealthResponse],
    summary="Deep health check across all subsystems",
)
async def health_check(request: Request) -> ApiResponse[HealthResponse]:
    settings = get_settings()
    orchestrator = getattr(request.app.state, "orchestrator", None)

    if orchestrator is None:
        return ApiResponse.ok(
            HealthResponse(
                status="degraded",
                version="0.1.0",
                environment=settings.mcp_env.value,
                checks={"orchestrator": HealthCheck(status="unhealthy", message="not initialized")},
            )
        )

    try:
        health_data = await orchestrator.health_check()
    except Exception as exc:
        health_data = {"status": "unhealthy", "checks": {}, "error": str(exc)}

    checks: dict[str, HealthCheck] = {}
    for name, info in health_data.get("checks", {}).items():
        checks[name] = HealthCheck(
            status=info.get("status", "unknown"),
            message=info.get("error", ""),
        )

    return ApiResponse.ok(
        HealthResponse(
            status=health_data.get("status", "unknown"),
            version="0.1.0",
            environment=settings.mcp_env.value,
            checks=checks,
        )
    )
