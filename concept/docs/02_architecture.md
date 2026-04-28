# 02 — MCP Architecture

## The Three-Role Model

MCP defines exactly three roles. Every participant is exactly one of them.

```
┌─────────────────────────────────────────────────────────────────────┐
│                            HOST                                     │
│  (Claude Desktop / VS Code / custom agent / Jupyter notebook)       │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │                    MCP CLIENT LAYER                           │  │
│  │  ┌─────────────┐   ┌─────────────┐   ┌─────────────────────┐ │  │
│  │  │  Client 1   │   │  Client 2   │   │      Client N       │ │  │
│  │  │  (1:1 with  │   │  (1:1 with  │   │    (1:1 with        │ │  │
│  │  │  Server A)  │   │  Server B)  │   │    Server N)        │ │  │
│  │  └──────┬──────┘   └──────┬──────┘   └──────────┬──────────┘ │  │
│  └─────────────────────────────────────────────────────────────────┘  │
└──────────┬────────────────────┬─────────────────────┬───────────────┘
           │ Transport A        │ Transport B          │ Transport N
    ┌──────▼──────┐      ┌──────▼──────┐      ┌───────▼──────┐
    │  SERVER A   │      │  SERVER B   │      │   SERVER N   │
    │             │      │             │      │              │
    │  Tools      │      │  Resources  │      │  Prompts     │
    │  Resources  │      │  Tools      │      │  Sampling    │
    └─────────────┘      └─────────────┘      └──────────────┘
```

### Host
- The **user-facing application**; owns the LLM and the context window.
- Decides *which* servers to connect to (policy decisions live here).
- Aggregates results from multiple clients and injects them into LLM context.
- Handles human-in-the-loop approval for sensitive tool calls.
- Examples: Claude Desktop, Continue.dev, a custom Python agent, an IDE extension.

### Client
- Lives **inside** the host; one client per server connection.
- Speaks the MCP wire protocol over a transport.
- Maintains the session state (initialized, capabilities, pending requests).
- Translates host API calls (e.g., `call_tool("search", {...})`) into JSON-RPC messages.
- A single host can manage **N clients simultaneously**.

### Server
- An **independent process** (or in-process module) that exposes capabilities.
- Intentionally small and focused: *one server, one concern*.
- Has no knowledge of the host or other servers.
- Stateless between requests (recommended) or stateful (session-scoped).

---

## Capability Negotiation

During `initialize`, both sides advertise what they support. Neither side can use a feature the
other hasn't declared.

```
Client → Server:  initialize  {clientInfo, protocolVersion, capabilities}
Server → Client:  initialize result {serverInfo, protocolVersion, capabilities}
Client → Server:  notifications/initialized
```

### Client Capabilities

```jsonc
{
  "capabilities": {
    "roots": { "listChanged": true },     // can receive roots/list_changed notifications
    "sampling": {}                         // can handle sampling/createMessage requests
  }
}
```

### Server Capabilities

```jsonc
{
  "capabilities": {
    "tools":     { "listChanged": true },  // exposes tools; list may change at runtime
    "resources": {
      "subscribe": true,                   // supports resource subscriptions
      "listChanged": true
    },
    "prompts":   { "listChanged": true },  // exposes prompt templates
    "logging":   {}                        // can send log messages to client
  }
}
```

If a server does not include `"tools"` in capabilities, the client MUST NOT call `tools/list` or
`tools/call`. Violations return a JSON-RPC error.

---

## Request / Response Flow

```
HOST                    CLIENT                  SERVER
 │                        │                       │
 │ call_tool("search")    │                       │
 ├──────────────────────► │                       │
 │                        │  tools/call (JSON-RPC)│
 │                        ├──────────────────────►│
 │                        │                       │ (executes)
 │                        │◄──────────────────────┤
 │                        │  result / error       │
 │◄───────────────────────┤                       │
 │ ToolResult             │                       │
```

All messages are **JSON-RPC 2.0**:
- **Requests** have `id`, `method`, `params` — expect a response.
- **Notifications** have `method`, `params`, no `id` — fire and forget.
- **Responses** have `id`, `result` or `error`.

---

## Concurrency Model

- MCP is **fully async**. Multiple requests can be in-flight simultaneously.
- Responses may arrive **out of order** (matched by `id`).
- Servers should handle concurrent `tools/call` requests safely.
- Clients queue notifications and process them in order.

```
Client → Server:  tools/call  id=1  (long running)
Client → Server:  tools/call  id=2  (fast)
Server → Client:  result      id=2  (arrives first — totally valid)
Server → Client:  result      id=1
```

---

## Stateful vs. Stateless Servers

### Stateless (Recommended)
- Each `tools/call` is independent.
- Server holds no per-session data.
- Easy to scale horizontally.
- Examples: calculator, weather API, code linter.

### Stateful
- Server maintains per-session context (e.g., an open DB transaction, a browser session).
- Session is identified by the transport connection.
- Lifecycle is tied to `initialize` → `shutdown`.
- Examples: a browser automation server, an interactive REPL, a shopping cart.

