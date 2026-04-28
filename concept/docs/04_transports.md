# 04 — MCP Transports

A **transport** is the byte-level channel that carries JSON-RPC messages between client and server.
MCP defines three official transports. The protocol itself is transport-agnostic.

---

## Transport Comparison

| Feature | stdio | SSE (HTTP) | Streamable HTTP |
|---------|-------|------------|-----------------|
| **Direction** | Bidirectional | Server push + POST | Bidirectional streams |
| **Process model** | Local subprocess | Remote HTTP service | Remote HTTP service |
| **Auth** | OS-level (process owner) | HTTP headers / OAuth | HTTP headers / OAuth |
| **Latency** | Lowest (no network) | Network round-trip | Network round-trip |
| **Scalability** | Single host | Horizontally scalable | Horizontally scalable |
| **Best for** | Local tools, IDE plugins | Remote services, cloud | Remote services, streaming |
| **Framing** | Newline-delimited JSON | SSE events + POST | SSE events + POST |

---

## 1. stdio Transport (Standard I/O)

The simplest and most common transport for **local servers**.

### How it works
```
HOST PROCESS                    SERVER PROCESS
    │                                │
    │  spawn subprocess              │
    ├──────────────────────────────► │
    │                                │
    │  write JSON to stdin           │  read from stdin
    ├──────────────────────────────► │
    │                                │
    │  read from stdout              │  write JSON to stdout
    │◄─────────────────────────────── │
    │                                │
    │  server sends logs to stderr   │  write to stderr
    │◄─────────────────────────────── │
```

### Message framing
Each JSON-RPC message is a **single line** terminated by `\n`. No length prefix needed because JSON
parsers can read until the newline.

```
{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\n
{"jsonrpc":"2.0","id":1,"result":{"tools":[...]}}\n
```

### Server-side (Python SDK)
```python
import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.models import InitializationOptions

server = Server("my-server")

# ... register tools/resources/prompts ...

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="my-server",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )

if __name__ == "__main__":
    asyncio.run(main())
```

### Client-side (Python SDK)
```python
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    server_params = StdioServerParameters(
        command="python",
        args=["my_server.py"],
        env=None,                    # inherits host env; or pass explicit dict
    )
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(tools)

asyncio.run(main())
```

### Claude Desktop configuration (stdio)
```jsonc
// ~/Library/Application Support/Claude/claude_desktop_config.json  (macOS)
// %APPDATA%\Claude\claude_desktop_config.json                       (Windows)
{
  "mcpServers": {
    "my-tools": {
      "command": "python",
      "args": ["/path/to/my_server.py"],
      "env": {
        "API_KEY": "sk-..."
      }
    }
  }
}
```

### Security considerations
- Server inherits the host process's UID/GID.
- No authentication needed (trust comes from process ownership).
- Use `env` to pass secrets, not command-line args (visible in `ps`).
- Limit server process capabilities using OS-level sandboxing if needed.

---

## 2. SSE Transport (Server-Sent Events + HTTP POST)

For **remote servers** accessible over HTTP. The server pushes messages via a persistent SSE
connection; the client sends messages via HTTP POST.

### How it works
```
CLIENT                          SERVER (HTTP)
   │                                │
   │  GET /sse  (EventSource)       │
   ├──────────────────────────────► │
   │◄────────────── SSE stream ────── │  (persistent, server pushes here)
   │                                │
   │  POST /message  (request body) │
   ├──────────────────────────────► │
   │◄────────────── 202 Accepted ─── │  (response comes via SSE stream)
   │                                │
   │◄── event: message ─────────── │  (actual JSON-RPC response)
```

### SSE event format
```
event: message
data: {"jsonrpc":"2.0","id":1,"result":{"tools":[...]}}

event: message
data: {"jsonrpc":"2.0","method":"notifications/tools/list_changed","params":{}}
```

### Server-side (Python SDK)
```python
from mcp.server.sse import SseServerTransport
from starlette.applications import Starlette
from starlette.routing import Mount, Route

server = Server("my-remote-server")
# ... register capabilities ...

sse = SseServerTransport("/messages")  # POST endpoint path

async def handle_sse(request):
    async with sse.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], init_options)

async def handle_messages(request):
    await sse.handle_post_message(request.scope, request.receive, request._send)

app = Starlette(
    routes=[
        Route("/sse", endpoint=handle_sse),
        Mount("/messages", app=handle_messages),
    ]
)

# Run with: uvicorn server:app --host 0.0.0.0 --port 8080
```

### Client-side (Python SDK)
```python
from mcp.client.sse import sse_client

async with sse_client("http://localhost:8080/sse") as (read_stream, write_stream):
    async with ClientSession(read_stream, write_stream) as session:
        await session.initialize()
        result = await session.call_tool("search", {"query": "MCP"})
```

