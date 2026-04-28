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

---

## Structured Error Type Hierarchy

Define a rich error hierarchy for clear error categorisation:

```python
from dataclasses import dataclass
from mcp.types import INVALID_PARAMS, INTERNAL_ERROR

# Base application error
class MCPAppError(Exception):
    """Base class for all application-level MCP errors."""
    mcp_code:    int  = INTERNAL_ERROR
    http_status: int  = 500
    retryable:   bool = False

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}

# Validation errors
class ValidationError(MCPAppError):
    mcp_code    = INVALID_PARAMS
    http_status = 400
    retryable   = False

class PathTraversalError(ValidationError):
    pass

class InvalidInputError(ValidationError):
    pass

# Permission errors
class PermissionDeniedError(MCPAppError):
    mcp_code    = INVALID_PARAMS
    http_status = 403
    retryable   = False

class ScopeError(PermissionDeniedError):
    """Tool requires a scope the caller doesn't have."""
    pass

# Not-found errors
class NotFoundError(MCPAppError):
    mcp_code    = INVALID_PARAMS
    http_status = 404
    retryable   = False

# Transient errors (safe to retry)
class ServiceUnavailableError(MCPAppError):
    mcp_code    = INTERNAL_ERROR
    http_status = 503
    retryable   = True

class RateLimitError(ServiceUnavailableError):
    def __init__(self, message: str, retry_after_seconds: int = 60):
        super().__init__(message, {"retry_after": retry_after_seconds})
        self.retry_after = retry_after_seconds

class ExternalAPIError(ServiceUnavailableError):
    def __init__(self, message: str, status_code: int = 500, url: str = ""):
        super().__init__(message, {"upstream_status": status_code, "url": url})

# Convert to MCP errors automatically
def to_mcp_result(error: MCPAppError) -> list:
    """Convert typed application error to soft error result."""
    import mcp.types as types
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=str(error))],
        isError=True,
    )

def to_mcp_protocol_error(error: MCPAppError):
    """Convert typed application error to protocol error (for unrecoverable failures)."""
    from mcp.shared.exceptions import McpError
    from mcp.types import ErrorData
    return McpError(ErrorData(
        code=error.mcp_code,
        message=str(error),
        data=error.details or None,
    ))
```

---

## Error Aggregation (Multi-File / Batch Tools)

Collect and report errors across many items without failing the entire batch:

```python
from dataclasses import dataclass, field
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("batch-server")

@dataclass
class BatchResult:
    successes: list[dict] = field(default_factory=list)
    errors:    list[dict] = field(default_factory=list)

    def add_success(self, item: str, result: str) -> None:
        self.successes.append({"item": item, "result": result})

    def add_error(self, item: str, error: str) -> None:
        self.errors.append({"item": item, "error": error})

    def to_text(self) -> str:
        import json
        lines = [f"Processed: {len(self.successes)} OK, {len(self.errors)} failed"]
        if self.successes:
            lines.append(f"\nSuccesses ({len(self.successes)}):")
            lines.extend(f"  ✓ {s['item']}: {s['result']}" for s in self.successes)
        if self.errors:
            lines.append(f"\nErrors ({len(self.errors)}):")
            lines.extend(f"  ✗ {e['item']}: {e['error']}" for e in self.errors)
        return "\n".join(lines)

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


@mcp.tool()
async def batch_process_files(paths: list[str]) -> str:
    """
    Process multiple files and return aggregated results.
    Partial failures are reported but don't fail the entire batch.
    """
    result = BatchResult()

    for path in paths:
        try:
            content = await read_and_process(path)
            result.add_success(path, f"{len(content)} chars processed")
        except FileNotFoundError:
            result.add_error(path, "File not found")
        except PermissionError:
            result.add_error(path, "Access denied")
        except Exception as e:
            result.add_error(path, f"{type(e).__name__}: {e}")

    return result.to_text()
    # Note: isError=False even if some files failed — the tool itself ran successfully
```

---

## Dead Letter Pattern

Queue permanently-failing operations for manual review:

```python
import asyncio, json, time
from pathlib import Path

class DeadLetterQueue:
    """
    Stores operations that have failed all retries for manual investigation.
    """
    def __init__(self, path: str = "/var/log/mcp/dlq.jsonl"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def enqueue(
        self,
        tool_name: str,
        arguments: dict,
        error: str,
        attempts: int,
    ) -> None:
        entry = {
            "ts":          time.time(),
            "tool":        tool_name,
            "arguments":   arguments,
            "error":       error,
            "attempts":    attempts,
            "status":      "pending_review",
        }
        with self.path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def drain_reviewable(self) -> list[dict]:
        """Return all DLQ items for review."""
        if not self.path.exists():
            return []
        with self.path.open() as f:
            return [json.loads(line) for line in f if line.strip()]


dlq = DeadLetterQueue()

async def call_with_retry_and_dlq(
    tool_name: str,
    arguments: dict,
    max_attempts: int = 3,
) -> str:
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await execute_tool(tool_name, arguments)
        except ServiceUnavailableError as e:
            last_error = e
            if not e.retryable or attempt == max_attempts:
                break
            await asyncio.sleep(2 ** attempt)  # 2s, 4s

    # All retries exhausted — send to DLQ
    dlq.enqueue(
        tool_name=tool_name,
        arguments=arguments,
        error=str(last_error),
        attempts=max_attempts,
    )
    return f"Operation failed after {max_attempts} attempts and has been queued for review."
```