---

## Multi-Server Composition

A host connects to multiple servers simultaneously. The host's client manager:
1. Aggregates `tools/list` from all servers → presents unified tool list to LLM.
2. Routes `tools/call` to the correct server based on tool name prefix/namespace.
3. Merges `resources/list` from all servers into one browsable tree.
4. Lets the LLM use tools from server A whose output feeds into server B.

```python
# Pseudo-code in a host
tools = []
for client in clients:
    tools += await client.list_tools()

# Namespace collision avoidance
tools = [Tool(name=f"{client.server_name}__{t.name}", ...) for t in tools]
```

---

## In-Process vs. Out-of-Process Servers

### Out-of-Process (stdio / SSE)
- Server runs as a separate OS process.
- Strong isolation: server crash doesn't crash the host.
- Language-agnostic: server can be Python, TypeScript, Go, Rust…
- Communication via stdin/stdout or HTTP.

### In-Process
- Server lives inside the host Python process.
- Zero transport overhead.
- Used in testing and in embedded scenarios.
- Python SDK supports in-memory transports for this.

```python
# In-process server (testing / embedding)
from mcp import ClientSession, StandaloneServer
from mcp.shared.memory import create_connected_server_and_client_session

server = build_my_server()
async with create_connected_server_and_client_session(server) as client:
    tools = await client.list_tools()
```

---

## Security Boundaries

```
┌─────────────────────────────────────────────────────┐
│  HOST (trusted)                                     │
│  - Controls which servers are connected             │
│  - Controls what context is shown to LLM            │
│  - Human-in-the-loop approval lives here            │
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │  CLIENT (trusted — same process as host)     │   │
│  │  - Validates all server responses            │   │
│  │  - Enforces capability boundaries            │   │
│  └──────────────────────────────────────────────┘   │
└──────────────────┬──────────────────────────────────┘
                   │  ← trust boundary →
            ┌──────▼──────┐
            │  SERVER      │
            │ (untrusted)  │
            │ Can lie about│
            │ tool outputs │
            └─────────────┘
```

Servers are treated as **untrusted** by default. The host/client must:
- Validate JSON schema of inputs before calling
- Sanitise tool outputs before injecting into LLM context
- Never grant servers access to LLM system prompts
- Require explicit user consent before connecting a new server

---

## Python SDK Architecture Overview

```
mcp/
├── server/
│   ├── __init__.py        ← Server class, @server.list_tools() decorators
│   ├── fastmcp/           ← High-level FastMCP builder (recommended)
│   │   ├── __init__.py    ← FastMCP class, Context
│   │   ├── tools.py       ← @mcp.tool() decorator + auto-schema generation
│   │   ├── resources.py   ← @mcp.resource() decorator + URI template matching
│   │   └── prompts.py     ← @mcp.prompt() decorator
│   ├── stdio.py           ← stdio_server() context manager
│   ├── sse.py             ← SseServerTransport
│   └── models.py          ← InitializationOptions
├── client/
│   ├── __init__.py        ← ClientSession
│   ├── stdio.py           ← stdio_client() context manager
│   └── sse.py             ← SseClientTransport
├── types.py               ← All protocol types (Tool, Resource, Prompt, …)
└── shared/
    ├── memory.py          ← In-memory transport for testing
    └── session.py         ← BaseSession (shared client/server base)
```

---

## Deployment Topologies

### Topology 1 — Local Tools (Most Common)

```
┌──────────────────────────────────────────┐
│  User's Machine                          │
│                                          │
│  ┌────────────────────┐                  │
│  │  Claude Desktop    │                  │
│  │  (Host + Clients)  │                  │
│  └──────┬─────────────┘                  │
│         │ stdio (subprocess)              │
│  ┌──────▼──────┐  ┌──────────────┐       │
│  │ FS Server   │  │  Git Server  │       │
│  │ (Python)    │  │  (Node.js)   │       │
│  └─────────────┘  └──────────────┘       │
└──────────────────────────────────────────┘
```

Best for: IDE plugins, local file access, personal tools.

### Topology 2 — Remote Services (Team/Cloud)

```
┌─────────────────────────┐         ┌──────────────────────────┐
│  Developer Machine      │         │  Cloud / Internal         │
│                         │         │                           │
│  ┌───────────────────┐  │  HTTPS  │  ┌────────────────────┐  │
│  │  Claude / IDE     │  │ ──────► │  │  GitHub MCP Server │  │
│  │  (Host + Client)  │  │         │  │  (FastAPI + SSE)   │  │
│  └───────────────────┘  │         │  └────────────────────┘  │
│                         │         │  ┌────────────────────┐  │
│                         │  HTTPS  │  │  DB MCP Server     │  │
│                         │ ──────► │  │  (FastAPI + SSE)   │  │
└─────────────────────────┘         └──────────────────────────┘
```

Best for: shared company tools, SaaS integrations, team codebases.

### Topology 3 — Hybrid (Local + Remote)

