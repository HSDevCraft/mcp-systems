# 15 — Advanced Patterns

Production patterns for building robust, observable, and scalable MCP servers.

---

## 1. Server Composition — Router Pattern

Split a large server into focused sub-modules, composed at runtime:

```python
# tools/calculator.py
from mcp.server.fastmcp import FastMCP

calculator_mcp = FastMCP("calculator")

@calculator_mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

@calculator_mcp.tool()
def multiply(a: int, b: int) -> int:
    """Multiply two numbers."""
    return a * b
```

```python
# tools/filesystem.py
from mcp.server.fastmcp import FastMCP
from pathlib import Path

fs_mcp = FastMCP("filesystem")

@fs_mcp.tool()
def read_file(path: str) -> str:
    """Read a file."""
    return Path(path).read_text()
```

```python
# main_server.py — compose all sub-modules
from mcp.server.fastmcp import FastMCP
from tools.calculator import calculator_mcp
from tools.filesystem import fs_mcp

main = FastMCP("composed-server")
main.include_server(calculator_mcp, prefix="calc")    # → calc_add, calc_multiply
main.include_server(fs_mcp,         prefix="fs")      # → fs_read_file
```

---

## 2. Middleware Pattern

Apply cross-cutting concerns (logging, auth, timing) to all tool calls:

```python
import time, functools
from typing import Callable, Any

def tool_middleware(fn: Callable) -> Callable:
    """Wrap a tool handler with logging and timing."""
    @functools.wraps(fn)
    async def wrapper(name: str, arguments: dict) -> Any:
        start = time.perf_counter()
        logger.info(f"→ tool={name!r} args_keys={list(arguments.keys())}")
        try:
            result = await fn(name, arguments)
            elapsed = (time.perf_counter() - start) * 1000
            logger.info(f"← tool={name!r} ok elapsed={elapsed:.1f}ms")
            return result
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            logger.error(f"← tool={name!r} error={e!r} elapsed={elapsed:.1f}ms")
            raise
    return wrapper

@server.call_tool()
@tool_middleware
async def call_tool(name: str, arguments: dict):
    return await dispatch_tool(name, arguments)
```

---

## 3. Plugin / Registry Pattern

Dynamic tool registration without modifying the server code:

```python
from abc import ABC, abstractmethod
import mcp.types as types

class ToolPlugin(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def input_schema(self) -> dict: ...

    @abstractmethod
    async def execute(self, arguments: dict) -> list[types.TextContent]: ...


class ToolRegistry:
    def __init__(self):
        self._plugins: dict[str, ToolPlugin] = {}

    def register(self, plugin: ToolPlugin) -> None:
        self._plugins[plugin.name] = plugin

    def get_tools(self) -> list[types.Tool]:
        return [
            types.Tool(
                name=p.name,
                description=p.description,
                inputSchema=p.input_schema,
            )
            for p in self._plugins.values()
        ]

    async def call(self, name: str, arguments: dict):
        plugin = self._plugins.get(name)
        if not plugin:
            raise ValueError(f"Unknown tool: {name}")
        return await plugin.execute(arguments)


registry = ToolRegistry()

# Register plugins from external packages
registry.register(CalculatorPlugin())
registry.register(SearchPlugin(api_key=os.environ["SEARCH_KEY"]))

@server.list_tools()
async def list_tools():
    return registry.get_tools()

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    return await registry.call(name, arguments)
```

---

## 4. Caching Pattern

Cache expensive tool results and resource reads:

