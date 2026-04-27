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
