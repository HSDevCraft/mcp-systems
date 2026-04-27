# 06 — Resources

**Resources** are addressable data that the LLM can *read*. Unlike Tools, resources are conceptually
**read-only** — they expose data, not actions. They are addressed by URI and can represent files,
database rows, API responses, live sensor readings, or any content.

The LLM (via the host) decides which resources to read based on the conversation.

---

## Resource Anatomy

```
Resource
├── uri          string    Unique address (e.g. "file:///home/user/data.csv")
├── name         string    Human-readable display name
├── description  string?   Optional — what this resource contains
├── mimeType     string?   Optional — MIME type hint (e.g. "text/csv", "image/png")
└── size         integer?  Optional — byte size hint
```

### Wire format
```jsonc
{
  "uri":         "file:///workspace/README.md",
  "name":        "README.md",
  "description": "Project documentation and setup instructions",
  "mimeType":    "text/markdown"
}
```

---

## URI Schemes

MCP does not restrict URI schemes. Common conventions:

| Scheme | Example | Typical use |
|--------|---------|-------------|
| `file://` | `file:///home/user/notes.txt` | Local files |
| `https://` | `https://api.example.com/users/42` | Remote API data |
| `db://` | `db://postgres/orders/1234` | Database rows |
| `git://` | `git://repo/main/src/app.py` | Git file blobs |
| `memory://` | `memory://session/recent-context` | Agent memory |
| `custom://` | `myapp://config/prod` | Application-specific |

### URI Templates (dynamic resources)
```
file://{path}              → any file path
db://users/{user_id}       → any user record
repo://{owner}/{repo}/{sha}/  → any git file
```

---

## Resource Content Types

### Text resource
```jsonc
{
  "uri":      "file:///data/report.md",
  "mimeType": "text/markdown",
  "text":     "# Q4 Report\n\nRevenue grew 23%..."
}
```

### Binary resource (base64-encoded)
```jsonc
{
  "uri":      "file:///images/logo.png",
  "mimeType": "image/png",
  "blob":     "iVBORw0KGgoAAAANSUhEUgAA..."
}
```

A resource has either `text` **or** `blob`, never both.

---

## Defining Resources — Python SDK

### Low-level API (`Server`) — static list
```python
import mcp.types as types
from mcp.server import Server
from pathlib import Path

server = Server("file-server")

WORKSPACE = Path("/workspace")

@server.list_resources()
async def list_resources() -> list[types.Resource]:
    resources = []
    for p in WORKSPACE.rglob("*.md"):
        resources.append(types.Resource(
            uri=f"file://{p}",
            name=p.name,
            description=f"Markdown file: {p.relative_to(WORKSPACE)}",
            mimeType="text/markdown",
        ))
    return resources

@server.read_resource()
async def read_resource(uri: str) -> str | bytes:
    path = Path(uri.removeprefix("file://"))
    if not path.is_relative_to(WORKSPACE):
        raise ValueError(f"Access denied: {path}")
    return path.read_text(encoding="utf-8")
```

### High-level API (`FastMCP`) — URI templates
```python
from mcp.server.fastmcp import FastMCP
from pathlib import Path

mcp = FastMCP("file-server")

WORKSPACE = Path("/workspace")

@mcp.resource("file://{path}")
def read_file(path: str) -> str:
    """Read any file from the workspace."""
    full_path = WORKSPACE / path
    if not full_path.is_relative_to(WORKSPACE):
        raise PermissionError(f"Access denied: {path}")
    return full_path.read_text()

@mcp.resource("db://users/{user_id}")
async def get_user(user_id: str) -> str:
    """Fetch a user record from the database."""
    import json
    user = await db.fetch_one("SELECT * FROM users WHERE id = $1", user_id)
    if not user:
        raise ValueError(f"User not found: {user_id}")
    return json.dumps(dict(user))
```

### Listing static resources with FastMCP
```python
@mcp.resource("config://app")
def get_config() -> str:
    """Current application configuration."""
    import json
    return json.dumps({"env": "production", "version": "2.1.0"})

# FastMCP auto-registers static URIs in list_resources
```

---

## Resource Subscriptions

If the server declares `resources.subscribe: true`, clients can subscribe to change notifications.

### Subscribe to a resource
```jsonc
// Client → Server
{
  "jsonrpc": "2.0",
  "id": 10,
  "method": "resources/subscribe",
  "params": { "uri": "file:///workspace/config.yaml" }
}
```

### Server notifies when resource changes
```jsonc
// Server → Client notification
{
  "jsonrpc": "2.0",
  "method": "notifications/resources/updated",
  "params": { "uri": "file:///workspace/config.yaml" }
}
```

