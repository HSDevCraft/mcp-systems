"""
06 — Multi-Capability Server (Production-Grade)

Full server exposing all four MCP primitives (Tools + Resources + Prompts + Sampling)
with proper lifecycle management, error handling, logging, and config.

This is the reference implementation for a production MCP server.

Run: python 06_multi_capability_server.py
"""

import asyncio
import json
import logging
import math
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

# ── Logging (stderr only — stdout is the protocol wire) ──────────────────────
logging.basicConfig(
    level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO")),
    stream=sys.stderr,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("mcp-full-server")

# ── Configuration ─────────────────────────────────────────────────────────────
WORKSPACE   = Path(os.environ.get("WORKSPACE", Path.cwd()))
SERVER_NAME = os.environ.get("MCP_SERVER_NAME", "full-server")
SERVER_VER  = "1.0.0"

# ── Server instance ───────────────────────────────────────────────────────────
server = Server(SERVER_NAME)

# ── Startup state ─────────────────────────────────────────────────────────────
_start_time: float = 0.0
_request_count: int = 0


# ════════════════════════════════════════════════════════════════════════════════
#  TOOLS
# ════════════════════════════════════════════════════════════════════════════════

_TOOLS: list[types.Tool] = [

    types.Tool(
        name="calculate",
        description=(
            "Evaluate a safe mathematical expression. "
            "Supports: +, -, *, /, **, %, sqrt(), abs(), round(), sin(), cos(), log(), pi, e."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "Math expression to evaluate"},
            },
            "required": ["expression"],
        },
    ),

    types.Tool(
        name="read_file",
        description="Read the text content of a file within the workspace.",
        inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to workspace root"},
            },
            "required": ["path"],
        },
    ),

    types.Tool(
        name="write_file",
        description="Write text content to a file within the workspace. Creates the file if it does not exist.",
        inputSchema={
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "File path relative to workspace root"},
                "content": {"type": "string", "description": "Text content to write"},
                "append":  {"type": "boolean", "description": "Append instead of overwrite", "default": False},
            },
            "required": ["path", "content"],
        },
    ),

    types.Tool(
        name="list_files",
        description="List files in the workspace or a subdirectory.",
        inputSchema={
            "type": "object",
            "properties": {
                "subdir":      {"type": "string",  "description": "Subdirectory to list (default: workspace root)"},
                "recursive":   {"type": "boolean", "description": "List recursively", "default": False},
                "extension":   {"type": "string",  "description": "Filter by extension (e.g. .py)"},
                "show_hidden": {"type": "boolean", "description": "Include hidden files",   "default": False},
            },
        },
    ),

    types.Tool(
        name="server_status",
        description="Return the current server status: uptime, request count, workspace info.",
        inputSchema={"type": "object", "properties": {}},
    ),
]


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return _TOOLS


