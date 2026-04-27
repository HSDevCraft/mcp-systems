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
