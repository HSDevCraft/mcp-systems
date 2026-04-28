# 11 — Client Lifecycle

The **client** lives inside the host and manages one server connection. This document covers
everything a client (or host developer) needs to implement: connecting, initializing, using
capabilities, handling notifications, and disconnecting cleanly.

---

## Client Lifecycle Phases

```
CONNECT       INITIALIZE        DISCOVER          USE               DISCONNECT
──────────    ────────────      ──────────────    ────────────────  ──────────
Open          Send initialize   list_tools        call_tool         Send shutdown
transport     Wait for result   list_resources    read_resource     Close transport
              Send initialized  list_prompts      get_prompt
                                                  create_message
```

---

## Connecting and Initializing — Python SDK

### stdio client
```python
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def run():
    params = StdioServerParameters(
        command="python",
        args=["my_server.py"],
        env={"API_KEY": "sk-..."},
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            # Initialize negotiates capabilities
            result = await session.initialize()
            print(f"Connected to: {result.serverInfo.name} {result.serverInfo.version}")
            print(f"Capabilities: {result.capabilities}")

            # Now use the session
            await use_session(session)

asyncio.run(run())
```

### SSE client
```python
from mcp.client.sse import sse_client

async def run():
    async with sse_client(
        "https://myserver.example.com/mcp/sse",
        headers={"Authorization": "Bearer <token>"},
    ) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            await use_session(session)
```

---

## Discovering Capabilities

After `initialize`, always check what the server actually supports before calling:

```python
async def use_session(session: ClientSession) -> None:
    result = await session.initialize()
    caps = result.capabilities

    if caps.tools:
        tools = await session.list_tools()
        print(f"Tools: {[t.name for t in tools.tools]}")

    if caps.resources:
        resources = await session.list_resources()
        print(f"Resources: {[r.uri for r in resources.resources]}")

    if caps.prompts:
        prompts = await session.list_prompts()
        print(f"Prompts: {[p.name for p in prompts.prompts]}")
```

---

## Complete Capability Usage

### Calling a Tool
```python
from mcp.types import CallToolResult

result: CallToolResult = await session.call_tool(
    name="web_search",
    arguments={"query": "MCP protocol", "max_results": 3},
)

if result.isError:
    print(f"Tool error: {result.content[0].text}")
else:
    for block in result.content:
        if block.type == "text":
            print(block.text)
        elif block.type == "image":
            print(f"[Image: {block.mimeType}, {len(block.data)} bytes]")
```

### Reading a Resource
```python
from mcp.types import ReadResourceResult

result: ReadResourceResult = await session.read_resource("file:///workspace/README.md")
for content in result.contents:
    if hasattr(content, "text"):
        print(content.text)
    elif hasattr(content, "blob"):
        print(f"[Binary: {len(content.blob)} bytes]")
```

### Getting a Prompt
```python
from mcp.types import GetPromptResult

result: GetPromptResult = await session.get_prompt(
    name="code_review",
    arguments={"code": "def add(a, b): return a+b", "language": "python"},
)

print(f"Description: {result.description}")
for msg in result.messages:
    print(f"[{msg.role}]: {msg.content.text}")
```

### Paginating Lists
```python
async def list_all_tools(session: ClientSession):
    all_tools = []
    cursor = None
    while True:
        result = await session.list_tools(cursor=cursor)
        all_tools.extend(result.tools)
        if not result.nextCursor:
            break
        cursor = result.nextCursor
    return all_tools
```

---

## Handling Notifications

The client receives server-sent notifications asynchronously. Register handlers before `initialize`:

