"""Request/response logging middleware.

Emits a structured log entry for every request with:
  - method, path, status_code
  - latency_ms
  - request_id (generated per request)
  - user_id, tenant_id (from auth state)
  - content_length of response

Also binds request-level context vars so all downstream log entries
(from orchestrator, modules, memory) automatically include request_id.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from src.utils.logger import bind_request_context, clear_request_context, get_logger
from src.utils.metrics import get_metrics

logger = get_logger(__name__, component="request_logger")
metrics = get_metrics()


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Structured request/response logging middleware."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        user_id = getattr(request.state, "user_id", "anonymous")
        tenant_id = getattr(request.state, "tenant_id", "default")

        bind_request_context(
            request_id=request_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )

        start = time.perf_counter()
        status_code = 500

        try:
            response: Response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception as exc:
            logger.error(
                "unhandled_exception",
                path=request.url.path,
                method=request.method,
                error=str(exc),
            )
            raise
        finally:
            latency_ms = (time.perf_counter() - start) * 1000

            log_fn = logger.info if status_code < 400 else logger.warning
            if status_code >= 500:
                log_fn = logger.error

            log_fn(
                "request_complete",
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                latency_ms=round(latency_ms, 2),
                user_id=user_id,
                tenant_id=tenant_id,
            )

            metrics.record_request(
                method=request.method,
                endpoint=_normalize_path(request.url.path),
                status_code=status_code,
                latency_seconds=latency_ms / 1000,
            )

            clear_request_context()


def _normalize_path(path: str) -> str:
    """Replace UUID-like path segments with {id} for metric cardinality control."""
    import re
    uuid_pattern = re.compile(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        re.IGNORECASE,
    )
    return uuid_pattern.sub("{id}", path)
