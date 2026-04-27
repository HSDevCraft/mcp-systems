# 13 — Error Handling

MCP distinguishes between three kinds of errors. Understanding which to use — and when — is
critical for building reliable servers and robust clients.

---

## Error Taxonomy

```
MCP Errors
├── 1. Protocol errors (JSON-RPC level)
│       Transport failure, unknown method, malformed message
│       → JSON-RPC error object  { code, message, data }
│
├── 2. Tool execution errors (application level)
│       Tool ran but the operation failed (file not found, API down)
│       → Successful JSON-RPC response with isError: true in result
│
└── 3. Validation errors (parameter level)
        Wrong types, missing required fields, out-of-range values
        → JSON-RPC error -32602 (Invalid params)
```

---

## 1. Protocol Errors

These propagate as JSON-RPC error objects and are raised as `McpError` on the client side.

### Standard JSON-RPC codes

| Code | Constant | Use when |
|------|----------|----------|
| -32700 | `PARSE_ERROR` | Received invalid JSON |
| -32600 | `INVALID_REQUEST` | JSON-RPC structure is wrong |
| -32601 | `METHOD_NOT_FOUND` | Called unknown method |
| -32602 | `INVALID_PARAMS` | Wrong/missing parameters |
| -32603 | `INTERNAL_ERROR` | Unhandled server exception |

### MCP-specific codes

| Code | Use when |
|------|----------|
| -32001 | Request cancelled by client |
| -32002 | Response content too large |

### Raising protocol errors in Python SDK

```python
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS, INTERNAL_ERROR

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    # Unknown tool → METHOD_NOT_FOUND equivalent
    if name not in REGISTERED_TOOLS:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message=f"Unknown tool: {name!r}",
                data={"available": list(REGISTERED_TOOLS.keys())},
            )
        )
    # Server-side exception → INTERNAL_ERROR
    try:
        return await execute_tool(name, arguments)
    except Exception as e:
        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR,
                message="Tool execution failed",
                data={"error": str(e)},
            )
        )
```

---

## 2. Tool Execution Errors (Soft Errors)

The preferred pattern for application-level failures. The tool *ran correctly* but the operation
it was asked to do encountered an error.

### Returning isError: true

```python
@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "read_file":
        path = arguments["path"]
        try:
            with open(path) as f:
                return [types.TextContent(type="text", text=f.read())]

        except FileNotFoundError:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"File not found: {path}")],
                isError=True,
            )

        except PermissionError:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Permission denied: {path}")],
                isError=True,
            )

        except OSError as e:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"IO error reading {path}: {e}")],
                isError=True,
            )
```

### Using FastMCP — raise exceptions naturally

With FastMCP, any exception raised inside a tool is automatically wrapped as a soft error:

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("file-server")

@mcp.tool()
def read_file(path: str) -> str:
    """Read a text file."""
    # Raising ValueError / FileNotFoundError / etc. → isError=True automatically
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")
    if not os.path.isfile(path):
        raise ValueError(f"Not a file: {path}")
    with open(path) as f:
        return f.read()
```

---

## 3. Validation Errors

For input that doesn't match the expected schema or business rules.

```python
from pydantic import BaseModel, ValidationError, validator, Field

class SearchArgs(BaseModel):
    query:       str = Field(..., min_length=1, max_length=500)
    max_results: int = Field(default=10, ge=1, le=100)
    language:    str = Field(default="en", pattern=r"^[a-z]{2}(-[A-Z]{2})?$")

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "search":
        try:
            args = SearchArgs(**arguments)
        except ValidationError as e:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message="Invalid search arguments",
                    data=e.errors(),
                )
            )
        return await do_search(args)
```

---

## Error Handling on the Client Side

```python
from mcp.shared.exceptions import McpError
from mcp.types import INVALID_PARAMS, INTERNAL_ERROR
import asyncio