The client then re-calls `resources/read` to get the updated content.

### Python SDK — sending resource change notifications
```python
import asyncio
from watchfiles import awatch

async def watch_workspace(server_session):
    async for changes in awatch("/workspace"):
        for change_type, path in changes:
            await server_session.send_resource_updated(f"file://{path}")
```

### Unsubscribe
```jsonc
{
  "jsonrpc": "2.0",
  "id": 11,
  "method": "resources/unsubscribe",
  "params": { "uri": "file:///workspace/config.yaml" }
}
```

---

## Resource List Changes

When the set of available resources changes (e.g., a new file is created), notify all clients:

```python
# Server emits this notification
await session.send_resource_list_changed()
```

Clients re-call `resources/list` to refresh their view.

---

## Pagination

```python
@server.list_resources()
async def list_resources(cursor: str | None = None) -> types.ListResourcesResult:
    all_resources = get_all_resources()
    page_size = 50
    start = decode_cursor(cursor) if cursor else 0
    page = all_resources[start:start + page_size]
    next_cursor = encode_cursor(start + page_size) if start + page_size < len(all_resources) else None
    return types.ListResourcesResult(resources=page, nextCursor=next_cursor)
```

---

## Practical Resource Patterns

### Pattern 1: Directory Listing + File Reading
```python
@mcp.resource("dir://{path}")
def list_directory(path: str) -> str:
    """List files in a directory."""
    import json
    from pathlib import Path
    p = Path(path)
    entries = [
        {"name": e.name, "type": "dir" if e.is_dir() else "file", "size": e.stat().st_size if e.is_file() else 0}
        for e in sorted(p.iterdir())
    ]
    return json.dumps(entries, indent=2)

@mcp.resource("file://{path}")
def read_file(path: str) -> str:
    """Read a text file."""
    return Path(path).read_text()
```

### Pattern 2: Database Query as Resource
```python
@mcp.resource("db://products/{product_id}")
async def get_product(product_id: str) -> str:
    """Fetch product details from the database."""
    import json
    async with get_db() as conn:
        row = await conn.fetchrow("SELECT * FROM products WHERE id = $1", product_id)
    if not row:
        raise ValueError(f"Product {product_id} not found")
    return json.dumps(dict(row), default=str)
```

### Pattern 3: Live API Data
```python
@mcp.resource("weather://{city}")
async def get_weather(city: str) -> str:
    """Current weather for a city."""
    import httpx, json
    async with httpx.AsyncClient() as client:
        r = await client.get(f"https://wttr.in/{city}?format=j1")
        data = r.json()
    return json.dumps({
        "city": city,
        "temp_c": data["current_condition"][0]["temp_C"],
        "desc":   data["current_condition"][0]["weatherDesc"][0]["value"],
    })
```

### Pattern 4: Computed / Aggregated Resource
```python
@mcp.resource("report://summary")
async def generate_summary() -> str:
    """Aggregated business summary (recomputed on each read)."""
    import json
    async with get_db() as conn:
        stats = await conn.fetchrow("""
            SELECT
                COUNT(*)           AS total_orders,
                SUM(amount)        AS total_revenue,
                AVG(amount)        AS avg_order_value
            FROM orders
            WHERE created_at > NOW() - INTERVAL '30 days'
        """)
    return json.dumps(dict(stats), default=str)
```

---

## Resources vs. Tools — When to Use Which

| Scenario | Use |
|----------|-----|
| Reading a file | Resource |
| Writing a file | Tool |
| Getting DB record by ID | Resource |
| Running a DB query with complex filters | Tool |
| Fetching current weather | Resource |
| Sending an email | Tool |
| Getting config values | Resource |
| Executing code | Tool |
| Agent's memory recall | Resource |
| Agent's memory storage | Tool |

**Rule of thumb**: If it only *reads* data and has no side effects → Resource.
If it *changes* state or *does* something → Tool.

---

## Resource Design Best Practices

| Practice | Reason |
|----------|--------|
| Use stable, predictable URIs | LLMs may reference URIs in subsequent messages |
| Include MIME types | Helps the host decide how to render/inject the content |
| Keep descriptions short (1-2 sentences) | LLM sees these in the resource list |
| Return structured formats (JSON, markdown) | More useful to LLM than raw bytes |
| Validate URI parameters | Prevent path traversal and injection attacks |
| Limit resource content size | Large responses waste context window tokens |
| Use subscriptions for frequently-changing data | Avoids polling loops by LLM |
