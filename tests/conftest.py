"""Shared pytest fixtures for the MCP System test suite.

All fixtures that require external services (Redis, Qdrant) use in-memory
or mock substitutes so unit tests run without any infrastructure.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from src.api.main import _InMemoryRedis, create_app
from src.core.context_manager import ContextManager
from src.core.orchestrator import Orchestrator
from src.core.registry import ModuleRegistry
from src.memory.base import MemoryItem, MemoryTier
from src.memory.long_term import LongTermMemoryStore, MockEmbedder
from src.memory.manager import MemoryManager
from src.memory.short_term import ShortTermMemoryStore
from src.modules.plugins.echo import EchoModule
from src.modules.plugins.summarizer import SummarizerModule
from src.utils.config import Environment, Settings


# ── Settings override for tests ────────────────────────────────────────────────


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    return Settings(
        MCP_ENV="test",
        MCP_SECRET_KEY="test-secret-key-32-chars-minimum!",
        JWT_SECRET_KEY="test-jwt-secret-key-minimum-32-chars",
        REDIS_URL="redis://localhost:6379/15",
        QDRANT_URL="http://localhost:6333",
        LOG_LEVEL="WARNING",
        ENABLE_METRICS=False,
        RATE_LIMIT_ENABLED=False,
        CONTEXT_MAX_TOKENS=10000,
        CONTEXT_TTL_SECONDS=300,
    )


# ── In-memory Redis fixture ────────────────────────────────────────────────────


@pytest.fixture
def fake_redis() -> _InMemoryRedis:
    """In-memory Redis substitute — no infrastructure required."""
    return _InMemoryRedis()


# ── Mock Qdrant client ─────────────────────────────────────────────────────────


class MockQdrantClient:
    """Minimal Qdrant mock for unit tests."""

    def __init__(self) -> None:
        self._points: dict[str, dict] = {}
        self._collection_name: str = "mcp_memory"

    async def get_collections(self) -> Any:
        class _Collections:
            collections: list = []
        return _Collections()

    async def create_collection(self, **kwargs: Any) -> None:
        pass

    async def create_payload_index(self, **kwargs: Any) -> None:
        pass

    async def upsert(self, collection_name: str, points: list) -> None:
        for point in points:
            self._points[str(point.id)] = {
                "vector": point.vector,
                "payload": point.payload or {},
            }

    async def search(
        self,
        collection_name: str,
        query_vector: list,
        query_filter: Any = None,
        limit: int = 5,
        score_threshold: float = 0.0,
        with_payload: bool = True,
    ) -> list:
        # Return mock results from stored points
        results = []
        for point_id, data in list(self._points.items())[:limit]:
            payload = data["payload"]
            if payload.get("expired", False):
                continue

            class _Hit:
                id = point_id
                score = 0.85
                payload = data["payload"]

            results.append(_Hit())
        return results[:limit]

    async def set_payload(self, **kwargs: Any) -> None:
        points = kwargs.get("points", [])
        payload = kwargs.get("payload", {})
        for point_id in points:
            if str(point_id) in self._points:
                self._points[str(point_id)]["payload"].update(payload)

    async def get_collection(self, collection_name: str) -> Any:
        class _Info:
            vectors_count = len(self._points)
            indexed_vectors_count = len(self._points)
            points_count = len(self._points)
            status = "green"
        return _Info()

    def close(self) -> None:
        pass


@pytest.fixture
def mock_qdrant() -> MockQdrantClient:
    return MockQdrantClient()


# ── Embedder ───────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_embedder() -> MockEmbedder:
    return MockEmbedder(vector_size=128)


# ── Memory stores ──────────────────────────────────────────────────────────────


@pytest.fixture
def short_term_store(fake_redis: _InMemoryRedis, test_settings: Settings) -> ShortTermMemoryStore:
    return ShortTermMemoryStore(redis_client=fake_redis, settings=test_settings)


@pytest.fixture
def long_term_store(
    mock_qdrant: MockQdrantClient, mock_embedder: MockEmbedder, test_settings: Settings
) -> LongTermMemoryStore:
    return LongTermMemoryStore(
        qdrant_client=mock_qdrant, embedder=mock_embedder, settings=test_settings
    )


@pytest.fixture
def memory_manager(
    short_term_store: ShortTermMemoryStore, long_term_store: LongTermMemoryStore
) -> MemoryManager:
    return MemoryManager(short_term=short_term_store, long_term=long_term_store)


# ── Context manager ────────────────────────────────────────────────────────────


@pytest.fixture
def context_manager(fake_redis: _InMemoryRedis, test_settings: Settings) -> ContextManager:
    return ContextManager(redis_client=fake_redis, settings=test_settings)


# ── Module registry ────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def module_registry() -> AsyncGenerator[ModuleRegistry, None]:
    registry = ModuleRegistry()
    await registry.register(EchoModule())
    await registry.register(SummarizerModule())
    yield registry
    await registry.shutdown()


# ── Orchestrator ───────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def orchestrator(
    context_manager: ContextManager,
    memory_manager: MemoryManager,
    module_registry: ModuleRegistry,
    test_settings: Settings,
) -> Orchestrator:
    return Orchestrator(
        context_manager=context_manager,
        memory_manager=memory_manager,
        module_registry=module_registry,
        settings=test_settings,
    )


# ── FastAPI test client ────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def test_app(
    context_manager: ContextManager,
    memory_manager: MemoryManager,
    module_registry: ModuleRegistry,
    test_settings: Settings,
) -> Any:
    """Create a test FastAPI app with all dependencies injected."""
    app = create_app()

    orch = Orchestrator(
        context_manager=context_manager,
        memory_manager=memory_manager,
        module_registry=module_registry,
        settings=test_settings,
    )
    app.state.orchestrator = orch
    return app


@pytest_asyncio.fixture
async def api_client(test_app: Any) -> TestClient:
    """Sync test client (for simple unit tests)."""
    return TestClient(test_app, raise_server_exceptions=False)


@pytest_asyncio.fixture
async def async_api_client(test_app: Any) -> AsyncGenerator[AsyncClient, None]:
    """Async test client for testing async endpoints."""
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        yield client


# ── Helper: pre-auth headers ──────────────────────────────────────────────────


@pytest.fixture
def auth_headers(test_settings: Settings) -> dict[str, str]:
    """Generate valid JWT auth headers for test requests."""
    from src.utils.security import create_access_token

    token = create_access_token(
        subject="test-user",
        tenant_id="test-tenant",
        roles=["admin"],
    )
    return {"Authorization": f"Bearer {token}"}
