"""
04 — Prompts Server

Demonstrates all prompt patterns:
  - Simple text prompts
  - Prompts with multiple arguments
  - Multi-turn / few-shot prompts
  - Prompts that embed resources
  - Argument completion

Run: python 04_prompts_server.py
"""

import asyncio
import json
from pathlib import Path

import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

server = Server("prompts-demo")


# ── Prompt catalogue ──────────────────────────────────────────────────────────

PROMPTS: dict[str, types.Prompt] = {

    "code_review": types.Prompt(
        name="code_review",
        description="Perform a detailed code review focusing on correctness, security, and style.",
        arguments=[
            types.PromptArgument(name="code",     description="The code snippet to review",                  required=True),
            types.PromptArgument(name="language", description="Programming language (e.g. python, go, rust)", required=False),
            types.PromptArgument(name="focus",    description="Review focus: all | security | performance | style", required=False),
        ],
    ),

    "translate": types.Prompt(
        name="translate",
        description="Translate text to a target language while preserving tone and formatting.",
        arguments=[
            types.PromptArgument(name="text",            description="Text to translate",   required=True),
            types.PromptArgument(name="target_language", description="Target language name", required=True),
            types.PromptArgument(name="formality",       description="formal | informal | neutral", required=False),
        ],
    ),

    "summarise": types.Prompt(
        name="summarise",
        description="Summarise a long piece of text into a concise, structured summary.",
        arguments=[
            types.PromptArgument(name="text",   description="Text to summarise",                           required=True),
            types.PromptArgument(name="format", description="Output format: bullets | paragraph | tldr",   required=False),
            types.PromptArgument(name="length", description="Target length: short | medium | long",        required=False),
        ],
    ),

    "write_tests": types.Prompt(
        name="write_tests",
        description="Generate comprehensive unit tests for a given function or class.",
        arguments=[
            types.PromptArgument(name="code",       description="Code to write tests for",    required=True),
            types.PromptArgument(name="framework",  description="Test framework: pytest | unittest | jest | mocha", required=False),
            types.PromptArgument(name="coverage",   description="Coverage target: happy_path | edge_cases | all",    required=False),
        ],
    ),

    "explain_code": types.Prompt(
        name="explain_code",
        description="Explain what a piece of code does in plain English.",
        arguments=[
            types.PromptArgument(name="code",     description="Code to explain",                   required=True),
            types.PromptArgument(name="audience", description="Target audience: beginner | intermediate | expert", required=False),
            types.PromptArgument(name="language", description="Programming language",               required=False),
        ],
    ),

    "sql_writer": types.Prompt(
        name="sql_writer",
        description="Write a SQL query based on a natural language request and database schema.",
        arguments=[
            types.PromptArgument(name="schema",  description="Database schema (CREATE TABLE statements or description)", required=True),
            types.PromptArgument(name="request", description="Natural language description of what you need",             required=True),
            types.PromptArgument(name="dialect", description="SQL dialect: postgresql | mysql | sqlite | mssql",          required=False),
        ],
    ),

    "commit_message": types.Prompt(
        name="commit_message",
        description="Generate a conventional commit message from a git diff.",
        arguments=[
            types.PromptArgument(name="diff",  description="Git diff output",    required=True),
            types.PromptArgument(name="style", description="Style: conventional | angular | simple", required=False),
        ],
    ),

    "debug_error": types.Prompt(
        name="debug_error",
        description="Diagnose an error message and suggest fixes.",
        arguments=[
            types.PromptArgument(name="error",   description="Full error message or stack trace", required=True),
            types.PromptArgument(name="context", description="What the code was trying to do",    required=False),
            types.PromptArgument(name="code",    description="Relevant code snippet (if any)",    required=False),
        ],
    ),
}

# Completion options for common arguments
LANGUAGE_OPTIONS = ["python", "typescript", "javascript", "go", "rust", "java", "c++", "c#", "ruby", "php"]
LANGUAGE_OPTIONS_DISPLAY = ["Python", "TypeScript", "JavaScript", "Go", "Rust", "Java", "C++", "C#", "Ruby", "PHP"]
LANGUAGE_MAP = dict(zip(LANGUAGE_OPTIONS, LANGUAGE_OPTIONS_DISPLAY))


# ── List prompts ──────────────────────────────────────────────────────────────

@server.list_prompts()
async def list_prompts() -> list[types.Prompt]:
    return list(PROMPTS.values())


# ── Get (expand) a prompt ──────────────────────────────────────────────────────

