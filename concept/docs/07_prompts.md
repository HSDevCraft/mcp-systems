# 07 — Prompts

**Prompts** are reusable, parameterised message templates that a server exposes to the host.
Unlike Tools (called by the LLM) or Resources (read by the LLM), Prompts are invoked
**by the user or the host** — they are a way to package common interaction patterns and inject
them into the LLM context with a single command.

Think of them as slash commands (`/code-review`, `/summarise`, `/translate`) backed by server logic.

---

## Prompt Anatomy

```
Prompt
├── name         string      Unique identifier (e.g. "code_review")
├── description  string?     Human-readable purpose
└── arguments    Argument[]  Typed parameters the prompt accepts
        Argument
        ├── name        string   Parameter name
        ├── description string?  What this argument controls
        └── required    bool     Whether the argument must be supplied
```

### Wire format
```jsonc
{
  "name": "code_review",
  "description": "Perform a thorough code review of the provided code snippet.",
  "arguments": [
    {
      "name": "code",
      "description": "The code snippet to review",
      "required": true
    },
    {
      "name": "language",
      "description": "Programming language (e.g. python, typescript)",
      "required": false
    },
    {
      "name": "focus",
      "description": "Review focus: security | performance | style | all",
      "required": false
    }
  ]
}
```

---

## Prompt Result

When a client calls `prompts/get`, the server returns an expanded **list of messages** ready to be
injected into the LLM context:

```jsonc
{
  "description": "Code review for Python code",
  "messages": [
    {
      "role": "user",
      "content": {
        "type": "text",
        "text": "Please review the following Python code with a focus on security:\n\n```python\ndef login(username, password):\n    query = f\"SELECT * FROM users WHERE name='{username}'\"\n    ...\n```"
      }
    }
  ]
}
```

### Message roles
- `"user"` — message attributed to the human user
- `"assistant"` — message attributed to the AI assistant (for few-shot examples)

### Content types in messages
```jsonc
// Text
{ "type": "text", "text": "Review this code..." }

// Image (embed a screenshot in the prompt)
{ "type": "image", "data": "<base64>", "mimeType": "image/png" }

// Embedded resource (attach a file)
{
  "type": "resource",
  "resource": {
    "uri": "file:///workspace/app.py",
    "mimeType": "text/x-python",
    "text": "def login(...):\n    ..."
  }
}
```

---

## Defining Prompts — Python SDK

### Low-level API (`Server`)
```python
import mcp.types as types
from mcp.server import Server

server = Server("prompt-server")

@server.list_prompts()
async def list_prompts() -> list[types.Prompt]:
    return [
        types.Prompt(
            name="code_review",
            description="Perform a thorough code review",
            arguments=[
                types.PromptArgument(name="code",     description="Code to review",          required=True),
                types.PromptArgument(name="language", description="Programming language",     required=False),
                types.PromptArgument(name="focus",    description="security|performance|all", required=False),
            ],
        ),
        types.Prompt(
            name="translate",
            description="Translate text to another language",
            arguments=[
                types.PromptArgument(name="text",            description="Text to translate", required=True),
                types.PromptArgument(name="target_language", description="Target language",   required=True),
            ],
        ),
    ]

@server.get_prompt()
async def get_prompt(
    name: str, arguments: dict[str, str] | None
) -> types.GetPromptResult:
    args = arguments or {}

    if name == "code_review":
        code     = args.get("code", "")
        language = args.get("language", "")
        focus    = args.get("focus", "all")

        lang_hint = f" ({language})" if language else ""
        prompt_text = (
            f"Please perform a {focus} code review of the following{lang_hint} code.\n"
            f"Check for: bugs, security vulnerabilities, performance issues, and style problems.\n"
            f"For each issue found, explain the problem and suggest a fix.\n\n"
            f"```{language}\n{code}\n```"
        )
        return types.GetPromptResult(
            description=f"Code review (focus: {focus})",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text=prompt_text),
                )
            ],
        )

    if name == "translate":
        return types.GetPromptResult(
            description=f"Translate to {args.get('target_language', '?')}",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text=f"Translate the following text to {args['target_language']}.\n"
                             f"Preserve formatting and tone.\n\n{args['text']}",
                    ),
                )
            ],
        )

    raise ValueError(f"Unknown prompt: {name}")
```

### High-level API (`FastMCP`)
```python
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.prompts import base

