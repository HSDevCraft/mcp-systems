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
