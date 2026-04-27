"""
01 — Hello World MCP Server (stdio)

The minimal possible MCP server. One tool, one resource, one prompt.
Run with: python 01_hello_world_server.py

Connect via Claude Desktop by adding to claude_desktop_config.json:
{
  "mcpServers": {
    "hello-world": {
      "command": "python",
      "args": ["/path/to/01_hello_world_server.py"]
    }
  }
}
"""

import asyncio
import sys

import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

# ── Create the server instance ────────────────────────────────────────────────
server = Server("hello-world")


# ── Tool handler ──────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="greet",
            description="Greet a person by name and return a friendly greeting message.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The name of the person to greet",
                    },
                    "formal": {
                        "type": "boolean",
                        "description": "Use formal greeting (default: false)",
                        "default": False,
                    },
                },
                "required": ["name"],
            },
        )
    ]


@server.call_tool()
async def call_tool(
    name: str, arguments: dict
) -> list[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    if name == "greet":
        person = arguments["name"]
        formal = arguments.get("formal", False)

        if formal:
            greeting = f"Good day, {person}. I trust you are well."
        else:
            greeting = f"Hello, {person}! Great to meet you!"

        return [types.TextContent(type="text", text=greeting)]

    raise ValueError(f"Unknown tool: {name!r}")


# ── Resource handler ──────────────────────────────────────────────────────────

@server.list_resources()
async def list_resources() -> list[types.Resource]:
    return [
        types.Resource(
            uri="info://server",
            name="Server Info",
            description="Basic information about this MCP server",
            mimeType="text/plain",
        )
    ]


@server.read_resource()
async def read_resource(uri: str) -> str:
    if uri == "info://server":
        return (
            "Hello World MCP Server\n"
            "Version: 1.0.0\n"
            "Tools: greet\n"
            "Resources: info://server\n"
            "Prompts: introduce\n"
        )
    raise ValueError(f"Unknown resource: {uri!r}")


# ── Prompt handler ────────────────────────────────────────────────────────────

@server.list_prompts()
async def list_prompts() -> list[types.Prompt]:
    return [
        types.Prompt(
            name="introduce",
            description="Generate a self-introduction for a given role.",
            arguments=[
                types.PromptArgument(
                    name="role",
                    description="Your professional role (e.g. software engineer, designer)",
                    required=True,
                ),
                types.PromptArgument(
                    name="years_experience",
                    description="Years of experience in this role",
                    required=False,
                ),
            ],
        )
    ]


@server.get_prompt()
async def get_prompt(
    name: str, arguments: dict[str, str] | None
) -> types.GetPromptResult:
    if name == "introduce":
        args   = arguments or {}
        role   = args.get("role", "professional")
        years  = args.get("years_experience", "")
        exp    = f" with {years} years of experience" if years else ""

        return types.GetPromptResult(
            description=f"Self-introduction for {role}",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text=(
                            f"Please write a brief, friendly self-introduction for a {role}{exp}. "
                            f"Keep it to 2-3 sentences. Make it sound natural and approachable."
                        ),
                    ),
                )
            ],
        )

    raise ValueError(f"Unknown prompt: {name!r}")


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="hello-world",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
