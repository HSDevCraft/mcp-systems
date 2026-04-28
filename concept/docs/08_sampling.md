# 08 — Sampling

**Sampling** is the most powerful and most unusual MCP primitive. It inverts the usual direction:
instead of the client calling the server, the **server asks the client to perform an LLM
completion**. This allows servers to implement agentic behaviours (multi-step reasoning, loops,
sub-agents) without having direct access to an LLM.

---

## Why Sampling Exists

```
Normal flow (Tools / Resources):
  Host ──► Client ──► Server   (host initiates)

Sampling flow:
  Server ──► Client ──► Host ──► LLM ──► Client ──► Server
  (server initiates an LLM call through the client)
```

Use cases:
- **Multi-step agents**: Server orchestrates a loop of LLM calls, tool calls, and decisions.
- **Sub-agents**: Server delegates a sub-task to the LLM and uses the result.
- **Summarisation within tools**: Server compresses context before returning to host.
- **Structured extraction**: Server asks LLM to parse raw text into JSON.
- **Reflection / critique**: Server asks LLM to verify its own previous output.

---

## Capability Declaration

Sampling requires the **client** to declare support during initialization:

```jsonc
// Client capabilities (must include "sampling")
{
  "capabilities": {
    "sampling": {}
  }
}
```

If the client does not declare `sampling`, the server MUST NOT make sampling requests.

---

## The `sampling/createMessage` Request

### Wire format (server → client)
```jsonc
{
  "jsonrpc": "2.0",
  "id": 5,
  "method": "sampling/createMessage",
  "params": {
    "messages": [
      {
        "role": "user",
        "content": {
          "type": "text",
          "text": "Extract all email addresses from the following text and return them as a JSON array:\n\nContact us at alice@example.com or bob@corp.io for support."
        }
      }
    ],
    "modelPreferences": {
      "hints": [
        { "name": "claude-3-5-sonnet" }
      ],
      "costPriority":         0.3,
      "speedPriority":        0.5,
      "intelligencePriority": 0.8
    },
    "systemPrompt": "You are a precise data extraction assistant. Always return valid JSON.",
    "includeContext": "none",      // "none" | "thisServer" | "allServers"
    "temperature": 0.0,
    "maxTokens": 256,
    "stopSequences": ["\n\n"],
    "metadata": { "task": "email-extraction" }
  }
}
```

### Response (client → server)
```jsonc
{
  "jsonrpc": "2.0",
  "id": 5,
  "result": {
    "role": "assistant",
    "content": {
      "type": "text",
      "text": "[\"alice@example.com\", \"bob@corp.io\"]"
    },
    "model":       "claude-3-5-sonnet-20241022",
    "stopReason":  "end_turn"    // "end_turn" | "stop_sequence" | "max_tokens"
  }
}
```

---

## Sampling Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `messages` | `SamplingMessage[]` | Conversation history to send to LLM |
| `modelPreferences` | `ModelPreferences?` | Hints for model selection |
| `systemPrompt` | `string?` | System prompt for this sampling call |
| `includeContext` | `string?` | Whether to include host's conversation context |
| `temperature` | `float?` | Randomness (0.0 = deterministic, 1.0 = creative) |
| `maxTokens` | `integer` | **Required.** Maximum tokens to generate |
| `stopSequences` | `string[]?` | Stop generation at these strings |
| `metadata` | `object?` | Arbitrary metadata passed through |

### `includeContext` values
| Value | Meaning |
|-------|---------|
| `"none"` | Only the provided `messages` — no host context |
| `"thisServer"` | Include recent messages involving this server |
| `"allServers"` | Include all recent conversation context |

### `ModelPreferences`
The server hints at what kind of model it wants, but the **host decides** which model to use:
```jsonc
{
  "hints": [
    { "name": "claude-3-5-sonnet" },   // preferred model names (in priority order)
    { "name": "claude-3-haiku" }       // fallback
  ],
  "costPriority":         0.0,  // 0=ignore cost,  1=minimize cost
  "speedPriority":        0.0,  // 0=ignore speed, 1=maximize speed
  "intelligencePriority": 1.0   // 0=ignore quality, 1=maximize quality
}
```

---

## Making Sampling Requests — Python SDK

### Low-level (`Server` with request context)
```python
from mcp.server import Server
from mcp.shared.context import RequestContext
import mcp.types as types

server = Server("agent-server")

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "extract_emails":
        text = arguments["text"]

        # Make a sampling request to the client's LLM
        result = await server.request_context.session.create_message(
            messages=[
                types.SamplingMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text=f"Extract all email addresses from this text as a JSON array:\n\n{text}",
                    ),
                )
            ],
            max_tokens=256,
            system_prompt="Return only valid JSON. No explanation.",
            temperature=0.0,
        )

        return [types.TextContent(type="text", text=result.content.text)]
```

