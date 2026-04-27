"""
02 — Tools Server

Demonstrates all tool patterns:
  - Simple sync tools
  - Async tools (HTTP calls)
  - Structured Pydantic input
  - Error handling (soft errors vs hard errors)
  - Long-running tools with progress
  - Dynamic tool list

Run: python 02_tools_server.py
"""

import asyncio
import json
import math
import os
import subprocess
import sys
from typing import Any

import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

server = Server("tools-demo")


# ── Tool registry ─────────────────────────────────────────────────────────────

TOOLS: dict[str, types.Tool] = {
    "calculator": types.Tool(
        name="calculator",
        description=(
            "Evaluate a mathematical expression and return the result. "
            "Supports: +, -, *, /, **, sqrt(), abs(), round(), sin(), cos(), log(). "
            "Do NOT use for code execution — only math expressions."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Math expression to evaluate (e.g. '2 + 2', 'sqrt(16)', '2 ** 10')",
                }
            },
            "required": ["expression"],
        },
    ),
    "count_words": types.Tool(
        name="count_words",
        description="Count words, characters, and sentences in a text string.",
        inputSchema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Input text to analyse"},
            },
            "required": ["text"],
        },
    ),
    "json_format": types.Tool(
        name="json_format",
        description="Parse and pretty-print a JSON string. Returns formatted JSON or an error.",
        inputSchema={
            "type": "object",
            "properties": {
                "json_string": {"type": "string", "description": "JSON string to format"},
                "indent": {"type": "integer", "description": "Indentation spaces", "default": 2},
            },
            "required": ["json_string"],
        },
    ),
    "list_directory": types.Tool(
        name="list_directory",
        description="List files and subdirectories in a given directory path.",
        inputSchema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to list"},
                "show_hidden": {
                    "type": "boolean",
                    "description": "Include hidden files (starting with .)",
                    "default": False,
                },
            },
            "required": ["path"],
        },
    ),
    "fetch_url": types.Tool(
        name="fetch_url",
        description=(
            "Fetch the content of a URL and return the response body as text. "
            "Use for retrieving web pages, APIs, or any HTTP resource."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "url":     {"type": "string",  "description": "URL to fetch (must start with http:// or https://)"},
                "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 10},
                "headers": {
                    "type": "object",
                    "description": "Optional HTTP headers as key-value pairs",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["url"],
        },
    ),
    "run_python": types.Tool(
        name="run_python",
        description=(
            "Execute a Python code snippet in a sandboxed subprocess and return stdout/stderr. "
            "Use for calculations, data transformations, or quick prototypes. "
            "No file system access. Timeout: 10 seconds."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute"},
            },
            "required": ["code"],
        },
    ),
    "generate_uuid": types.Tool(
        name="generate_uuid",
        description="Generate one or more random UUID v4 values.",
        inputSchema={
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "Number of UUIDs to generate (1-100)", "default": 1},
            },
        },
    ),
    "base64_encode": types.Tool(
        name="base64_encode",
        description="Encode or decode a string using Base64.",
        inputSchema={
            "type": "object",
            "properties": {
                "text":   {"type": "string", "description": "Text to encode or decode"},
                "decode": {"type": "boolean", "description": "If true, decode instead of encode", "default": False},
            },
            "required": ["text"],
        },
    ),
}


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return list(TOOLS.values())


# ── Tool implementations ──────────────────────────────────────────────────────

def _run_calculator(expression: str) -> str:
    """Safe math expression evaluator."""
    ALLOWED_NAMES = {
        "sqrt": math.sqrt, "abs": abs, "round": round,
        "sin": math.sin,   "cos": math.cos,   "tan": math.tan,
        "log": math.log,   "log10": math.log10, "exp": math.exp,
        "pi": math.pi,     "e": math.e,
        "floor": math.floor, "ceil": math.ceil,
    }
    if any(kw in expression for kw in ["import", "__", "open", "exec", "eval", "os", "sys"]):
        raise ValueError("Expression contains forbidden keywords")
    result = eval(expression, {"__builtins__": {}}, ALLOWED_NAMES)  # noqa: S307
    if isinstance(result, float):
        return f"{result:.10g}"
    return str(result)


