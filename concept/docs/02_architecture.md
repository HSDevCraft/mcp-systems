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
│   ├── __init__.py      ← Server class, @server.list_tools() decorators
│   ├── stdio.py         ← stdio_server() context manager
│   ├── sse.py           ← SseServerTransport
│   └── models.py        ← InitializationOptions
├── client/
│   ├── __init__.py      ← ClientSession
│   ├── stdio.py         ← stdio_client() context manager
│   └── sse.py           ← SseClientTransport
├── types.py             ← All protocol types (Tool, Resource, Prompt, …)
└── shared/
    ├── memory.py        ← In-memory transport for testing
    └── session.py       ← BaseSession (shared client/server base)
```
