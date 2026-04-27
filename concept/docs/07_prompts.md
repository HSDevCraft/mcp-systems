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
