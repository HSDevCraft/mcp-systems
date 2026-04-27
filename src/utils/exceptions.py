"""Custom exception hierarchy for the MCP System.

All exceptions carry an HTTP status code and an error code string
so the API layer can produce RFC 7807-compliant Problem Details responses
without any additional mapping logic.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any


class MCPError(Exception):
    """Base exception for all MCP System errors.

    Attributes:
        message: Human-readable error description.
        error_code: Machine-readable dot-separated code (e.g. "context.not_found").
        status_code: HTTP status code to return to the client.
        detail: Optional dict with additional structured context.
    """

    error_code: str = "mcp.error"
    status_code: int = HTTPStatus.INTERNAL_SERVER_ERROR

    def __init__(
        self,
        message: str,
        detail: dict[str, Any] | None = None,
        *,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}
        if error_code is not None:
            self.error_code = error_code

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": f"https://errors.mcp-system.io/{self.error_code.replace('.', '/')}",
            "title": self.__class__.__name__,
            "status": self.status_code,
            "detail": self.message,
            **self.detail,
        }


# ── Context ───────────────────────────────────────────────────────────────────


class ContextNotFoundError(MCPError):
    """Raised when a context ID does not exist in the store."""

    error_code = "context.not_found"
    status_code = HTTPStatus.NOT_FOUND

    def __init__(self, context_id: str) -> None:
        super().__init__(
            f"Context '{context_id}' not found",
            {"context_id": context_id},
        )


class ContextOverflowError(MCPError):
    """Raised when appending a message would exceed the token budget."""

    error_code = "context.overflow"
    status_code = HTTPStatus.UNPROCESSABLE_ENTITY

    def __init__(self, context_id: str, current: int, limit: int, needed: int) -> None:
        super().__init__(
            f"Context '{context_id}' token budget exceeded: "
            f"current={current}, needed={needed}, limit={limit}",
            {
                "context_id": context_id,
                "current_tokens": current,
                "needed_tokens": needed,
                "token_limit": limit,
            },
        )


class ContextSealedError(MCPError):
    """Raised when attempting to mutate a sealed context."""

    error_code = "context.sealed"
    status_code = HTTPStatus.CONFLICT

    def __init__(self, context_id: str) -> None:
        super().__init__(
            f"Context '{context_id}' is sealed and cannot be modified",
            {"context_id": context_id},
        )


class ContextExpiredError(MCPError):
    """Raised when a context has passed its TTL."""

    error_code = "context.expired"
    status_code = HTTPStatus.GONE

    def __init__(self, context_id: str) -> None:
        super().__init__(
            f"Context '{context_id}' has expired",
            {"context_id": context_id},
        )


# ── Module ────────────────────────────────────────────────────────────────────


class ModuleNotFoundError(MCPError):
    """Raised when a module name (or name@version) is not registered."""

    error_code = "module.not_found"
    status_code = HTTPStatus.NOT_FOUND

    def __init__(self, name: str, version: str | None = None) -> None:
        label = f"{name}@{version}" if version else name
        super().__init__(
            f"Module '{label}' is not registered",
            {"module_name": name, "module_version": version},
        )


class ModuleExecutionError(MCPError):
    """Raised when a module's execute() raises an unexpected exception."""

    error_code = "module.execution_error"
    status_code = HTTPStatus.INTERNAL_SERVER_ERROR

    def __init__(self, module_name: str, cause: str) -> None:
        super().__init__(
            f"Module '{module_name}' execution failed: {cause}",
            {"module_name": module_name, "cause": cause},
        )


class ModuleTimeoutError(MCPError):
    """Raised when a module execution exceeds its timeout."""

    error_code = "module.timeout"
    status_code = HTTPStatus.GATEWAY_TIMEOUT

    def __init__(self, module_name: str, timeout: float) -> None:
        super().__init__(
            f"Module '{module_name}' timed out after {timeout}s",
            {"module_name": module_name, "timeout_seconds": timeout},
        )