```python
from mcp import ClientSession
from mcp.types import (
    ToolListChangedNotification,
    ResourceListChangedNotification,
    ResourceUpdatedNotification,
    LoggingMessageNotification,
    ProgressNotification,
)

class SmartClient:
    def __init__(self):
        self.tools_cache: list = []

    async def connect(self, read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            # Register notification handlers BEFORE initialize
            session.on_notification = self.handle_notification

            await session.initialize()
            self.tools_cache = (await session.list_tools()).tools
            # ... use session ...

    async def handle_notification(self, notification) -> None:
        match notification:
            case ToolListChangedNotification():
                # Refresh tools cache
                self.tools_cache = (await self.session.list_tools()).tools
                print("Tool list refreshed")

            case ResourceUpdatedNotification(params=p):
                print(f"Resource changed: {p.uri}")
                # Re-read the resource and update context

            case ResourceListChangedNotification():
                print("Resource list changed")

            case LoggingMessageNotification(params=p):
                print(f"[{p.level.upper()}] {p.data}")

            case ProgressNotification(params=p):
                pct = f"{p.progress*100:.0f}%" if p.progress is not None else "?"
                print(f"Progress [{p.progressToken}]: {pct}")
```

---

## Implementing Sampling (Client Side)

If your client declares `sampling` capability, you must handle `sampling/createMessage` requests:

```python
from mcp.types import CreateMessageRequest, CreateMessageResult, SamplingMessage

class SamplingEnabledClient:
    def __init__(self, llm_client):
        self.llm = llm_client  # e.g. Anthropic client

    async def handle_sampling_request(
        self, request: CreateMessageRequest
    ) -> CreateMessageResult:
        # 1. Human-in-the-loop approval (optional but recommended)
        approved = await self.request_human_approval(request)
        if not approved:
            raise PermissionError("User denied sampling request")

        # 2. Make the actual LLM call
        messages = [
            {"role": m.role, "content": m.content.text}
            for m in request.params.messages
        ]

        response = await self.llm.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=request.params.maxTokens,
            system=request.params.systemPrompt or "",
            messages=messages,
            temperature=request.params.temperature or 1.0,
        )

        return CreateMessageResult(
            role="assistant",
            content=types.TextContent(type="text", text=response.content[0].text),
            model=response.model,
            stopReason=response.stop_reason,
        )
```

---

## Implementing Roots (Client Side)

If the host wants to grant file access to servers:

```python
from mcp.types import ListRootsResult, Root

class RootsEnabledClient:
    def __init__(self, workspace_paths: list[str]):
        self.roots = [
            Root(uri=f"file://{p}", name=p.split("/")[-1])
            for p in workspace_paths
        ]

    async def handle_list_roots(self) -> ListRootsResult:
        return ListRootsResult(roots=self.roots)

    async def add_root(self, path: str, name: str, session: ClientSession) -> None:
        self.roots.append(Root(uri=f"file://{path}", name=name))
        await session.send_roots_list_changed()

    async def remove_root(self, path: str, session: ClientSession) -> None:
        self.roots = [r for r in self.roots if r.uri != f"file://{path}"]
        await session.send_roots_list_changed()
```

---

## Multi-Server Client Manager

Managing multiple server connections in a host:

```python
import asyncio
from dataclasses import dataclass, field
from mcp import ClientSession
from mcp.client.stdio import stdio_client
from mcp import StdioServerParameters

@dataclass
class ServerConfig:
    name:    str
    command: str
    args:    list[str] = field(default_factory=list)
    env:     dict      = field(default_factory=dict)

class MCPClientManager:
    def __init__(self):
        self.sessions: dict[str, ClientSession] = {}
        self.tools: dict[str, str] = {}        # tool_name → server_name
        self.resources: dict[str, str] = {}    # uri → server_name

    async def connect(self, config: ServerConfig) -> None:
        params = StdioServerParameters(
            command=config.command,
            args=config.args,
            env=config.env or None,
        )
        # Note: In a real implementation you'd manage the context managers properly
        read_stream, write_stream = await self._open_transport(params)
        session = ClientSession(read_stream, write_stream)
        await session.__aenter__()
        await session.initialize()
        self.sessions[config.name] = session
        await self._index_capabilities(config.name, session)

    async def _index_capabilities(self, server_name: str, session: ClientSession) -> None:
        caps = (await session.initialize()).capabilities
        if caps.tools:
            tools = await session.list_tools()
            for t in tools.tools:
                self.tools[f"{server_name}__{t.name}"] = server_name
        if caps.resources:
            resources = await session.list_resources()
            for r in resources.resources:
                self.resources[r.uri] = server_name

    async def call_tool(self, namespaced_name: str, arguments: dict):
        server_name = self.tools.get(namespaced_name)
        if not server_name:
            raise KeyError(f"Tool not found: {namespaced_name}")
        _, tool_name = namespaced_name.split("__", 1)
        return await self.sessions[server_name].call_tool(tool_name, arguments)

    async def read_resource(self, uri: str):
        server_name = self.resources.get(uri)
        if not server_name:
            raise KeyError(f"Resource not found: {uri}")
        return await self.sessions[server_name].read_resource(uri)

    async def get_all_tools(self) -> list:
        all_tools = []
        for server_name, session in self.sessions.items():
            caps = session._initialized_result.capabilities
            if caps and caps.tools:
                result = await session.list_tools()
                for t in result.tools:
                    # Namespace the tool name to avoid collisions
                    all_tools.append({
                        "name":        f"{server_name}__{t.name}",
                        "description": t.description,
                        "inputSchema": t.inputSchema,
                    })
        return all_tools

    async def disconnect_all(self) -> None:
        for session in self.sessions.values():
            await session.__aexit__(None, None, None)
        self.sessions.clear()
```

---

## Error Handling in Clients

```python
from mcp.types import McpError
import mcp.types as types

async def safe_call_tool(session: ClientSession, name: str, args: dict) -> str:
    try:
        result = await session.call_tool(name, args)
        if result.isError:
            return f"Tool error: {result.content[0].text}"
        return "\n".join(
            c.text for c in result.content if hasattr(c, "text")
        )
    except McpError as e:
        return f"MCP error {e.error.code}: {e.error.message}"
    except asyncio.TimeoutError:
        return "Tool call timed out"
    except Exception as e:
        return f"Unexpected error: {e}"
```

---

## Timeouts and Retries

```python
import asyncio
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
async def resilient_call_tool(session: ClientSession, name: str, args: dict):
    return await asyncio.wait_for(
        session.call_tool(name, args),
        timeout=30.0,
    )
```

---

## Connection Health Monitoring

```python
import asyncio

async def keep_alive(session: ClientSession, interval: float = 30.0) -> None:
    """Periodically ping the server to detect stale connections."""
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.wait_for(session.send_ping(), timeout=5.0)
        except (asyncio.TimeoutError, Exception):
            # Connection is dead; trigger reconnect logic
            raise ConnectionError("Server ping failed")
```

---

## Auto-Reconnect Client

A production-grade client that automatically reconnects after network failures:

```python
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client

class ReconnectingClient:
    """
    Wraps ClientSession with automatic reconnect on failure.
    Suitable for long-running agents that must survive network blips.
    """

    def __init__(
        self,
        server_params: StdioServerParameters | str,  # StdioServerParameters or SSE URL
        max_reconnects: int = -1,                     # -1 = infinite
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        on_reconnect=None,                            # async callback(session)
    ):
        self.params = server_params
        self.max_reconnects = max_reconnects
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.on_reconnect = on_reconnect
        self._session: ClientSession | None = None
        self._reconnect_count = 0

    async def _connect_once(self):
        if isinstance(self.params, str):
            cm = sse_client(self.params)
        else:
            cm = stdio_client(self.params)

        async with cm as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                self._session = session
                if self.on_reconnect:
                    await self.on_reconnect(session)
                # Keep alive with periodic pings
                while True:
                    await asyncio.sleep(30)
                    try:
                        await asyncio.wait_for(session.send_ping(), timeout=5.0)
                    except (asyncio.TimeoutError, Exception):
                        break  # Connection dead; trigger reconnect

    async def run(self):
        attempt = 0
        while self.max_reconnects < 0 or attempt <= self.max_reconnects:
            try:
                await self._connect_once()
                attempt = 0  # reset on clean exit
            except (ConnectionError, EOFError, OSError) as e:
                self._reconnect_count += 1
                delay = min(self.base_delay * (2 ** attempt), self.max_delay)
                print(f"Connection lost ({e}); reconnecting in {delay:.1f}s (attempt {attempt})")
                await asyncio.sleep(delay)
                attempt += 1

    @property
    def session(self) -> ClientSession:
        if not self._session:
            raise RuntimeError("Not connected")
        return self._session
```