mcp = FastMCP("prompt-server")

@mcp.prompt()
def code_review(code: str, language: str = "python", focus: str = "all") -> str:
    """Perform a thorough code review of the provided code snippet."""
    return (
        f"Please review this {language} code (focus: {focus}):\n\n"
        f"```{language}\n{code}\n```\n\n"
        f"Check for: correctness, security, performance, readability."
    )

@mcp.prompt()
def translate(text: str, target_language: str) -> str:
    """Translate text to the specified language."""
    return f"Translate to {target_language}, preserving tone and formatting:\n\n{text}"
```

---

## Multi-Turn Prompts (Few-Shot Examples)

Prompts can inject multiple messages (user + assistant pairs) to create few-shot examples:

```python
@server.get_prompt()
async def get_prompt(name: str, arguments: dict | None) -> types.GetPromptResult:
    if name == "sql_writer":
        schema  = arguments.get("schema", "")
        request = arguments.get("request", "")
        return types.GetPromptResult(
            description="SQL query writer with examples",
            messages=[
                # Few-shot example 1
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text="Get all active users"),
                ),
                types.PromptMessage(
                    role="assistant",
                    content=types.TextContent(
                        type="text",
                        text="```sql\nSELECT * FROM users WHERE active = TRUE;\n```",
                    ),
                ),
                # Few-shot example 2
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text="Count orders per customer"),
                ),
                types.PromptMessage(
                    role="assistant",
                    content=types.TextContent(
                        type="text",
                        text="```sql\nSELECT customer_id, COUNT(*) FROM orders GROUP BY customer_id;\n```",
                    ),
                ),
                # Actual request
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text=f"Schema:\n{schema}\n\nRequest: {request}",
                    ),
                ),
            ],
        )
```

---

## Embedding Resources in Prompts

Attach file contents directly into the prompt messages:

```python
from pathlib import Path

@server.get_prompt()
async def get_prompt(name: str, arguments: dict | None) -> types.GetPromptResult:
    if name == "review_file":
        path = arguments["path"]
        content = Path(path).read_text()
        return types.GetPromptResult(
            description=f"Review {path}",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.EmbeddedResource(
                        type="resource",
                        resource=types.TextResourceContents(
                            uri=f"file://{path}",
                            mimeType="text/plain",
                            text=content,
                        ),
                    ),
                ),
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text="Please review the above file for quality and correctness.",
                    ),
                ),
            ],
        )
```

---

## Argument Completion

Servers can provide auto-complete suggestions for prompt arguments:

```jsonc
// Client → Server
{
  "method": "completion/complete",
  "params": {
    "ref": { "type": "ref/prompt", "name": "translate" },
    "argument": { "name": "target_language", "value": "fr" }
  }
}

// Server → Client
{
  "result": {
    "completion": {
      "values": ["French", "Français"],
      "total": 2,
      "hasMore": false
    }
  }
}
```

```python
@server.complete()
async def complete(
    ref: types.PromptReference | types.ResourceReference,
    argument: types.CompletionArgument,
) -> types.Completion:
    if isinstance(ref, types.PromptReference) and ref.name == "translate":
        if argument.name == "target_language":
            languages = ["French", "Spanish", "German", "Japanese", "Chinese", "Arabic"]
            matches = [l for l in languages if l.lower().startswith(argument.value.lower())]
            return types.Completion(values=matches)
    return types.Completion(values=[])
```

---

## Prompt Design Best Practices

| Practice | Why |
|----------|-----|
| **Declare all variable parts as arguments** | Makes prompts reusable and testable |
| **Use `required=False` with sensible defaults** | Improves UX — users can call prompts with minimal input |
| **Include task-specific instructions in the template** | Prompts should be opinionated, not generic |
| **Combine with resources** | Embed relevant files/data directly; don't ask LLM to fetch them |
| **Provide few-shot examples** | Dramatically improves output quality for structured tasks |
| **Keep prompt names as slug-case** | `code_review` not `CodeReview` — consistent with slash command UX |
| **Write description for the UI** | This is shown to the *human user* in prompt lists |

---

## Dynamic Prompt Generation

Generate prompt content from databases, configurations, or user context:

```python
import mcp.types as types
from mcp.server import Server

server = Server("dynamic-prompts")

