# 10 — Server Lifecycle

A complete guide to building, starting, running, and shutting down an MCP server correctly.

---

## Lifecycle Phases

```
STARTUP                INITIALIZATION            RUNNING              SHUTDOWN
──────────────────     ──────────────────────    ───────────────────  ───────────
Load config            Receive initialize        Handle requests      Flush buffers
Connect to DBs         Declare capabilities      Emit notifications   Close connections
Load models            Complete handshake        Process tools/       Release resources
Set up caches                                    resources/prompts    Exit process
```

---

## Server Initialization Options

When running the server, you must provide `InitializationOptions`:

```python
from mcp.server.models import InitializationOptions
from mcp.server import NotificationOptions

init_options = InitializationOptions(
    server_name="my-server",       # shown in client logs and UIs
    server_version="1.2.0",        # semver
    capabilities=server.get_capabilities(
        notification_options=NotificationOptions(
            tools_changed=True,     # will send notifications/tools/list_changed
            resources_changed=True, # will send notifications/resources/list_changed
            prompts_changed=True,   # will send notifications/prompts/list_changed
        ),
        experimental_capabilities={},
    ),
)
```

---

## Full Lifecycle — Low-Level Server

```python
import asyncio
import signal
from contextlib import asynccontextmanager

import mcp.types as types
from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

# ── 1. Global state (initialised before server starts) ───────────────────────
db_pool = None
http_client = None

# ── 2. Server definition ──────────────────────────────────────────────────────
server = Server("lifecycle-demo")

# ── 3. Capability handlers ────────────────────────────────────────────────────
@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="query_db",
            description="Query the database",
            inputSchema={
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "SQL query"}
                },
                "required": ["sql"],
            },
        )
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "query_db":
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(arguments["sql"])
            return [types.TextContent(type="text", text=str(list(rows)))]
    raise ValueError(f"Unknown tool: {name}")

# ── 4. Startup / shutdown logic ───────────────────────────────────────────────
async def startup() -> None:
    global db_pool, http_client
    import asyncpg, httpx
    db_pool = await asyncpg.create_pool(dsn="postgresql://localhost/mydb", min_size=2, max_size=10)
    http_client = httpx.AsyncClient(timeout=30.0)

async def shutdown() -> None:
    if db_pool:
        await db_pool.close()
    if http_client:
        await http_client.aclose()

# ── 5. Main entry point ───────────────────────────────────────────────────────
async def main() -> None:
    await startup()
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="lifecycle-demo",
                    server_version="1.0.0",
                    capabilities=server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )
    finally:
        await shutdown()

if __name__ == "__main__":
    asyncio.run(main())
```

---

## Lifecycle — FastMCP (Recommended)

FastMCP supports a `lifespan` context manager for clean startup/shutdown:

```python
from contextlib import asynccontextmanager
from mcp.server.fastmcp import FastMCP

@asynccontextmanager
async def lifespan(app):
    # Startup
    import asyncpg
    app.state.db = await asyncpg.create_pool(dsn="postgresql://localhost/mydb")
    print("Database connected")

    yield  # Server is running

    # Shutdown
    await app.state.db.close()
    print("Database disconnected")


mcp = FastMCP("lifecycle-demo", lifespan=lifespan)

@mcp.tool()
async def query_db(sql: str) -> str:
    """Run a SQL query."""
    async with mcp.state.db.acquire() as conn:
        rows = await conn.fetch(sql)
        return str(list(rows))
```

---

## Capability Registration Patterns

### Conditional capabilities (feature flags)
```python
import os

server = Server("conditional-server")

ENABLE_EXPERIMENTAL = os.getenv("ENABLE_EXPERIMENTAL", "false") == "true"

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    tools = [TOOL_SEARCH, TOOL_CALCULATE]
    if ENABLE_EXPERIMENTAL:
        tools.append(TOOL_EXPERIMENTAL)
    return tools
```

### Dynamic capability announcement
```python
loaded_plugins: set[str] = set()

async def load_plugin(name: str) -> None:
    loaded_plugins.add(name)
    # Notify all connected clients that tool list changed
    await server.request_context.session.send_tool_list_changed()

async def unload_plugin(name: str) -> None:
    loaded_plugins.discard(name)
    await server.request_context.session.send_tool_list_changed()
```

---

## Health Checks

Expose a health tool or implement a lightweight ping:

```python
import time

START_TIME = time.time()

@server.list_tools()
async def list_tools():
    return [
        types.Tool(
            name="server_health",
            description="Check server health and uptime",
            inputSchema={"type": "object", "properties": {}},
        )
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "server_health":
        uptime = time.time() - START_TIME
        return [types.TextContent(type="text", text=(
            f"Status: healthy\n"
            f"Uptime: {uptime:.0f}s\n"
            f"DB: {'connected' if db_pool else 'disconnected'}"
        ))]
```

---

## Graceful Shutdown Patterns

