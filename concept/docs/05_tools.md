# 05 — Tools

Tools are the most used MCP primitive. A **Tool** is a function that the LLM can call, optionally
with side effects (writing files, sending emails, executing code). The LLM decides *when* to call
a tool based on its description and the conversation context.

---

## Tool Anatomy

```
Tool
├── name          string    Unique identifier within the server (e.g. "search_web")
├── description   string    Natural language description for the LLM (crucial for quality)
└── inputSchema   object    JSON Schema describing accepted arguments
```

### Wire format
```jsonc
{
  "name": "search_web",
  "description": "Search the web and return the top N results with titles and URLs.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "query":        { "type": "string",  "description": "Search query" },
      "max_results":  { "type": "integer", "description": "Max results to return", "default": 5 }
    },
    "required": ["query"]
  }
}
```

---

## Tool Result Types

A tool call returns a list of **content blocks**. Each block is one of:

### TextContent
```jsonc
{ "type": "text", "text": "The answer is 42." }
```

### ImageContent
```jsonc
{
  "type": "image",
  "data": "<base64-encoded-bytes>",
  "mimeType": "image/png"
}
```

### EmbeddedResource
```jsonc
{
  "type": "resource",
  "resource": {
    "uri": "file:///path/to/result.json",
    "mimeType": "application/json",
    "text": "{\"count\": 10}"
  }
}
```

### Full result envelope
```jsonc
{
  "content": [
    { "type": "text", "text": "Found 3 results:" },
    { "type": "text", "text": "1. https://example.com — Example Site\n2. …" }
  ],
  "isError": false    // true = tool ran but returned an error condition
}
```

---

## Defining Tools — Python SDK

### Low-level API (`Server`)
```python
import mcp.types as types
from mcp.server import Server

server = Server("tools-demo")

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="add",
            description="Add two integers and return the sum.",
            inputSchema={
                "type": "object",
                "properties": {
                    "a": {"type": "integer", "description": "First operand"},
                    "b": {"type": "integer", "description": "Second operand"},
                },
                "required": ["a", "b"],
            },
        ),
        types.Tool(
            name="read_file",
            description="Read the contents of a text file from the filesystem.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute file path"},
                },
                "required": ["path"],
            },
        ),
    ]

@server.call_tool()
async def call_tool(
    name: str, arguments: dict
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    if name == "add":
        result = arguments["a"] + arguments["b"]
        return [types.TextContent(type="text", text=str(result))]

    if name == "read_file":
        path = arguments["path"]
        try:
            with open(path) as f:
                return [types.TextContent(type="text", text=f.read())]
        except FileNotFoundError:
            return [types.TextContent(type="text", text=f"Error: file not found: {path}")]

    raise ValueError(f"Unknown tool: {name}")
```

### High-level API (`FastMCP`) — recommended
```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("tools-demo")

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two integers and return the sum."""
    return a + b

@mcp.tool()
def read_file(path: str) -> str:
    """Read the contents of a text file from the filesystem."""
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        raise ValueError(f"File not found: {path}")

@mcp.tool()
async def fetch_url(url: str, timeout: int = 10) -> str:
    """Fetch the content of a URL and return the HTML."""
    import httpx
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text
```

FastMCP automatically:
- Extracts `name` from function name
- Extracts `description` from docstring
- Generates `inputSchema` from type annotations + default values
- Wraps return values in `TextContent`

---

## Input Schema Patterns

### Required primitive fields
```python
@mcp.tool()
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email."""
    ...
```

### Optional fields with defaults
```python
@mcp.tool()
def search(query: str, max_results: int = 10, language: str = "en") -> str:
    """Search the web."""
    ...
```

### Enum / literal types
```python
from typing import Literal

@mcp.tool()
def set_log_level(level: Literal["debug", "info", "warning", "error"]) -> str:
    """Set the logging level."""
    ...
```

### Structured input with Pydantic
```python
from pydantic import BaseModel, Field

class CreateIssueInput(BaseModel):
    title:    str = Field(description="Issue title")
    body:     str = Field(description="Issue body (markdown)")
    labels:   list[str] = Field(default=[], description="GitHub label names")
    assignee: str | None = Field(default=None, description="GitHub username")

@mcp.tool()
def create_issue(input: CreateIssueInput) -> str:
    """Create a GitHub issue."""
    ...
```