# Prompts loaded from a database at request time
@server.get_prompt()
async def get_prompt(name: str, arguments: dict | None) -> types.GetPromptResult:
    args = arguments or {}

    if name == "project_assistant":
        project_id = args.get("project_id", "")

        # Fetch project-specific context from DB
        async with get_db() as conn:
            project = await conn.fetchrow(
                "SELECT name, description, tech_stack, coding_style FROM projects WHERE id = $1",
                project_id,
            )
            recent_issues = await conn.fetch(
                "SELECT title, status FROM issues WHERE project_id = $1 ORDER BY created_at DESC LIMIT 5",
                project_id,
            )

        if not project:
            raise ValueError(f"Project not found: {project_id}")

        issues_text = "\n".join(
            f"- [{r['status']}] {r['title']}" for r in recent_issues
        )

        system_prompt = (
            f"You are an assistant for the **{project['name']}** project.\n"
            f"Description: {project['description']}\n"
            f"Tech stack: {project['tech_stack']}\n"
            f"Coding style: {project['coding_style']}\n\n"
            f"Recent issues:\n{issues_text}"
        )

        return types.GetPromptResult(
            description=f"Project assistant for {project['name']}",
            messages=[
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(type="text", text=system_prompt),
                )
            ],
        )


@server.list_prompts()
async def list_prompts() -> list[types.Prompt]:
    # Dynamically list prompts based on available projects
    async with get_db() as conn:
        projects = await conn.fetch("SELECT id, name FROM projects")

    return [
        types.Prompt(
            name="project_assistant",
            description="Context-aware assistant for a specific project",
            arguments=[
                types.PromptArgument(
                    name="project_id",
                    description=f"Project ID (available: {', '.join(str(p['id']) for p in projects)})",
                    required=True,
                )
            ],
        )
    ]
```

---

## Prompt Chaining

Build complex workflows by chaining multiple prompt invocations:

```python
@server.get_prompt()
async def get_prompt(name: str, arguments: dict | None) -> types.GetPromptResult:
    args = arguments or {}

    if name == "bug_investigation":
        error_message = args.get("error", "")
        code_context  = args.get("code", "")

        # Chain: hypothesis → reproduction → fix suggestion
        return types.GetPromptResult(
            description="Multi-step bug investigation workflow",
            messages=[
                # Step 1: Understand the error
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text=(
                            f"**Error message:**\n```\n{error_message}\n```\n\n"
                            f"**Code context:**\n```python\n{code_context}\n```\n\n"
                            "First, identify the most likely root cause. List 3 hypotheses."
                        ),
                    ),
                ),
                # Step 2: Reproduction (LLM fills this in)
                types.PromptMessage(
                    role="assistant",
                    content=types.TextContent(
                        type="text",
                        text=(
                            "Based on the error, here are the most likely root causes:\n"
                            "1. [Hypothesis 1]\n2. [Hypothesis 2]\n3. [Hypothesis 3]"
                        ),
                    ),
                ),
                # Step 3: Fix request
                types.PromptMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text=(
                            "For the most likely hypothesis, provide:\n"
                            "1. A minimal reproduction script\n"
                            "2. The exact fix with code\n"
                            "3. Unit test to prevent regression"
                        ),
                    ),
                ),
            ],
        )
```

---

## Prompt Versioning

Manage prompt evolution without breaking existing callers:

```python
@server.list_prompts()
async def list_prompts() -> list[types.Prompt]:
    return [
        # Current version
        types.Prompt(
            name="summarise",
            description="Summarise text (current version with structured output)",
            arguments=[
                types.PromptArgument(name="text", description="Text to summarise", required=True),
                types.PromptArgument(name="format", description="Output format: bullet|paragraph|json", required=False),
                types.PromptArgument(name="length", description="Target length: short|medium|long", required=False),
            ],
        ),
        # Legacy version — kept for backward compatibility
        types.Prompt(
            name="summarise_v1",
            description="[DEPRECATED] Use 'summarise' instead. Simple text summariser.",
            arguments=[
                types.PromptArgument(name="text", description="Text to summarise", required=True),
            ],
        ),
    ]

@server.get_prompt()
async def get_prompt(name: str, arguments: dict | None) -> types.GetPromptResult:
    args = arguments or {}

    if name == "summarise_v1":
        # Legacy — delegate to current implementation
        return await _summarise_impl(args["text"], fmt="paragraph", length="medium")

    if name == "summarise":
        fmt    = args.get("format", "bullet")
        length = args.get("length", "medium")
        return await _summarise_impl(args["text"], fmt=fmt, length=length)

    raise ValueError(f"Unknown prompt: {name}")
