# 03 — MCP Protocol (JSON-RPC 2.0)

## Foundation: JSON-RPC 2.0

MCP uses **JSON-RPC 2.0** as its wire protocol. All messages are JSON objects sent over a transport.
The spec is at https://www.jsonrpc.org/specification

### Message Types

#### 1. Request (expects a Response)
```jsonc
{
  "jsonrpc": "2.0",
  "id": 1,                        // integer or string; must be unique per in-flight request
  "method": "tools/call",
  "params": {
    "name": "search",
    "arguments": { "query": "MCP protocol" }
  }
}
```

#### 2. Response (success)
```jsonc
{
  "jsonrpc": "2.0",
  "id": 1,                        // matches the request id
  "result": {
    "content": [{ "type": "text", "text": "Found 42 results…" }],
    "isError": false
  }
}
```

#### 3. Response (error)
```jsonc
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32602,               // standard or MCP-specific error code
    "message": "Invalid params",
    "data": { "field": "query", "reason": "required" }
  }
}
```

#### 4. Notification (no response expected)
```jsonc
{
  "jsonrpc": "2.0",
  // NO "id" field
  "method": "notifications/tools/list_changed",
  "params": {}
}
```

---

## Session Lifecycle (Finite State Machine)

```
                ┌──────────┐
                │  CLOSED  │
                └────┬─────┘
                     │ transport connected
                     ▼
                ┌──────────┐
                │CONNECTING│
                └────┬─────┘
                     │ client sends initialize
                     ▼
             ┌───────────────┐
             │ INITIALIZING  │
             └───────┬───────┘
                     │ server responds + client sends initialized
                     ▼
             ┌───────────────┐
             │    RUNNING    │◄──────────────────────┐
             └───────┬───────┘                       │
                     │  (normal operation)            │
                     │  requests / notifications      │
                     │  flow freely in both directions│
                     │                               │
                     │ client or server sends shutdown│
                     ▼                               │
             ┌───────────────┐                       │
             │  SHUTTING     │                       │
             │    DOWN       │  (pending requests    │
             └───────┬───────┘   complete; no new    │
                     │           ones accepted)       │
                     ▼
                ┌──────────┐
                │  CLOSED  │
                └──────────┘
```

---

## Initialization Handshake (Detailed)

### Step 1 — Client sends `initialize`
```jsonc
{
  "jsonrpc": "2.0",
  "id": 0,
  "method": "initialize",
  "params": {
    "protocolVersion": "2024-11-05",
    "clientInfo": {
      "name": "my-host",
      "version": "1.0.0"
    },
    "capabilities": {
      "roots":    { "listChanged": true },
      "sampling": {}
    }
  }
}
```

### Step 2 — Server responds
```jsonc
{
  "jsonrpc": "2.0",
  "id": 0,
  "result": {
    "protocolVersion": "2024-11-05",
    "serverInfo": {
      "name": "my-tools-server",
      "version": "2.1.0"
    },
    "capabilities": {
      "tools":     { "listChanged": false },
      "resources": { "subscribe": true, "listChanged": true },
      "logging":   {}
    }
  }
}
```

### Step 3 — Client sends `initialized` notification
```jsonc
{
  "jsonrpc": "2.0",
  "method": "notifications/initialized",
  "params": {}
}
```

After step 3, the session is **RUNNING** and both sides may send arbitrary requests.

---

## All MCP Methods

### Server-side methods (client calls these)

| Method | Description |
|--------|-------------|
| `initialize` | Start session, negotiate capabilities |
| `ping` | Keep-alive check |
| `tools/list` | Get all tools (paginated) |
| `tools/call` | Invoke a tool |
| `resources/list` | Get all resources (paginated) |
| `resources/read` | Read a resource by URI |
| `resources/subscribe` | Subscribe to resource changes |
| `resources/unsubscribe` | Unsubscribe from resource changes |
| `prompts/list` | Get all prompt templates (paginated) |
| `prompts/get` | Expand a prompt template |
| `completion/complete` | Get argument completion suggestions |
| `logging/setLevel` | Set server log level |

### Client-side methods (server calls these)