### Handling OS signals (stdio server)
```python
import asyncio, signal

shutdown_event = asyncio.Event()

def handle_sigterm():
    shutdown_event.set()

async def main():
    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGTERM, handle_sigterm)
    loop.add_signal_handler(signal.SIGINT,  handle_sigterm)

    await startup()
    try:
        async with stdio_server() as (r, w):
            server_task = asyncio.create_task(server.run(r, w, init_options))
            done, _ = await asyncio.wait(
                [server_task, asyncio.create_task(shutdown_event.wait())],
                return_when=asyncio.FIRST_COMPLETED,
            )
    finally:
        await shutdown()
```

### Draining in-flight requests
```python
in_flight: set[asyncio.Task] = set()

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    task = asyncio.current_task()
    in_flight.add(task)
    try:
        return await do_work(name, arguments)
    finally:
        in_flight.discard(task)

async def graceful_shutdown():
    if in_flight:
        await asyncio.gather(*in_flight, return_exceptions=True)
    await shutdown()
```

---

## Configuration Best Practices

```python
from pydantic import BaseSettings, Field

class ServerConfig(BaseSettings):
    server_name:    str  = Field(default="my-server")
    server_version: str  = Field(default="1.0.0")
    db_url:         str  = Field(..., env="DATABASE_URL")
    api_key:        str  = Field(..., env="API_KEY")
    max_results:    int  = Field(default=10, env="MAX_RESULTS")
    log_level:      str  = Field(default="INFO", env="LOG_LEVEL")

    class Config:
        env_file = ".env"

config = ServerConfig()
```

### Configuration via environment (Claude Desktop)
```jsonc
{
  "mcpServers": {
    "my-server": {
      "command": "python",
      "args": ["server.py"],
      "env": {
        "DATABASE_URL": "postgresql://...",
        "API_KEY": "sk-...",
        "LOG_LEVEL": "INFO"
      }
    }
  }
}
```

---

## Logging Best Practices

### Send logs to client via MCP logging
```python
@server.call_tool()
async def call_tool(name: str, arguments: dict):
    session = server.request_context.session

    await session.send_log_message(
        level="info",
        data=f"Executing tool: {name}",
        logger="tool-handler",
    )
    result = await execute(name, arguments)
    await session.send_log_message(
        level="debug",
        data=f"Tool {name} completed in {elapsed:.2f}s",
    )
    return result
```

### Write to stderr for host-level logging
```python
import sys, json, logging

# Configure structured logging to stderr
logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}',
    stream=sys.stderr,  # IMPORTANT: never write to stdout (used for protocol)
)
logger = logging.getLogger(__name__)
```

**Critical**: In stdio mode, `stdout` is the protocol wire. Never print to stdout.
All debug output must go to `stderr`.

---

## Versioning Strategy

| Field | Meaning | When to bump |
|-------|---------|--------------|
| `server_version` | Server implementation version | Any server-side change |
| `protocolVersion` | MCP protocol version | Only when switching protocol versions |

Use semver for `server_version`:
- **PATCH** (1.0.X): bug fixes, no API changes
- **MINOR** (1.X.0): new tools/resources added (backward compatible)
- **MAJOR** (X.0.0): breaking changes (tools removed/renamed, schema changes)

---

## Kubernetes Lifecycle Integration

### Readiness and Liveness Probes for SSE servers

```python
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse
import time

mcp = FastMCP("k8s-server")
START_TIME = time.time()

# Health check endpoints exposed alongside the MCP SSE endpoint
async def readiness_probe(request):
    """K8s readiness probe — is this pod ready to serve traffic?"""
    checks = {}
    all_ok = True

    # Check DB connection
    try:
        async with get_db() as conn:
            await conn.fetchval("SELECT 1")
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {e}"
        all_ok = False

    status = 200 if all_ok else 503
    return JSONResponse({"status": "ready" if all_ok else "not_ready", "checks": checks}, status_code=status)


async def liveness_probe(request):
    """K8s liveness probe — is this pod still alive (not deadlocked)?"""
    uptime = time.time() - START_TIME
    return JSONResponse({"status": "alive", "uptime_seconds": uptime})


async def startup_probe(request):
    """K8s startup probe — has initialization completed?"""
    if not _initialized:
        return JSONResponse({"status": "starting"}, status_code=503)
    return JSONResponse({"status": "started"})


# Compose MCP SSE app + health endpoints
sse = SseServerTransport("/messages")

app = Starlette(routes=[
    Route("/healthz/live",    liveness_probe),
    Route("/healthz/ready",   readiness_probe),
    Route("/healthz/startup", startup_probe),
    Route("/sse",             handle_sse),
    Mount("/messages",        app=handle_messages),
])
```

### Kubernetes deployment manifest

```yaml
# deploy/kubernetes/mcp-server.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mcp-server
spec:
  replicas: 3
  selector:
    matchLabels:
      app: mcp-server
  template:
    metadata:
      labels:
        app: mcp-server
    spec:
      containers:
      - name: mcp-server
        image: my-mcp-server:1.2.0
        ports:
        - containerPort: 8080
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: mcp-secrets
              key: database-url
        livenessProbe:
          httpGet:
            path: /healthz/live
            port: 8080
          initialDelaySeconds: 10
          periodSeconds: 30
          timeoutSeconds: 5
        readinessProbe:
          httpGet:
            path: /healthz/ready
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 10
          timeoutSeconds: 3
          failureThreshold: 3
        startupProbe:
          httpGet:
            path: /healthz/startup
            port: 8080
          failureThreshold: 30    # 30 × 10s = 5 min startup budget
          periodSeconds: 10
        lifecycle:
          preStop:
            exec:
              command: ["/bin/sh", "-c", "sleep 5"]  # drain connections before shutdown
        resources:
          requests:
            memory: "256Mi"
            cpu: "250m"
          limits:
            memory: "512Mi"
            cpu: "1000m"
```

