# 09 — Roots

**Roots** define the filesystem boundaries (or workspace boundaries) that the host makes available
to the server. They are a security mechanism: the server knows exactly which paths it is allowed to
access, and the client knows which paths to grant.

---

## What Roots Are

A root is simply a URI (typically `file://`) representing a workspace directory or resource scope
that the host has consented to share with a connected server.

```
Without roots:                     With roots:
Server can try to access           Server is told:
any path (permission denied        "You may access:
at OS level, but no guardrails).     /home/user/project
                                     /home/user/docs"
```

### Root structure
```jsonc
{
  "uri":  "file:///home/user/project",
  "name": "My Project"               // optional, human-readable
}
```

---

## How Roots Work

### Step 1 — Client declares `roots` capability
```jsonc
{
  "capabilities": {
    "roots": { "listChanged": true }
  }
}
```

### Step 2 — Server requests the roots list
```jsonc
// Server → Client
{ "jsonrpc": "2.0", "id": 1, "method": "roots/list" }

// Client → Server
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "roots": [
      { "uri": "file:///home/user/project",  "name": "My Project" },
      { "uri": "file:///home/user/documents", "name": "Documents" }
    ]
  }
}
```

### Step 3 — Server restricts access to declared roots
```python
from pathlib import Path

class FileServer:
    def __init__(self):
        self.allowed_roots: list[Path] = []

    async def on_roots_updated(self, roots: list[types.Root]) -> None:
        self.allowed_roots = [Path(r.uri.removeprefix("file://")) for r in roots]

    def is_path_allowed(self, path: str) -> bool:
        p = Path(path).resolve()
        return any(p.is_relative_to(root) for root in self.allowed_roots)
```

### Step 4 — Roots change notification (optional)
If roots change while the session is active, the client sends:
```jsonc
{
  "jsonrpc": "2.0",
  "method": "notifications/roots/list_changed"
}
```

The server should call `roots/list` again to refresh.

---

## Fetching Roots — Python SDK

### In a tool handler
```python
@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "list_files":
        # Fetch roots from the client
        roots_result = await server.request_context.session.list_roots()
        roots = roots_result.roots

        files = []
        for root in roots:
            root_path = Path(root.uri.removeprefix("file://"))
            if root_path.is_dir():
                for p in root_path.rglob("*"):
                    if p.is_file():
                        files.append(str(p))

        return [types.TextContent(type="text", text="\n".join(files[:100]))]
```

### Reacting to root changes
```python
@server.list_roots_changed()
async def on_roots_changed() -> None:
    roots_result = await server.request_context.session.list_roots()
    update_allowed_paths(roots_result.roots)
```

---

## Roots in Practice

### Use case 1: File editing server
```python
async def validate_path(path: str, session) -> Path:
    """Ensure path is within declared roots."""
    roots_result = await session.list_roots()
    allowed = [Path(r.uri.removeprefix("file://")) for r in roots_result.roots]
    p = Path(path).resolve()
    for root in allowed:
        if p.is_relative_to(root):
            return p
    raise PermissionError(f"Path {path} is outside of declared roots")

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "write_file":
        path = await validate_path(arguments["path"], server.request_context.session)
        path.write_text(arguments["content"])
        return [types.TextContent(type="text", text=f"Written: {path}")]
```

### Use case 2: Dynamic resource listing from roots
```python
@server.list_resources()
async def list_resources() -> list[types.Resource]:
    try:
        roots_result = await server.request_context.session.list_roots()
    except Exception:
        return []  # no roots available

    resources = []
    for root in roots_result.roots:
        root_path = Path(root.uri.removeprefix("file://"))
        for p in root_path.rglob("*.py"):
            resources.append(types.Resource(
                uri=f"file://{p}",
                name=p.name,
                mimeType="text/x-python",
            ))
    return resources
```

### Use case 3: Watching roots for changes
```python
import asyncio
from watchfiles import awatch

async def watch_roots(server, session):
    roots_result = await session.list_roots()
    watch_dirs = [r.uri.removeprefix("file://") for r in roots_result.roots]

    async for changes in awatch(*watch_dirs):
        for change_type, path in changes:
            # Notify clients that this resource has changed
            await session.send_resource_updated(f"file://{path}")
            # If new file added/deleted, also send list_changed
            await session.send_resource_list_changed()
```

---

## Non-Filesystem Roots

Roots don't have to be file paths. Any URI scheme works:

```jsonc
// A server that accesses a database namespace
{ "uri": "db://mycompany/production", "name": "Production DB" }

// A server that accesses a git repo
{ "uri": "git://github.com/myorg/myrepo", "name": "My Repo" }

// A server with access to a specific Slack workspace
{ "uri": "slack://workspace/T01234567", "name": "Company Slack" }
```

The server defines the semantics of its URI scheme; roots just tell it which namespaces are
in scope.

---

## Security Model

```
HOST (trust anchor)
  ├── Decides which roots to expose per server
  ├── May require user confirmation before adding roots
  ├── Can revoke roots at any time (sends list_changed notification)
  └── Enforces OS permissions (server still needs filesystem access rights)

SERVER
  ├── MUST honour the declared roots
  ├── SHOULD refuse requests for paths outside roots
  ├── MUST fetch fresh roots after list_changed notification
  └── SHOULD NOT cache roots indefinitely
```

Key point: roots are **advisory** — they communicate intent. Actual enforcement still requires
OS-level permissions. A well-written server enforces both.

---

## Best Practices

| Practice | Reason |
|----------|--------|
| Always call `roots/list` before accessing paths | Roots may change at runtime |
| Validate every path against current roots | Defense in depth against path traversal |
| Handle `roots/list_changed` notifications | Roots can be added/removed live |
| Use `Path.resolve()` before comparing | Prevents symlink / `..` traversal attacks |
| Default to empty roots = read-only mode | Graceful degradation when no roots declared |
| Log all cross-root access attempts | Security audit trail |