### High-level (`FastMCP` with Context)
```python
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp import Context

mcp = FastMCP("agent-server")

@mcp.tool()
async def summarise_and_answer(document: str, question: str, ctx: Context) -> str:
    """Summarise a document then answer a question about it."""
    # Step 1: summarise
    summary_result = await ctx.sample(
        f"Summarise this document in 3 bullet points:\n\n{document}",
        max_tokens=200,
    )
    summary = summary_result.content.text

    # Step 2: answer based on summary
    answer_result = await ctx.sample(
        f"Summary:\n{summary}\n\nQuestion: {question}\n\nAnswer concisely:",
        max_tokens=150,
    )
    return answer_result.content.text
```

---

## Agentic Loop Pattern

The most powerful sampling pattern: server implements a ReAct-style loop.

```python
import json

@mcp.tool()
async def research_agent(topic: str, ctx: Context) -> str:
    """Run a multi-step research agent on the given topic."""
    history = []
    system = (
        "You are a research agent. When you need information, respond with:\n"
        "ACTION: search\nQUERY: <your search query>\n\n"
        "When you have enough information, respond with:\n"
        "FINAL: <your complete answer>"
    )

    history.append({
        "role": "user",
        "content": types.TextContent(type="text", text=f"Research this topic: {topic}"),
    })

    for step in range(5):  # max 5 iterations
        result = await ctx.sample(
            messages=history,
            system_prompt=system,
            max_tokens=512,
        )
        response_text = result.content.text
        history.append({"role": "assistant", "content": result.content})

        if response_text.startswith("FINAL:"):
            return response_text.removeprefix("FINAL:").strip()

        if "ACTION: search" in response_text:
            query_line = [l for l in response_text.split("\n") if l.startswith("QUERY:")]
            if query_line:
                query = query_line[0].removeprefix("QUERY:").strip()
                # Execute the search tool
                search_results = await do_search(query)
                history.append({
                    "role": "user",
                    "content": types.TextContent(
                        type="text",
                        text=f"Search results for '{query}':\n{search_results}",
                    ),
                })

    return "Research incomplete: exceeded maximum steps."
```

---

## Human-in-the-Loop

The MCP spec explicitly requires that **sampling requests go through the host for human approval**.
The host is responsible for:
1. Showing the proposed LLM call to the user (if configured)
2. Letting the user modify or reject the request
3. Actually making the LLM API call
4. Returning the result to the server

This means servers **cannot silently call the LLM** — there is always a human checkpoint.

```
Server ──► sampling/createMessage ──► Client ──► Host
                                                   │
                                              [Human sees:
                                               "Server wants to
                                                ask the LLM:
                                                '...'  [Allow] [Modify] [Deny]"]
                                                   │
                                              LLM API call
                                                   │
                                              response ──► Client ──► Server
```

---

## Security Considerations

- **Prompt injection**: Malicious content in tool results can hijack the sampling prompt. Always
  sanitise external data before including it in sampling messages.
- **Token budget**: Track tokens used across all sampling calls; implement a per-session budget.
- **Model selection**: The client decides the actual model; servers cannot force a specific model.
- **Loop prevention**: Always implement a maximum iteration count for agentic loops.
- **Sensitive data**: Never include API keys, passwords, or PII in sampling messages.

---

## Sampling vs. Direct LLM Access

| | Sampling (via MCP) | Direct LLM access |
|--|--------------------|--------------------|
| **Control** | Host controls model, cost | Server controls model |
| **Visibility** | Human can inspect/modify | Opaque to host |
| **Auth** | Uses host's API credentials | Server needs its own key |
| **Cost** | Billed to host | Billed to server operator |
| **Trustworthiness** | Human approved | Unchecked |
| **Best for** | Agentic tools in trusted hosts | Standalone agents |

---

## Token Budget Management

Track and enforce token usage across all sampling calls within a session:

```python
from contextvars import ContextVar
from mcp.server.fastmcp import FastMCP, Context

mcp = FastMCP("budget-aware-server")

# Per-session token budget
_tokens_used: ContextVar[int] = ContextVar("tokens_used", default=0)
SESSION_TOKEN_BUDGET = 10_000  # max tokens per session

async def sample_with_budget(
    ctx: Context,
    prompt: str,
    max_tokens: int,
    **kwargs,
):
    """Make a sampling call, tracking and enforcing token budget."""
    used = _tokens_used.get()
    remaining = SESSION_TOKEN_BUDGET - used

    if remaining <= 0:
        raise RuntimeError("Session token budget exhausted. Please start a new session.")

    # Cap max_tokens to remaining budget
    actual_max = min(max_tokens, remaining)

    result = await ctx.sample(prompt, max_tokens=actual_max, **kwargs)

    # Estimate tokens used (approximate; host returns model info but not token counts)
    estimated_tokens = len(prompt.split()) + len(result.content.text.split())
    _tokens_used.set(used + estimated_tokens)

    return result


@mcp.tool()
async def analyse_document(document: str, ctx: Context) -> str:
    """Analyse a document with token budget enforcement."""
    summary = await sample_with_budget(
        ctx,
        f"Summarise this document in 5 bullet points:\n\n{document}",
        max_tokens=500,
    )
    analysis = await sample_with_budget(
        ctx,
        f"Based on this summary, identify the 3 key risks:\n{summary.content.text}",
        max_tokens=300,
    )
    return f"**Summary:**\n{summary.content.text}\n\n**Key Risks:**\n{analysis.content.text}"
```