### Authentication with SSE
```python
# Pass auth headers to sse_client
async with sse_client(
    "https://api.example.com/mcp/sse",
    headers={"Authorization": "Bearer <token>"}
) as streams:
    ...
```

---

## 3. Streamable HTTP Transport (Recommended for new remote servers)

An evolution of SSE that uses a **single endpoint** for both directions using HTTP streaming.
Responses are streamed back on the same connection as the request.

```
CLIENT                          SERVER (HTTP POST /mcp)
   │                                │
   │  POST /mcp  { request body }   │
   ├──────────────────────────────► │
   │◄────── streaming response ──── │  (response + any notifications streamed)
   │  (Content-Type: text/event-stream or application/json)
```

### Server-side (Python SDK ≥ 1.2)
```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("my-server")

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

# Exposes a Streamable HTTP endpoint via FastMCP
if __name__ == "__main__":
    mcp.run(transport="streamable-http", port=8080)
```

---

## FastMCP — High-Level Transport Abstraction

The Python SDK ships `FastMCP`, a higher-level builder that handles transport wiring automatically:

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo-server")

@mcp.tool()
def calculator(expression: str) -> str:
    """Evaluate a math expression."""
    return str(eval(expression))

@mcp.resource("file://{path}")
def read_file(path: str) -> str:
    with open(path) as f:
        return f.read()

if __name__ == "__main__":
    # stdio (default) — for local tools
    mcp.run()

    # SSE — for remote servers
    # mcp.run(transport="sse", port=8080)

    # Streamable HTTP
    # mcp.run(transport="streamable-http", port=8080)
```

---

## Choosing the Right Transport

```
Is the server local (same machine as host)?
        │
       YES ──► stdio   (simplest, lowest latency, no auth needed)
        │
       NO
        │
        ├── Need to push real-time notifications (resource changes)?
        │        YES ──► SSE or Streamable HTTP
        │        NO  ──► Streamable HTTP (simpler client code)
        │
        └── Building a new server today?
                 ──► Streamable HTTP (recommended going forward)
```

---

## Custom Transport

You can implement any transport by providing two `anyio` streams:

```python
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
import mcp.types as types

# Your custom transport must provide:
#   read_stream:  MemoryObjectReceiveStream[types.JSONRPCMessage | Exception]
#   write_stream: MemoryObjectSendStream[types.JSONRPCMessage]

await server.run(read_stream, write_stream, init_options)
```

This is how the **in-memory transport** works for testing (see `mcp.shared.memory`).

---

## Transport Security Summary

| Transport | Encryption | Authentication |
|-----------|-----------|----------------|
| stdio | N/A (IPC) | OS process permissions |
| SSE | TLS (HTTPS) | HTTP Authorization header, cookies, OAuth |
| Streamable HTTP | TLS (HTTPS) | HTTP Authorization header, OAuth 2.1 |

**Always use HTTPS** for SSE and Streamable HTTP in production.
Never send API keys as query parameters (visible in logs).

---

## Reconnection Patterns

### Client-side reconnection for SSE

```python
import asyncio
from mcp.client.sse import sse_client
from mcp import ClientSession

async def connect_with_retry(
    url: str,
    headers: dict,
    max_attempts: int = 5,
    base_delay: float = 1.0,
) -> None:
    for attempt in range(max_attempts):
        try:
            async with sse_client(url, headers=headers) as (r, w):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    print(f"Connected on attempt {attempt + 1}")
                    await run_session(session)
                    return  # clean exit
        except (ConnectionError, OSError) as e:
            if attempt == max_attempts - 1:
                raise
            delay = base_delay * (2 ** attempt)  # 1s, 2s, 4s, 8s, 16s
            print(f"Connection failed ({e}); retrying in {delay:.1f}s…")
            await asyncio.sleep(delay)
```

### Server-side reconnection handling

SSE servers are inherently stateless between connections. Design for it:

```python
from mcp.server.fastmcp import FastMCP
from contextlib import asynccontextmanager

# Session state lives in the session, not globals
@asynccontextmanager
async def lifespan(app):
    # Shared resources (DB pools) live here — survive reconnects
    import asyncpg
    app.state.db = await asyncpg.create_pool(dsn="postgresql://localhost/mydb")
    yield
    await app.state.db.close()

mcp = FastMCP("reconnect-safe", lifespan=lifespan)

@mcp.tool()
async def get_status() -> str:
    """Get server status. Safe to call after reconnect."""
    return "Server is running"
