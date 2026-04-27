"""Unit tests for the memory subsystem."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.memory.base import MemoryTier
from src.memory.manager import MemoryManager
from src.memory.short_term import ShortTermMemoryStore

pytestmark = pytest.mark.unit


class TestShortTermMemoryStore:
    async def test_write_and_retrieve_by_session(self, short_term_store):
        session_id = str(uuid4())
        await short_term_store.write(
            content="Test memory content",
            metadata={"tenant_id": "t1", "session_id": session_id, "role": "user"},
        )
        items = await short_term_store.retrieve(
            query=None,
            k=10,
            filters={"tenant_id": "t1", "session_id": session_id},
        )
        assert len(items) == 1
        assert items[0].content == "Test memory content"

    async def test_write_returns_memory_id(self, short_term_store):
        memory_id = await short_term_store.write(
            content="content",
            metadata={"tenant_id": "t1"},
        )
        assert isinstance(memory_id, str)
        assert len(memory_id) > 0

    async def test_write_idempotent_same_content(self, short_term_store):
        session_id = str(uuid4())
        meta = {"tenant_id": "t1", "session_id": session_id}
        id1 = await short_term_store.write(content="same content", metadata=meta)
        id2 = await short_term_store.write(content="same content", metadata=meta)
        assert id1 == id2

    async def test_retrieve_empty_session(self, short_term_store):
        items = await short_term_store.retrieve(
            query=None,
            k=5,
            filters={"tenant_id": "t1", "session_id": str(uuid4())},
        )
        assert items == []

    async def test_delete_removes_item(self, short_term_store):
        memory_id = await short_term_store.write(
            content="to delete",
            metadata={"tenant_id": "t1"},
        )
        deleted = await short_term_store.delete(memory_id, tenant_id="t1")
        assert deleted is True

    async def test_ping_returns_true(self, short_term_store):
        result = await short_term_store.ping()
        assert result is True

    async def test_retrieve_respects_limit(self, short_term_store):
        session_id = str(uuid4())
        meta = {"tenant_id": "t1", "session_id": session_id}
        for i in range(5):
            await short_term_store.write(content=f"item {i} unique text", metadata=meta)
        items = await short_term_store.retrieve(query=None, k=3, filters=meta)
        assert len(items) <= 3


class TestMemoryManager:
    async def test_write_short_term(self, memory_manager):
        memory_id = await memory_manager.write(
            content="short term content",
            metadata={"tenant_id": "t1", "session_id": str(uuid4())},
            tier=MemoryTier.SHORT_TERM,
        )
        assert memory_id is not None

    async def test_write_long_term(self, memory_manager):
        memory_id = await memory_manager.write(
            content="long term content for vector store",
            metadata={"tenant_id": "t1"},
            tier=MemoryTier.LONG_TERM,
        )
        assert memory_id is not None

    async def test_write_working_raises(self, memory_manager):
        with pytest.raises(KeyError):
            await memory_manager.write(
                content="working",
                metadata={},
                tier=MemoryTier.WORKING,
            )

    async def test_retrieve_short_term(self, memory_manager):
        session_id = uuid4()
        await memory_manager.write(
            content="session content",
            metadata={"tenant_id": "t1", "session_id": str(session_id)},
            tier=MemoryTier.SHORT_TERM,
        )
        items = await memory_manager.retrieve(
            query=None,
            tier=MemoryTier.SHORT_TERM,
            session_id=session_id,
            tenant_id="t1",
        )
        assert len(items) >= 1

    async def test_retrieve_long_term_semantic(self, memory_manager):
        await memory_manager.write(
            content="The capital of France is Paris",
            metadata={"tenant_id": "t1"},
            tier=MemoryTier.LONG_TERM,
        )
        items = await memory_manager.retrieve(
            query="What is the capital of France?",
            tier=MemoryTier.LONG_TERM,
            tenant_id="t1",
        )
        assert isinstance(items, list)

    async def test_ping_short_term(self, memory_manager):
        result = await memory_manager.ping_short_term()
        assert result is True

    async def test_get_stats_returns_stats_object(self, memory_manager):
        stats = await memory_manager.get_stats()
        assert hasattr(stats, "redis_connected")
        assert hasattr(stats, "qdrant_connected")

    async def test_retrieve_and_stitch_returns_merged(self, memory_manager):
        session_id = uuid4()
        await memory_manager.write(
            content="stitched memory content",
            metadata={"tenant_id": "t1", "session_id": str(session_id)},
            tier=MemoryTier.SHORT_TERM,
        )
        items = await memory_manager.retrieve_and_stitch(
            query="stitched memory",
            tenant_id="t1",
            session_id=session_id,
            token_budget=500,
        )
        assert isinstance(items, list)
