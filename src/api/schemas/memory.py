"""Request/response schemas for memory endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class StoreMemoryRequest(BaseModel):
    content: str = Field(..., description="Text content to store", min_length=1)
    tier: str = Field(
        default="short_term",
        description="Target tier: short_term | long_term",
    )
    session_id: UUID | None = Field(default=None, description="Session scoping")
    context_id: UUID | None = Field(default=None, description="Context scoping")
    role: str = Field(default="assistant", description="Role tag for the memory item")
    tags: list[str] = Field(default_factory=list)
    ttl: int | None = Field(default=None, description="TTL in seconds (short-term only)")
    metadata: dict[str, Any] = Field(default_factory=dict)


class StoreMemoryResponse(BaseModel):
    memory_id: str
    tier: str
    content_preview: str
    token_count: int


class RetrieveMemoryRequest(BaseModel):
    query: str = Field(..., description="Semantic search query", min_length=1)
    tier: str = Field(default="long_term", description="Source tier: short_term | long_term")
    session_id: UUID | None = Field(default=None)
    k: int = Field(default=5, ge=1, le=50)
    score_threshold: float = Field(default=0.6, ge=0.0, le=1.0)


class MemoryItemResponse(BaseModel):
    id: str
    content: str
    tier: str
    role: str
    timestamp: datetime
    importance_score: float
    token_count: int
    tags: list[str] = []
    metadata: dict[str, Any] = {}


class RetrieveMemoryResponse(BaseModel):
    results: list[MemoryItemResponse]
    total: int
    query: str
    tier: str


class MemoryStatsResponse(BaseModel):
    short_term_memory_bytes: int
    long_term_vectors: int
    redis_connected: bool
    qdrant_connected: bool