---

## Error Budget Tracking

Track error rates to enforce SLOs (Service Level Objectives):

```python
import asyncio, time
from collections import deque

class ErrorBudget:
    """
    Sliding-window error rate tracker.
    Raises alarm when error rate exceeds the SLO threshold.
    """
    def __init__(
        self,
        window_seconds: float = 300.0,  # 5 minute window
        error_budget_pct: float = 1.0,  # 1% error budget
    ):
        self.window     = window_seconds
        self.budget_pct = error_budget_pct
        self._events: deque[tuple[float, bool]] = deque()  # (timestamp, is_error)
        self._lock = asyncio.Lock()

    async def record(self, is_error: bool) -> None:
        async with self._lock:
            now = time.monotonic()
            self._events.append((now, is_error))
            # Trim old events
            while self._events and now - self._events[0][0] > self.window:
                self._events.popleft()

    async def current_error_rate(self) -> float:
        async with self._lock:
            if not self._events:
                return 0.0
            errors = sum(1 for _, is_err in self._events if is_err)
            return errors / len(self._events)

    async def is_budget_exhausted(self) -> bool:
        rate = await self.current_error_rate()
        return rate > (self.budget_pct / 100)


error_budget = ErrorBudget(window_seconds=300, error_budget_pct=1.0)

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if await error_budget.is_budget_exhausted():
        return types.CallToolResult(
            content=[types.TextContent(
                type="text",
                text="Service is experiencing elevated error rates. Try again in a few minutes.",
            )],
            isError=True,
        )
    try:
        result = await execute_tool(name, arguments)
        await error_budget.record(is_error=False)
        return result
    except Exception as e:
        await error_budget.record(is_error=True)
        raise
```

---

## Observability-First Error Handling

Structure errors for log aggregation and alerting:

```python
import logging, sys, traceback, time

# Structured JSON logger
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(message)s",  # raw JSON
)
logger = logging.getLogger("mcp-server")

def log_error(
    tool_name: str,
    error: Exception,
    arguments: dict,
    duration_ms: float,
) -> None:
    """Emit a structured error log for aggregation by Loki/DataDog/CloudWatch."""
    import json
    entry = {
        "level":       "error",
        "ts":          time.time(),
        "service":     "mcp-server",
        "tool":        tool_name,
        "error_type":  type(error).__name__,
        "error_msg":   str(error),
        "duration_ms": duration_ms,
        "retryable":   getattr(error, "retryable", False),
        # Include traceback only in debug mode
        "traceback":   traceback.format_exc() if logger.isEnabledFor(logging.DEBUG) else None,
    }
    logger.error(json.dumps(entry))


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    start = time.perf_counter()
    try:
        result = await execute_tool(name, arguments)
        return result
    except MCPAppError as e:
        duration = (time.perf_counter() - start) * 1000
        log_error(name, e, arguments, duration)
        return to_mcp_result(e)
    except Exception as e:
        duration = (time.perf_counter() - start) * 1000
        log_error(name, e, arguments, duration)
        raise  # unexpected errors become protocol-level errors
```

---

## Common Error Handling Pitfalls

| Pitfall | Problem | Fix |
|---------|---------|-----|
| Swallowing exceptions silently | Errors hidden; hard to debug | Always log before swallowing |
| Using protocol errors for logic failures | LLM cannot distinguish error types | Use `isError=True` for application errors |
| No retry on transient errors | Unnecessary failures on network blips | Retry `INTERNAL_ERROR` codes with exponential backoff |
| Exposing stack traces in error messages | Security risk; implementation leakage | Expose only error type and message; log stack trace internally |
| No error categorisation | All errors treated identically | Use typed error hierarchy; set `retryable` flags |
| Failing entire batch on one item error | Poor UX; wasted partial work | Use `BatchResult` pattern; report per-item status |
| No DLQ for permanent failures | Lost operations; no audit trail | Enqueue permanently-failed items for manual review |

---

## Key Takeaways

- MCP has **three error levels**: protocol errors (JSON-RPC), soft tool errors (`isError: true`), and validation errors (`-32602`).
- **Typed error hierarchies** make error handling predictable and testable.
- **Batch tools** should aggregate errors per item, not fail on first error.
- **Dead letter queues** preserve permanently-failed operations for review.
- **Error budgets** enforce SLOs and protect downstream systems from cascading failures.
- **Structured logging** in JSON enables log aggregation and alerting systems to parse errors automatically.