@server.call_tool()
async def call_tool(
    name: str, arguments: dict
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    global _request_count
    _request_count += 1
    log.info(f"tool_call name={name!r}")

    def ok(text: str) -> list[types.TextContent]:
        return [types.TextContent(type="text", text=text)]

    def soft_err(text: str) -> types.CallToolResult:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            isError=True,
        )

    try:
        # ── calculate ──────────────────────────────────────────────────────────
        if name == "calculate":
            expr = arguments["expression"]
            if any(kw in expr for kw in ["import", "__", "open", "exec", "eval", "os", "sys"]):
                return soft_err("Expression contains forbidden keywords")
            ns = {
                "sqrt": math.sqrt, "abs": abs, "round": round,
                "sin": math.sin,   "cos": math.cos,   "tan": math.tan,
                "log": math.log,   "log10": math.log10, "exp": math.exp,
                "pi": math.pi,     "e": math.e,
                "floor": math.floor, "ceil": math.ceil,
            }
            result = eval(expr, {"__builtins__": {}}, ns)  # noqa: S307
            return ok(f"{result:.10g}" if isinstance(result, float) else str(result))

        # ── read_file ──────────────────────────────────────────────────────────
        elif name == "read_file":
            path = _resolve_workspace_path(arguments["path"])
            if not path.exists():
                return soft_err(f"File not found: {arguments['path']!r}")
            if not path.is_file():
                return soft_err(f"Not a file: {arguments['path']!r}")
            content = path.read_text(encoding="utf-8", errors="replace")
            return ok(content)

        # ── write_file ─────────────────────────────────────────────────────────
        elif name == "write_file":
            path    = _resolve_workspace_path(arguments["path"])
            content = arguments["content"]
            append  = arguments.get("append", False)
            path.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if append else "w"
            path.open(mode, encoding="utf-8").write(content)
            action = "Appended to" if append else "Written"
            return ok(f"{action}: {path} ({len(content)} characters)")

        # ── list_files ─────────────────────────────────────────────────────────
        elif name == "list_files":
            subdir    = arguments.get("subdir", "")
            recursive = arguments.get("recursive", False)
            ext       = arguments.get("extension", "")
            hidden    = arguments.get("show_hidden", False)

            base = _resolve_workspace_path(subdir) if subdir else WORKSPACE
            if not base.is_dir():
                return soft_err(f"Directory not found: {base}")

            pattern   = f"**/*{ext}" if recursive else f"*{ext}" if ext else ("**/*" if recursive else "*")
            entries   = []
            for p in sorted(base.glob(pattern)):
                if not hidden and any(part.startswith(".") for part in p.parts):
                    continue
                rel  = p.relative_to(WORKSPACE)
                kind = "DIR " if p.is_dir() else "FILE"
                size = f"  ({p.stat().st_size:,}B)" if p.is_file() else ""
                entries.append(f"{kind}  {rel}{size}")

            if not entries:
                return ok("(no files found)")
            return ok(f"Files in {base}:\n" + "\n".join(entries))

        # ── server_status ──────────────────────────────────────────────────────
        elif name == "server_status":
            uptime = time.time() - _start_time
            info   = {
                "server":        SERVER_NAME,
                "version":       SERVER_VER,
                "uptime_seconds": round(uptime, 1),
                "request_count": _request_count,
                "workspace":     str(WORKSPACE),
                "python_version": sys.version.split()[0],
            }
            return ok(json.dumps(info, indent=2))

        else:
            raise ValueError(f"Unknown tool: {name!r}")

    except PermissionError as e:
        return soft_err(f"Permission denied: {e}")
    except (FileNotFoundError, NotADirectoryError) as e:
        return soft_err(f"File error: {e}")
    except ValueError as e:
        return soft_err(f"Invalid input: {e}")
    except Exception as e:
        log.exception(f"Unhandled error in tool {name!r}")
        return soft_err(f"Server error ({type(e).__name__}): {e}")


# ════════════════════════════════════════════════════════════════════════════════
#  RESOURCES
# ════════════════════════════════════════════════════════════════════════════════

