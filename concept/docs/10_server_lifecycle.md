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
