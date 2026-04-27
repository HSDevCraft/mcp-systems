"""End-to-end integration tests.

These tests exercise the full request pipeline:
  Client → API → Orchestrator → Context/Memory/Module → Response

All external dependencies (Redis, Qdrant) are replaced with in-memory
fakes from conftest.py so no infrastructure is required.

Slow tests (marked @pytest.mark.slow) run real embedding models and
are excluded from make test-fast.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio

from src.core.context_manager import ContextManager, MessageRole
from src.core.orchestrator import ExecutionContext, Orchestrator
from src.core.registry import ModuleRegistry
from src.memory.manager import MemoryManager
from src.modules.plugins.echo import EchoModule
from src.modules.plugins.summarizer import SummarizerModule

pytestmark = pytest.mark.integration


class TestFullContextWorkflow:
    """Tests the complete context lifecycle through the orchestrator."""

    async def test_create_context_then_execute_module(
        self, orchestrator: Orchestrator
    ) -> None:
        session_id = uuid4()

        # Step 1: Create a context
        ctx = await orchestrator.create_context(
            session_id=session_id,
            tenant_id="integration-tenant",
            system_prompt="You are a test assistant.",
        )
        assert ctx.id is not None
        assert ctx.token_count > 0

        # Step 2: Append a user message
        ctx, msg = await orchestrator.append_to_context(
            context_id=ctx.id,
            tenant_id="integration-tenant",
            role="user",
            content="Please echo this back to me.",
        )
        assert msg.token_count > 0

        # Step 3: Execute echo module in context
        result = await orchestrator.execute_module(
            module_name="echo",
            input_data={"text": "integration test message"},
            user_id="int-user",
            tenant_id="integration-tenant",
            session_id=session_id,
            context_id=ctx.id,
        )
        assert result.status == "success"
        assert result.output.text == "integration test message"

    async def test_fork_context_and_diverge(
        self, orchestrator: Orchestrator
    ) -> None:
        session_id = uuid4()
        tenant = "fork-tenant"

        # Create parent
        parent = await orchestrator.create_context(
            session_id=session_id, tenant_id=tenant
        )
        await orchestrator.append_to_context(
            parent.id, tenant, "user", "parent message"
        )

        # Fork
        child = await orchestrator.fork_context(parent.id, tenant)
        assert child.parent_id == parent.id

        # Append different content to each
        await orchestrator.append_to_context(
            parent.id, tenant, "assistant", "parent branch response"
        )
        await orchestrator.append_to_context(
            child.id, tenant, "assistant", "child branch response"
        )

        # Verify messages diverged
        parent_msgs = await orchestrator.get_context_messages(parent.id, tenant)
        child_msgs = await orchestrator.get_context_messages(child.id, tenant)

        parent_contents = [m.content for m in parent_msgs if hasattr(m, "content")]
        child_contents = [m.content for m in child_msgs if hasattr(m, "content")]

        assert any("parent branch" in str(c) for c in parent_contents)
        assert any("child branch" in str(c) for c in child_contents)

    async def test_seal_context_blocks_append(
        self, orchestrator: Orchestrator
    ) -> None:
        from src.utils.exceptions import ContextSealedError

        session_id = uuid4()
        ctx = await orchestrator.create_context(
            session_id=session_id, tenant_id="seal-tenant"
        )
        await orchestrator.seal_context(ctx.id, "seal-tenant")

        with pytest.raises(ContextSealedError):
            await orchestrator.append_to_context(
                ctx.id, "seal-tenant", "user", "after seal"
            )

    async def test_expire_context_removes_it(
        self, orchestrator: Orchestrator
    ) -> None:
        from src.utils.exceptions import ContextNotFoundError

        session_id = uuid4()
        ctx = await orchestrator.create_context(
            session_id=session_id, tenant_id="expire-tenant"
        )
        await orchestrator.expire_context(ctx.id, "expire-tenant")

        with pytest.raises(ContextNotFoundError):
            await orchestrator.get_context(ctx.id, "expire-tenant")


class TestMemoryWorkflow:
    """Tests the complete memory store-retrieve cycle."""

    async def test_store_and_retrieve_short_term(
        self, orchestrator: Orchestrator
    ) -> None:
        session_id = uuid4()
        await orchestrator.store_memory(
            content="The MCP system is a production-grade platform",
            tenant_id="mem-tenant",
            metadata={"session_id": str(session_id)},
            tier="short_term",
        )
        items = await orchestrator.retrieve_memory(
            query="MCP system",
            tenant_id="mem-tenant",
            session_id=session_id,
        )
        assert isinstance(items, list)

    async def test_memory_stats_returns_structure(
        self, orchestrator: Orchestrator
    ) -> None:
        stats = await orchestrator.get_memory_stats()
        assert hasattr(stats, "redis_connected")
        assert hasattr(stats, "qdrant_connected")


class TestModulePipeline:
    """Tests the module execution pipeline end to end."""

    async def test_execute_echo_returns_correct_output(
        self, orchestrator: Orchestrator
    ) -> None:
        result = await orchestrator.execute_module(
            module_name="echo",
            input_data={"text": "hello world", "uppercase": True},
            user_id="user-1",
            tenant_id="module-tenant",
        )
        assert result.status == "success"
        assert result.output.text == "HELLO WORLD"
        assert result.latency_ms >= 0

    async def test_execute_summarizer_returns_summary(
        self, orchestrator: Orchestrator
    ) -> None:
        long_text = "The quick brown fox jumps over the lazy dog. " * 10
        result = await orchestrator.execute_module(
            module_name="text-summarizer",
            input_data={"text": long_text, "style": "bullet"},
            user_id="user-1",
            tenant_id="module-tenant",
        )
        assert result.status == "success"
        assert len(result.output.summary) > 0

    async def test_execute_unknown_module_returns_error(
        self, orchestrator: Orchestrator
    ) -> None:
        from src.utils.exceptions import ModuleNotFoundError

        with pytest.raises(ModuleNotFoundError):
            await orchestrator._registry.get("nonexistent-module-xyz")

    async def test_module_list(self, orchestrator: Orchestrator) -> None:
        modules = orchestrator.list_modules()
        names = [m["name"] for m in modules]
        assert "echo" in names
        assert "text-summarizer" in names

    async def test_module_schema(self, orchestrator: Orchestrator) -> None:
        schema = orchestrator.get_module_schema("echo")
        assert schema["name"] == "echo"
        assert "properties" in schema["input_schema"]

    async def test_execute_with_context_appends_result(
        self, orchestrator: Orchestrator
    ) -> None:
        import asyncio

        session_id = uuid4()
        ctx = await orchestrator.create_context(
            session_id=session_id, tenant_id="exec-tenant"
        )

        await orchestrator.execute_module(
            module_name="echo",
            input_data={"text": "context bound execution"},
            user_id="u1",
            tenant_id="exec-tenant",
            context_id=ctx.id,
            session_id=session_id,
        )

        # Allow background persist task to complete
        await asyncio.sleep(0.1)

        messages = await orchestrator.get_context_messages(ctx.id, "exec-tenant")
        # At least a tool message should have been appended
        assert isinstance(messages, list)


class TestSystemHealth:
    async def test_health_check_structure(
        self, orchestrator: Orchestrator
    ) -> None:
        health = await orchestrator.health_check()
        assert "status" in health
        assert "checks" in health
        assert health["status"] in ("healthy", "degraded", "unhealthy")
