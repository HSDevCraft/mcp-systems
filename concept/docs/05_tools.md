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

---

## Tool Annotations

Tool annotations provide **hints** to the client and host about the nature and safety of a tool. They do not change behaviour — they help the host make approval decisions.

```jsonc
{
  "name": "delete_file",
  "description": "Permanently delete a file.",
  "inputSchema": { ... },
  "annotations": {
    "title":            "Delete File",  // human-readable display name
    "readOnlyHint":     false,          // true = tool only reads, no side effects
    "destructiveHint":  true,           // true = may have irreversible consequences
    "idempotentHint":   false,          // true = calling N times = calling once
    "openWorldHint":    false           // true = tool accesses external systems (internet)
  }
}
```

### Annotation meanings

| Annotation | Default | Meaning |
|------------|---------|---------|
| `readOnlyHint` | `false` | Tool makes no writes; safe to call without confirmation |
| `destructiveHint` | `true` | Tool may irreversibly modify or delete data |
| `idempotentHint` | `false` | Repeated calls with same args produce the same result |
| `openWorldHint` | `true` | Tool accesses the internet or external APIs |

> Annotations are **hints only** — they must not be used for security enforcement. The host may choose to show a confirmation dialog when `destructiveHint: true`.

### Python SDK — adding annotations to FastMCP

```python
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

mcp = FastMCP("annotated-server")

@mcp.tool(
    annotations=ToolAnnotations(
        read_only_hint=True,
        open_world_hint=False,
    )
)
def read_config(key: str) -> str:
    """Read a configuration value. Read-only, no side effects."""
    return CONFIG.get(key, "")

@mcp.tool(
    annotations=ToolAnnotations(
        destructive_hint=True,
        idempotent_hint=False,
    )
)
def delete_record(record_id: str) -> str:
    """Permanently delete a record from the database."""
    DB.delete(record_id)
    return f"Deleted record {record_id}"

@mcp.tool(
    annotations=ToolAnnotations(
        open_world_hint=True,
        read_only_hint=True,
    )
)
async def fetch_url(url: str) -> str:
    """Fetch the contents of a URL. Read-only, accesses the internet."""
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url)
        return r.text
```

---

## Tool Chaining / Composition

Tools that build on other tools, enabling multi-step workflows:

```python
from mcp.server.fastmcp import FastMCP, Context

mcp = FastMCP("pipeline-server")

@mcp.tool()
async def search_and_summarise(query: str, ctx: Context) -> str:
    """
    Search the web for a query and return a concise summary.
    Combines web_search and LLM summarisation in one step.
    """
    # Step 1: search
    search_result = await ctx.call_tool("web_search", {"query": query, "max_results": 5})
    raw_text = search_result.content[0].text if search_result.content else ""

    if not raw_text or "No results" in raw_text:
        return f"No results found for: {query}"

    # Step 2: summarise with LLM sampling
    summary = await ctx.sample(
        f"Summarise these search results in 3 bullet points:\n\n{raw_text}",
        max_tokens=300,
        temperature=0.3,
    )
    return summary.content.text


@mcp.tool()
async def research_and_store(topic: str, output_path: str, ctx: Context) -> str:
    """
    Research a topic and save the summary to a file.
    Chains: search_and_summarise → write_file.
    """
    # Step 1: research
    summary = await ctx.call_tool("search_and_summarise", {"query": topic})
    content = summary.content[0].text

    # Step 2: store
    result = await ctx.call_tool("write_file", {"path": output_path, "content": content})
    return f"Research complete. Saved to {output_path}.\n\n{content}"
```

---

## Returning Multiple Content Blocks

A tool can return a mixed list of text, images, and embedded resources:

```python
import base64
from pathlib import Path
import mcp.types as types
from mcp.server import Server

server = Server("rich-output")

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "analyze_image":
        path = arguments["path"]
        image_bytes = Path(path).read_bytes()
        image_b64 = base64.b64encode(image_bytes).decode()

        # Return both a text analysis and the image itself
        return [
            types.TextContent(
                type="text",
                text=f"Image analysis for {path}:\n- Size: {len(image_bytes)} bytes\n- Format: PNG",
            ),
            types.ImageContent(
                type="image",
                data=image_b64,
                mimeType="image/png",
            ),
        ]
```

---

## Tool Versioning

Handle breaking changes without removing old tools:

```python
@mcp.tool()
def search(query: str, max_results: int = 5) -> str:
    """[DEPRECATED] Search the web. Use search_v2 for language filtering."""
    return _search_impl(query, max_results, language="en")

@mcp.tool()
def search_v2(query: str, max_results: int = 5, language: str = "en", since_days: int = 0) -> str:
    """
    Search the web with language and recency filters.
    Prefer this over the deprecated `search` tool.
    Returns a numbered list with title, URL, and snippet.
    """
    return _search_impl(query, max_results, language=language, since_days=since_days)
```

---

## Writing LLM-Friendly Tool Descriptions

The LLM uses the `description` field to decide **when** and **how** to call a tool. Poor descriptions = poor tool use.

```python
# BAD — vague, gives LLM no guidance
@mcp.tool()
def get_data(id: str) -> str:
    """Get data."""
    ...

# GOOD — tells LLM when to use it, what it returns, what inputs mean
@mcp.tool()
def get_order(order_id: str) -> str:
    """
    Retrieve full details of a customer order by its ID.
    Returns: order status, items, quantities, prices, shipping address, and tracking number.
    Use this when the user asks about a specific order or its status.
    order_id: The alphanumeric order identifier (e.g. 'ORD-2024-0042').
    Returns an error message if the order does not exist.
    """
    ...
```

**Description writing guidelines**:
1. First sentence: what the tool **does**.
2. Second sentence: what it **returns**.
3. When to **use** it (vs. alternatives).
4. Parameter clarifications (if not obvious from the name/type).
5. Error conditions and their messages.

---

## Common Tool Pitfalls

| Pitfall | Problem | Fix |
|---------|---------|-----|
| Generic description | LLM calls wrong tool or wrong time | Write specific, LLM-targeted descriptions |
| No input validation beyond JSON Schema | Business rule violations | Add Pydantic validators or manual checks |
| Returning raw stack traces | Security risk; confuses LLM | Catch exceptions; return clean error messages |
| Blocking sync I/O in async handler | Event loop blocked; other tools freeze | Use `asyncio.to_thread()` for sync I/O |
| Infinite loops in agentic tools | Server hangs; resource exhaustion | Always set a max iteration count |
| Missing `isError: true` on failures | LLM treats errors as success | Return `CallToolResult(isError=True)` on failure |
| Tool side effects in read-only tools | Unexpected state changes | Set `readOnlyHint: true` only for truly read-only tools |
| Returning secrets in tool output | Credential leakage | Filter all sensitive values from responses |

---

## Key Takeaways

- **Tools are the action primitive** — they run code, have side effects, and return content blocks.
- **Annotations** (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) help hosts make approval decisions.
- **Descriptions are for the LLM**, not humans — write them as natural language guidance.
- Tools can return **mixed content** (text + images + resources) in a single call.
- Use **FastMCP** for automatic schema generation from type hints and docstrings.
- **Tool chaining** via `ctx.call_tool()` enables multi-step workflows within a single tool call.
