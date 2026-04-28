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

---

## 13. Server Federation

Connect multiple specialised MCP servers behind a single gateway server that routes requests:

```python
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp import FastMCP
from mcp.server import Server
import mcp.types as types

# ── Federation Gateway ────────────────────────────────────────────────────────
class MCPFederationGateway:
    """
    A gateway MCP server that federates requests to upstream MCP servers.
    From the client's perspective, it looks like one unified server.
    """

    def __init__(self):
        self._upstreams: dict[str, ClientSession] = {}
        self._tool_routing: dict[str, str] = {}    # tool_name → upstream_name
        self._resource_routing: dict[str, str] = {} # uri_prefix → upstream_name

    async def register_upstream(
        self,
        name: str,
        params: StdioServerParameters,
        tool_prefix: str = "",
    ) -> None:
        read_stream, write_stream = await self._open_stdio(params)
        session = ClientSession(read_stream, write_stream)
        await session.__aenter__()
        await session.initialize()

        self._upstreams[name] = session

        # Index all tools from this upstream
        if session._initialized_result.capabilities.tools:
            tools = await session.list_tools()
            for tool in tools.tools:
                routed_name = f"{tool_prefix}{tool.name}" if tool_prefix else tool.name
                self._tool_routing[routed_name] = name

    async def get_all_tools(self) -> list[types.Tool]:
        all_tools = []
        for upstream_name, session in self._upstreams.items():
            if session._initialized_result.capabilities.tools:
                result = await session.list_tools()
                for tool in result.tools:
                    # Re-namespace tool for gateway
                    prefixed = types.Tool(
                        name=f"{upstream_name}__{tool.name}",
                        description=f"[{upstream_name}] {tool.description}",
                        inputSchema=tool.inputSchema,
                    )
                    all_tools.append(prefixed)
        return all_tools

    async def call_tool(self, namespaced_name: str, arguments: dict):
        upstream_name, _, tool_name = namespaced_name.partition("__")
        session = self._upstreams.get(upstream_name)
        if not session:
            raise ValueError(f"Unknown upstream: {upstream_name!r}")
        return await session.call_tool(tool_name, arguments)


# ── Gateway server ────────────────────────────────────────────────────────────
gateway = MCPFederationGateway()
server  = Server("federation-gateway")

@server.list_tools()
async def list_tools():
    return await gateway.get_all_tools()

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    return await gateway.call_tool(name, arguments)
```

---

## 14. Event-Driven MCP (Webhook Integration)

Trigger MCP resource notifications from external webhooks:

```python
import asyncio
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse
from starlette.requests import Request
from mcp.server.sse import SseServerTransport
from mcp.server import Server
import mcp.types as types

server = Server("event-driven")
sse    = SseServerTransport("/messages")

# Store active sessions to broadcast notifications
_active_sessions: list = []
_session_lock = asyncio.Lock()

@server.list_resources()
async def list_resources():
    return [
        types.Resource(uri="events://latest", name="Latest Event", mimeType="application/json"),
        types.Resource(uri="events://history", name="Event History", mimeType="application/json"),
    ]

@server.read_resource()
async def read_resource(uri: str) -> str:
    import json
    if uri == "events://latest":
        return json.dumps(_event_store.get_latest())
    if uri == "events://history":
        return json.dumps(_event_store.get_history(limit=50))
    raise ValueError(f"Unknown resource: {uri}")


# Webhook endpoint — receives events from external systems
async def handle_webhook(request: Request):
    """Receive external events and broadcast to all connected MCP clients."""
    payload = await request.json()
    event_type = payload.get("type", "unknown")

    # Store the event
    _event_store.add(payload)

    # Notify all active MCP sessions
    async with _session_lock:
        sessions = list(_active_sessions)

    for session in sessions:
        try:
            # Notify clients that the events resource has been updated
            await session.send_resource_updated("events://latest")
        except Exception:
            pass  # Session may have disconnected

    return JSONResponse({"status": "ok", "event_type": event_type})


async def handle_sse(request: Request):
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        session = streams[2]  # the server session object
        async with _session_lock:
            _active_sessions.append(session)
        try:
            await server.run(streams[0], streams[1], init_options)
        finally:
            async with _session_lock:
                _active_sessions.remove(session)

app = Starlette(routes=[
    Route("/webhook",  handle_webhook,  methods=["POST"]),
    Route("/sse",      handle_sse),
    Mount("/messages", handle_messages),
])
```

