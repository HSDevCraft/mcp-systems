"""
05 — Sampling Server

Demonstrates all sampling patterns:
  - Basic LLM completion request from server to client
  - Agentic ReAct loop
  - Multi-step summarisation pipeline
  - Structured data extraction
  - Model preferences

The host/client MUST declare sampling capability for these tools to work.

Run: python 05_sampling_server.py
"""

import asyncio
import json
import re

import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

server = Server("sampling-demo")


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _sample(
    prompt: str,
    *,
    system: str | None = None,
    max_tokens: int = 512,
    temperature: float = 0.7,
    model_hint: str | None = None,
) -> str:
    """Make a sampling request to the client's LLM and return the text."""
    ctx = server.request_context
    if not ctx:
        raise RuntimeError("No request context — cannot make sampling request")

    messages = [
        types.SamplingMessage(
            role="user",
            content=types.TextContent(type="text", text=prompt),
        )
    ]

    model_prefs = None
    if model_hint:
        model_prefs = types.ModelPreferences(
            hints=[types.ModelHint(name=model_hint)],
            intelligencePriority=0.8,
            speedPriority=0.2,
        )

    result = await ctx.session.create_message(
        messages=messages,
        system_prompt=system,
        max_tokens=max_tokens,
        temperature=temperature,
        model_preferences=model_prefs,
        include_context="none",
    )

    if hasattr(result.content, "text"):
        return result.content.text
    return str(result.content)


async def _sample_conversation(
    messages: list[tuple[str, str]],
    *,
    system: str | None = None,
    max_tokens: int = 512,
) -> str:
    """Make a multi-turn sampling request."""
    ctx = server.request_context
    sampling_messages = [
        types.SamplingMessage(
            role=role,
            content=types.TextContent(type="text", text=text),
        )
        for role, text in messages
    ]
    result = await ctx.session.create_message(
        messages=sampling_messages,
        system_prompt=system,
        max_tokens=max_tokens,
    )
    if hasattr(result.content, "text"):
        return result.content.text
    return str(result.content)


# ── Tools ─────────────────────────────────────────────────────────────────────