@server.get_prompt()
async def get_prompt(
    name: str, arguments: dict[str, str] | None
) -> types.GetPromptResult:
    args = arguments or {}

    # ── code_review ────────────────────────────────────────────────────────────
    if name == "code_review":
        code     = args.get("code", "")
        language = args.get("language", "")
        focus    = args.get("focus", "all")

        lang_hint = f" ({language})" if language else ""
        fence     = language or ""

        focus_map = {
            "security":    "security vulnerabilities and unsafe patterns",
            "performance": "performance bottlenecks and algorithmic inefficiencies",
            "style":       "code style, naming conventions, and readability",
            "all":         "correctness, security, performance, and style",
        }
        focus_desc = focus_map.get(focus, focus_map["all"])

        return types.GetPromptResult(
            description=f"Code review{lang_hint} (focus: {focus})",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text=(
                            f"Please review the following{lang_hint} code, focusing on {focus_desc}.\n\n"
                            f"For each issue:\n"
                            f"1. Describe the problem clearly\n"
                            f"2. Explain why it matters\n"
                            f"3. Provide a corrected code snippet\n\n"
                            f"```{fence}\n{code}\n```"
                        ),
                    ),
                )
            ],
        )

    # ── translate ──────────────────────────────────────────────────────────────
    if name == "translate":
        text     = args.get("text", "")
        target   = args.get("target_language", "English")
        formality = args.get("formality", "neutral")

        formality_map = {
            "formal":   "formal, professional register",
            "informal": "informal, conversational register",
            "neutral":  "neutral register",
        }
        register = formality_map.get(formality, formality_map["neutral"])

        return types.GetPromptResult(
            description=f"Translate to {target} ({formality})",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text=(
                            f"Translate the following text to {target}.\n"
                            f"Use a {register}.\n"
                            f"Preserve all formatting, paragraph structure, and line breaks.\n"
                            f"Do not add explanations — return only the translation.\n\n"
                            f"{text}"
                        ),
                    ),
                )
            ],
        )

    # ── summarise ──────────────────────────────────────────────────────────────
    if name == "summarise":
        text    = args.get("text", "")
        fmt     = args.get("format", "bullets")
        length  = args.get("length", "medium")

        format_map = {
            "bullets":   "a bulleted list of key points",
            "paragraph": "a single concise paragraph",
            "tldr":      "a TL;DR one-liner followed by 3 key points",
        }
        length_map = {
            "short":  "2-3 sentences or bullets",
            "medium": "5-7 sentences or bullets",
            "long":   "a comprehensive summary covering all major points",
        }

        return types.GetPromptResult(
            description=f"Summarise ({fmt}, {length})",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text=(
                            f"Summarise the following text as {format_map.get(fmt, 'bullets')}.\n"
                            f"Target length: {length_map.get(length, 'medium')}.\n"
                            f"Focus on the most important information.\n\n"
                            f"---\n{text}\n---"
                        ),
                    ),
                )
            ],
        )

    # ── write_tests ────────────────────────────────────────────────────────────
    if name == "write_tests":
        code      = args.get("code", "")
        framework = args.get("framework", "pytest")
        coverage  = args.get("coverage", "all")

        coverage_map = {
            "happy_path": "happy path and expected use cases only",
            "edge_cases": "edge cases, boundary conditions, and error cases",
            "all":        "happy path, edge cases, boundary conditions, and error handling",
        }

        # Few-shot example
        return types.GetPromptResult(
            description=f"Write {framework} tests (coverage: {coverage})",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text="Write tests for: def add(a: int, b: int) -> int: return a + b",
                    ),
                ),
                types.PromptMessage(
                    role="assistant",
                    content=types.TextContent(
                        type="text",
                        text=(
                            "```python\nimport pytest\n\n"
                            "def test_add_positive():\n    assert add(2, 3) == 5\n\n"
                            "def test_add_negative():\n    assert add(-1, -1) == -2\n\n"
                            "def test_add_zero():\n    assert add(0, 5) == 5\n```"
                        ),
                    ),
                ),
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text=(
                            f"Write comprehensive {framework} tests for the following code.\n"
                            f"Cover: {coverage_map.get(coverage, 'all')}.\n"
                            f"Include docstrings for each test explaining what it verifies.\n\n"
                            f"```\n{code}\n```"
                        ),
                    ),
                ),
            ],
        )

    # ── explain_code ───────────────────────────────────────────────────────────
    if name == "explain_code":
        code     = args.get("code", "")
        audience = args.get("audience", "intermediate")
        language = args.get("language", "")
        lang_hint = f" ({language})" if language else ""

        audience_map = {
            "beginner":     "someone new to programming — avoid jargon, use analogies",
            "intermediate": "a developer familiar with programming but not this specific code",
            "expert":       "an expert — be concise and use technical terminology freely",
        }

        return types.GetPromptResult(
            description=f"Explain{lang_hint} code (audience: {audience})",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text=(
                            f"Explain the following{lang_hint} code to {audience_map.get(audience, '')}.\n"
                            f"Cover: what it does, how it works, and any important design decisions.\n\n"
                            f"```{language}\n{code}\n```"
                        ),
                    ),
                )
            ],
        )

    # ── sql_writer ─────────────────────────────────────────────────────────────
    if name == "sql_writer":
        schema  = args.get("schema", "")
        request = args.get("request", "")
        dialect = args.get("dialect", "postgresql")

        return types.GetPromptResult(
            description=f"Write {dialect} SQL: {request[:40]}",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text=(
                            f"Database schema ({dialect}):\n```sql\n{schema}\n```\n\n"
                            f"Write a {dialect} SQL query to: {request}\n\n"
                            f"Requirements:\n"
                            f"- Use proper {dialect}-specific syntax\n"
                            f"- Add comments explaining non-obvious parts\n"
                            f"- Consider performance (indexes, JOINs)\n"
                            f"- Handle NULL values appropriately"
                        ),
                    ),
                )
            ],
        )

    # ── commit_message ─────────────────────────────────────────────────────────
    if name == "commit_message":
        diff  = args.get("diff", "")
        style = args.get("style", "conventional")

        style_instructions = {
            "conventional": (
                "Use the Conventional Commits format: <type>(<scope>): <description>\n"
                "Types: feat, fix, docs, style, refactor, test, chore\n"
                "Example: feat(auth): add OAuth2 login support"
            ),
            "angular":  "Use Angular commit format with type, scope, and description.",
            "simple":   "Write a clear, concise commit message in imperative mood.",
        }

        return types.GetPromptResult(
            description=f"Generate {style} commit message",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text=(
                            f"Generate a commit message for the following git diff.\n"
                            f"{style_instructions.get(style, style_instructions['conventional'])}\n"
                            f"Keep the subject line under 72 characters.\n"
                            f"If needed, add a body explaining the 'why'.\n\n"
                            f"```diff\n{diff}\n```"
                        ),
                    ),
                )
            ],
        )

    # ── debug_error ────────────────────────────────────────────────────────────
    if name == "debug_error":
        error   = args.get("error", "")
        context = args.get("context", "")
        code    = args.get("code", "")

        parts = [
            "Please help me debug this error.",
            f"\nError:\n```\n{error}\n```",
        ]
        if context:
            parts.append(f"\nContext: {context}")
        if code:
            parts.append(f"\nRelevant code:\n```\n{code}\n```")
        parts.append(
            "\nPlease:\n"
            "1. Explain what caused the error\n"
            "2. Provide a corrected code snippet\n"
            "3. Suggest how to prevent this in the future"
        )

        return types.GetPromptResult(
            description="Debug error",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text="\n".join(parts)),
                )
            ],
        )

    raise ValueError(f"Unknown prompt: {name!r}")