---

## Structured Output Extraction

Use sampling to reliably extract structured data from unstructured text:

```python
import json
from pydantic import BaseModel, ValidationError
from mcp.server.fastmcp import FastMCP, Context

mcp = FastMCP("structured-output")

class PersonInfo(BaseModel):
    name:  str
    email: str | None
    phone: str | None
    company: str | None

class ExtractedPeople(BaseModel):
    people: list[PersonInfo]

@mcp.tool()
async def extract_contact_info(text: str, ctx: Context) -> str:
    """
    Extract structured contact information from unstructured text.
    Returns a JSON array of people with name, email, phone, company fields.
    """
    system_prompt = (
        "You are a data extraction assistant. "
        "Extract contact information and return ONLY valid JSON matching this schema:\n"
        '{"people": [{"name": "...", "email": "...", "phone": "...", "company": "..."}]}\n'
        "Use null for missing fields. Never add explanation text."
    )

    for attempt in range(3):  # retry up to 3 times for valid JSON
        result = await ctx.sample(
            f"Extract all contact information from this text:\n\n{text}",
            system_prompt=system_prompt,
            max_tokens=1000,
            temperature=0.0,  # deterministic for structured extraction
        )
        try:
            data = json.loads(result.content.text)
            validated = ExtractedPeople(**data)
            return validated.model_dump_json(indent=2)
        except (json.JSONDecodeError, ValidationError) as e:
            if attempt == 2:
                return json.dumps({"error": f"Failed to extract after 3 attempts: {e}"})
            # Let LLM try again with the error as context
            text = f"{text}\n\n[Previous response was invalid JSON: {e}. Try again.]"

    return json.dumps({"people": []})
```

---

## Multi-Modal Sampling

Include images in sampling requests (requires multi-modal model support):

```python
import base64
from pathlib import Path
import mcp.types as types
from mcp.server import Server

server = Server("multimodal-server")

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "analyse_screenshot":
        image_path = arguments["image_path"]
        question   = arguments.get("question", "Describe what you see.")

        # Read and encode the image
        image_bytes = Path(image_path).read_bytes()
        image_b64   = base64.b64encode(image_bytes).decode()
        mime_type   = "image/png"  # or detect from extension

        # Send image in the sampling messages
        result = await server.request_context.session.create_message(
            messages=[
                types.SamplingMessage(
                    role="user",
                    content=types.ImageContent(
                        type="image",
                        data=image_b64,
                        mimeType=mime_type,
                    ),
                ),
                types.SamplingMessage(
                    role="user",
                    content=types.TextContent(
                        type="text",
                        text=question,
                    ),
                ),
            ],
            max_tokens=512,
            model_preferences=types.ModelPreferences(
                hints=[{"name": "claude-3-5-sonnet"}],  # needs vision capability
                intelligence_priority=0.8,
            ),
        )
        return [types.TextContent(type="text", text=result.content.text)]
```

---

## Cost Tracking Pattern

Track LLM costs across sampling calls for billing or budgeting:

```python
from dataclasses import dataclass, field
from contextvars import ContextVar

@dataclass
class SamplingCostTracker:
    calls: int = 0
    estimated_input_tokens:  int = 0
    estimated_output_tokens: int = 0

    @property
    def estimated_cost_usd(self) -> float:
        # Claude 3.5 Sonnet pricing (approximate, per million tokens)
        INPUT_COST_PER_MTK  = 3.00
        OUTPUT_COST_PER_MTK = 15.00
        return (
            self.estimated_input_tokens  * INPUT_COST_PER_MTK  / 1_000_000 +
            self.estimated_output_tokens * OUTPUT_COST_PER_MTK / 1_000_000
        )

_cost_tracker: ContextVar[SamplingCostTracker] = ContextVar(
    "cost_tracker",
    default=None,
)

async def tracked_sample(ctx: Context, prompt: str, max_tokens: int, **kwargs):
    tracker = _cost_tracker.get()
    if tracker is None:
        tracker = SamplingCostTracker()
        _cost_tracker.set(tracker)

    result = await ctx.sample(prompt, max_tokens=max_tokens, **kwargs)

    tracker.calls += 1
    tracker.estimated_input_tokens  += len(prompt.split()) * 4 // 3  # rough token estimate
    tracker.estimated_output_tokens += len(result.content.text.split()) * 4 // 3

    return result

@mcp.tool()
async def expensive_research(topic: str, ctx: Context) -> str:
    """Research a topic with cost tracking."""
    tracker = SamplingCostTracker()
    _cost_tracker.set(tracker)

    step1 = await tracked_sample(ctx, f"Key facts about {topic}:", max_tokens=500)
    step2 = await tracked_sample(ctx, f"Implications of: {step1.content.text}", max_tokens=500)

    cost_summary = (
        f"\n\n---\n"
        f"Sampling stats: {tracker.calls} calls, "
        f"~{tracker.estimated_input_tokens + tracker.estimated_output_tokens} total tokens, "
        f"~${tracker.estimated_cost_usd:.4f} USD"
    )
    return step2.content.text + cost_summary
```