---

## 15. Horizontal Scaling with Redis Pub/Sub

For multi-instance SSE deployments where notifications must reach all instances:

```python
import asyncio, json
import redis.asyncio as aioredis
from mcp.server.sse import SseServerTransport
from mcp.server import Server

server  = Server("distributed-server")
sse     = SseServerTransport("/messages")

# Redis pub/sub channel for cross-instance notifications
REDIS_CHANNEL = "mcp:notifications"
redis_client  = None
_local_sessions: set = set()


async def setup_redis(url: str = "redis://localhost:6379") -> None:
    global redis_client
    redis_client = await aioredis.from_url(url)

    # Start subscriber in background
    asyncio.create_task(_redis_subscriber())


async def _redis_subscriber() -> None:
    """Listen for cross-instance notifications and fan out to local sessions."""
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(REDIS_CHANNEL)

    async for message in pubsub.listen():
        if message["type"] != "message":
            continue
        data = json.loads(message["data"])
        notification_type = data.get("type")
        uri = data.get("uri")

        # Broadcast to all sessions on this instance
        for session in list(_local_sessions):
            try:
                if notification_type == "resource_updated" and uri:
                    await session.send_resource_updated(uri)
                elif notification_type == "tool_list_changed":
                    await session.send_tool_list_changed()
            except Exception:
                _local_sessions.discard(session)


async def broadcast_resource_updated(uri: str) -> None:
    """Publish a resource-updated notification to ALL instances via Redis."""
    if redis_client:
        await redis_client.publish(
            REDIS_CHANNEL,
            json.dumps({"type": "resource_updated", "uri": uri}),
        )


# Usage: after a resource changes, broadcast to all instances
@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "update_config":
        config.update(arguments["key"], arguments["value"])
        await broadcast_resource_updated("config://app")  # notifies ALL instances
        return [types.TextContent(type="text", text="Config updated")]
```

---

## 16. CQRS Pattern (Command/Query Separation)

Separate read (query) tools and write (command) tools for clarity and safety:

```python
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("cqrs-server")

# ── QUERY tools (read-only, safe, no side effects) ────────────────────────────
@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
async def get_order(order_id: str) -> str:
    """QUERY: Get current state of an order. Returns order details."""
    order = await order_repo.find_by_id(order_id)
    if not order:
        raise ValueError(f"Order not found: {order_id}")
    return order.to_json()

@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
async def list_orders(
    status: str = "all",
    page: int = 1,
    limit: int = 20,
) -> str:
    """QUERY: List orders with optional status filter."""
    orders = await order_repo.list(status=status, offset=(page-1)*limit, limit=limit)
    return orders.to_json()

@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
async def search_orders(query: str) -> str:
    """QUERY: Full-text search across orders."""
    results = await order_search.search(query)
    return results.to_json()

# ── COMMAND tools (write, require confirmation, have side effects) ─────────────
@mcp.tool(annotations=ToolAnnotations(destructive_hint=False, idempotent_hint=True))
async def create_order(customer_id: str, items: list[dict]) -> str:
    """COMMAND: Create a new order. Returns new order ID."""
    order = await order_service.create(customer_id=customer_id, items=items)
    return f"Order created: {order.id}"

@mcp.tool(annotations=ToolAnnotations(destructive_hint=True, idempotent_hint=False))
async def cancel_order(order_id: str, reason: str) -> str:
    """COMMAND: Cancel an order. Irreversible. Use only if user explicitly requests cancellation."""
    result = await order_service.cancel(order_id=order_id, reason=reason)
    return f"Order {order_id} cancelled: {result.status}"

@mcp.tool(annotations=ToolAnnotations(destructive_hint=False, idempotent_hint=True))
async def update_order_status(order_id: str, status: str) -> str:
    """COMMAND: Update order status. Valid statuses: pending, processing, shipped, delivered."""
    valid_statuses = {"pending", "processing", "shipped", "delivered"}
    if status not in valid_statuses:
        raise ValueError(f"Invalid status: {status}. Must be one of: {valid_statuses}")
    await order_service.update_status(order_id=order_id, status=status)
    return f"Order {order_id} status updated to {status}"
```