```

---

## Reverse Proxy Configuration

### Nginx configuration for SSE transport

```nginx
# /etc/nginx/sites-available/mcp-server
server {
    listen 443 ssl;
    server_name mcp.example.com;

    ssl_certificate     /etc/ssl/certs/mcp.crt;
    ssl_certificate_key /etc/ssl/private/mcp.key;

    location /sse {
        proxy_pass         http://localhost:8080/sse;
        proxy_http_version 1.1;

        # Critical for SSE: disable buffering
        proxy_buffering           off;
        proxy_cache               off;
        proxy_read_timeout        3600s;   # Keep SSE connection alive
        proxy_send_timeout        3600s;

        # Forward client IP
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE headers
        add_header Cache-Control no-cache;
        add_header Content-Type  text/event-stream;
    }

    location /messages {
        proxy_pass         http://localhost:8080/messages;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_read_timeout 60s;
    }
}
```

### Traefik labels (Docker Compose)

```yaml
services:
  mcp-server:
    image: my-mcp-server:latest
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.mcp.rule=Host(`mcp.example.com`)"
      - "traefik.http.routers.mcp.tls.certresolver=letsencrypt"
      # Disable buffering for SSE
      - "traefik.http.middlewares.mcp-sse.headers.customResponseHeaders.X-Accel-Buffering=no"
      - "traefik.http.services.mcp.loadbalancer.server.port=8080"
```

---

## Session Multiplexing (SSE)

The SSE transport supports multiple concurrent client sessions on a single server process via session IDs injected in the SSE endpoint URL:

```
Client A: GET /sse?session_id=abc123
Client B: GET /sse?session_id=def456

Client A: POST /messages?session_id=abc123  { tools/call }
Client B: POST /messages?session_id=def456  { resources/read }
```

The `SseServerTransport` handles session routing automatically. Each SSE connection creates a new, isolated session.

```python
from mcp.server.sse import SseServerTransport

sse = SseServerTransport("/messages")

async def handle_sse(request):
    # Each connection = new session = independent MCP lifecycle
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await server.run(streams[0], streams[1], init_options)
        # When this context exits, the session is closed
```

---

## Transport Performance Characteristics

| Metric | stdio | SSE (HTTP) | Streamable HTTP |
|--------|-------|------------|-----------------|
| **Round-trip latency** | ~0.1ms | ~2-50ms (local), 50-200ms (internet) | ~2-50ms |
| **Throughput** | OS pipe buffer (64KB) | HTTP/1.1 or HTTP/2 | HTTP/2 with streaming |
| **Max concurrent sessions** | 1 (per process) | Limited by server threads/coroutines | Same |
| **Reconnect cost** | Re-spawn subprocess | HTTP reconnect + re-initialize | HTTP reconnect + re-initialize |
| **Firewall friendly** | Local only | HTTPS (port 443) | HTTPS (port 443) |
| **Load balanceable** | No | Yes (with sticky sessions) | Yes (with sticky sessions) |

> **Sticky sessions are required for SSE servers** — the SSE stream and POST messages must go to the same server instance. Use IP-hash or session-cookie affinity at the load balancer level.

---

## stdio Process Management

### Robust subprocess launch

```python
import asyncio
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client

# With environment isolation
params = StdioServerParameters(
    command="python",
    args=["-u", "server.py"],         # -u = unbuffered stdout
    env={
        "PATH": "/usr/local/bin:/usr/bin",   # explicit PATH
        "HOME": "/home/user",
        "API_KEY": os.environ["API_KEY"],     # pass only what's needed
    },
)

# Detect subprocess crash
async with stdio_client(params) as (r, w):
    async with ClientSession(r, w) as session:
        try:
            await session.initialize()
        except EOFError:
            print("Server process crashed immediately — check stderr for errors")
```

### Restarting a crashed stdio server

```python
async def managed_stdio_server(params: StdioServerParameters, on_ready):
    while True:
        try:
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    await on_ready(session)
        except (EOFError, ConnectionResetError) as e:
            print(f"Server died ({e}); restarting in 3s…")
            await asyncio.sleep(3)
```

---

## Common Transport Pitfalls

| Pitfall | Transport | Problem | Fix |
|---------|-----------|---------|-----|
| No `proxy_buffering off` | SSE | Nginx buffers SSE — messages arrive in bursts | Set `proxy_buffering off` |
| Short `proxy_read_timeout` | SSE | Nginx closes idle SSE connections | Set timeout ≥ 3600s |
| No sticky sessions | SSE | POST goes to different server than SSE stream | Configure session affinity at LB |
| Using port 8080 without TLS in production | HTTP | Credentials exposed in plaintext | Always terminate TLS at proxy |
| stdout buffering in server | stdio | Messages delayed or not sent | Use `python -u` or `sys.stdout.flush()` |
| Large single JSON message | All | Parser chokes on multi-MB response | Chunk large data; use resources for big content |

---

## Key Takeaways

- **stdio** is best for local tools — zero network overhead, OS-level isolation.
- **SSE** is the current standard for remote servers — HTTP-compatible, push capable.
- **Streamable HTTP** is the recommended transport for new remote servers going forward.
- For SSE behind a proxy, **disable proxy buffering** and set long read timeouts.
- **Sticky session routing** is mandatory for SSE horizontal scaling.
- **Reconnection logic** belongs in the client — design servers to be stateless across connections.
