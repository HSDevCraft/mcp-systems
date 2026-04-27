"""
03 — Resources Server

Demonstrates all resource patterns:
  - Static resources (fixed URIs)
  - Dynamic resources (URI templates)
  - Text and binary resources
  - Resource subscriptions (live updates)
  - Paginated resource lists

Run: python 03_resources_server.py
"""

import asyncio
import base64
import json
import mimetypes
import os
import sys
from pathlib import Path

import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

server = Server("resources-demo")

WORKSPACE = Path(os.environ.get("WORKSPACE", Path.cwd()))

# ── In-memory "database" for demo purposes ─────────────────────────────────────

USERS_DB: dict[str, dict] = {
    "1": {"id": "1", "name": "Alice Johnson",   "email": "alice@example.com",  "role": "admin",  "active": True},
    "2": {"id": "2", "name": "Bob Smith",        "email": "bob@example.com",    "role": "user",   "active": True},
    "3": {"id": "3", "name": "Carol Williams",   "email": "carol@example.com",  "role": "viewer", "active": False},
}

NOTES_DB: dict[str, dict] = {
    "note-1": {"id": "note-1", "title": "Meeting Notes",   "body": "Discussed Q4 roadmap. Action: update backlog.", "tags": ["work", "planning"]},
    "note-2": {"id": "note-2", "title": "Shopping List",   "body": "Milk, eggs, bread, coffee beans",               "tags": ["personal"]},
    "note-3": {"id": "note-3", "title": "Python Tips",     "body": "Use dataclasses for simple value objects. Prefer composition over inheritance.", "tags": ["tech", "python"]},
}

APP_CONFIG = {
    "environment":    "development",
    "version":        "2.1.0",
    "debug":          True,
    "max_file_size":  "10MB",
    "allowed_origins": ["http://localhost:3000", "https://app.example.com"],
    "features": {
        "dark_mode":      True,
        "notifications":  True,
        "beta_features":  False,
    },
}


# ── Resource listing ──────────────────────────────────────────────────────────

@server.list_resources()
async def list_resources() -> list[types.Resource]:
    resources: list[types.Resource] = []

    # Static resources
    resources.append(types.Resource(
        uri="config://app",
        name="Application Config",
        description="Current application configuration (JSON)",
        mimeType="application/json",
    ))
    resources.append(types.Resource(
        uri="stats://system",
        name="System Stats",
        description="Real-time CPU, memory, and disk statistics",
        mimeType="application/json",
    ))

    # Dynamic — users
    for user_id, user in USERS_DB.items():
        resources.append(types.Resource(
            uri=f"db://users/{user_id}",
            name=f"User: {user['name']}",
            description=f"User record for {user['email']} (role: {user['role']})",
            mimeType="application/json",
        ))

    # Dynamic — notes
    for note_id, note in NOTES_DB.items():
        resources.append(types.Resource(
            uri=f"notes://{note_id}",
            name=f"Note: {note['title']}",
            description=f"Tags: {', '.join(note['tags'])}",
            mimeType="text/markdown",
        ))

    # Workspace files (first 20 files)
    count = 0
    for p in sorted(WORKSPACE.rglob("*")):
        if p.is_file() and not any(part.startswith(".") for part in p.parts):
            mime, _ = mimetypes.guess_type(str(p))
            resources.append(types.Resource(
                uri=f"file://{p}",
                name=p.name,
                description=f"File: {p.relative_to(WORKSPACE)}",
                mimeType=mime or "application/octet-stream",
            ))
            count += 1
            if count >= 20:
                break

    return resources


# ── Resource reading ──────────────────────────────────────────────────────────

@server.read_resource()
async def read_resource(uri: str) -> str | bytes:

    # ── config://app ──────────────────────────────────────────────────────────
    if uri == "config://app":
        return json.dumps(APP_CONFIG, indent=2)

    # ── stats://system ────────────────────────────────────────────────────────
    if uri == "stats://system":
        import time
        stats: dict = {"timestamp": time.time()}
        try:
            import psutil
            stats["cpu_percent"]    = psutil.cpu_percent(interval=0.1)
            stats["memory_percent"] = psutil.virtual_memory().percent
            stats["disk_percent"]   = psutil.disk_usage("/").percent
        except ImportError:
            stats["note"] = "Install psutil for real stats"
            stats["cpu_percent"]    = 42.0
            stats["memory_percent"] = 67.0
            stats["disk_percent"]   = 55.0
        return json.dumps(stats, indent=2)

    # ── db://users/{id} ───────────────────────────────────────────────────────
    if uri.startswith("db://users/"):
        user_id = uri.removeprefix("db://users/")
        user = USERS_DB.get(user_id)
        if not user:
            raise ValueError(f"User not found: {user_id!r}")
        return json.dumps(user, indent=2)

    # ── notes://{id} ──────────────────────────────────────────────────────────
    if uri.startswith("notes://"):
        note_id = uri.removeprefix("notes://")
        note = NOTES_DB.get(note_id)
        if not note:
            raise ValueError(f"Note not found: {note_id!r}")
        return (
            f"# {note['title']}\n\n"
            f"**Tags:** {', '.join(note['tags'])}\n\n"
            f"{note['body']}"
        )

    # ── file://{path} ─────────────────────────────────────────────────────────
    if uri.startswith("file://"):
        path = Path(uri.removeprefix("file://"))

        # Security: must be within workspace
        try:
            resolved = path.resolve()
            workspace_resolved = WORKSPACE.resolve()
            if not resolved.is_relative_to(workspace_resolved):
                raise PermissionError(f"Access denied — path outside workspace: {path}")
        except (ValueError, RuntimeError):
            raise PermissionError(f"Invalid path: {path}")

        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        if not path.is_file():
            raise ValueError(f"Not a file: {path}")

        mime, _ = mimetypes.guess_type(str(path))
        is_text = mime is None or mime.startswith("text/") or mime in {
            "application/json", "application/xml", "application/javascript",
            "application/x-python", "application/yaml",
        }

        if is_text:
            return path.read_text(encoding="utf-8", errors="replace")
        else:
            return path.read_bytes()

    raise ValueError(f"Unknown resource URI: {uri!r}")


# ── Resource subscriptions ────────────────────────────────────────────────────

_subscriptions: set[str] = set()
_subscription_tasks: dict[str, asyncio.Task] = {}


@server.subscribe_resource()
async def subscribe_resource(uri: str) -> None:
    _subscriptions.add(uri)
    if uri == "stats://system" and uri not in _subscription_tasks:
        _subscription_tasks[uri] = asyncio.create_task(_watch_stats(uri))


@server.unsubscribe_resource()
async def unsubscribe_resource(uri: str) -> None:
    _subscriptions.discard(uri)
    task = _subscription_tasks.pop(uri, None)
    if task:
        task.cancel()


async def _watch_stats(uri: str) -> None:
    """Push stats updates every 5 seconds while subscribed."""
    while uri in _subscriptions:
        await asyncio.sleep(5)
        if uri in _subscriptions:
            ctx = server.request_context
            if ctx:
                await ctx.session.send_resource_updated(uri)


# ── Utility: update a note (simulates external mutation → subscription trigger) ─

def update_note(note_id: str, body: str) -> None:
    if note_id in NOTES_DB:
        NOTES_DB[note_id]["body"] = body
        # In a real server, notify subscribers here


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="resources-demo",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(resources_changed=True),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