---

## Rolling Update Strategy

Ensure zero-downtime updates with proper lifecycle hooks:

```python
import asyncio, signal
from contextlib import asynccontextmanager
from mcp.server.fastmcp import FastMCP

_shutdown_event = asyncio.Event()
_active_connections = 0

@asynccontextmanager
async def lifespan(app):
    global _active_connections

    # Register SIGTERM handler (sent by K8s during rolling update)
    def handle_sigterm(*args):
        print("SIGTERM received — starting graceful shutdown", flush=True)
        _shutdown_event.set()

    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT,  handle_sigterm)

    # Startup
    app.state.db = await create_db_pool()
    app.state.ready = True
    print("Server ready", flush=True)

    yield  # Running

    # Shutdown — wait for active connections to drain
    print("Draining connections…", flush=True)
    deadline = asyncio.get_event_loop().time() + 30.0  # 30s drain timeout
    while _active_connections > 0:
        if asyncio.get_event_loop().time() > deadline:
            print(f"Drain timeout: {_active_connections} connections still active", flush=True)
            break
        await asyncio.sleep(0.5)

    await app.state.db.close()
    print("Shutdown complete", flush=True)

mcp = FastMCP("rolling-safe", lifespan=lifespan)
```

---

## Multi-Session Server

One server process handling multiple concurrent client sessions:

```python
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from mcp.server.sse import SseServerTransport
from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions

@dataclass
class SessionState:
    session_id: str
    connected_at: float
    tool_calls: int = 0
    last_activity: float = field(default_factory=lambda: __import__("time").time())

_sessions: dict[str, SessionState] = {}
_sessions_lock = asyncio.Lock()

server = Server("multi-session")

@server.list_tools()
async def list_tools():
    return [
        types.Tool(
            name="session_info",
            description="Get information about the current session.",
            inputSchema={"type": "object", "properties": {}},
        )
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "session_info":
        # Access per-session context
        ctx = server.request_context
        session_id = id(ctx.session)

        async with _sessions_lock:
            state = _sessions.get(session_id)
            if state:
                state.tool_calls += 1
                state.last_activity = __import__("time").time()

        return [types.TextContent(
            type="text",
            text=f"Session {session_id}: {state.tool_calls if state else 0} tool calls",
        )]

async def handle_sse(request):
    import time
    session_id = id(request)
    async with _sessions_lock:
        _sessions[session_id] = SessionState(
            session_id=str(session_id),
            connected_at=time.time(),
        )
    try:
        async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
            await server.run(streams[0], streams[1], init_options)
    finally:
        async with _sessions_lock:
            _sessions.pop(session_id, None)
```

---

## Structured Startup / Shutdown Checklist

```
STARTUP                                    SHUTDOWN
───────────────────────────────────────    ────────────────────────────────────────
[ ] Load & validate configuration          [ ] Stop accepting new connections
[ ] Connect to databases (pool)            [ ] Complete in-flight requests
[ ] Connect to caches (Redis)              [ ] Flush pending log messages
[ ] Load ML models / embeddings            [ ] Close DB connections gracefully
[ ] Warm up connection pools               [ ] Close HTTP clients
[ ] Run health checks on dependencies      [ ] Flush metrics to Prometheus
[ ] Register signal handlers               [ ] Remove from load balancer (preStop hook)
[ ] Mark server as ready                   [ ] Exit with code 0
```

---

## Common Lifecycle Pitfalls

| Pitfall | Problem | Fix |
|---------|---------|-----|
| No `preStop` hook in K8s | Requests cut off during rolling update | Add `lifecycle.preStop: sleep 5` |
| Missing startup probe | Readiness probe fails before init done | Use `startupProbe` with high `failureThreshold` |
| Global state in multi-session server | Sessions corrupt each other's state | Use `contextvars.ContextVar` or per-request objects |
| No graceful drain | Active tool calls interrupted by shutdown | Track in-flight work; drain before exit |
| Writing to stdout in stdio mode | Protocol stream corruption | Use `sys.stderr` or logging to file |
| Crash on missing env var at import time | No error context in logs | Validate config at startup, not at import |

---

## Key Takeaways

- **Lifecycle = Startup → RUNNING → Shutdown** — each phase has distinct responsibilities.
- Use **FastMCP `lifespan`** for clean resource acquisition and release.
- For Kubernetes: implement all three probes: **liveness**, **readiness**, **startup**.
- **`preStop` hooks** give the pod time to drain connections before termination.
- **Per-session state** must use `contextvars` or be stored in session-specific objects — never in globals.
- Always handle **SIGTERM** for graceful shutdown in containerised deployments.
