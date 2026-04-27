"""Abstract base classes and domain models for the memory subsystem."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class MemoryTier(str, Enum):
    WORKING = "working"
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"


class MemoryItem(BaseModel):
    """A single memory item stored in the memory system."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    content: str
    tier: MemoryTier
    tenant_id: str
    session_id: str | None = None
    context_id: str | None = None
    role: str = "assistant"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    importance_score: float = 1.0
    token_count: int = 0
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    expired: bool = False

    def to_message_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "metadata": {
                "memory_id": self.id,
                "timestamp": self.timestamp.isoformat(),
                "importance": self.importance_score,
                **self.metadata,
            },
        }


class MemoryStats(BaseModel):
    """Statistics across all memory tiers."""

    short_term_count: int = 0
    long_term_count: int = 0
    short_term_memory_bytes: int = 0
    long_term_vectors: int = 0
    redis_connected: bool = False
    qdrant_connected: bool = False


class HealthStatus(BaseModel):
    healthy: bool
    message: str = ""
    latency_ms: float | None = None


class AbstractMemoryStore(ABC):
    """Abstract interface for a single-tier memory store."""

    @abstractmethod
    async def write(
        self,
        content: str,
        metadata: dict[str, Any],
        ttl: int | None = None,
    ) -> str:
        """Write a memory item. Returns memory_id."""
        ...

    @abstractmethod
    async def retrieve(
        self,
        query: str | None,
        k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[MemoryItem]:
        """Retrieve memory items matching a query or filters."""
        ...

    @abstractmethod
    async def delete(self, memory_id: str) -> bool:
        """Delete a memory item. Returns True if deleted."""
        ...

    @abstractmethod
    async def ping(self) -> bool:
        """Health check. Returns True if backend is reachable."""
        ...

    @abstractmethod
    async def get_stats(self) -> dict[str, Any]:
        """Return tier-specific statistics."""
        ...
