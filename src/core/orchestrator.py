"""Orchestrator — central coordinator for all MCP request processing.

The orchestrator is the only component that holds references to all
three managers (ContextManager, MemoryManager, ModuleRegistry). It
coordinates their interactions so the individual managers remain decoupled.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import UUID, uuid4

from src.core.types import ExecutionContext, ModuleResult  # noqa: F401 — re-exported
from src.utils.config import Settings, get_settings
from src.utils.exceptions import ModuleExecutionError
from src.utils.logger import get_logger
from src.utils.metrics import get_metrics

logger = get_logger(__name__, component="orchestrator")
metrics = get_metrics()


# ── Orchestrator ───────────────────────────────────────────────────────────────


class Orchestrator:
    """Central request coordinator.

    Wires together ContextManager, MemoryManager, and ModuleRegistry to
    serve API requests. Each method represents one high-level operation
    exposed through the API layer.

    Args:
        context_manager: ContextManager instance.
        memory_manager: MemoryManager instance.
        module_registry: ModuleRegistry instance.
        settings: Application settings.
    """

    def __init__(
        self,
        context_manager: Any,
        memory_manager: Any,
        module_registry: Any,
        settings: Settings | None = None,
    ) -> None:
        self._ctx_mgr = context_manager
        self._mem_mgr = memory_manager
        self._registry = module_registry
        self._settings = settings or get_settings()

    # ── Module Execution ──────────────────────────────────────────────────────

    async def execute_module(
        self,
        module_name: str,
        input_data: dict[str, Any],
        user_id: str,
        tenant_id: str,
        session_id: UUID | None = None,
        context_id: UUID | None = None,
        module_version: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ModuleResult:
        """Execute a module within an optional context.

        Steps:
          1. Build ExecutionContext
          2. Load/create MCP context (if context_id provided)
          3. Pre-fetch relevant long-term memories (if session_id provided)
          4. Execute the module (with timeout)
          5. Persist output to context history + memory tiers

        Args:
            module_name: Registered module name.
            input_data: Raw input dict (will be validated by module schema).
            user_id: Authenticated user.
            tenant_id: Tenant namespace.
            session_id: Optional session ID for memory retrieval.
            context_id: Optional context to append result to.
            module_version: Optional version pin.
            metadata: Arbitrary client-supplied metadata.

        Returns:
            ModuleResult with output and execution metadata.
        """
        request_id = uuid4()
        exec_ctx = ExecutionContext(
            request_id=request_id,
            context_id=context_id,
            session_id=session_id,
            user_id=user_id,
            tenant_id=tenant_id,
            metadata=metadata or {},
            timeout=self._settings.modules_timeout_seconds,
        )
        exec_ctx.bind_log(
            request_id=str(request_id),
            module_name=module_name,
            tenant_id=tenant_id,
        )

        start = time.perf_counter()

        # Parallelize: load context + pre-fetch memories
        ctx_task = None
        memory_task = None

        if context_id is not None:
            ctx_task = asyncio.create_task(
                self._ctx_mgr.get(context_id, tenant_id)
            )

        if session_id is not None:
            # Derive query from input for semantic memory retrieval
            query = self._derive_memory_query(input_data, module_name)
            memory_task = asyncio.create_task(
                self._safe_memory_retrieve(query, tenant_id, session_id)
            )

        mcp_context = None
        if ctx_task:
            try:
                mcp_context = await ctx_task
                exec_ctx.working_memory["mcp_context"] = mcp_context
            except Exception as exc:
                exec_ctx.logger.warning("context_load_failed", error=str(exc))

        if memory_task:
            try:
                memories = await memory_task
                exec_ctx.working_memory["relevant_memories"] = memories
            except Exception as exc:
                exec_ctx.logger.warning("memory_prefetch_failed", error=str(exc))

        # Execute module
        try:
            output = await self._registry.execute(
                name=module_name,
                input_data=input_data,
                execution_context=exec_ctx,
                version=module_version,
                timeout=self._settings.modules_timeout_seconds,
            )
            status = "success"
            error_msg = None
        except Exception as exc:
            latency_ms = (time.perf_counter() - start) * 1000
            error_msg = str(exc)
            status = "timeout" if "timeout" in type(exc).__name__.lower() else "error"
            exec_ctx.logger.error(
                "module_execution_failed",
                module_name=module_name,
                error=error_msg,
                latency_ms=latency_ms,
            )
            return ModuleResult(
                module_name=module_name,
                module_version=module_version or "latest",
                output=None,
                latency_ms=latency_ms,
                status=status,
                error=error_msg,
                request_id=request_id,
            )

        latency_ms = (time.perf_counter() - start) * 1000
        mod_version: str = getattr(
            self._registry.get(module_name, module_version), "version", "1.0.0"
        )

        # Persist result asynchronously (non-blocking)
        asyncio.create_task(
            self._persist_result(
                output=output,
                module_name=module_name,
                context_id=context_id,
                session_id=session_id,
                tenant_id=tenant_id,
            )
        )

        exec_ctx.logger.info(
            "module_executed",
            module_name=module_name,
            status=status,
            latency_ms=round(latency_ms, 2),
        )

        return ModuleResult(
            module_name=module_name,
            module_version=mod_version,
            output=output,
            latency_ms=latency_ms,
            status=status,
            request_id=request_id,
        )

    # ── Context Operations (delegated) ────────────────────────────────────────

    async def create_context(
        self,
        session_id: UUID,
        tenant_id: str,
        **kwargs: Any,
    ) -> Any:
        return await self._ctx_mgr.create(
            session_id=session_id, tenant_id=tenant_id, **kwargs
        )

    async def get_context(self, context_id: UUID, tenant_id: str) -> Any:
        return await self._ctx_mgr.get(context_id, tenant_id)

    async def append_to_context(
        self,
        context_id: UUID,
        tenant_id: str,
        role: str,
        content: str,
        **kwargs: Any,
    ) -> tuple[Any, Any]:
        from src.core.context_manager import MessageRole

        return await self._ctx_mgr.append_message(
            context_id=context_id,
            tenant_id=tenant_id,
            role=MessageRole(role),
            content=content,
            **kwargs,
        )

    async def fork_context(
        self, context_id: UUID, tenant_id: str, **kwargs: Any
    ) -> Any:
        return await self._ctx_mgr.fork(context_id, tenant_id, **kwargs)

    async def seal_context(self, context_id: UUID, tenant_id: str) -> Any:
        return await self._ctx_mgr.seal(context_id, tenant_id)

    async def expire_context(self, context_id: UUID, tenant_id: str) -> None:
        await self._ctx_mgr.expire(context_id, tenant_id)

    async def get_context_messages(
        self, context_id: UUID, tenant_id: str, **kwargs: Any
    ) -> list[Any]:
        return await self._ctx_mgr.get_messages(context_id, tenant_id, **kwargs)

    # ── Memory Operations (delegated) ─────────────────────────────────────────

    async def store_memory(
        self,
        content: str,
        tenant_id: str,
        metadata: dict[str, Any] | None = None,
        tier: str = "short_term",
    ) -> str:
        from src.memory.base import MemoryTier

        return await self._mem_mgr.write(
            content=content,
            metadata=metadata or {},
            tier=MemoryTier(tier),
        )

    async def retrieve_memory(
        self,
        query: str,
        tenant_id: str,
        session_id: UUID | None = None,
        k: int = 5,
    ) -> list[Any]:
        from src.memory.base import MemoryTier

        return await self._mem_mgr.retrieve(
            query=query,
            tier=MemoryTier.LONG_TERM,
            session_id=session_id,
            k=k,
        )

    async def delete_memory(
        self, memory_id: str, tier: str = "long_term"
    ) -> bool:
        """Delete a memory item from the specified tier.

        Args:
            memory_id: The memory item ID.
            tier: Target tier string ("short_term" | "long_term").

        Returns:
            True if deleted, False if not found.
        """
        from src.memory.base import MemoryTier

        return await self._mem_mgr.delete(memory_id, MemoryTier(tier))

    async def get_memory_stats(self) -> dict[str, Any]:
        return await self._mem_mgr.get_stats()

    # ── Module Operations (delegated) ─────────────────────────────────────────

    def list_modules(self) -> list[dict[str, Any]]:
        return self._registry.list_modules()

    def get_module_schema(
        self, name: str, version: str | None = None
    ) -> dict[str, Any]:
        module = self._registry.get(name, version)
        return {
            "name": module.name,
            "version": module.version,
            "description": module.description,
            "tags": getattr(module, "tags", []),
            "input_schema": (
                module.input_schema.model_json_schema()
                if hasattr(module, "input_schema")
                else {}
            ),
            "output_schema": (
                module.output_schema.model_json_schema()
                if hasattr(module, "output_schema")
                else {}
            ),
        }

    async def module_health(self) -> dict[str, Any]:
        return await self._registry.health_check_all()

    # ── System Health ─────────────────────────────────────────────────────────

    async def health_check(self) -> dict[str, Any]:
        """Check health of all subsystems."""
        checks: dict[str, Any] = {}

        # Redis check
        try:
            await self._mem_mgr.ping_short_term()
            checks["redis"] = {"status": "healthy"}
        except Exception as exc:
            checks["redis"] = {"status": "unhealthy", "error": str(exc)}

        # Qdrant check
        try:
            await self._mem_mgr.ping_long_term()
            checks["qdrant"] = {"status": "healthy"}
        except Exception as exc:
            checks["qdrant"] = {"status": "unhealthy", "error": str(exc)}

        # Modules check
        try:
            module_health = await self._registry.health_check_all()
            unhealthy = [k for k, v in module_health.items() if not v.get("healthy")]
            checks["modules"] = {
                "status": "healthy" if not unhealthy else "degraded",
                "total": len(module_health),
                "unhealthy": unhealthy,
            }
        except Exception as exc:
            checks["modules"] = {"status": "unknown", "error": str(exc)}

        overall = (
            "healthy"
            if all(v.get("status") == "healthy" for v in checks.values())
            else "degraded"
        )
        return {"status": overall, "checks": checks}

    # ── Private Helpers ───────────────────────────────────────────────────────

    async def _safe_memory_retrieve(
        self, query: str, tenant_id: str, session_id: UUID
    ) -> list[Any]:
        """Memory retrieval that returns [] on failure (non-critical path)."""
        try:
            from src.memory.base import MemoryTier

            return await self._mem_mgr.retrieve(
                query=query,
                tier=MemoryTier.LONG_TERM,
                session_id=session_id,
                k=5,
            )
        except Exception as exc:
            logger.warning("memory_retrieve_degraded", error=str(exc))
            return []

    async def _persist_result(
        self,
        output: Any,
        module_name: str,
        context_id: UUID | None,
        session_id: UUID | None,
        tenant_id: str,
    ) -> None:
        """Persist module output to context history and memory (background task)."""
        from src.core.context_manager import MessageRole
        from src.memory.base import MemoryTier

        # Serialize output for storage
        if hasattr(output, "model_dump"):
            content = str(output.model_dump())
        elif output is not None:
            content = str(output)
        else:
            return

        # Append to context if provided
        if context_id is not None:
            try:
                await self._ctx_mgr.append_message(
                    context_id=context_id,
                    tenant_id=tenant_id,
                    role=MessageRole.TOOL,
                    content=content,
                    metadata={"module": module_name},
                )
            except Exception as exc:
                logger.warning("context_append_failed", error=str(exc))

        # Write to short-term memory
        try:
            await self._mem_mgr.write(
                content=content,
                metadata={
                    "module": module_name,
                    "session_id": str(session_id) if session_id else None,
                    "tenant_id": tenant_id,
                },
                tier=MemoryTier.SHORT_TERM,
            )
        except Exception as exc:
            logger.warning("short_term_write_failed", error=str(exc))

        # Write to long-term memory (fire and forget)
        try:
            await self._mem_mgr.write(
                content=content,
                metadata={
                    "module": module_name,
                    "session_id": str(session_id) if session_id else None,
                    "tenant_id": tenant_id,
                    "role": "tool",
                },
                tier=MemoryTier.LONG_TERM,
            )
        except Exception as exc:
            logger.warning("long_term_write_failed", error=str(exc))

    @staticmethod
    def _derive_memory_query(input_data: dict[str, Any], module_name: str) -> str:
        """Derive a semantic search query from module input."""
        text_fields = ["text", "query", "content", "message", "prompt", "question"]
        for field in text_fields:
            if field in input_data and isinstance(input_data[field], str):
                return input_data[field][:500]
        return f"module:{module_name} " + " ".join(
            str(v)[:100] for v in input_data.values() if isinstance(v, str)
        )