```python
import hashlib, json, time
from functools import lru_cache

class TTLCache:
    def __init__(self, ttl_seconds: float = 60.0):
        self.ttl  = ttl_seconds
        self._data: dict[str, tuple[Any, float]] = {}

    def _key(self, name: str, args: dict) -> str:
        return hashlib.sha256(f"{name}:{json.dumps(args, sort_keys=True)}".encode()).hexdigest()

    def get(self, name: str, args: dict) -> Any | None:
        k = self._key(name, args)
        if k in self._data:
            value, ts = self._data[k]
            if time.monotonic() - ts < self.ttl:
                return value
            del self._data[k]
        return None

    def set(self, name: str, args: dict, value: Any) -> None:
        self._data[self._key(name, args)] = (value, time.monotonic())

cache = TTLCache(ttl_seconds=300)  # 5-minute cache

CACHEABLE_TOOLS = {"web_search", "get_weather", "fetch_exchange_rates"}

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name in CACHEABLE_TOOLS:
        cached = cache.get(name, arguments)
        if cached is not None:
            return cached
    result = await execute_tool(name, arguments)
    if name in CACHEABLE_TOOLS and not result[0].text.startswith("Error"):
        cache.set(name, arguments, result)
    return result
```

---

## 5. Dependency Injection Pattern

Inject services (DB, HTTP client, config) into handlers without globals:

```python
from dataclasses import dataclass
import asyncpg, httpx

@dataclass
class ServerDeps:
    db:     asyncpg.Pool
    http:   httpx.AsyncClient
    config: Settings

# Stored in a context variable (async-safe)
from contextvars import ContextVar
_deps: ContextVar[ServerDeps] = ContextVar("deps")

def get_deps() -> ServerDeps:
    return _deps.get()

@contextlib.asynccontextmanager
async def lifespan(app):
    deps = ServerDeps(
        db=await asyncpg.create_pool(dsn=config.db_url),
        http=httpx.AsyncClient(timeout=30),
        config=config,
    )
    token = _deps.set(deps)
    yield
    _deps.reset(token)
    await deps.db.close()
    await deps.http.aclose()

mcp = FastMCP("di-server", lifespan=lifespan)

@mcp.tool()
async def get_user(user_id: str) -> str:
    """Get user by ID."""
    deps = get_deps()
    row = await deps.db.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
    return json.dumps(dict(row))
```

---

## 6. Streaming / Long-Running Tools with Progress

```python
import asyncio
from mcp.server.fastmcp import FastMCP, Context

mcp = FastMCP("streaming-server")

@mcp.tool()
async def process_large_dataset(file_path: str, ctx: Context) -> str:
    """Process a large dataset file with progress reporting."""
    with open(file_path) as f:
        lines = f.readlines()

    total   = len(lines)
    results = []

    for i, line in enumerate(lines):
        results.append(transform(line.strip()))

        # Report progress every 100 items
        if i % 100 == 0:
            await ctx.report_progress(i, total)
            await asyncio.sleep(0)  # yield event loop

    await ctx.report_progress(total, total)
    return f"Processed {total} lines. Summary: {summarize(results)}"
```

---

## 7. Multi-Tenant Server

Route requests to tenant-specific resources based on a header or token claim:

```python
from contextvars import ContextVar

_tenant_id: ContextVar[str] = ContextVar("tenant_id", default="default")

class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        tenant = extract_tenant_from_token(request.headers.get("Authorization", ""))
        token = _tenant_id.set(tenant)
        try:
            return await call_next(request)
        finally:
            _tenant_id.reset(token)

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    tenant = _tenant_id.get()
    # Route to tenant-specific DB schema
    async with get_tenant_db(tenant) as conn:
        return await execute_tool_for_tenant(name, arguments, conn)
```

---

## 8. Observability — Metrics + Tracing

```python
from prometheus_client import Counter, Histogram, start_http_server
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider

# Prometheus metrics
TOOL_CALLS   = Counter("mcp_tool_calls_total",   "Tool calls", ["tool", "status"])
TOOL_LATENCY = Histogram("mcp_tool_latency_seconds", "Tool latency", ["tool"])

# OpenTelemetry tracer
tracer = trace.get_tracer("mcp-server")

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    with tracer.start_as_current_span(f"tool/{name}") as span:
        span.set_attribute("tool.name", name)
        span.set_attribute("tool.args_keys", str(list(arguments.keys())))

        with TOOL_LATENCY.labels(tool=name).time():
            try:
                result = await execute_tool(name, arguments)
                status = "error" if getattr(result[0], "isError", False) else "ok"
                TOOL_CALLS.labels(tool=name, status=status).inc()
                span.set_attribute("tool.status", status)
                return result
            except Exception as e:
                TOOL_CALLS.labels(tool=name, status="exception").inc()
                span.record_exception(e)
                raise
```

