#!/usr/bin/env python3
"""MCP System — End-to-end quickstart demonstration.

Runs the full pipeline in-process (no external infrastructure needed):
  1. Spin up core components with in-memory Redis fallback
  2. Register built-in modules
  3. Create a context
  4. Execute modules (echo, summarizer)
  5. Store and retrieve memory
  6. Show system health

Usage:
    python scripts/quickstart.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

# Allow running from repo root or scripts/ directory
sys.path.insert(0, str(Path(__file__).parent.parent))


async def main() -> None:
    from src.api.main import _InMemoryRedis
    from src.core.context_manager import ContextManager, MessageRole
    from src.core.orchestrator import Orchestrator
    from src.core.registry import ModuleRegistry
    from src.memory.long_term import LongTermMemoryStore, MockEmbedder
    from src.memory.manager import MemoryManager
    from src.memory.short_term import ShortTermMemoryStore
    from src.modules.plugins.echo import EchoModule
    from src.modules.plugins.summarizer import SummarizerModule
    from src.utils.config import override_settings
    from src.utils.logger import configure_logging

    configure_logging(log_level="WARNING", log_format="text")

    settings = override_settings(
        MCP_ENV="development",
        MCP_SECRET_KEY="quickstart-secret-32-chars-minimum",
        JWT_SECRET_KEY="quickstart-jwt-secret-32-chars-ok",
    )

    print("\n" + "="*60)
    print("  MCP System — Quickstart Demo")
    print("="*60)

    # ── 1. Bootstrap infrastructure ───────────────────────────────
    print("\n[1] Bootstrapping components...")
    redis         = _InMemoryRedis()
    embedder      = MockEmbedder(vector_size=128)
    short_term    = ShortTermMemoryStore(redis_client=redis, settings=settings)
    long_term     = LongTermMemoryStore(
        qdrant_client=_MockQdrant(), embedder=embedder, settings=settings
    )
    memory_mgr    = MemoryManager(short_term=short_term, long_term=long_term)
    context_mgr   = ContextManager(redis_client=redis, settings=settings)
    registry      = ModuleRegistry()
    orchestrator  = Orchestrator(
        context_manager=context_mgr,
        memory_manager=memory_mgr,
        module_registry=registry,
        settings=settings,
    )
    print("   ✓ Components initialised (no external services required)")

    # ── 2. Register modules ───────────────────────────────────────
    print("\n[2] Registering modules...")
    await registry.register(EchoModule())
    await registry.register(SummarizerModule())
    modules = registry.list_modules()
    for m in modules:
        print(f"   ✓ {m['name']} v{m['version']} — {m['description'][:50]}")

    # ── 3. Create a context ───────────────────────────────────────
    print("\n[3] Creating context...")
    session_id = uuid4()
    ctx = await orchestrator.create_context(
        session_id=session_id,
        tenant_id="demo-tenant",
        system_prompt="You are a helpful AI assistant powered by MCP.",
        metadata={"demo": True},
    )
    print(f"   ✓ Context ID:       {ctx.id}")
    print(f"   ✓ Token budget:     {ctx.token_count}/{ctx.max_tokens}")
    print(f"   ✓ Status:           {ctx.status.value}")

    # ── 4. Append messages ────────────────────────────────────────
    print("\n[4] Appending messages...")
    ctx, msg = await orchestrator.append_to_context(
        context_id=ctx.id,
        tenant_id="demo-tenant",
        role="user",
        content="Can you process this text for me?",
    )
    print(f"   ✓ Message appended — role={msg.role.value}, tokens={msg.token_count}")
    print(f"   ✓ Context token count: {ctx.token_count}")

    # ── 5. Execute echo module ────────────────────────────────────
    print("\n[5] Executing 'echo' module...")
    result = await orchestrator.execute_module(
        module_name="echo",
        input_data={"text": "Hello from MCP System!", "uppercase": True},
        user_id="demo-user",
        tenant_id="demo-tenant",
        session_id=session_id,
        context_id=ctx.id,
    )
    print(f"   ✓ Status:    {result.status}")
    print(f"   ✓ Output:    {result.output.text}")
    print(f"   ✓ Latency:   {result.latency_ms:.2f}ms")

    # ── 6. Execute summarizer module ──────────────────────────────
    print("\n[6] Executing 'text-summarizer' module...")
    long_text = (
        "The Model Context Protocol (MCP) is an open standard that enables AI systems "
        "to connect with external data sources, tools, and services through a unified interface. "
        "It solves the N×M integration problem by providing a single protocol that any AI client "
        "can implement to access any MCP-compatible server. This dramatically reduces the "
        "integration burden for developers building AI applications at scale."
    )
    result2 = await orchestrator.execute_module(
        module_name="text-summarizer",
        input_data={"text": long_text, "style": "bullet", "max_words": 30},
        user_id="demo-user",
        tenant_id="demo-tenant",
        session_id=session_id,
    )
    print(f"   ✓ Status:              {result2.status}")
    print(f"   ✓ Original words:      {result2.output.original_word_count}")
    print(f"   ✓ Summary words:       {result2.output.summary_word_count}")
    print(f"   ✓ Compression ratio:   {result2.output.compression_ratio:.2%}")
    print(f"   ✓ Summary:\n{result2.output.summary}")

    # ── 7. Store and retrieve memory ──────────────────────────────
    print("\n[7] Memory store and retrieve...")
    mem_id = await orchestrator.store_memory(
        content="The user is interested in MCP and AI integration patterns.",
        tenant_id="demo-tenant",
        metadata={"session_id": str(session_id), "role": "user"},
        tier="short_term",
    )
    print(f"   ✓ Stored memory ID: {mem_id}")

    retrieved = await orchestrator.retrieve_memory(
        query="AI integration",
        tenant_id="demo-tenant",
        session_id=session_id,
    )
    print(f"   ✓ Retrieved {len(retrieved)} memory items")

    # ── 8. Fork context ───────────────────────────────────────────
    print("\n[8] Forking context (for A/B agent branching)...")
    child_ctx = await orchestrator.fork_context(ctx.id, "demo-tenant")
    print(f"   ✓ Parent ID: {ctx.id}")
    print(f"   ✓ Child ID:  {child_ctx.id}")
    print(f"   ✓ Child parent_id: {child_ctx.parent_id}")

    # ── 9. System health check ────────────────────────────────────
    print("\n[9] System health check...")
    health = await orchestrator.health_check()
    print(f"   ✓ Overall status: {health['status']}")
    for component, info in health.get("checks", {}).items():
        icon = "✓" if info.get("status") == "healthy" else "~"
        print(f"   {icon} {component}: {info.get('status', 'unknown')}")

    # ── 10. Module health ─────────────────────────────────────────
    print("\n[10] Module health check...")
    mod_health = await orchestrator.module_health()
    for mod_key, status in mod_health.items():
        icon = "✓" if status.get("healthy") else "✗"
        print(f"   {icon} {mod_key}: {status.get('message', '')}")

    # ── Final stats ───────────────────────────────────────────────
    stats = await orchestrator.get_memory_stats()
    msgs  = await orchestrator.get_context_messages(ctx.id, "demo-tenant")

    print("\n" + "="*60)
    print("  Demo Complete — Summary")
    print("="*60)
    print(f"  Context messages:     {len(msgs)}")
    print(f"  Redis connected:      {stats.redis_connected}")
    print(f"  Qdrant connected:     {stats.qdrant_connected}")
    print(f"  Modules registered:   {len(modules)}")
    print("\n  Next steps:")
    print("    make api             → Start the full REST API server")
    print("    make docker-up       → Full stack with Redis + Qdrant + Grafana")
    print("    make test-fast       → Run the unit test suite")
    print("    open http://localhost:8000/docs  → Interactive API docs")
    print()


class _MockQdrant:
    """Minimal Qdrant mock for quickstart (no real Qdrant needed)."""
    _points: dict = {}

    async def get_collections(self):
        class _R:
            collections = []
        return _R()

    async def create_collection(self, **kw): pass
    async def create_payload_index(self, **kw): pass

    async def upsert(self, collection_name, points):
        for p in points:
            self._points[str(p.id)] = {"vector": p.vector, "payload": p.payload}

    async def search(self, collection_name, query_vector, query_filter=None,
                     limit=5, score_threshold=0.0, with_payload=True):
        return []

    async def set_payload(self, **kw): pass

    async def get_collection(self, name):
        class _I:
            vectors_count = 0
            indexed_vectors_count = 0
            points_count = 0
            status = "green"
        return _I()

    def close(self): pass


if __name__ == "__main__":
    asyncio.run(main())
