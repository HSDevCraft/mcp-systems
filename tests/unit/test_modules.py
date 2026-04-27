"""Unit tests for MCPModule implementations and ModuleRegistry."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.orchestrator import ExecutionContext
from src.core.registry import ModuleRegistry
from src.modules.base import HealthStatus, MCPModule
from src.modules.plugins.echo import EchoInput, EchoModule, EchoOutput
from src.modules.plugins.summarizer import SummarizerInput, SummarizerModule
from src.utils.exceptions import (
    ModuleExecutionError,
    ModuleLoadError,
    ModuleNotFoundError,
    ModuleTimeoutError,
)

pytestmark = pytest.mark.unit


def _make_ctx(**kwargs) -> ExecutionContext:
    return ExecutionContext(
        request_id=uuid4(),
        tenant_id="test-tenant",
        user_id="test-user",
        **kwargs,
    )


# ── EchoModule ────────────────────────────────────────────────────────────────


class TestEchoModule:
    @pytest.fixture
    def module(self) -> EchoModule:
        return EchoModule()

    async def test_execute_returns_same_text(self, module):
        ctx = _make_ctx()
        output = await module.execute(EchoInput(text="hello"), ctx)
        assert output.text == "hello"

    async def test_execute_uppercase(self, module):
        ctx = _make_ctx()
        output = await module.execute(EchoInput(text="hello", uppercase=True), ctx)
        assert output.text == "HELLO"
        assert "uppercase" in output.transformations

    async def test_execute_repeat(self, module):
        ctx = _make_ctx()
        output = await module.execute(EchoInput(text="hi", repeat=3), ctx)
        assert output.text == "hi hi hi"
        assert "repeat:3" in output.transformations

    async def test_execute_prefix(self, module):
        ctx = _make_ctx()
        output = await module.execute(EchoInput(text="world", prefix="Hello "), ctx)
        assert output.text == "Hello world"

    async def test_char_count_correct(self, module):
        ctx = _make_ctx()
        output = await module.execute(EchoInput(text="abc"), ctx)
        assert output.char_count == 3

    async def test_health_check_always_healthy(self, module):
        status = await module.health_check()
        assert status.healthy is True

    async def test_on_load_does_not_raise(self, module):
        await module.on_load()

    async def test_empty_transformations_when_no_options(self, module):
        ctx = _make_ctx()
        output = await module.execute(EchoInput(text="plain"), ctx)
        assert output.transformations == []


# ── SummarizerModule ──────────────────────────────────────────────────────────


class TestSummarizerModule:
    @pytest.fixture
    def module(self) -> SummarizerModule:
        return SummarizerModule()

    async def test_execute_returns_summary(self, module):
        ctx = _make_ctx()
        long_text = "The quick brown fox. " * 20
        output = await module.execute(SummarizerInput(text=long_text), ctx)
        assert len(output.summary) > 0

    async def test_bullet_style(self, module):
        ctx = _make_ctx()
        text = "First sentence. Second sentence. Third sentence."
        output = await module.execute(SummarizerInput(text=text, style="bullet"), ctx)
        assert "•" in output.summary

    async def test_tldr_style(self, module):
        ctx = _make_ctx()
        text = "First sentence. Second sentence."
        output = await module.execute(SummarizerInput(text=text, style="tldr"), ctx)
        assert "TL;DR" in output.summary

    async def test_compression_ratio_between_0_and_1(self, module):
        ctx = _make_ctx()
        text = "Word " * 100
        output = await module.execute(SummarizerInput(text=text), ctx)
        assert 0.0 <= output.compression_ratio <= 1.0

    async def test_original_word_count_matches(self, module):
        ctx = _make_ctx()
        text = "one two three four five"
        output = await module.execute(SummarizerInput(text=text), ctx)
        assert output.original_word_count == 5

    async def test_health_check_no_llm(self, module):
        status = await module.health_check()
        assert status.healthy is True


# ── ModuleRegistry ────────────────────────────────────────────────────────────


class TestModuleRegistry:
    @pytest.fixture
    def registry(self) -> ModuleRegistry:
        return ModuleRegistry()

    async def test_register_and_get(self, registry):
        await registry.register(EchoModule())
        module = registry.get("echo")
        assert module.name == "echo"

    async def test_get_latest_version(self, registry):
        m1 = EchoModule()
        m1.version = "1.0.0"
        m2 = EchoModule()
        m2.version = "1.2.0"
        await registry.register(m1)
        await registry.register(m2)
        latest = registry.get("echo")
        assert latest.version == "1.2.0"

    async def test_get_pinned_version(self, registry):
        m1 = EchoModule()
        m1.version = "1.0.0"
        m2 = EchoModule()
        m2.version = "2.0.0"
        await registry.register(m1)
        await registry.register(m2)
        pinned = registry.get("echo", version="1.0.0")
        assert pinned.version == "1.0.0"

    async def test_get_not_registered_raises(self, registry):
        with pytest.raises(ModuleNotFoundError):
            registry.get("nonexistent")

    async def test_is_registered_true(self, registry):
        await registry.register(EchoModule())
        assert registry.is_registered("echo") is True

    async def test_is_registered_false(self, registry):
        assert registry.is_registered("nonexistent") is False

    async def test_list_modules_returns_all(self, registry):
        await registry.register(EchoModule())
        await registry.register(SummarizerModule())
        modules = registry.list_modules()
        names = [m["name"] for m in modules]
        assert "echo" in names
        assert "text-summarizer" in names

    async def test_execute_echo_module(self, registry):
        await registry.register(EchoModule())
        ctx = _make_ctx()
        output = await registry.execute(
            "echo", {"text": "hello"}, ctx
        )
        assert output.text == "hello"

    async def test_execute_timeout_raises(self, registry):
        import asyncio

        class SlowModule(MCPModule):
            name = "slow"
            description = "Slow module"
            version = "1.0.0"
            input_schema = EchoInput
            output_schema = EchoOutput

            async def execute(self, input, ctx):
                await asyncio.sleep(10)
                return EchoOutput(text="done", char_count=4, word_count=1, transformations=[])

            async def health_check(self):
                return HealthStatus(healthy=True)

        await registry.register(SlowModule())
        ctx = _make_ctx()
        with pytest.raises(ModuleTimeoutError):
            await registry.execute("slow", {"text": "x"}, ctx, timeout=0.01)

    async def test_unregister_removes_module(self, registry):
        await registry.register(EchoModule())
        await registry.unregister("echo")
        assert registry.is_registered("echo") is False

    async def test_health_check_all(self, registry):
        await registry.register(EchoModule())
        health = await registry.health_check_all()
        assert "echo@1.0.0" in health
        assert health["echo@1.0.0"]["healthy"] is True

    async def test_module_without_name_raises_value_error(self, registry):
        class BadModule(MCPModule):
            name = ""
            description = "bad"
            version = "1.0.0"
            input_schema = EchoInput
            output_schema = EchoOutput

            async def execute(self, input, ctx):
                return EchoOutput(text="x", char_count=1, word_count=1, transformations=[])

            async def health_check(self):
                return HealthStatus(healthy=True)

        with pytest.raises(ValueError, match="must define 'name'"):
            await registry.register(BadModule())
