"""FastAPI dependency injection — centralised.

All routers import dependencies from here rather than reading
request.app.state directly. This makes testing easier (override at
one place) and keeps the coupling explicit.

Usage in a router:
    from src.api.dependencies import get_orchestrator, get_tenant_id

    @router.post("/")
    async def my_endpoint(
        orchestrator: Orchestrator = Depends(get_orchestrator),
        tenant_id: str = Depends(get_tenant_id),
    ) -> ...:
        ...
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Request

from src.core.orchestrator import Orchestrator


def get_orchestrator(request: Request) -> Orchestrator:
    """Return the singleton Orchestrator from app state."""
    return request.app.state.orchestrator  # type: ignore[no-any-return]


def get_tenant_id(request: Request) -> str:
    """Return the authenticated tenant_id from request state."""
    return getattr(request.state, "tenant_id", "default")


def get_user_id(request: Request) -> str:
    """Return the authenticated user_id from request state."""
    return getattr(request.state, "user_id", "anonymous")


def get_roles(request: Request) -> list[str]:
    """Return the authenticated user's roles."""
    return getattr(request.state, "roles", [])


def get_request_id(request: Request) -> str:
    """Return the request-scoped UUID string."""
    return getattr(request.state, "request_id", "")


def require_role(required: str) -> Any:
    """Dependency factory — asserts caller has a specific role."""
    from src.utils.exceptions import AuthorizationError

    def _check(roles: list[str] = Depends(get_roles)) -> None:
        if required not in roles and "admin" not in roles:
            raise AuthorizationError(action=f"access:{required}", resource="endpoint")

    return Depends(_check)