class ModuleLoadError(MCPError):
    """Raised when a module fails to load (on_load hook failure)."""

    error_code = "module.load_error"
    status_code = HTTPStatus.INTERNAL_SERVER_ERROR

    def __init__(self, module_name: str, cause: str) -> None:
        super().__init__(
            f"Module '{module_name}' failed to load: {cause}",
            {"module_name": module_name, "cause": cause},
        )


class ModuleValidationError(MCPError):
    """Raised when module input fails schema validation."""

    error_code = "module.validation_error"
    status_code = HTTPStatus.UNPROCESSABLE_ENTITY

    def __init__(self, module_name: str, errors: list[dict[str, Any]]) -> None:
        super().__init__(
            f"Module '{module_name}' input validation failed",
            {"module_name": module_name, "validation_errors": errors},
        )


# ── Memory ────────────────────────────────────────────────────────────────────


class MemoryError(MCPError):
    """Base class for memory subsystem errors."""

    error_code = "memory.error"
    status_code = HTTPStatus.INTERNAL_SERVER_ERROR


class MemoryWriteError(MemoryError):
    """Raised when a memory write fails."""

    error_code = "memory.write_error"

    def __init__(self, tier: str, cause: str) -> None:
        super().__init__(
            f"Memory write to tier '{tier}' failed: {cause}",
            {"tier": tier, "cause": cause},
        )


class MemoryReadError(MemoryError):
    """Raised when a memory read fails."""

    error_code = "memory.read_error"

    def __init__(self, tier: str, cause: str) -> None:
        super().__init__(
            f"Memory read from tier '{tier}' failed: {cause}",
            {"tier": tier, "cause": cause},
        )


# ── Auth ──────────────────────────────────────────────────────────────────────


class AuthenticationError(MCPError):
    """Raised when credentials are missing or invalid."""

    error_code = "auth.authentication_error"
    status_code = HTTPStatus.UNAUTHORIZED

    def __init__(self, message: str = "Authentication required") -> None:
        super().__init__(message)


class AuthorizationError(MCPError):
    """Raised when a user lacks permission for the requested action."""

    error_code = "auth.authorization_error"
    status_code = HTTPStatus.FORBIDDEN

    def __init__(self, action: str, resource: str) -> None:
        super().__init__(
            f"Not authorized to {action} on {resource}",
            {"action": action, "resource": resource},
        )


class TokenExpiredError(AuthenticationError):
    """Raised when a JWT token has expired."""

    error_code = "auth.token_expired"

    def __init__(self) -> None:
        super().__init__("Token has expired")


# ── Rate Limiting ─────────────────────────────────────────────────────────────


class RateLimitError(MCPError):
    """Raised when rate limit is exceeded."""

    error_code = "rate_limit.exceeded"
    status_code = HTTPStatus.TOO_MANY_REQUESTS

    def __init__(self, limit: int, window: int, retry_after: int) -> None:
        super().__init__(
            f"Rate limit of {limit} requests per {window}s exceeded",
            {
                "limit": limit,
                "window_seconds": window,
                "retry_after_seconds": retry_after,
            },
        )


# ── Validation ────────────────────────────────────────────────────────────────


class ValidationError(MCPError):
    """Raised when input fails general validation (outside Pydantic)."""

    error_code = "validation.error"
    status_code = HTTPStatus.UNPROCESSABLE_ENTITY

    def __init__(self, message: str, field: str | None = None) -> None:
        detail: dict[str, Any] = {}
        if field:
            detail["field"] = field
        super().__init__(message, detail)


# ── Infrastructure ────────────────────────────────────────────────────────────


class StorageUnavailableError(MCPError):
    """Raised when a storage backend (Redis/Qdrant) is unreachable."""

    error_code = "storage.unavailable"
    status_code = HTTPStatus.SERVICE_UNAVAILABLE

    def __init__(self, backend: str, cause: str) -> None:
        super().__init__(
            f"Storage backend '{backend}' is unavailable: {cause}",
            {"backend": backend, "cause": cause},
        )
