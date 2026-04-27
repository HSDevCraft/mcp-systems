"""Sliding-window rate limiting middleware backed by Redis.

Rate limit is applied per (tenant_id, api_key/user_id) pair.
When the limit is hit, a 429 is returned with a Retry-After header.

Algorithm: Sliding window counter using Redis INCR + EXPIRE.
  - On each request: INCR the counter key, set EXPIRE if key is new
  - If counter > limit: reject with 429
  - Key format: mcp:ratelimit:{tenant_id}:{user_id}:{window_start}
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.utils.config import get_settings
from src.utils.logger import get_logger
from src.utils.metrics import get_metrics

logger = get_logger(__name__, component="rate_limit_middleware")
metrics = get_metrics()

_EXEMPT_PATHS = {"/health", "/health/ready", "/health/live", "/metrics"}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-tenant sliding-window rate limiter.

    Args:
        app: ASGI application.
        redis_client: Async Redis client for counter storage.
    """

    def __init__(self, app: Any, redis_client: Any | None = None) -> None:
        super().__init__(app)
        self._redis = redis_client
        self._settings = get_settings()

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if not self._settings.rate_limit_enabled:
            return await call_next(request)

        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        if self._redis is None:
            return await call_next(request)

        tenant_id = getattr(request.state, "tenant_id", "default")
        user_id = getattr(request.state, "user_id", "anonymous")
        window = self._settings.rate_limit_window
        limit = self._settings.rate_limit_requests

        window_start = int(time.time()) // window
        rate_key = f"mcp:ratelimit:{tenant_id}:{user_id}:{window_start}"

        try:
            current = await self._redis.incr(rate_key)
            if current == 1:
                await self._redis.expire(rate_key, window * 2)

            if current > limit:
                retry_after = window - (int(time.time()) % window)
                metrics.record_rate_limit_event("rejected")
                logger.warning(
                    "rate_limit_exceeded",
                    tenant_id=tenant_id,
                    user_id=user_id,
                    current=current,
                    limit=limit,
                )
                return JSONResponse(
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                    content={
                        "type": "https://errors.mcp-system.io/rate_limit/exceeded",
                        "title": "RateLimitError",
                        "status": 429,
                        "detail": f"Rate limit of {limit} requests per {window}s exceeded",
                        "retry_after_seconds": retry_after,
                    },
                )

            metrics.record_rate_limit_event("allowed")

        except Exception as exc:
            logger.warning("rate_limit_check_failed", error=str(exc))
            # Fail open — don't block requests if Redis is unavailable

        return await call_next(request)
