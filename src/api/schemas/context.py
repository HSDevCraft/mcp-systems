"""Request/response schemas for context endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class CreateContextRequest(BaseModel):
    session_id: UUID = Field(..., description="Session this context belongs to")
    max_tokens: int | None = Field(
        default=None, description="Token budget cap (defaults to server config)"
    )
    ttl_seconds: int | None = Field(
        default=None, description="Context TTL in seconds"
    )
    system_prompt: str | None = Field(
        default=None, description="Optional system message to pre-populate"
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class AppendMessageRequest(BaseModel):
    role: str = Field(
        ..., description="Message role: user | assistant | system | tool"
    )
    content: str = Field(..., description="Message content", min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AppendMessageResponse(BaseModel):
    context_id: UUID
    message_id: UUID
    role: str
    token_count: int
    context_token_count: int
    context_remaining_tokens: int


class ContextResponse(BaseModel):
    id: UUID
    session_id: UUID
    status: str
    token_count: int
    max_tokens: int
    remaining_tokens: int
    message_count: int
    created_at: datetime
    updated_at: datetime
    parent_id: UUID | None = None
    metadata: dict[str, Any] = {}


class ForkContextResponse(BaseModel):
    parent_id: UUID
    child_id: UUID
    child_context: ContextResponse


class MessageResponse(BaseModel):
    id: UUID
    role: str
    content: str
    token_count: int
    timestamp: datetime
    metadata: dict[str, Any] = {}


class GetMessagesResponse(BaseModel):
    context_id: UUID
    messages: list[MessageResponse]
    total: int
    page: int
    page_size: int