### List / array inputs
```python
@mcp.tool()
def batch_translate(texts: list[str], target_language: str) -> list[str]:
    """Translate multiple texts to the target language."""
    ...
```

---

## Error Handling in Tools

### Soft error (tool ran, result is an error message)
```python
@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "divide":
        a, b = arguments["a"], arguments["b"]
        if b == 0:
            # Return error content — isError=True tells LLM this is an error
            return [types.TextContent(type="text", text="Division by zero")]
        return [types.TextContent(type="text", text=str(a / b))]
```

The SDK sets `isError=True` automatically when you return error content, OR you can set it
explicitly:
```python
return types.CallToolResult(
    content=[types.TextContent(type="text", text="Something went wrong")],
    isError=True,
)
```

### Hard error (protocol-level — tool could not run at all)
```python
@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name not in KNOWN_TOOLS:
        raise ValueError(f"Unknown tool: {name}")  # → JSON-RPC error -32601
```

Use hard errors for: unknown tool name, malformed arguments, auth failures.
Use soft errors for: operation failed but tool executed correctly.

---

## Long-Running Tools with Progress

```python
from mcp.server.session import ServerSession

@server.call_tool()
async def call_tool(name: str, arguments: dict, session: ServerSession = None):
    if name == "process_dataset":
        items = arguments["items"]
        results = []
        for i, item in enumerate(items):
            # Send progress notification
            if session:
                await session.send_progress_notification(
                    progress_token=arguments.get("_meta", {}).get("progressToken"),
                    progress=i / len(items),
                    total=len(items),
                )
            results.append(process(item))
        return [types.TextContent(type="text", text="\n".join(results))]
```

---

## Dynamic Tool Lists

If your tools change at runtime (e.g., user installs a plugin), notify the client:

```python
from mcp.server import NotificationOptions

# When tools change:
await server.request_context.session.send_tool_list_changed()
```

The client will re-call `tools/list` to refresh.

---

## Tool Design Best Practices

| Practice | Why |
|----------|-----|
| **Write descriptions for the LLM, not humans** | The LLM uses the description to decide *when* to call the tool. Be specific about inputs/outputs/side effects. |
| **One tool = one responsibility** | `read_file` and `write_file` should be separate tools, not one tool with a `mode` argument. |
| **Validate inputs explicitly** | Don't trust that JSON Schema validation is enough — check business constraints too. |
| **Return structured text when possible** | JSON, markdown tables, or delimited lists are easier for the LLM to parse than prose. |
| **Idempotent reads, explicit writes** | Make read tools obviously safe; make write tools require an explicit confirmation flag if destructive. |
| **Never return secrets** | API keys, passwords, tokens should never appear in tool output. |
| **Timeout every external call** | Set explicit timeouts on HTTP requests, DB queries, subprocess runs. |
| **Log inputs at INFO, outputs at DEBUG** | Inputs help debug LLM decisions; outputs can be verbose. |

---

## Example: Production-Grade Tool

```python
import httpx
from pydantic import BaseModel, HttpUrl, Field
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("web-tools")

class SearchInput(BaseModel):
    query:       str       = Field(description="Search query string")
    max_results: int       = Field(default=5, ge=1, le=20, description="Number of results (1-20)")
    language:    str       = Field(default="en", description="BCP-47 language code")

@mcp.tool()
async def web_search(input: SearchInput) -> str:
    """
    Search the web using Brave Search API.
    Returns a numbered list of results with title, URL, and snippet.
    Use this tool when you need up-to-date information not in your training data.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": input.query, "count": input.max_results, "lang": input.language},
            headers={"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY},
        )
        response.raise_for_status()
        data = response.json()

    results = data.get("web", {}).get("results", [])
    if not results:
        return "No results found."

    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. **{r['title']}**")
        lines.append(f"   {r['url']}")
        lines.append(f"   {r.get('description', 'No description')}")
        lines.append("")
    return "\n".join(lines)
```
