"""Context Manager — owns the full lifecycle of MCP context objects.

A context is the unit of stateful interaction between a client and the system.
It tracks a message history, token budget, lifecycle status, and metadata.
All mutations are persisted to Redis (short-term store).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

import orjson
from pydantic import BaseModel, Field

from src.utils.config import Settings, get_settings
from src.utils.exceptions import (
    ContextExpiredError,
    ContextNotFoundError,
    ContextOverflowError,
    ContextSealedError,
)
from src.utils.logger import get_logger
from src.utils.metrics import get_metrics
from src.utils.security import count_tokens, sanitize_string

logger = get_logger(__name__, component="context_manager")
metrics = get_metrics()


# ── Domain Models ─────────────────────────────────────────────────────────────


class ContextStatus(str, Enum):
    ACTIVE = "active"
    SEALED = "sealed"
    EXPIRED = "expired"
    ARCHIVED = "archived"


class OverflowStrategy(str, Enum):
    EVICT = "evict"        # Drop oldest messages
    SUMMARIZE = "summarize"  # Summarize oldest messages (requires LLM)
    REJECT = "reject"      # Raise ContextOverflowError


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class MessagePart(BaseModel):
    type: str           # "text" | "image_url" | "tool_result"
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    role: MessageRole
    content: str | list[MessagePart]
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    token_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    def get_text_content(self) -> str:
        """Return plain-text representation of content."""
        if isinstance(self.content, str):
            return self.content
        return " ".join(
            part.content for part in self.content if isinstance(part.content, str)
        )


class Context(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    tenant_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ttl_seconds: int = 86400
    token_count: int = 0
    max_tokens: int = 128000
    status: ContextStatus = ContextStatus.ACTIVE
    parent_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def is_active(self) -> bool:
        return self.status == ContextStatus.ACTIVE

    def is_sealed(self) -> bool:
        return self.status == ContextStatus.SEALED

    def remaining_tokens(self) -> int:
        return max(0, self.max_tokens - self.token_count)


# ── Context Manager ────────────────────────────────────────────────────────────


class ContextManager:
    """Manages context lifecycle: create, get, append, fork, seal, expire.

    All operations are async and backed by Redis for shared state across
    API replicas. Messages are stored separately in a Redis List for
    efficient append and range queries.

    Args:
        redis_client: Async Redis client instance.
        settings: Application settings (for key prefixes, TTL, etc.).
        overflow_strategy: What to do when token budget is exceeded.
    """

    def __init__(
        self,
        redis_client: Any,
        settings: Settings | None = None,
        overflow_strategy: OverflowStrategy = OverflowStrategy.EVICT,
    ) -> None:
        self._redis = redis_client
        self._settings = settings or get_settings()
        self._overflow_strategy = overflow_strategy
        self._lock_prefix = "mcp:lock:ctx:"

    # ── Context CRUD ──────────────────────────────────────────────────────────

    async def create(
        self,
        session_id: UUID,
        tenant_id: str,
        max_tokens: int | None = None,
        ttl_seconds: int | None = None,
        metadata: dict[str, Any] | None = None,
        system_prompt: str | None = None,
    ) -> Context:
        """Create and persist a new context.

        Args:
            session_id: Session this context belongs to.
            tenant_id: Tenant namespace.
            max_tokens: Token budget cap (defaults to settings value).
            ttl_seconds: Context TTL (defaults to settings value).
            metadata: Arbitrary client-supplied metadata.
            system_prompt: Optional system message to pre-populate.

        Returns:
            Newly created Context object.
        """
        context = Context(
            session_id=session_id,
            tenant_id=tenant_id,
            max_tokens=max_tokens or self._settings.context_max_tokens,
            ttl_seconds=ttl_seconds or self._settings.context_ttl_seconds,
            metadata=metadata or {},
        )

        await self._save_context(context)

        if system_prompt:
            system_msg = Message(
                role=MessageRole.SYSTEM,
                content=sanitize_string(system_prompt),
                token_count=count_tokens(system_prompt),
            )
            await self._append_message_internal(context, system_msg)

        metrics.record_context_operation("create", "success")
        logger.info(
            "context_created",
            context_id=str(context.id),
            session_id=str(session_id),
            tenant_id=tenant_id,
        )
        return context

    async def get(self, context_id: UUID, tenant_id: str) -> Context:
        """Load a context from Redis.

        Args:
            context_id: Context identifier.
            tenant_id: Tenant namespace (for key scoping).

        Returns:
            Deserialized Context object.

        Raises:
            ContextNotFoundError: If context does not exist.
            ContextExpiredError: If context TTL has passed.
        """
        key = self._settings.get_context_key(tenant_id, str(context_id))
        raw = await self._redis.get(key)

        if raw is None:
            raise ContextNotFoundError(str(context_id))

        context = Context.model_validate(orjson.loads(raw))

        if context.status == ContextStatus.EXPIRED:
            raise ContextExpiredError(str(context_id))

        # Sliding TTL refresh on access
        await self._redis.expire(key, context.ttl_seconds)
        metrics.record_context_operation("get", "success")
        return context

    async def get_or_create(
        self,
        context_id: UUID | None,
        session_id: UUID,
        tenant_id: str,
        **create_kwargs: Any,
    ) -> Context:
        """Get existing context or create a new one.

        Args:
            context_id: Optional existing context ID.
            session_id: Session ID for new context creation.
            tenant_id: Tenant namespace.
            **create_kwargs: Extra kwargs passed to create().

        Returns:
            Existing or newly created Context.
        """
        if context_id is not None:
            try:
                return await self.get(context_id, tenant_id)
            except ContextNotFoundError:
                pass
        return await self.create(session_id=session_id, tenant_id=tenant_id, **create_kwargs)

    async def append_message(
        self,
        context_id: UUID,
        tenant_id: str,
        role: MessageRole,
        content: str | list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> tuple[Context, Message]:
        """Append a message to a context with token budget enforcement.

        Args:
            context_id: Target context.
            tenant_id: Tenant namespace.
            role: Message role.
            content: Message content (str or multimodal parts).
            metadata: Optional message metadata.

        Returns:
            Tuple of (updated Context, created Message).

        Raises:
            ContextNotFoundError: If context does not exist.
            ContextSealedError: If context is sealed.
            ContextOverflowError: If token budget exceeded and strategy is REJECT.
        """
        async with self._acquire_lock(str(context_id)):
            context = await self.get(context_id, tenant_id)

            if context.is_sealed():
                raise ContextSealedError(str(context_id))

            if isinstance(content, str):
                text = sanitize_string(content)
                msg_content: str | list[MessagePart] = text
            else:
                parts = [MessagePart(**p) for p in content]
                text = " ".join(p.content for p in parts)
                msg_content = parts

            new_tokens = count_tokens(text)
            message = Message(
                role=role,
                content=msg_content,
                token_count=new_tokens,
                metadata=metadata or {},
            )

            if context.token_count + new_tokens > context.max_tokens:
                context = await self._handle_overflow(context, new_tokens)

            context = await self._append_message_internal(context, message)
            metrics.record_context_operation("append", "success")
            metrics.observe_context_tokens(context.token_count)
            return context, message

    async def fork(
        self,
        context_id: UUID,
        tenant_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> Context:
        """Create a child context with the parent's message history.

        The parent context remains ACTIVE. The child is an independent copy
        that can diverge without affecting the parent.

        Args:
            context_id: Parent context ID.
            tenant_id: Tenant namespace.
            metadata: Optional metadata for the child context.

        Returns:
            Newly created child Context.
        """
        parent = await self.get(context_id, tenant_id)
        parent_messages = await self.get_messages(context_id, tenant_id)

        child = Context(
            session_id=parent.session_id,
            tenant_id=tenant_id,
            max_tokens=parent.max_tokens,
            ttl_seconds=parent.ttl_seconds,
            token_count=parent.token_count,
            parent_id=parent.id,
            metadata={**parent.metadata, **(metadata or {}), "forked_from": str(parent.id)},
        )
        await self._save_context(child)

        # Copy parent messages to child
        if parent_messages:
            msgs_key = self._settings.get_messages_key(tenant_id, str(child.id))
            pipeline = self._redis.pipeline()
            for msg in reversed(parent_messages):
                pipeline.lpush(msgs_key, orjson.dumps(msg.model_dump(mode="json")))
            pipeline.expire(msgs_key, child.ttl_seconds)
            await pipeline.execute()

        metrics.record_context_operation("fork", "success")
        logger.info(
            "context_forked",
            parent_id=str(context_id),
            child_id=str(child.id),
            tenant_id=tenant_id,
        )
        return child

    async def seal(self, context_id: UUID, tenant_id: str) -> Context:
        """Mark a context as sealed (immutable).

        Sealed contexts cannot receive new messages. They are preserved
        for audit and replay purposes.
        """
        async with self._acquire_lock(str(context_id)):
            context = await self.get(context_id, tenant_id)
            context.status = ContextStatus.SEALED
            context.updated_at = datetime.now(UTC)
            await self._save_context(context)
        metrics.record_context_operation("seal", "success")
        return context

    async def expire(self, context_id: UUID, tenant_id: str) -> None:
        """Immediately expire a context (soft delete).

        The Redis key is deleted. Long-term memory entries for this context
        are soft-deleted asynchronously.
        """
        ctx_key = self._settings.get_context_key(tenant_id, str(context_id))
        msgs_key = self._settings.get_messages_key(tenant_id, str(context_id))
        await self._redis.delete(ctx_key, msgs_key)
        metrics.record_context_operation("expire", "success")
        logger.info("context_expired", context_id=str(context_id), tenant_id=tenant_id)

    async def get_messages(
        self,
        context_id: UUID,
        tenant_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Message]:
        """Retrieve messages for a context in chronological order.

        Args:
            context_id: Context identifier.
            tenant_id: Tenant namespace.
            limit: Max number of messages to return.
            offset: Skip the first N messages (oldest first).

        Returns:
            List of Message objects, oldest first.
        """
        key = self._settings.get_messages_key(tenant_id, str(context_id))
        # Redis list is stored newest-first (LPUSH), so we reverse the range
        total = await self._redis.llen(key)
        start = max(0, total - offset - limit)
        end = max(-1, total - offset - 1)

        if start > end:
            return []

        raw_messages = await self._redis.lrange(key, start, end)
        messages = [
            Message.model_validate(orjson.loads(raw))
            for raw in reversed(raw_messages)
        ]
        return messages

    # ── Internal Helpers ──────────────────────────────────────────────────────

    async def _save_context(self, context: Context) -> None:
        key = self._settings.get_context_key(context.tenant_id, str(context.id))
        await self._redis.set(
            key,
            orjson.dumps(context.model_dump(mode="json")),
            ex=context.ttl_seconds,
        )

    async def _append_message_internal(
        self, context: Context, message: Message
    ) -> Context:
        msgs_key = self._settings.get_messages_key(
            context.tenant_id, str(context.id)
        )
        pipeline = self._redis.pipeline()
        pipeline.lpush(msgs_key, orjson.dumps(message.model_dump(mode="json")))
        pipeline.ltrim(msgs_key, 0, self._settings.context_max_messages - 1)
        pipeline.expire(msgs_key, context.ttl_seconds)
        await pipeline.execute()

        context.token_count += message.token_count
        context.updated_at = datetime.now(UTC)
        await self._save_context(context)
        return context

    async def _handle_overflow(
        self, context: Context, needed_tokens: int
    ) -> Context:
        """Apply overflow strategy when token budget is exceeded."""
        if self._overflow_strategy == OverflowStrategy.REJECT:
            raise ContextOverflowError(
                str(context.id),
                context.token_count,
                context.max_tokens,
                needed_tokens,
            )

        if self._overflow_strategy == OverflowStrategy.EVICT:
            # Remove oldest messages until we have enough budget
            msgs_key = self._settings.get_messages_key(
                context.tenant_id, str(context.id)
            )
            while context.token_count + needed_tokens > context.max_tokens:
                raw = await self._redis.rpop(msgs_key)
                if raw is None:
                    break
                evicted = Message.model_validate(orjson.loads(raw))
                context.token_count = max(0, context.token_count - evicted.token_count)
            await self._save_context(context)
            logger.warning(
                "context_overflow_evicted",
                context_id=str(context.id),
                remaining_tokens=context.token_count,
            )

        return context

    def _acquire_lock(self, resource_id: str) -> Any:
        """Return an async context manager for distributed locking via Redis."""
        return _RedisLock(self._redis, f"{self._lock_prefix}{resource_id}", ttl=5)


class _RedisLock:
    """Simple Redis-based distributed lock using SET NX PX."""

    def __init__(self, redis: Any, key: str, ttl: int = 5) -> None:
        self._redis = redis
        self._key = key
        self._ttl = ttl
        self._token = str(uuid4())

    async def __aenter__(self) -> "_RedisLock":
        for attempt in range(10):
            acquired = await self._redis.set(
                self._key, self._token, nx=True, ex=self._ttl
            )
            if acquired:
                return self
            await asyncio.sleep(0.05 * (attempt + 1))
        raise TimeoutError(f"Could not acquire lock on {self._key}")

    async def __aexit__(self, *args: Any) -> None:
        stored = await self._redis.get(self._key)
        if stored and stored.decode() == self._token:
            await self._redis.delete(self._key)
