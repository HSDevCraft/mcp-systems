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

---

## Symlink Handling

Symlinks are a common path traversal vector. Always resolve paths before checking roots:

```python
from pathlib import Path

def validate_and_resolve(path_str: str, allowed_roots: list[Path]) -> Path:
    """
    Safely resolve a path, preventing symlink traversal attacks.
    Raises PermissionError if the resolved path is outside all allowed roots.
    """
    candidate = Path(path_str)

    # resolve() follows ALL symlinks and normalises '..'
    # This prevents:  /workspace/link -> /etc/passwd
    # And:            /workspace/../etc/shadow
    try:
        resolved = candidate.resolve(strict=False)  # strict=False: don't require existence
    except (OSError, RuntimeError) as e:
        raise PermissionError(f"Cannot resolve path: {path_str}: {e}")

    for root in allowed_roots:
        resolved_root = root.resolve()
        try:
            resolved.relative_to(resolved_root)  # raises ValueError if not under root
            return resolved  # path is within this root — allow
        except ValueError:
            continue

    raise PermissionError(
        f"Path '{resolved}' is outside all allowed roots: "
        f"{[str(r.resolve()) for r in allowed_roots]}"
    )


# Usage
@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "read_file":
        roots_result = await server.request_context.session.list_roots()
        allowed = [Path(r.uri.removeprefix("file://")) for r in roots_result.roots]

        try:
            safe_path = validate_and_resolve(arguments["path"], allowed)
        except PermissionError as e:
            return [types.TextContent(type="text", text=f"Access denied: {e}")]

        return [types.TextContent(type="text", text=safe_path.read_text())]
```

---

## Multi-Workspace Roots

Handling multiple workspace roots simultaneously:

```python
from pathlib import Path
from dataclasses import dataclass
import mcp.types as types
from mcp.server import Server

server = Server("multi-workspace")

@dataclass
class WorkspaceIndex:
    roots: list[Path]
    files: dict[str, Path]  # relative_path → absolute_path

    @classmethod
    async def build(cls, session) -> "WorkspaceIndex":
        roots_result = await session.list_roots()
        roots = [Path(r.uri.removeprefix("file://")) for r in roots_result.roots]

        files = {}
        for root in roots:
            if not root.is_dir():
                continue
            for p in root.rglob("*"):
                if p.is_file():
                    # Relative to root, prefixed with root name
                    rel = p.relative_to(root)
                    key = f"{root.name}/{rel}"
                    files[key] = p

        return cls(roots=roots, files=files)


@server.list_resources()
async def list_resources() -> list[types.Resource]:
    try:
        index = await WorkspaceIndex.build(server.request_context.session)
    except Exception:
        return []

    return [
        types.Resource(
            uri=f"file://{path}",
            name=key,
            description=f"File in workspace: {key}",
        )
        for key, path in index.files.items()
    ]


@server.read_resource()
async def read_resource(uri: str) -> str:
    roots_result = await server.request_context.session.list_roots()
    roots = [Path(r.uri.removeprefix("file://")) for r in roots_result.roots]

    path = validate_and_resolve(uri.removeprefix("file://"), roots)
    return path.read_text(encoding="utf-8", errors="replace")
```

---

## Root Scope Inheritance

Pattern for granting sub-scoped roots based on user permissions:

```python
from pathlib import Path

PERMISSION_MATRIX = {
    "read_only":  ["src/", "docs/"],
    "developer":  ["src/", "docs/", "tests/", "scripts/"],
    "admin":      ["src/", "docs/", "tests/", "scripts/", "config/", "secrets/"],
}

def filter_roots_by_permission(
    requested_roots: list[str],
    user_role: str,
    base_path: Path,
) -> list[Path]:
    """
    Filter requested roots to only those permitted for the user's role.
    A sub-administrator cannot grant themselves access beyond their own roots.
    """
    allowed_prefixes = PERMISSION_MATRIX.get(user_role, [])
    allowed_abs = [base_path / prefix for prefix in allowed_prefixes]

    granted = []
    for requested in requested_roots:
        req_path = (base_path / requested).resolve()
        for allowed in allowed_abs:
            allowed_resolved = allowed.resolve()
            try:
                req_path.relative_to(allowed_resolved)
                granted.append(req_path)
                break
            except ValueError:
                continue

    return granted
```

