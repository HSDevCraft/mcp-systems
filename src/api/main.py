"""FastAPI application factory and entry point.

Wires together all components:
  - Dependency injection (Redis, Qdrant, embedder, managers)
  - Middleware stack (auth, rate limit, logging)
  - Router registration
  - Startup/shutdown lifecycle hooks
  - Exception handlers
  - Prometheus metrics endpoint
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

import orjson
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from src.api.middleware.auth import AuthMiddleware
from src.api.middleware.logging import RequestLoggingMiddleware
from src.api.middleware.rate_limit import RateLimitMiddleware
from src.api.routers import context, health, memory, modules
from src.core.context_manager import ContextManager
from src.core.orchestrator import Orchestrator
from src.core.registry import ModuleRegistry
from src.memory.manager import MemoryManager
from src.memory.short_term import ShortTermMemoryStore
from src.memory.long_term import LongTermMemoryStore
from src.modules.plugins.echo import EchoModule
from src.modules.plugins.memory_retriever import MemoryRetrieverModule
from src.modules.plugins.summarizer import SummarizerModule
from src.utils.config import get_settings
from src.utils.exceptions import MCPError
from src.utils.logger import configure_logging, get_logger

logger = get_logger(__name__, component="app")


# ── Application Factory ────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        Fully configured FastAPI instance ready to serve requests.
    """
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format.value)

    app = FastAPI(
        title="MCP System",
        description=(
            "Production-grade Model Context Protocol platform. "
            "Manages context, memory, and modular AI interactions at enterprise scale."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=_lifespan,
        default_response_class=_ORJSONResponse,
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Custom middleware (applied in reverse order — last added = outermost) ─
    # Order of execution: LoggingMiddleware → AuthMiddleware → RateLimitMiddleware → Router
    app.add_middleware(RateLimitMiddleware, redis_client=None)  # injected after Redis init
    app.add_middleware(AuthMiddleware)
    app.add_middleware(RequestLoggingMiddleware)

    # ── Routers ───────────────────────────────────────────────────────────────
    api_prefix = "/api/v1"
    app.include_router(context.router, prefix=api_prefix)
    app.include_router(memory.router, prefix=api_prefix)
    app.include_router(modules.router, prefix=api_prefix)
    app.include_router(health.router)

    # ── Root endpoint ─────────────────────────────────────────────────────────
    @app.get("/", include_in_schema=False)
    async def root() -> dict:
        return {
            "service": "MCP System",
            "version": "0.1.0",
            "docs": "/docs",
            "health": "/health",
        }

    # ── Prometheus metrics endpoint ────────────────────────────────────────────
    if settings.enable_metrics:
        @app.get("/metrics", include_in_schema=False)
        async def metrics() -> Response:
            return Response(
                content=generate_latest(),
                media_type=CONTENT_TYPE_LATEST,
            )

    # ── Exception handlers ─────────────────────────────────────────────────────
    @app.exception_handler(MCPError)
    async def mcp_error_handler(request: Request, exc: MCPError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_dict(),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "unhandled_error",
            error=str(exc),
            path=request.url.path,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "type": "https://errors.mcp-system.io/internal_error",
                "title": "InternalServerError",
                "status": 500,
                "detail": "An unexpected error occurred",
            },
        )

    return app


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize and teardown all application dependencies."""
    settings = get_settings()
    logger.info("mcp_system_starting", environment=settings.mcp_env.value)

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_client = await _init_redis(settings)
    app.state.redis = redis_client

    # ── Qdrant ────────────────────────────────────────────────────────────────
    qdrant_client, embedder = await _init_qdrant(settings)
    app.state.qdrant = qdrant_client

    # ── Memory system ─────────────────────────────────────────────────────────
    short_term_store = ShortTermMemoryStore(redis_client=redis_client, settings=settings)
    long_term_store = LongTermMemoryStore(
        qdrant_client=qdrant_client, embedder=embedder, settings=settings
    )
    if qdrant_client is not None:
        try:
            await long_term_store.ensure_collection()
        except Exception as exc:
            logger.warning("qdrant_collection_init_failed", error=str(exc))

    memory_manager = MemoryManager(short_term=short_term_store, long_term=long_term_store)

    # ── Context manager ───────────────────────────────────────────────────────
    context_manager = ContextManager(redis_client=redis_client, settings=settings)

    # ── Module registry ───────────────────────────────────────────────────────
    registry = ModuleRegistry()
    await _register_builtin_modules(registry, memory_manager)

    if settings.modules_dir.exists():
        await registry.discover(settings.modules_dir)

    # ── Orchestrator ──────────────────────────────────────────────────────────
    orchestrator = Orchestrator(
        context_manager=context_manager,
        memory_manager=memory_manager,
        module_registry=registry,
        settings=settings,
    )
    app.state.orchestrator = orchestrator

    # Update rate limiter with live Redis client
    _inject_redis_into_middleware(app, redis_client)

    logger.info(
        "mcp_system_ready",
        modules_registered=len(registry.list_modules()),
        host=settings.mcp_host,
        port=settings.mcp_port,
    )

    yield  # Application runs here

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("mcp_system_shutting_down")
    await registry.shutdown()

    if redis_client is not None:
        await redis_client.aclose()

    if qdrant_client is not None:
        try:
            await qdrant_client.close()
        except Exception:
            pass  # sync fallback for mock clients

    logger.info("mcp_system_stopped")


# ── Dependency Init Helpers ────────────────────────────────────────────────────


async def _init_redis(settings: object) -> object:
    """Initialize async Redis client with connection pool."""
    try:
        import redis.asyncio as aioredis

        pool = aioredis.ConnectionPool.from_url(
            settings.redis_url,  # type: ignore[attr-defined]
            max_connections=settings.redis_max_connections,  # type: ignore[attr-defined]
            socket_timeout=settings.redis_socket_timeout,  # type: ignore[attr-defined]
            decode_responses=False,
        )
        client = aioredis.Redis(connection_pool=pool)
        await client.ping()
        logger.info("redis_connected", url=settings.redis_url)  # type: ignore[attr-defined]
        return client
    except Exception as exc:
        logger.warning("redis_unavailable", error=str(exc))
        return _InMemoryRedis()  # fallback for dev/test


async def _init_qdrant(settings: object) -> tuple[object | None, object]:
    """Initialize Qdrant client and embedding model."""
    from src.memory.long_term import MockEmbedder

    embedder = MockEmbedder(vector_size=settings.qdrant_vector_size)  # type: ignore[attr-defined]

    try:
        from qdrant_client import AsyncQdrantClient

        client = AsyncQdrantClient(
            url=settings.qdrant_url,  # type: ignore[attr-defined]
            api_key=settings.qdrant_api_key,  # type: ignore[attr-defined]
            timeout=10,
        )
        await client.get_collections()
        logger.info("qdrant_connected", url=settings.qdrant_url)  # type: ignore[attr-defined]
        return client, embedder
    except Exception as exc:
        logger.warning("qdrant_unavailable", error=str(exc))
        return None, embedder


async def _register_builtin_modules(
    registry: ModuleRegistry, memory_manager: MemoryManager
) -> None:
    """Register built-in modules at startup."""
    builtin_modules = [
        EchoModule(),
        SummarizerModule(),
        MemoryRetrieverModule(memory_manager=memory_manager),
    ]
    for module in builtin_modules:
        try:
            await registry.register(module)
        except Exception as exc:
            logger.warning(
                "builtin_module_registration_failed",
                module=getattr(module, "name", "unknown"),
                error=str(exc),
            )


def _inject_redis_into_middleware(app: FastAPI, redis_client: object) -> None:
    """Inject live Redis client into rate limiter middleware after startup."""
    for middleware in app.middleware_stack.__dict__.get("app", []):
        if hasattr(middleware, "_redis") and middleware._redis is None:  # type: ignore[union-attr]
            middleware._redis = redis_client  # type: ignore[union-attr]
            break


# ── In-memory Redis fallback (dev/test without Redis) ─────────────────────────


class _InMemoryRedis:
    """Minimal in-memory Redis substitute for development without Redis.

    Supports: get, set, delete, hset, hget, hgetall, lpush, ltrim, lrange,
    llen, rpop, expire, ping, zadd, zrevrange, zrange, incr, pipeline.
    NOT safe for concurrent use. For testing only.
    """

    def __init__(self) -> None:
        self._store: dict = {}
        self._lists: dict = {}
        self._sorted_sets: dict = {}

    async def ping(self) -> bool:
        return True

    async def get(self, key: str) -> bytes | None:
        return self._store.get(key)

    async def set(self, key: str, value: object, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self._store:
            return False
        self._store[key] = value if isinstance(value, bytes) else str(value).encode()
        return True

    async def delete(self, *keys: str) -> int:
        count = 0
        for key in keys:
            if key in self._store:
                del self._store[key]
                count += 1
            if key in self._lists:
                del self._lists[key]
                count += 1
        return count

    async def expire(self, key: str, seconds: int) -> bool:
        return True

    async def hset(self, key: str, mapping: dict | None = None, **kwargs: object) -> int:
        if key not in self._store:
            self._store[key] = {}
        d = mapping or kwargs
        self._store[key].update(d)
        return len(d)

    async def hget(self, key: str, field: str) -> bytes | None:
        return self._store.get(key, {}).get(field)

    async def lpush(self, key: str, *values: object) -> int:
        if key not in self._lists:
            self._lists[key] = []
        for v in values:
            self._lists[key].insert(0, v)
        return len(self._lists[key])

    async def ltrim(self, key: str, start: int, end: int) -> bool:
        if key in self._lists:
            self._lists[key] = self._lists[key][start: end + 1]
        return True

    async def lrange(self, key: str, start: int, end: int) -> list:
        lst = self._lists.get(key, [])
        if end == -1:
            return lst[start:]
        return lst[start: end + 1]

    async def llen(self, key: str) -> int:
        return len(self._lists.get(key, []))

    async def rpop(self, key: str) -> object | None:
        lst = self._lists.get(key, [])
        return lst.pop() if lst else None

    async def incr(self, key: str) -> int:
        val = int(self._store.get(key, b"0") or b"0")
        val += 1
        self._store[key] = str(val).encode()
        return val

    async def zadd(self, key: str, mapping: dict) -> int:
        if key not in self._sorted_sets:
            self._sorted_sets[key] = {}
        self._sorted_sets[key].update(mapping)
        return len(mapping)

    async def zrevrange(self, key: str, start: int, end: int) -> list:
        ss = self._sorted_sets.get(key, {})
        sorted_items = sorted(ss.keys(), key=lambda k: ss[k], reverse=True)
        if end == -1:
            return [k.encode() if isinstance(k, str) else k for k in sorted_items[start:]]
        return [
            k.encode() if isinstance(k, str) else k
            for k in sorted_items[start: end + 1]
        ]

    async def info(self, section: str = "all") -> dict:
        return {"used_memory": 0, "used_memory_human": "0B"}

    async def aclose(self) -> None:
        pass

    def pipeline(self) -> "_InMemoryPipeline":
        return _InMemoryPipeline(self)


class _InMemoryPipeline:
    def __init__(self, redis: _InMemoryRedis) -> None:
        self._redis = redis
        self._commands: list = []

    def set(self, *args: object, **kwargs: object) -> "_InMemoryPipeline":
        self._commands.append(("set", args, kwargs))
        return self

    def lpush(self, *args: object) -> "_InMemoryPipeline":
        self._commands.append(("lpush", args, {}))
        return self

    def ltrim(self, *args: object) -> "_InMemoryPipeline":
        self._commands.append(("ltrim", args, {}))
        return self

    def expire(self, *args: object) -> "_InMemoryPipeline":
        self._commands.append(("expire", args, {}))
        return self

    def zadd(self, *args: object, **kwargs: object) -> "_InMemoryPipeline":
        self._commands.append(("zadd", args, kwargs))
        return self

    def hset(self, *args: object, **kwargs: object) -> "_InMemoryPipeline":
        self._commands.append(("hset", args, kwargs))
        return self

    async def execute(self) -> list:
        results = []
        for cmd, args, kwargs in self._commands:
            method = getattr(self._redis, cmd)
            result = await method(*args, **kwargs)
            results.append(result)
        self._commands.clear()
        return results


# ── Custom JSON response ───────────────────────────────────────────────────────


class _ORJSONResponse(JSONResponse):
    """JSON response using orjson for serialization (faster + UUID support)."""

    def render(self, content: object) -> bytes:
        return orjson.dumps(
            content,
            option=orjson.OPT_NON_STR_KEYS | orjson.OPT_SERIALIZE_UUID,
        )


# ── Application instance ───────────────────────────────────────────────────────

app = create_app()


# ── Entry point ────────────────────────────────────────────────────────────────


def main() -> None:
    """Start the production server. Used as pyproject.toml script entry."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "src.api.main:app",
        host=settings.mcp_host,
        port=settings.mcp_port,
        workers=settings.mcp_workers if settings.is_production else 1,
        reload=settings.is_development,
        log_level=settings.log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