---

## Context Window Management

Intelligently manage what gets injected into the LLM context from MCP resources and tool results:

```python
from typing import TypedDict

class ContextMessage(TypedDict):
    role: str
    content: str

class ContextWindowManager:
    """
    Manages LLM context window usage when combining MCP results with conversation history.
    """

    def __init__(self, max_tokens: int = 100_000, reserved_for_output: int = 4_000):
        self.max_tokens = max_tokens
        self.reserved   = reserved_for_output
        self._available  = max_tokens - reserved_for_output

    def estimate_tokens(self, text: str) -> int:
        # Rough estimate: ~4 characters per token (adjust per model)
        return len(text) // 4

    def fit_tool_result(self, result: str, max_chars: int = 20_000) -> str:
        """Truncate tool results to fit context budget."""
        if len(result) <= max_chars:
            return result
        kept = result[:max_chars]
        return f"{kept}\n\n[... truncated {len(result) - max_chars} chars ...]"

    def build_context(
        self,
        system_prompt: str,
        conversation: list[ContextMessage],
        tool_results: list[tuple[str, str]],  # (tool_name, result_text)
    ) -> tuple[str, list[ContextMessage]]:
        """
        Build the context to send to the LLM, fitting within token budget.
        Returns (system_prompt, trimmed_messages).
        """
        used = self.estimate_tokens(system_prompt)

        # Add tool results as context (newest first)
        enriched_conversation = list(conversation)
        for tool_name, result in reversed(tool_results):
            snippet = self.fit_tool_result(result)
            msg: ContextMessage = {
                "role": "user",
                "content": f"[Tool result: {tool_name}]\n{snippet}",
            }
            msg_tokens = self.estimate_tokens(msg["content"])
            if used + msg_tokens < self._available:
                enriched_conversation.insert(0, msg)
                used += msg_tokens

        # Trim oldest conversation messages if over budget
        while enriched_conversation and used > self._available:
            oldest = enriched_conversation.pop(0)
            used -= self.estimate_tokens(oldest["content"])

        return system_prompt, enriched_conversation
```

---

## LLM Integration Pattern

End-to-end pattern showing how a host integrates MCP tool results into LLM calls:

```python
import asyncio, json
from mcp import ClientSession
from anthropic import Anthropic

class MCPLLMHost:
    """
    A complete host that:
    1. Lists tools from MCP servers
    2. Injects them into Claude's tool-use API
    3. Executes tool calls via MCP
    4. Returns the final answer
    """

    def __init__(self, session: ClientSession, anthropic_client: Anthropic):
        self.session = session
        self.llm     = anthropic_client

    async def get_tools_for_claude(self) -> list[dict]:
        """Convert MCP tools to Anthropic tool schema."""
        result = await self.session.list_tools()
        return [
            {
                "name":         t.name,
                "description":  t.description or "",
                "input_schema": t.inputSchema,
            }
            for t in result.tools
        ]

    async def run(self, user_message: str) -> str:
        """Run an agentic loop until Claude returns a final text response."""
        tools       = await self.get_tools_for_claude()
        messages    = [{"role": "user", "content": user_message}]

        while True:
            response = self.llm.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=4096,
                tools=tools,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                # Final text response
                return next(
                    (block.text for block in response.content if hasattr(block, "text")),
                    ""
                )

            if response.stop_reason == "tool_use":
                # Execute all tool calls concurrently
                tool_uses = [b for b in response.content if b.type == "tool_use"]
                tool_results = await asyncio.gather(*[
                    self._execute_tool(tu.name, tu.input, tu.id)
                    for tu in tool_uses
                ])

                messages.append({
                    "role":    "user",
                    "content": tool_results,
                })
            else:
                break

        return "No final answer produced."

    async def _execute_tool(self, name: str, args: dict, tool_use_id: str) -> dict:
        try:
            result = await asyncio.wait_for(
                self.session.call_tool(name, args),
                timeout=30.0,
            )
            content = "\n".join(
                c.text for c in result.content if hasattr(c, "text")
            )
            return {
                "type":        "tool_result",
                "tool_use_id": tool_use_id,
                "content":     content,
                "is_error":    result.isError,
            }
        except asyncio.TimeoutError:
            return {
                "type":        "tool_result",
                "tool_use_id": tool_use_id,
                "content":     "Tool call timed out after 30 seconds.",
                "is_error":    True,
            }
        except Exception as e:
            return {
                "type":        "tool_result",
                "tool_use_id": tool_use_id,
                "content":     f"Tool execution error: {e}",
                "is_error":    True,
            }
```