---

## ReAct Pattern (Reason + Act)

A production-grade ReAct agent using structured tool dispatch:

```python
import json, re
from mcp.server.fastmcp import FastMCP, Context

mcp = FastMCP("react-agent")

REACT_SYSTEM = """You are a ReAct agent. For each step respond with exactly one of:

THOUGHT: <your reasoning about what to do next>
ACTION: <tool_name>
ACTION_INPUT: <JSON object with tool arguments>

Or when done:
THOUGHT: <final reasoning>
FINAL_ANSWER: <your complete answer>

Available tools: {tools}"""

@mcp.tool()
async def react_agent(question: str, ctx: Context) -> str:
    """
    Run a ReAct (Reason + Act) agent to answer a question.
    The agent iteratively thinks, selects tools, and acts until it has an answer.
    """
    available_tools = ["web_search", "read_file", "get_weather", "calculate"]
    system = REACT_SYSTEM.format(tools=", ".join(available_tools))

    conversation = [{"role": "user", "content": f"Question: {question}"}]

    for step in range(8):  # max 8 steps
        response = await ctx.sample(
            messages=conversation,
            system_prompt=system,
            max_tokens=512,
            temperature=0.1,
        )
        text = response.content.text
        conversation.append({"role": "assistant", "content": text})

        # Parse FINAL_ANSWER
        if "FINAL_ANSWER:" in text:
            return text.split("FINAL_ANSWER:", 1)[1].strip()

        # Parse ACTION
        action_match  = re.search(r"ACTION:\s*(\w+)", text)
        input_match   = re.search(r"ACTION_INPUT:\s*(\{.*?\})", text, re.DOTALL)

        if action_match and input_match:
            tool_name = action_match.group(1)
            try:
                tool_args = json.loads(input_match.group(1))
                tool_result = await ctx.call_tool(tool_name, tool_args)
                observation = tool_result.content[0].text if tool_result.content else "No result"
            except Exception as e:
                observation = f"Tool error: {e}"

            conversation.append({
                "role": "user",
                "content": f"OBSERVATION: {observation}",
            })
        else:
            # No parseable action — prompt for correction
            conversation.append({
                "role": "user",
                "content": "Please provide an ACTION and ACTION_INPUT, or a FINAL_ANSWER.",
            })

    return "Agent exceeded maximum steps without a final answer."
```

---

## Common Sampling Pitfalls

| Pitfall | Problem | Fix |
|---------|---------|-----|
| No max iteration limit | Infinite agent loop; runaway costs | Always set `for step in range(N)` |
| No token budget | Single tool call uses entire context | Implement per-session token budgets |
| Deterministic for creative tasks | Low quality output | Use `temperature=0.7` for creative; `0.0` for extraction |
| Including sensitive data in messages | PII/credential leakage | Sanitise inputs; strip secrets before sampling |
| Ignoring `stopReason: max_tokens` | Truncated output silently treated as complete | Check `result.stopReason` and handle truncation |
| Using sampling for simple string ops | Unnecessary LLM cost | Only use sampling for tasks requiring genuine language understanding |
| Not handling sampling not supported | Crash when client lacks `sampling` capability | Check `caps.sampling` before making sampling requests |

---

## Key Takeaways

- **Sampling inverts the flow** — the server asks the *host's* LLM to generate completions.
- The **host controls model selection** — servers only hint via `ModelPreferences`.
- **Human approval** is required before the LLM is called — servers cannot silently invoke LLMs.
- **Token budgets** prevent runaway costs in agentic loops.
- Use `temperature=0.0` for **structured extraction**; `0.7+` for **creative generation**.
- The **ReAct pattern** (Reason + Act) is the standard for multi-step agentic tool use via sampling.
- Always set a **maximum iteration count** in agentic loops to prevent infinite recursion.
