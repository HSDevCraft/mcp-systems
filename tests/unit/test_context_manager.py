"""Unit tests for ContextManager."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.context_manager import (
    Context,
    ContextManager,
    ContextStatus,
    Message,
    MessageRole,
    OverflowStrategy,
)
from src.utils.exceptions import (
    ContextNotFoundError,
    ContextOverflowError,
    ContextSealedError,
)


pytestmark = pytest.mark.unit


class TestContextCreate:
    async def test_create_returns_active_context(self, context_manager, test_settings):
        ctx = await context_manager.create(
            session_id=uuid4(), tenant_id="test-tenant"
        )
        assert ctx.id is not None
        assert ctx.status == ContextStatus.ACTIVE
        assert ctx.token_count == 0

    async def test_create_with_system_prompt_counts_tokens(self, context_manager):
        ctx = await context_manager.create(
            session_id=uuid4(),
            tenant_id="test-tenant",
            system_prompt="You are a helpful assistant.",
        )
        assert ctx.token_count > 0

    async def test_create_with_custom_max_tokens(self, context_manager):
        ctx = await context_manager.create(
            session_id=uuid4(), tenant_id="test-tenant", max_tokens=512
        )
        assert ctx.max_tokens == 512

    async def test_create_stores_metadata(self, context_manager):
        meta = {"project": "alpha", "env": "test"}
        ctx = await context_manager.create(
            session_id=uuid4(), tenant_id="test-tenant", metadata=meta
        )
        assert ctx.metadata["project"] == "alpha"


class TestContextGet:
    async def test_get_existing_context(self, context_manager):
        created = await context_manager.create(session_id=uuid4(), tenant_id="t1")
        fetched = await context_manager.get(created.id, "t1")
        assert fetched.id == created.id

    async def test_get_nonexistent_raises_not_found(self, context_manager):
        with pytest.raises(ContextNotFoundError):
            await context_manager.get(uuid4(), "t1")

    async def test_get_wrong_tenant_raises_not_found(self, context_manager):
        ctx = await context_manager.create(session_id=uuid4(), tenant_id="tenant-a")
        with pytest.raises(ContextNotFoundError):
            await context_manager.get(ctx.id, "tenant-b")


class TestAppendMessage:
    async def test_append_user_message(self, context_manager):
        ctx = await context_manager.create(session_id=uuid4(), tenant_id="t1")
        updated, msg = await context_manager.append_message(
            context_id=ctx.id,
            tenant_id="t1",
            role=MessageRole.USER,
            content="Hello world",
        )
        assert updated.token_count > 0
        assert msg.role == MessageRole.USER
        assert msg.content == "Hello world"

    async def test_append_increments_token_count(self, context_manager):
        ctx = await context_manager.create(session_id=uuid4(), tenant_id="t1")
        updated, _ = await context_manager.append_message(
            context_id=ctx.id, tenant_id="t1", role=MessageRole.USER, content="Test"
        )
        assert updated.token_count > ctx.token_count

    async def test_append_to_sealed_context_raises(self, context_manager):
        ctx = await context_manager.create(session_id=uuid4(), tenant_id="t1")
        await context_manager.seal(ctx.id, "t1")
        with pytest.raises(ContextSealedError):
            await context_manager.append_message(
                ctx.id, "t1", MessageRole.USER, "after seal"
            )

    async def test_overflow_evict_strategy(self, fake_redis, test_settings):
        mgr = ContextManager(
            redis_client=fake_redis,
            settings=test_settings,
            overflow_strategy=OverflowStrategy.EVICT,
        )
        ctx = await mgr.create(session_id=uuid4(), tenant_id="t1", max_tokens=20)
        # Fill up to near limit
        for _ in range(3):
            await mgr.append_message(ctx.id, "t1", MessageRole.USER, "word word word")
        # Should evict rather than raise
        updated, _ = await mgr.append_message(
            ctx.id, "t1", MessageRole.USER, "new message"
        )
        assert updated.token_count <= updated.max_tokens

    async def test_overflow_reject_strategy_raises(self, fake_redis, test_settings):
        mgr = ContextManager(
            redis_client=fake_redis,
            settings=test_settings,
            overflow_strategy=OverflowStrategy.REJECT,
        )
        ctx = await mgr.create(session_id=uuid4(), tenant_id="t1", max_tokens=5)
        with pytest.raises(ContextOverflowError):
            await mgr.append_message(
                ctx.id, "t1", MessageRole.USER, "this is a very long message that exceeds budget"
            )


class TestContextFork:
    async def test_fork_creates_child_with_parent_id(self, context_manager):
        parent = await context_manager.create(session_id=uuid4(), tenant_id="t1")
        child = await context_manager.fork(parent.id, "t1")
        assert child.parent_id == parent.id
        assert child.id != parent.id

    async def test_fork_child_has_parent_messages(self, context_manager):
        parent = await context_manager.create(session_id=uuid4(), tenant_id="t1")
        await context_manager.append_message(
            parent.id, "t1", MessageRole.USER, "initial message"
        )
        child = await context_manager.fork(parent.id, "t1")
        child_msgs = await context_manager.get_messages(child.id, "t1")
        assert len(child_msgs) >= 1

    async def test_fork_parent_remains_active(self, context_manager):
        parent = await context_manager.create(session_id=uuid4(), tenant_id="t1")
        await context_manager.fork(parent.id, "t1")
        parent_after = await context_manager.get(parent.id, "t1")
        assert parent_after.status == ContextStatus.ACTIVE


class TestContextSealAndExpire:
    async def test_seal_changes_status(self, context_manager):
        ctx = await context_manager.create(session_id=uuid4(), tenant_id="t1")
        sealed = await context_manager.seal(ctx.id, "t1")
        assert sealed.status == ContextStatus.SEALED

    async def test_expire_removes_context(self, context_manager):
        ctx = await context_manager.create(session_id=uuid4(), tenant_id="t1")
        await context_manager.expire(ctx.id, "t1")
        with pytest.raises(ContextNotFoundError):
            await context_manager.get(ctx.id, "t1")


class TestGetMessages:
    async def test_get_messages_returns_chronological_order(self, context_manager):
        ctx = await context_manager.create(session_id=uuid4(), tenant_id="t1")
        await context_manager.append_message(ctx.id, "t1", MessageRole.USER, "first")
        await context_manager.append_message(ctx.id, "t1", MessageRole.ASSISTANT, "second")
        messages = await context_manager.get_messages(ctx.id, "t1")
        assert messages[0].content == "first"
        assert messages[1].content == "second"

    async def test_get_messages_empty_context(self, context_manager):
        ctx = await context_manager.create(session_id=uuid4(), tenant_id="t1")
        messages = await context_manager.get_messages(ctx.id, "t1")
        assert messages == []
