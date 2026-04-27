"""Common API response envelope schemas."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """Standard API response envelope.

    All endpoints return this envelope. Clients check the 'error' field
    first; if None, 'data' contains the result.
    """

    data: T | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    error: ErrorDetail | None = None

    @classmethod
    def ok(cls, data: T, **meta: Any) -> "ApiResponse[T]":
        return cls(data=data, meta=meta)

    @classmethod
    def fail(cls, error: "ErrorDetail") -> "ApiResponse[None]":
        return cls(data=None, error=error)


class ErrorDetail(BaseModel):
    """RFC 7807 Problem Details."""

    type: str = "https://errors.mcp-system.io/error"
    title: str
    status: int
    detail: str
    instance: str | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorDetail


class PaginationMeta(BaseModel):
    total: int
    page: int
    page_size: int
    has_next: bool
    has_prev: bool


class HealthCheck(BaseModel):
    status: str
    message: str = ""
    latency_ms: float | None = None


class HealthResponse(BaseModel):
    status: str  # "healthy" | "degraded" | "unhealthy"
    version: str = "0.1.0"
    environment: str = "development"
    checks: dict[str, HealthCheck] = Field(default_factory=dict)