| Method | Description |
|--------|-------------|
| `sampling/createMessage` | Request LLM completion from client |
| `roots/list` | Get allowed filesystem roots |

### Notifications: Client → Server

| Notification | Description |
|--------------|-------------|
| `notifications/initialized` | Session init complete |
| `notifications/roots/list_changed` | Client roots have changed |
| `notifications/cancelled` | Cancel a pending request |

### Notifications: Server → Client

| Notification | Description |
|--------------|-------------|
| `notifications/tools/list_changed` | Tool list has changed |
| `notifications/resources/list_changed` | Resource list has changed |
| `notifications/resources/updated` | A specific resource has changed |
| `notifications/prompts/list_changed` | Prompt list has changed |
| `notifications/message` | Log message from server |
| `notifications/progress` | Progress update for long operation |

---

## Pagination

List endpoints return paginated results using cursor-based pagination.

```jsonc
// Request
{
  "method": "tools/list",
  "params": { "cursor": "eyJwYWdlIjogMn0=" }  // optional; omit for first page
}

// Response
{
  "result": {
    "tools": [ /* ...up to N items */ ],
    "nextCursor": "eyJwYWdlIjogM30="  // omitted if last page
  }
}
```

The cursor is opaque to the client — pass it back as-is to get the next page.

---

## Progress Notifications

For long-running tools, the server may emit progress notifications before the final result.

```jsonc
// Server → Client notification (during a tools/call with id=42)
{
  "jsonrpc": "2.0",
  "method": "notifications/progress",
  "params": {
    "progressToken": 42,      // matches request id or a client-provided token
    "progress": 0.65,         // 0.0 – 1.0
    "total": 100              // optional denominator
  }
}
```

Clients that want progress on a request pass `_meta.progressToken` in params:
```jsonc
{
  "method": "tools/call",
  "params": {
    "name": "long_task",
    "arguments": {},
    "_meta": { "progressToken": 42 }
  }
}
```

---

## Cancellation

Either side can cancel a pending request:
```jsonc
{
  "jsonrpc": "2.0",
  "method": "notifications/cancelled",
  "params": {
    "requestId": 42,
    "reason": "User cancelled"
  }
}
```

The receiving side SHOULD abort the in-flight work but MAY still send a response.

---

## Error Codes

### Standard JSON-RPC codes

| Code | Name | Meaning |
|------|------|---------|
| -32700 | Parse error | Invalid JSON |
| -32600 | Invalid request | Not a valid JSON-RPC object |
| -32601 | Method not found | Unknown method |
| -32602 | Invalid params | Wrong parameter types/values |
| -32603 | Internal error | Server-side exception |

### MCP-specific codes

| Code | Name | Meaning |
|------|------|---------|
| -32001 | Request cancelled | Request was cancelled by client |
| -32002 | Content too large | Response exceeds size limit |

### Tool execution errors

Note: a tool that *runs successfully but returns an error result* uses `isError: true` in the
result body — **not** a JSON-RPC error. JSON-RPC errors are for protocol-level failures.

```jsonc
// Tool ran, but the operation failed (correct pattern)
{
  "result": {
    "content": [{ "type": "text", "text": "File not found: /foo/bar.txt" }],
    "isError": true
  }
}

// Protocol error (wrong pattern for tool logic errors)
{
  "error": { "code": -32603, "message": "File not found" }  // ← don't use for logic errors
}
```

---

## Ping / Keep-Alive

```jsonc
// Client → Server
{ "jsonrpc": "2.0", "id": 99, "method": "ping" }

// Server → Client
{ "jsonrpc": "2.0", "id": 99, "result": {} }
```

Use ping to detect stale connections before making a tool call.

---

## Protocol Version Negotiation

- Client sends its supported `protocolVersion` in `initialize`.
- Server responds with the version it will use (may downgrade to an older version).
- If server cannot support the client's minimum version, it returns an error.
- Current stable version: **`"2024-11-05"`**

```python
# Python SDK — version is handled automatically
from mcp.server.models import InitializationOptions

options = InitializationOptions(
    server_name="my-server",
    server_version="1.0.0",
    capabilities=server.get_capabilities(...)
)
```