---

## Caching Tool Metadata

Avoid repeated `tools/list` and `resources/list` calls with smart caching:

```python
import asyncio, time
from mcp import ClientSession
from mcp.types import Tool, Resource

class CachedCapabilities:
    def __init__(self, ttl_seconds: float = 60.0):
        self._tools:     list[Tool]     | None = None
        self._resources: list[Resource] | None = None
        self._tools_ts:  float | None = None
        self._res_ts:    float | None = None
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()

    def invalidate_tools(self):
        self._tools = None

    def invalidate_resources(self):
        self._resources = None

    async def get_tools(self, session: ClientSession) -> list[Tool]:
        async with self._lock:
            now = time.monotonic()
            if self._tools is None or (now - (self._tools_ts or 0)) > self._ttl:
                result = await session.list_tools()
                self._tools    = result.tools
                self._tools_ts = now
        return self._tools

    async def get_resources(self, session: ClientSession) -> list[Resource]:
        async with self._lock:
            now = time.monotonic()
            if self._resources is None or (now - (self._res_ts or 0)) > self._ttl:
                result = await session.list_resources()
                self._resources = result.resources
                self._res_ts    = now
        return self._resources


caps_cache = CachedCapabilities(ttl_seconds=60)

# Invalidate when server sends notifications
async def handle_notification(notification) -> None:
    match notification:
        case ToolListChangedNotification():
            caps_cache.invalidate_tools()
        case ResourceListChangedNotification():
            caps_cache.invalidate_resources()
```

---

## Common Client Lifecycle Pitfalls

| Pitfall | Problem | Fix |
|---------|---------|-----|
| Calling methods before `initialize()` | `McpError -32600` (session not ready) | Always `await session.initialize()` first |
| Not registering notification handlers before `initialize()` | Notifications missed during handshake | Register handlers before calling `initialize()` |
| No reconnect logic | Agent dies permanently on one network blip | Implement exponential backoff reconnection |
| Re-calling `list_tools()` on every LLM turn | Unnecessary latency | Cache tools; invalidate on `list_changed` notification |
| Injecting all tool results into context | Context window exhaustion | Use `ContextWindowManager` to fit within token budget |
| No timeout on `call_tool` | Slow tool hangs entire agent loop | Always wrap tool calls in `asyncio.wait_for(..., timeout=30)` |
| Concurrent `initialize()` calls | Session state corruption | Initialize once; share the session |

---

## Key Takeaways

- **One client per server connection** — the SDK manages the session internally.
- **Initialize before use** — always `await session.initialize()` before any other call.
- **Register notification handlers before `initialize()`** to avoid missing early notifications.
- **Cache capability metadata** (`list_tools`, `list_resources`) and invalidate on `list_changed` notifications.
- **LLM integration** = list MCP tools → inject into LLM API → execute tool calls via MCP → loop until final answer.
- **Auto-reconnect** is essential for production agents — use exponential backoff with jitter.