```

---

## Argument Completion — Extended Patterns

```python
@server.complete()
async def complete(
    ref: types.PromptReference | types.ResourceReference,
    argument: types.CompletionArgument,
) -> types.Completion:
    # Prompt argument completion
    if isinstance(ref, types.PromptReference):
        if ref.name == "code_review" and argument.name == "language":
            languages = [
                "python", "typescript", "javascript", "rust",
                "go", "java", "kotlin", "swift", "c++", "c#",
            ]
            prefix = argument.value.lower()
            matches = [l for l in languages if l.startswith(prefix)]
            return types.Completion(values=matches, hasMore=False)

        if ref.name == "project_assistant" and argument.name == "project_id":
            # Fetch from DB with prefix filter
            async with get_db() as conn:
                projects = await conn.fetch(
                    "SELECT id::text FROM projects WHERE id::text LIKE $1 LIMIT 10",
                    f"{argument.value}%",
                )
            return types.Completion(values=[p["id"] for p in projects])

    # Resource URI completion
    if isinstance(ref, types.ResourceReference):
        if ref.uri.startswith("file://"):
            prefix_path = ref.uri.removeprefix("file://")
            from pathlib import Path
            parent = Path(prefix_path).parent
            if parent.is_dir():
                matches = [
                    f"file://{p}" for p in parent.iterdir()
                    if str(p).startswith(prefix_path)
                ][:10]
                return types.Completion(values=matches)

    return types.Completion(values=[])
```

---

## Testing Prompts

```python
# tests/test_prompts.py
import pytest
import pytest_asyncio
from mcp.shared.memory import create_connected_server_and_client_session
from my_server import build_server

@pytest_asyncio.fixture
async def client():
    async with create_connected_server_and_client_session(build_server()) as session:
        yield session

@pytest.mark.asyncio
async def test_list_prompts_not_empty(client):
    result = await client.list_prompts()
    assert len(result.prompts) > 0

@pytest.mark.asyncio
async def test_code_review_prompt_structure(client):
    result = await client.get_prompt(
        "code_review",
        {"code": "def add(a, b): return a+b", "language": "python", "focus": "security"},
    )
    assert result.messages, "Prompt must return at least one message"
    assert result.messages[0].role == "user"
    text = result.messages[0].content.text
    assert "python" in text.lower(), "Language must appear in prompt"
    assert "security" in text.lower(), "Focus must appear in prompt"

@pytest.mark.asyncio
async def test_prompt_with_missing_optional_args(client):
    # Optional args should have sensible defaults
    result = await client.get_prompt("code_review", {"code": "x = 1"})
    assert result.messages  # should work with only required arg

@pytest.mark.asyncio
async def test_prompt_missing_required_arg_raises(client):
    from mcp.shared.exceptions import McpError
    with pytest.raises((McpError, Exception)):
        await client.get_prompt("code_review", {})  # code is required
```

---

## Common Prompt Pitfalls

| Pitfall | Problem | Fix |
|---------|---------|-----|
| Hardcoding context in template | Template cannot adapt to different users/projects | Fetch context dynamically from DB/config |
| Arguments with no completion support | Poor UX; users must guess valid values | Implement `completion/complete` for all enum-like args |
| Too many arguments | Users overwhelmed; hard to invoke | Keep required args ≤ 3; use optional with defaults |
| Ignoring the `description` field on prompts | Host UI shows no guidance | Write user-facing descriptions (not LLM-facing) |
| Static prompt text | Quality degrades as use cases evolve | Version prompts; test output quality |
| Embedding secrets in prompt templates | Credential leakage in prompt log | Never embed API keys, tokens, or PII in prompt content |

---

## Key Takeaways

- **Prompts are invoked by the user/host**, not the LLM — they package common interaction patterns.
- They return **lists of messages** (with roles) ready to inject into the LLM conversation.
- **Dynamic generation** from databases enables project-specific, user-specific prompt personalisation.
- **Argument completion** (`completion/complete`) provides IDE-style autocomplete for prompt arguments.
- **Version prompts** explicitly when changing their structure, and keep old versions for backward compatibility.
- **Test prompts** like any other code — validate structure, argument handling, and output quality.