---

## 9. Versioned Tool APIs

Handle breaking changes gracefully:

```python
@server.list_tools()
async def list_tools():
    return [
        types.Tool(
            name="search",           # current stable
            description="Search the web. Returns structured results.",
            inputSchema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        ),
        types.Tool(
            name="search_v2",        # new version with more features
            description="Search the web (v2) with language and date filters.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query":    {"type": "string"},
                    "language": {"type": "string", "default": "en"},
                    "since":    {"type": "string", "description": "ISO date"},
                },
                "required": ["query"],
            },
        ),
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "search":
        return await search_v1(arguments["query"])
    if name == "search_v2":
        return await search_v2(**arguments)
```

---

## 10. Circuit Breaker

Prevent cascading failures when an external service is down:

```python
import time

class CircuitBreaker:
    def __init__(self, failure_threshold=5, recovery_timeout=60):
        self.failures = 0
        self.threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.last_failure_time = None
        self.state = "closed"  # closed | open | half-open

    def can_attempt(self) -> bool:
        if self.state == "closed":
            return True
        if self.state == "open":
            if time.monotonic() - self.last_failure_time > self.recovery_timeout:
                self.state = "half-open"
                return True
            return False
        return True  # half-open: allow one attempt

    def record_success(self):
        self.failures = 0
        self.state = "closed"

    def record_failure(self):
        self.failures += 1
        self.last_failure_time = time.monotonic()
        if self.failures >= self.threshold:
            self.state = "open"

breaker = CircuitBreaker()

@mcp.tool()
async def call_external_api(query: str) -> str:
    """Call an external API with circuit breaker protection."""
    if not breaker.can_attempt():
        return "Service temporarily unavailable (circuit breaker open). Try again in ~60s."
    try:
        result = await do_api_call(query)
        breaker.record_success()
        return result
    except Exception as e:
        breaker.record_failure()
        raise
```

---

## 11. Request Deduplication

Prevent duplicate operations from concurrent identical requests:

```python
import asyncio

_in_flight: dict[str, asyncio.Future] = {}

@mcp.tool()
async def idempotent_tool(key: str) -> str:
    """Safe to call multiple times; identical concurrent calls share one result."""
    if key in _in_flight:
        return await asyncio.shield(_in_flight[key])

    future: asyncio.Future = asyncio.get_event_loop().create_future()
    _in_flight[key] = future
    try:
        result = await do_expensive_work(key)
        future.set_result(result)
        return result
    except Exception as e:
        future.set_exception(e)
        raise
    finally:
        _in_flight.pop(key, None)
```

---

## 12. FastMCP Context Object

`Context` in FastMCP provides access to per-request utilities:

```python
from mcp.server.fastmcp import FastMCP, Context

mcp = FastMCP("context-demo")

@mcp.tool()
async def smart_tool(query: str, ctx: Context) -> str:
    """Tool that uses the MCP context."""
    # Structured logging (appears in host logs)
    await ctx.info(f"Processing query: {query!r}")

    # Report progress
    await ctx.report_progress(0, 3)

    step1 = await do_step1(query)
    await ctx.report_progress(1, 3)
    await ctx.debug(f"Step 1 done: {step1!r}")

    step2 = await do_step2(step1)
    await ctx.report_progress(2, 3)

    # Make an LLM sampling request
    summary = await ctx.sample(
        f"Summarise this in one sentence: {step2}",
        max_tokens=100,
    )
    await ctx.report_progress(3, 3)

    # Read a resource
    config_data = await ctx.read_resource("config://app")

    return f"{summary.content.text}\n\nConfig: {config_data}"
```