# ── Argument completion ───────────────────────────────────────────────────────

@server.complete()
async def complete(
    ref: types.PromptReference | types.ResourceReference,
    argument: types.CompletionArgument,
) -> types.Completion:
    if not isinstance(ref, types.PromptReference):
        return types.Completion(values=[])

    arg_name = argument.name
    partial  = argument.value.lower()

    if arg_name == "language":
        matches = [l for l in LANGUAGE_OPTIONS if l.startswith(partial)]
        return types.Completion(values=matches[:10])

    if arg_name == "target_language":
        languages = [
            "French", "Spanish", "German", "Italian", "Portuguese", "Dutch",
            "Russian", "Japanese", "Chinese", "Korean", "Arabic", "Hindi",
            "Swedish", "Norwegian", "Danish", "Polish", "Turkish",
        ]
        matches = [l for l in languages if l.lower().startswith(partial)]
        return types.Completion(values=matches[:10])

    if arg_name == "framework" and ref.name == "write_tests":
        options = ["pytest", "unittest", "jest", "mocha", "vitest", "jasmine", "rspec"]
        matches = [o for o in options if o.startswith(partial)]
        return types.Completion(values=matches)

    if arg_name == "dialect" and ref.name == "sql_writer":
        options = ["postgresql", "mysql", "sqlite", "mssql", "oracle"]
        matches = [o for o in options if o.startswith(partial)]
        return types.Completion(values=matches)

    return types.Completion(values=[])


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="prompts-demo",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