---

## 17. Lazy-Loading Resources

Load resource data only when requested, not at server startup:

```python
import asyncio
from functools import lru_cache
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("lazy-server")

# Lazy-loaded, connection-cached DB schema
_schema_cache: dict | None = None
_schema_lock = asyncio.Lock()

async def get_db_schema() -> dict:
    """Fetch DB schema once and cache indefinitely (restart to refresh)."""
    global _schema_cache
    if _schema_cache is not None:
        return _schema_cache
    async with _schema_lock:
        if _schema_cache is not None:
            return _schema_cache
        # Double-checked locking
        async with get_db() as conn:
            tables = await conn.fetch(
                "SELECT table_name, column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "ORDER BY table_name, ordinal_position"
            )
        schema = {}
        for row in tables:
            tbl = row["table_name"]
            schema.setdefault(tbl, []).append({
                "column": row["column_name"],
                "type":   row["data_type"],
            })
        _schema_cache = schema
    return _schema_cache


@mcp.resource("db://schema")
async def get_schema() -> str:
    """Database schema (lazily loaded, cached in memory)."""
    import json
    schema = await get_db_schema()
    return json.dumps(schema, indent=2)


@mcp.resource("db://schema/{table}")
async def get_table_schema(table: str) -> str:
    """Schema for a specific table."""
    import json
    schema = await get_db_schema()
    if table not in schema:
        raise ValueError(f"Table not found: {table}")
    return json.dumps({"table": table, "columns": schema[table]}, indent=2)
```

---

## Pattern Decision Guide

```
What do you need?
│
├── Multiple specialised servers with a unified interface?
│   └── ✓ Server Federation (Pattern 13)
│
├── Real-time event push from external systems?
│   └── ✓ Event-Driven / Webhook (Pattern 14)
│
├── Cross-instance notifications in a scaled deployment?
│   └── ✓ Redis Pub/Sub broadcast (Pattern 15)
│
├── Safe/dangerous tool separation?
│   └── ✓ CQRS with Annotations (Pattern 16)
│
├── Expensive DB queries at startup?
│   └── ✓ Lazy-Loading Resources (Pattern 17)
│
├── Cross-cutting concerns (logging, auth, metrics)?
│   └── ✓ Middleware Pattern (Pattern 2)
│
├── Dynamic tool registration?
│   └── ✓ Plugin/Registry Pattern (Pattern 3)
│
├── Expensive tool results?
│   └── ✓ Caching Pattern (Pattern 4)
│
├── Long-running operations?
│   └── ✓ Streaming with Progress (Pattern 6)
│
└── Multi-tenant isolation?
    └── ✓ Multi-Tenant Pattern (Pattern 7)
```

---

## Common Advanced Pattern Pitfalls

| Pitfall | Pattern | Problem | Fix |
|---------|---------|---------|-----|
| No sticky sessions in federation | Federation | Upstream session state lost on re-route | Use consistent hashing or session affinity |
| Missing reconnect in federation | Federation | Upstream crash silently breaks gateway | Add health checks and reconnect logic to each upstream |
| Webhook replay attacks | Event-driven | Duplicate events processed multiple times | Add idempotency key deduplication |
| No Redis pub/sub reconnect | Horizontal scaling | Redis disconnect breaks all notifications | Auto-reconnect subscriber on disconnect |
| CQRS commands without confirmations | CQRS | Destructive operations executed without user awareness | Set `destructiveHint=True`; host shows confirmation |
| Schema cache stale after migration | Lazy loading | Old schema used after DB migration | Expose a `refresh_schema` tool; cache with TTL |

---

## Key Takeaways

- **Federation** enables a unified view across specialised servers — one client, many upstreams.
- **Event-driven** MCP bridges webhooks and real-time external events to subscribed LLM sessions.
- **Redis pub/sub** enables cross-instance notifications in horizontally-scaled SSE deployments.
- **CQRS** with tool annotations gives hosts clear signals about read-only vs. destructive operations.
- **Lazy-loading** with double-checked locking avoids expensive startup operations and race conditions.
- Always combine advanced patterns with the **observability** (Pattern 8) and **circuit breaker** (Pattern 10) patterns in production.