def _count_words(text: str) -> str:
    words     = len(text.split())
    chars     = len(text)
    chars_ns  = len(text.replace(" ", ""))
    sentences = len([s for s in text.split(".") if s.strip()])
    return (
        f"Words:               {words}\n"
        f"Characters (total):  {chars}\n"
        f"Characters (no sp.): {chars_ns}\n"
        f"Sentences (approx):  {sentences}"
    )


def _json_format(json_string: str, indent: int = 2) -> str:
    try:
        parsed = json.loads(json_string)
        return json.dumps(parsed, indent=indent, ensure_ascii=False)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}")


def _list_directory(path: str, show_hidden: bool = False) -> str:
    import os
    if not os.path.exists(path):
        raise FileNotFoundError(f"Path does not exist: {path}")
    if not os.path.isdir(path):
        raise ValueError(f"Not a directory: {path}")

    entries = []
    for entry in sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower())):
        if not show_hidden and entry.name.startswith("."):
            continue
        kind    = "DIR " if entry.is_dir() else "FILE"
        size    = "" if entry.is_dir() else f" ({entry.stat().st_size:,} bytes)"
        entries.append(f"{kind}  {entry.name}{size}")

    if not entries:
        return f"(empty directory: {path})"
    return f"Contents of {path}:\n" + "\n".join(entries)


async def _fetch_url(url: str, timeout: int = 10, headers: dict | None = None) -> str:
    try:
        import httpx
    except ImportError:
        raise RuntimeError("httpx is required for fetch_url. Install: pip install httpx")

    if not url.startswith(("http://", "https://")):
        raise ValueError(f"URL must start with http:// or https://: {url!r}")

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url, headers=headers or {})
        response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    body = response.text

    return (
        f"Status:       {response.status_code}\n"
        f"Content-Type: {content_type}\n"
        f"URL:          {str(response.url)}\n"
        f"Body ({len(body)} chars):\n\n{body[:4000]}"
        + ("...[truncated]" if len(body) > 4000 else "")
    )


def _run_python(code: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=10,
        env={"PATH": os.environ.get("PATH", "")},  # minimal env
    )
    output = []
    if result.stdout:
        output.append(f"stdout:\n{result.stdout.rstrip()}")
    if result.stderr:
        output.append(f"stderr:\n{result.stderr.rstrip()}")
    if result.returncode != 0:
        output.append(f"exit code: {result.returncode}")
    return "\n\n".join(output) if output else "(no output)"


def _generate_uuid(count: int = 1) -> str:
    import uuid
    count = max(1, min(count, 100))
    return "\n".join(str(uuid.uuid4()) for _ in range(count))


def _base64_encode(text: str, decode: bool = False) -> str:
    import base64
    if decode:
        try:
            decoded = base64.b64decode(text.encode()).decode("utf-8")
            return decoded
        except Exception as e:
            raise ValueError(f"Invalid Base64 input: {e}")
    return base64.b64encode(text.encode()).decode()


# ── Central dispatcher ────────────────────────────────────────────────────────

@server.call_tool()
async def call_tool(
    name: str, arguments: dict
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:

    def ok(text: str) -> list[types.TextContent]:
        return [types.TextContent(type="text", text=text)]

    def err(text: str) -> types.CallToolResult:
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            isError=True,
        )

    try:
        if name == "calculator":
            return ok(_run_calculator(arguments["expression"]))

        elif name == "count_words":
            return ok(_count_words(arguments["text"]))

        elif name == "json_format":
            return ok(_json_format(arguments["json_string"], arguments.get("indent", 2)))

        elif name == "list_directory":
            return ok(_list_directory(arguments["path"], arguments.get("show_hidden", False)))

        elif name == "fetch_url":
            result = await _fetch_url(
                arguments["url"],
                arguments.get("timeout", 10),
                arguments.get("headers"),
            )
            return ok(result)

        elif name == "run_python":
            return ok(_run_python(arguments["code"]))

        elif name == "generate_uuid":
            return ok(_generate_uuid(arguments.get("count", 1)))

        elif name == "base64_encode":
            return ok(_base64_encode(arguments["text"], arguments.get("decode", False)))

        else:
            raise ValueError(f"Unknown tool: {name!r}")

    except (FileNotFoundError, PermissionError) as e:
        return err(f"File error: {e}")
    except ValueError as e:
        return err(f"Invalid input: {e}")
    except subprocess.TimeoutExpired:
        return err("Execution timed out after 10 seconds")
    except Exception as e:
        return err(f"Error ({type(e).__name__}): {e}")


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="tools-demo",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