```
┌───────────────────────────────────────────────────────────────┐
│  Host                                                         │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  Client Manager                                         │  │
│  │  ┌────────┐  ┌────────┐  ┌────────┐  ┌────────────┐    │  │
│  │  │Client 1│  │Client 2│  │Client 3│  │  Client 4  │    │  │
│  │  └───┬────┘  └───┬────┘  └───┬────┘  └─────┬──────┘    │  │
│  └──────────────────────────────────────────────────────────┘  │
└──────┬──────────────┬───────────┬──────────────┬───────────────┘
       │ stdio        │ stdio     │ HTTPS         │ HTTPS
  ┌────▼────┐   ┌─────▼────┐  ┌──▼──────────┐ ┌──▼──────────┐
  │FS Server│   │Git Server│  │ GitHub API  │ │ Slack API   │
  │(local)  │   │(local)   │  │ (remote)    │ │ (remote)    │
  └─────────┘   └──────────┘  └─────────────┘ └─────────────┘
```

Best for: power users with both local and cloud tools.

### Topology 4 — Embedded / In-Process

```
┌────────────────────────────────────────────────────────┐
│  Single Python Process                                  │
│                                                        │
│  ┌──────────────────────────┐                          │
│  │  Host + LLM Logic        │                          │
│  └──────────────────────────┘                          │
│         ↕ in-memory transport (no subprocess)          │
│  ┌──────────────────────────┐                          │
│  │  MCP Server (in-process) │                          │
│  │  (testing or embedding)  │                          │
│  └──────────────────────────┘                          │
└────────────────────────────────────────────────────────┘
```

Best for: unit tests, notebooks, tightly coupled services.

---

## Server-Initiated Flow (Sampling)

The most complex message flow — server initiates an LLM call back through the client:

```
HOST                  CLIENT                SERVER                LLM API
  │                     │                     │                     │
  │ call_tool("agent")  │                     │                     │
  ├────────────────────►│                     │                     │
  │                     │ tools/call id=1     │                     │
  │                     ├────────────────────►│                     │
  │                     │                     │ (thinks, needs LLM) │
  │                     │ sampling/createMsg  │                     │
  │                     │◄────────────────────┤                     │
  │  [Human approval UI]│                     │                     │
  │◄────────────────────┤                     │                     │
  │ [User approves]     │                     │                     │
  ├────────────────────►│                     │                     │
  │                     │─────────────────────────────────────────►│
  │                     │◄────────── LLM response ─────────────────┤
  │                     │ sampling result     │                     │
  │                     ├────────────────────►│                     │
  │                     │                     │ (processes result)  │
  │                     │ tools/call result   │                     │
  │                     │◄────────────────────┤                     │
  │ ToolResult          │                     │                     │
  │◄────────────────────┤                     │                     │
```

Key insight: a single `tools/call` can trigger multiple `sampling/createMessage` round-trips internally.

---

## Client Manager — Tool Namespace Design

When a host connects to multiple servers, tool names must be unique. The standard approach:

```python
# Namespace strategy options:
# 1. Server-prefix (most common)
"github__create_pr"      # server_name + __ + tool_name
"filesystem__read_file"

# 2. Domain-prefix
"vcs.create_pr"
"fs.read_file"

# 3. Tool registry with alias resolution
# Store: {"create_pr": "github", "read_file": "filesystem"}
# If collision: raise error or use server prefix

# In practice (pseudo-code):
class ToolNamespace:
    def mangle(self, server: str, tool: str) -> str:
        return f"{server}__{tool}"

    def demangle(self, namespaced: str) -> tuple[str, str]:
        server, _, tool = namespaced.partition("__")
        return server, tool
```

---

## Common Architectural Pitfalls

| Pitfall | Symptom | Fix |
|---------|---------|-----|
| **Stdout pollution** | Protocol corruption in stdio mode | Never `print()` to stdout in servers; use `stderr` |
| **Blocking tool handlers** | Client timeouts on long operations | Use `async def` for all tool handlers; never `time.sleep()` |
| **Missing capability check** | `McpError -32601` on first call | Always check `caps.tools` before calling `tools/list` |
| **Shared mutable state** | Race conditions in concurrent tool calls | Use `asyncio.Lock` or `contextvars` for per-request state |
| **Hardcoded server names** | Name collisions in multi-server host | Use namespacing (`server__toolname`) |
| **No roots enforcement** | Path traversal vulnerability | Always call `roots/list` and validate against results |
| **Session state in global vars** | Multi-session servers share state | Use `server.request_context` for per-session data |
| **Not handling `initialized` notification** | Server acts before handshake completes | Wait for `notifications/initialized` before doing work |

---

## Key Takeaways

- **One client = one server connection.** A host manages N clients for N servers simultaneously.
- **Servers are untrusted by default.** All trust decisions live in the host.
- **Choose topology by latency and isolation needs**: stdio for local, SSE/HTTP for remote, in-process for testing.
- **The sampling flow is bidirectional** — servers can ask the host to call the LLM on their behalf.
- **Namespace your tools** when managing multiple servers to prevent name collisions.
