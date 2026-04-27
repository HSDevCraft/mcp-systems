"""Unit tests for the Orchestrator."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.orchestrator import ExecutionContext, ModuleResult, Orchestrator
from src.utils.exceptions import ModuleNotFoundError

pytestmark = pytest.mark.unit


class TestOrchestratorContextDelegation:
    async def test_create_context(self, orchestrator: Orchestrator):
        ctx = await orchestrator.create_context(
            session_id=uuid4(), tenant_id="t1"
        )
        assert ctx.id is not None
        assert ctx.tenant_id == "t1"

    async def test_get_context(self, orchestrator: Orchestrator):
        ctx = await orchestrator.create_context(session_id=uuid4(), tenant_id="t1")
        fetched = await orchestrator.get_context(ctx.id, "t1")
        assert fetched.id == ctx.id

    async def test_append_message(self, orchestrator: Orchestrator):
        ctx = await orchestrator.create_context(session_id=uuid4(), tenant_id="t1")
        updated_ctx, msg = await orchestrator.append_to_context(
            ctx.id, "t1", "user", "Hello orchestrator"
        )
        assert msg.content == "Hello orchestrator"
        assert updated_ctx.token_count > 0

    async def test_fork_context(self, orchestrator: Orchestrator):
        ctx = await orchestrator.create_context(session_id=uuid4(), tenant_id="t1")
        child = await orchestrator.fork_context(ctx.id, "t1")
        assert child.parent_id == ctx.id

    async def test_seal_context(self, orchestrator: Orchestrator):
        from src.core.context_manager import ContextStatus

        ctx = await orchestrator.create_context(session_id=uuid4(), tenant_id="t1")
        sealed = await orchestrator.seal_context(ctx.id, "t1")
        assert sealed.status == ContextStatus.SEALED

    async def test_expire_context(self, orchestrator: Orchestrator):
        from src.utils.exceptions import ContextNotFoundError

        ctx = await orchestrator.create_context(session_id=uuid4(), tenant_id="t1")
        await orchestrator.expire_context(ctx.id, "t1")
        with pytest.raises(ContextNotFoundError):
            await orchestrator.get_context(ctx.id, "t1")

    async def test_get_context_messages(self, orchestrator: Orchestrator):
        ctx = await orchestrator.create_context(session_id=uuid4(), tenant_id="t1")
        await orchestrator.append_to_context(ctx.id, "t1", "user", "test message")
        msgs = await orchestrator.get_context_messages(ctx.id, "t1")
        assert len(msgs) >= 1


class TestOrchestratorModuleExecution:
    async def test_execute_echo_success(self, orchestrator: Orchestrator):
        result = await orchestrator.execute_module(
            module_name="echo",
            input_data={"text": "hello"},
            user_id="u1",
            tenant_id="t1",
        )
        assert result.status == "success"
        assert result.module_name == "echo"
        assert result.output.text == "hello"
        assert result.latency_ms >= 0

    async def test_execute_unknown_module_returns_error_result(
        self, orchestrator: Orchestrator
    ):
        with pytest.raises(ModuleNotFoundError):
            await orchestrator._registry.get("nonexistent-xyz")

    async def test_execute_module_with_context(self, orchestrator: Orchestrator):
        ctx = await orchestrator.create_context(session_id=uuid4(), tenant_id="t1")
        result = await orchestrator.execute_module(
            module_name="echo",
            input_data={"text": "with context"},
            user_id="u1",
            tenant_id="t1",
            context_id=ctx.id,
        )
        assert result.status == "success"

    async def test_execute_module_with_session(self, orchestrator: Orchestrator):
        result = await orchestrator.execute_module(
            module_name="echo",
            input_data={"text": "with session"},
            user_id="u1",
            tenant_id="t1",
            session_id=uuid4(),
        )
        assert result.status == "success"

    async def test_list_modules(self, orchestrator: Orchestrator):
        modules = orchestrator.list_modules()
        assert isinstance(modules, list)
        names = [m["name"] for m in modules]
        assert "echo" in names

    async def test_get_module_schema(self, orchestrator: Orchestrator):
        schema = orchestrator.get_module_schema("echo")
        assert schema["name"] == "echo"
        assert "input_schema" in schema
        assert "output_schema" in schema

    async def test_get_schema_unknown_module_raises(self, orchestrator: Orchestrator):
        with pytest.raises(ModuleNotFoundError):
            orchestrator.get_module_schema("nonexistent-xyz")


class TestOrchestratorMemoryDelegation:
    async def test_store_short_term_memory(self, orchestrator: Orchestrator):
        memory_id = await orchestrator.store_memory(
            content="test content",
            tenant_id="t1",
            tier="short_term",
        )
        assert memory_id is not None

    async def test_store_long_term_memory(self, orchestrator: Orchestrator):
        memory_id = await orchestrator.store_memory(
            content="long term knowledge",
            tenant_id="t1",
            tier="long_term",
        )
        assert memory_id is not None

    async def test_retrieve_memory_returns_list(self, orchestrator: Orchestrator):
        items = await orchestrator.retrieve_memory(
            query="test query", tenant_id="t1"
        )
        assert isinstance(items, list)

    async def test_get_memory_stats(self, orchestrator: Orchestrator):
        stats = await orchestrator.get_memory_stats()
        assert hasattr(stats, "redis_connected")


class TestOrchestratorHealthCheck:
    async def test_health_check_structure(self, orchestrator: Orchestrator):
        health = await orchestrator.health_check()
        assert "status" in health
        assert "checks" in health

    async def test_health_check_status_values(self, orchestrator: Orchestrator):
        health = await orchestrator.health_check()
        assert health["status"] in {"healthy", "degraded", "unhealthy"}

    async def test_module_health(self, orchestrator: Orchestrator):
        module_health = await orchestrator.module_health()
        assert isinstance(module_health, dict)
        assert "echo@1.0.0" in module_health


class TestExecutionContext:
    def test_default_execution_context(self):
        ctx = ExecutionContext()
        assert ctx.user_id == "anonymous"
        assert ctx.tenant_id == "default"
        assert ctx.working_memory == {}
        assert ctx.timeout == 30.0

    def test_set_and_get_working_memory(self):
        ctx = ExecutionContext()
        ctx.set_working("key", "value")
        assert ctx.get_working("key") == "value"

    def test_get_working_memory_default(self):
        ctx = ExecutionContext()
        assert ctx.get_working("missing", "fallback") == "fallback"

    def test_bind_log(self):
        ctx = ExecutionContext()
        ctx.bind_log(module_name="echo", tenant_id="t1")
        # Verify no exception is raised

    def test_module_result_fields(self):
        result = ModuleResult(
            module_name="echo",
            module_version="1.0.0",
            output=None,
            latency_ms=5.0,
            status="success",
        )
        assert result.module_name == "echo"
        assert result.status == "success"
        assert result.error is None