---

## Caching Roots

Roots are fetched on demand. Cache them to avoid repeated RPC calls:

```python
import asyncio
from pathlib import Path

class CachedRootsManager:
    def __init__(self, ttl_seconds: float = 5.0):
        self._roots: list[Path] | None = None
        self._fetched_at: float | None = None
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()

    async def get_roots(self, session) -> list[Path]:
        import time
        async with self._lock:
            now = time.monotonic()
            if (
                self._roots is None
                or self._fetched_at is None
                or now - self._fetched_at > self._ttl
            ):
                result = await session.list_roots()
                self._roots = [Path(r.uri.removeprefix("file://")) for r in result.roots]
                self._fetched_at = now
        return self._roots

    def invalidate(self) -> None:
        """Call when roots/list_changed notification is received."""
        self._roots = None
        self._fetched_at = None


_roots_cache = CachedRootsManager(ttl_seconds=5.0)

@server.list_roots_changed()
async def on_roots_changed() -> None:
    _roots_cache.invalidate()  # Force re-fetch on next access
```

---

## Non-File Root Patterns

Roots beyond the filesystem:

```python
# Database namespace root
# { "uri": "db://production/public", "name": "Production Public Schema" }
#
# Server interprets: only access tables in the "public" schema
# of the "production" database

@server.read_resource()
async def read_resource(uri: str) -> str:
    if uri.startswith("db://"):
        # Parse: db://{db_name}/{schema_name}/{table_name}
        parts = uri.removeprefix("db://").split("/")
        db_name, schema, table = parts[0], parts[1], parts[2] if len(parts) > 2 else None

        # Validate against declared roots
        roots_result = await server.request_context.session.list_roots()
        allowed_namespaces = [
            r.uri.removeprefix("db://")
            for r in roots_result.roots
            if r.uri.startswith("db://")
        ]

        namespace = f"{db_name}/{schema}"
        if not any(namespace.startswith(ns) for ns in allowed_namespaces):
            raise PermissionError(f"DB namespace {namespace!r} not in allowed roots")

        if table:
            async with get_db(db_name) as conn:
                rows = await conn.fetch(f"SELECT * FROM {schema}.{table} LIMIT 100")
            return json.dumps([dict(r) for r in rows], default=str)

    raise ValueError(f"Unsupported URI scheme: {uri}")
```

---

## Common Roots Pitfalls

| Pitfall | Problem | Fix |
|---------|---------|-----|
| Not resolving symlinks | Symlink traversal to `/etc/passwd` | Always use `Path.resolve()` |
| Caching roots forever | Stale roots after user removes workspace | Cache with short TTL; invalidate on `list_changed` |
| Not calling `roots/list` in resources handler | Lists inaccessible resources | Always fetch fresh roots in `list_resources()` |
| Using `startswith` instead of `relative_to` | Path prefix collision (`/a/b` matches `/a/bc`) | Use `Path.relative_to()` which is exact |
| Missing `strict=False` in `resolve()` | Raises `FileNotFoundError` for new files | Use `resolve(strict=False)` to allow non-existent targets |
| No fallback when no roots declared | Server crashes on first tool call | Default to `[]` and return empty/restricted results |

---

## Key Takeaways

- **Roots are advisory** — they communicate intent, not enforcement. The server must enforce them actively.
- **Always call `roots/list` before accessing paths** — roots can change during a session.
- **Resolve symlinks** with `Path.resolve()` before any security check — symlinks are a traversal vector.
- **Handle `roots/list_changed`** by invalidating caches and re-fetching roots.
- **Non-file roots** work the same way — any URI scheme can be scoped via roots.
- **Default to empty roots = restricted mode** for graceful degradation.