TOOLS = [
    types.Tool(
        name="smart_summarise",
        description=(
            "Summarise a long text using the host's LLM. "
            "Better than static prompts because the server controls the summarisation logic."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "text":   {"type": "string",  "description": "Text to summarise"},
                "points": {"type": "integer", "description": "Number of bullet points", "default": 5},
            },
            "required": ["text"],
        },
    ),
    types.Tool(
        name="extract_structured",
        description=(
            "Extract structured data (JSON) from unstructured text using the host's LLM. "
            "Returns a JSON object with the extracted fields."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "text":   {"type": "string", "description": "Unstructured input text"},
                "schema": {"type": "string", "description": "Description of fields to extract (plain English)"},
            },
            "required": ["text", "schema"],
        },
    ),
    types.Tool(
        name="classify",
        description=(
            "Classify text into one of the provided categories using the host's LLM."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "text":       {"type": "string",   "description": "Text to classify"},
                "categories": {
                    "type":  "array",
                    "items": {"type": "string"},
                    "description": "List of possible categories",
                },
                "multi_label": {"type": "boolean", "description": "Allow multiple labels", "default": False},
            },
            "required": ["text", "categories"],
        },
    ),
    types.Tool(
        name="iterative_improve",
        description=(
            "Iteratively improve a piece of text through multiple LLM refinement passes. "
            "Runs up to N rounds of critique + revision."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "text":   {"type": "string",  "description": "Initial text to improve"},
                "goal":   {"type": "string",  "description": "What improvement you want (e.g. 'make it more concise')"},
                "rounds": {"type": "integer", "description": "Number of refinement rounds (1-3)", "default": 2},
            },
            "required": ["text", "goal"],
        },
    ),
    types.Tool(
        name="research_agent",
        description=(
            "A simple ReAct-style research agent. The server orchestrates multiple LLM calls "
            "to answer a question by reasoning and (simulated) searching. "
            "Demonstrates agentic sampling loop."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "question":  {"type": "string",  "description": "The question to research and answer"},
                "max_steps": {"type": "integer", "description": "Maximum reasoning steps (1-5)", "default": 3},
            },
            "required": ["question"],
        },
    ),
    types.Tool(
        name="chain_of_thought",
        description=(
            "Solve a complex reasoning problem step-by-step using the host's LLM. "
            "First generates a chain of thought, then produces the final answer."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "problem": {"type": "string", "description": "The problem or question to solve"},
            },
            "required": ["problem"],
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return TOOLS


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

    # ── smart_summarise ────────────────────────────────────────────────────────
    if name == "smart_summarise":
        text   = arguments["text"]
        points = min(max(arguments.get("points", 5), 1), 20)

        result = await _sample(
            f"Summarise the following text in exactly {points} concise bullet points.\n"
            f"Each bullet should be 1-2 sentences. Start each with '•'.\n\n"
            f"{text}",
            system="You are a precise summarisation assistant. Follow the format exactly.",
            max_tokens=800,
            temperature=0.3,
        )
        return ok(result)

    # ── extract_structured ─────────────────────────────────────────────────────
    if name == "extract_structured":
        text   = arguments["text"]
        schema = arguments["schema"]

        raw = await _sample(
            f"Extract the following information from the text below.\n"
            f"Fields to extract: {schema}\n\n"
            f"Return ONLY a valid JSON object. No explanation. No markdown fences.\n\n"
            f"Text:\n{text}",
            system="You are a data extraction assistant. Return only valid JSON.",
            max_tokens=512,
            temperature=0.0,
            model_hint="claude-3-5-sonnet",
        )

        # Validate JSON
        try:
            parsed = json.loads(raw.strip())
            return ok(json.dumps(parsed, indent=2))
        except json.JSONDecodeError:
            # Try to extract JSON from the response
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group())
                    return ok(json.dumps(parsed, indent=2))
                except json.JSONDecodeError:
                    pass
            return ok(f"Extracted (raw):\n{raw}")

    # ── classify ───────────────────────────────────────────────────────────────
    if name == "classify":
        text       = arguments["text"]
        categories = arguments["categories"]
        multi      = arguments.get("multi_label", False)

        cats_list = "\n".join(f"- {c}" for c in categories)
        if multi:
            instruction = f"Classify the text into ALL applicable categories from the list below.\nReturn a JSON array of matching category names."
        else:
            instruction = f"Classify the text into EXACTLY ONE category from the list below.\nReturn a JSON object: {{\"category\": \"<chosen category>\", \"confidence\": 0.0-1.0, \"reason\": \"...\"}}"

        raw = await _sample(
            f"{instruction}\n\nCategories:\n{cats_list}\n\nText:\n{text}\n\nReturn only JSON.",
            system="You are a text classification assistant. Return only valid JSON.",
            max_tokens=256,
            temperature=0.0,
        )

        try:
            result = json.loads(raw.strip())
            return ok(json.dumps(result, indent=2))
        except json.JSONDecodeError:
            return ok(raw)

    # ── iterative_improve ──────────────────────────────────────────────────────
    if name == "iterative_improve":
        text   = arguments["text"]
        goal   = arguments["goal"]
        rounds = min(max(arguments.get("rounds", 2), 1), 3)

        current = text
        log     = [f"Original:\n{current}"]

        for i in range(rounds):
            # Step 1: Critique
            critique = await _sample(
                f"Critique this text. Goal: {goal}\n\n"
                f"Identify 3 specific improvements needed. Be concise.\n\n"
                f"Text:\n{current}",
                max_tokens=300,
                temperature=0.4,
            )

            # Step 2: Revise based on critique
            current = await _sample(
                f"Rewrite the text to address this critique:\n{critique}\n\n"
                f"Goal: {goal}\n\n"
                f"Original text:\n{current}\n\n"
                f"Return only the improved text, no commentary.",
                max_tokens=len(current.split()) * 3,
                temperature=0.5,
            )
            log.append(f"\nRound {i+1} critique:\n{critique}\n\nRevised:\n{current}")

        summary = (
            f"Improved text ({rounds} rounds):\n\n{current}\n\n"
            f"{'─' * 40}\n"
            f"Improvement log:\n{''.join(log)}"
        )
        return ok(summary)

    # ── research_agent ─────────────────────────────────────────────────────────
    if name == "research_agent":
        question  = arguments["question"]
        max_steps = min(max(arguments.get("max_steps", 3), 1), 5)

        system = (
            "You are a research assistant with access to an internal knowledge base.\n"
            "When you need to think through something, start with 'THOUGHT: <your reasoning>'\n"
            "When you have enough information, respond with 'ANSWER: <your complete answer>'\n"
            "Be concise and factual."
        )

        history: list[tuple[str, str]] = [("user", f"Research question: {question}")]
        steps_log = []

        for step in range(max_steps):
            response = await _sample_conversation(
                history,
                system=system,
                max_tokens=400,
            )
            history.append(("assistant", response))
            steps_log.append(f"Step {step + 1}:\n{response}")

            if response.startswith("ANSWER:"):
                answer = response.removeprefix("ANSWER:").strip()
                return ok(
                    f"Answer: {answer}\n\n"
                    f"{'─' * 40}\n"
                    f"Reasoning trace ({step + 1} steps):\n\n" +
                    "\n\n".join(steps_log)
                )

            # Continue reasoning
            history.append(("user", "Continue your research. What else do you know or need to find?"))

        # Fallback: ask for final answer
        final = await _sample_conversation(
            history + [("user", "Provide your best final answer now based on what you know.")],
            system=system,
            max_tokens=400,
        )
        return ok(
            f"Answer (max steps reached): {final}\n\n"
            f"{'─' * 40}\nReasoning:\n\n" + "\n\n".join(steps_log)
        )

    # ── chain_of_thought ───────────────────────────────────────────────────────
    if name == "chain_of_thought":
        problem = arguments["problem"]

        # Step 1: Generate chain of thought
        cot = await _sample(
            f"Let's think through this step by step.\n\nProblem: {problem}\n\n"
            f"Work through it carefully, showing your reasoning at each step.",
            max_tokens=600,
            temperature=0.3,
        )

        # Step 2: Extract the final answer
        answer = await _sample(
            f"Based on this reasoning:\n{cot}\n\n"
            f"What is the final, concise answer to: {problem}\n\n"
            f"Give only the answer, no additional reasoning.",
            max_tokens=200,
            temperature=0.0,
        )

        return ok(
            f"Final Answer: {answer}\n\n"
            f"{'─' * 40}\nReasoning:\n{cot}"
        )

    raise ValueError(f"Unknown tool: {name!r}")


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="sampling-demo",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