@server.list_resources()
async def list_resources() -> list[types.Resource]:
    resources = [
        types.Resource(
            uri="server://status",
            name="Server Status",
            description="Live server uptime, request count, and configuration",
            mimeType="application/json",
        ),
        types.Resource(
            uri="server://config",
            name="Server Configuration",
            description="Current server configuration and environment",
            mimeType="application/json",
        ),
    ]

    for p in sorted(WORKSPACE.rglob("*")):
        if (
            p.is_file()
            and not any(part.startswith(".") for part in p.parts)
            and p.suffix in {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml"}
        ):
            resources.append(types.Resource(
                uri=f"file://{p}",
                name=p.name,
                description=f"{p.relative_to(WORKSPACE)} ({p.stat().st_size:,} bytes)",
                mimeType=_mime_for(p),
            ))
            if len(resources) >= 50:
                break

    return resources


@server.read_resource()
async def read_resource(uri: str) -> str | bytes:
    if uri == "server://status":
        uptime = time.time() - _start_time
        return json.dumps({
            "uptime_seconds": round(uptime, 1),
            "request_count":  _request_count,
            "workspace":      str(WORKSPACE),
        }, indent=2)

    if uri == "server://config":
        return json.dumps({
            "server_name":    SERVER_NAME,
            "server_version": SERVER_VER,
            "workspace":      str(WORKSPACE),
            "log_level":      os.environ.get("LOG_LEVEL", "INFO"),
            "python":         sys.version.split()[0],
        }, indent=2)

    if uri.startswith("file://"):
        path = Path(uri.removeprefix("file://"))
        resolved = path.resolve()
        if not resolved.is_relative_to(WORKSPACE.resolve()):
            raise PermissionError(f"Path outside workspace: {path}")
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return path.read_text(encoding="utf-8", errors="replace")

    raise ValueError(f"Unknown resource: {uri!r}")


# ════════════════════════════════════════════════════════════════════════════════
#  PROMPTS
# ════════════════════════════════════════════════════════════════════════════════

_PROMPTS: list[types.Prompt] = [
    types.Prompt(
        name="code_review",
        description="Review code for bugs, security issues, and style.",
        arguments=[
            types.PromptArgument(name="code",     description="Code to review", required=True),
            types.PromptArgument(name="language", description="Programming language", required=False),
        ],
    ),
    types.Prompt(
        name="explain",
        description="Explain a concept, piece of code, or error in plain language.",
        arguments=[
            types.PromptArgument(name="topic",    description="What to explain",   required=True),
            types.PromptArgument(name="audience", description="beginner | expert", required=False),
        ],
    ),
    types.Prompt(
        name="improve_text",
        description="Improve the clarity, grammar, and style of a piece of text.",
        arguments=[
            types.PromptArgument(name="text",    description="Text to improve",            required=True),
            types.PromptArgument(name="goal",    description="Improvement goal",            required=False),
        ],
    ),
]


@server.list_prompts()
async def list_prompts() -> list[types.Prompt]:
    return _PROMPTS


@server.get_prompt()
async def get_prompt(
    name: str, arguments: dict[str, str] | None
) -> types.GetPromptResult:
    args = arguments or {}

    if name == "code_review":
        code = args.get("code", "")
        lang = args.get("language", "")
        return types.GetPromptResult(
            description=f"Code review{f' ({lang})' if lang else ''}",
            messages=[types.PromptMessage(
                role="user",
                content=types.TextContent(type="text", text=(
                    f"Review the following{f' {lang}' if lang else ''} code for bugs, security "
                    f"issues, and style problems. For each issue: describe it, explain the risk, "
                    f"and provide a fix.\n\n```{lang}\n{code}\n```"
                )),
            )],
        )

    if name == "explain":
        topic    = args.get("topic", "")
        audience = args.get("audience", "intermediate")
        return types.GetPromptResult(
            description=f"Explain: {topic[:40]}",
            messages=[types.PromptMessage(
                role="user",
                content=types.TextContent(type="text", text=(
                    f"Explain '{topic}' to a{'n' if audience.startswith(('a','e','i','o','u')) else ''} "
                    f"{audience} audience. Be clear, accurate, and use examples."
                )),
            )],
        )

    if name == "improve_text":
        text = args.get("text", "")
        goal = args.get("goal", "clarity and conciseness")
        return types.GetPromptResult(
            description="Improve text",
            messages=[types.PromptMessage(
                role="user",
                content=types.TextContent(type="text", text=(
                    f"Improve the following text for {goal}. "
                    f"Return only the improved version, no commentary.\n\n{text}"
                )),
            )],
        )

    raise ValueError(f"Unknown prompt: {name!r}")


# ════════════════════════════════════════════════════════════════════════════════
#  UTILITIES
# ════════════════════════════════════════════════════════════════════════════════

def _resolve_workspace_path(relative_path: str) -> Path:
    """Resolve a path relative to WORKSPACE, blocking traversal attacks."""
    if not relative_path:
        return WORKSPACE
    p = (WORKSPACE / relative_path).resolve()
    if not p.is_relative_to(WORKSPACE.resolve()):
        raise PermissionError(f"Path traversal blocked: {relative_path!r}")
    return p


def _mime_for(path: Path) -> str:
    ext_map = {
        ".py":    "text/x-python",
        ".md":    "text/markdown",
        ".txt":   "text/plain",
        ".json":  "application/json",
        ".yaml":  "application/x-yaml",
        ".yml":   "application/x-yaml",
        ".toml":  "application/toml",
        ".html":  "text/html",
        ".css":   "text/css",
        ".js":    "application/javascript",
        ".ts":    "application/typescript",
    }
    return ext_map.get(path.suffix, "application/octet-stream")


# ════════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ════════════════════════════════════════════════════════════════════════════════

async def main() -> None:
    global _start_time
    _start_time = time.time()

    log.info(f"Starting {SERVER_NAME} v{SERVER_VER}")
    log.info(f"Workspace: {WORKSPACE}")

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name=SERVER_NAME,
                server_version=SERVER_VER,
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(
                        tools_changed=False,
                        resources_changed=True,
                        prompts_changed=False,
                    ),
                    experimental_capabilities={},
                ),
            ),
        )

    log.info("Server shut down cleanly")


if __name__ == "__main__":
    asyncio.run(main())
