"""Authentication middleware supporting JWT tokens and API keys.

Auth flow:
  1. Check for X-API-Key header → validate against hashed key store
  2. Check for Authorization: Bearer <JWT> → decode and validate
  3. Inject user_id, tenant_id, roles into request.state
  4. Reject with 401 if neither credential is present and valid

Public paths (health, docs, openapi.json) bypass auth.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request, Response
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.utils.config import get_settings
from src.utils.exceptions import AuthenticationError, TokenExpiredError
from src.utils.logger import get_logger
from src.utils.metrics import get_metrics
from src.utils.security import decode_token, mask_secret

logger = get_logger(__name__, component="auth_middleware")
metrics = get_metrics()

_PUBLIC_PATHS = {
    "/",
    "/health",
    "/health/ready",
    "/health/live",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/metrics",
}


class AuthMiddleware(BaseHTTPMiddleware):
    """FastAPI/Starlette middleware for request authentication.

    Populates request.state with:
      - user_id: str
      - tenant_id: str
      - roles: list[str]
      - auth_type: "jwt" | "api_key" | "anonymous"
    """

    def __init__(self, app: Any, api_key_store: Any | None = None) -> None:
        super().__init__(app)
        self._settings = get_settings()
        self._api_key_store = api_key_store

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if request.url.path in _PUBLIC_PATHS or request.url.path.startswith("/docs"):
            request.state.user_id = "anonymous"
            request.state.tenant_id = "default"
            request.state.roles = []
            request.state.auth_type = "anonymous"
            return await call_next(request)

        # Try API key first (cheaper check)
        api_key = request.headers.get(self._settings.api_key_header)
        if api_key:
            result = await self._validate_api_key(api_key, request)
            if result is not None:
                return result

        # Try JWT
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            result = await self._validate_jwt(token, request)
            if result is not None:
                return result

        # No valid credential found
        metrics.record_auth_event("none", "failure")
        logger.warning(
            "auth_rejected",
            path=request.url.path,
            method=request.method,
        )
        return JSONResponse(
            status_code=401,
            content={
                "type": "https://errors.mcp-system.io/auth/authentication_error",
                "title": "AuthenticationError",
                "status": 401,
                "detail": "Authentication required: provide X-API-Key or Bearer token",
            },
        )

    async def _validate_api_key(
        self, raw_key: str, request: Request
    ) -> Response | None:
        """Validate API key. Returns error Response on failure, None on success."""
        if self._api_key_store is None:
            # No store configured — accept any key with prefix "mcp_" in dev mode
            if self._settings.is_development and raw_key.startswith("mcp_"):
                self._set_state(request, "dev-user", "default", [], "api_key")
                metrics.record_auth_event("api_key", "success")
                return None
            return JSONResponse(
                status_code=401,
                content={"detail": "API key store not configured"},
            )

        try:
            tenant_id, user_id, roles = await self._api_key_store.validate(raw_key)
            self._set_state(request, user_id, tenant_id, roles, "api_key")
            metrics.record_auth_event("api_key", "success")
            logger.debug(
                "api_key_auth_success",
                user_id=user_id,
                key_preview=mask_secret(raw_key, 4),
            )
            return None
        except AuthenticationError as exc:
            metrics.record_auth_event("api_key", "failure")
            logger.warning("api_key_auth_failed", error=str(exc))
            return JSONResponse(
                status_code=401,
                content={"detail": str(exc)},
            )

    async def _validate_jwt(
        self, token: str, request: Request
    ) -> Response | None:
        """Validate JWT. Returns error Response on failure, None on success."""
        try:
            payload = decode_token(token)
            user_id = payload.get("sub", "unknown")
            tenant_id = payload.get("tenant_id", "default")
            roles = payload.get("roles", [])
            self._set_state(request, user_id, tenant_id, roles, "jwt")
            metrics.record_auth_event("jwt", "success")
            return None
        except TokenExpiredError:
            metrics.record_auth_event("jwt", "failure")
            return JSONResponse(
                status_code=401,
                content={
                    "type": "https://errors.mcp-system.io/auth/token_expired",
                    "title": "TokenExpiredError",
                    "status": 401,
                    "detail": "JWT token has expired",
                },
            )
        except AuthenticationError as exc:
            metrics.record_auth_event("jwt", "failure")
            return JSONResponse(
                status_code=401,
                content={
                    "type": "https://errors.mcp-system.io/auth/authentication_error",
                    "title": "AuthenticationError",
                    "status": 401,
                    "detail": str(exc),
                },
            )

    @staticmethod
    def _set_state(
        request: Request,
        user_id: str,
        tenant_id: str,
        roles: list[str],
        auth_type: str,
    ) -> None:
        request.state.user_id = user_id
        request.state.tenant_id = tenant_id
        request.state.roles = roles
        request.state.auth_type = auth_type