async def call_tool_safely(session, name: str, args: dict) -> str:
    try:
        result = await asyncio.wait_for(
            session.call_tool(name, args),
            timeout=30.0
        )

        # Check for soft (application-level) error
        if result.isError:
            error_text = next(
                (c.text for c in result.content if hasattr(c, "text")), "Unknown error"
            )
            return f"[Tool error] {error_text}"

        # Success — extract text content
        return "\n".join(
            c.text for c in result.content if hasattr(c, "text")
        )

    except McpError as e:
        code = e.error.code
        msg  = e.error.message
        if code == INVALID_PARAMS:
            return f"[Invalid arguments] {msg}"
        elif code == INTERNAL_ERROR:
            return f"[Server error] {msg}"
        elif code == -32001:
            return "[Cancelled]"
        else:
            return f"[Protocol error {code}] {msg}"

    except asyncio.TimeoutError:
        return "[Timeout] Tool call exceeded 30 seconds"

    except ConnectionError:
        return "[Connection lost] Server disconnected"

    except Exception as e:
        return f"[Unexpected error] {type(e).__name__}: {e}"
```

---

## Error Propagation Patterns

### Pattern 1 — Error context enrichment
```python
import traceback

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        return await execute_tool(name, arguments)
    except McpError:
        raise  # re-raise MCP errors unchanged
    except Exception as e:
        # Add context before wrapping as internal error
        context = {
            "tool":      name,
            "traceback": traceback.format_exc(),
        }
        # In production: log the full traceback but don't expose it
        logger.error("Tool execution failed", extra=context)
        raise McpError(
            ErrorData(
                code=INTERNAL_ERROR,
                message=f"Tool '{name}' failed: {type(e).__name__}",
                # Don't include traceback in production — security risk
                data={"tool": name} if DEBUG else None,
            )
        )
```

### Pattern 2 — Partial results with errors
```python
@mcp.tool()
async def process_files(paths: list[str]) -> str:
    """Process multiple files, reporting per-file errors."""
    results = []
    errors  = []

    for path in paths:
        try:
            result = process_one(path)
            results.append(f"✓ {path}: {result}")
        except Exception as e:
            errors.append(f"✗ {path}: {e}")

    lines = results + (["", "Errors:"] + errors if errors else [])
    return "\n".join(lines)
    # Note: isError=False — the tool ran; some files just failed
```

### Pattern 3 — Retry with exponential backoff
```python
import asyncio

async def call_with_retry(
    session,
    name: str,
    args: dict,
    max_attempts: int = 3,
) -> types.CallToolResult:
    last_error = None
    for attempt in range(max_attempts):
        try:
            return await session.call_tool(name, args)
        except McpError as e:
            if e.error.code == INTERNAL_ERROR:
                last_error = e
                wait = 2 ** attempt  # 1s, 2s, 4s
                await asyncio.sleep(wait)
                continue
            raise  # don't retry client errors
    raise last_error
```

---

## Logging Errors

### Server-side logging
```python
import logging, sys

# Always log to stderr — stdout is the protocol wire
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mcp-server")

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    logger.info(f"tool_call name={name!r}")
    try:
        result = await execute_tool(name, arguments)
        logger.debug(f"tool_success name={name!r}")
        return result
    except Exception as e:
        logger.error(f"tool_error name={name!r} error={e!r}", exc_info=True)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=str(e))],
            isError=True,
        )
```

### Sending structured logs to client
```python
@server.call_tool()
async def call_tool(name: str, arguments: dict):
    session = server.request_context.session
    try:
        result = await execute_tool(name, arguments)
        return result
    except Exception as e:
        # Send structured log to client (appears in host's log UI)
        await session.send_log_message(
            level="error",
            data={"tool": name, "error": str(e), "type": type(e).__name__},
            logger="tool-handler",
        )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Error: {e}")],
            isError=True,
        )
```

---

## Error Handling Cheatsheet

| Situation | Correct pattern |
|-----------|-----------------|
| Unknown tool name | `raise McpError(code=INVALID_PARAMS)` |
| Missing required argument | `raise McpError(code=INVALID_PARAMS)` |
| File not found | Return `isError=True` with descriptive text |
| External API returned 404 | Return `isError=True` with descriptive text |
| External API returned 500 | Return `isError=True`; optionally retry |
| DB connection lost | `raise McpError(code=INTERNAL_ERROR)` |
| Auth/permission denied | Return `isError=True` with permission message |
| Rate limit exceeded | Return `isError=True` with retry-after hint |
| Timeout | Return `isError=True` with timeout message |
| Uncaught exception | Wrap in `McpError(INTERNAL_ERROR)`; log full trace |
| Path traversal attempt | `raise McpError(code=INVALID_PARAMS)` |
