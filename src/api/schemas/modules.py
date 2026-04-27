"""Request/response schemas for module endpoints."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ExecuteModuleRequest(BaseModel):
    input: dict[str, Any] = Field(..., description="Module input (validated against module schema)")
    context_id: UUID | None = Field(default=None, description="MCP context to append result to")
    session_id: UUID | None = Field(default=None, description="Session for memory retrieval")
    version: str | None = Field(default=None, description="Pin to a specific module version")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecuteModuleResponse(BaseModel):
    module_name: str
    module_version: str
    output: Any
    latency_ms: float
    status: str
    request_id: UUID
    context_id: UUID | None = None
    error: str | None = None


class ModuleSummary(BaseModel):
    name: str
    version: str
    description: str
    tags: list[str] = []
    input_schema: dict[str, Any] = {}
    output_schema: dict[str, Any] = {}


class ModuleListResponse(BaseModel):
    modules: list[ModuleSummary]
    total: int


class ModuleSchemaResponse(BaseModel):
    name: str
    version: str
    description: str
    tags: list[str] = []
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


class ModuleHealthItem(BaseModel):
    healthy: bool
    message: str = ""
    latency_ms: float | None = None


class ModuleHealthResponse(BaseModel):
    overall: str  # "healthy" | "degraded" | "unhealthy"
    modules: dict[str, ModuleHealthItem]
    total: int
    unhealthy_count: int
