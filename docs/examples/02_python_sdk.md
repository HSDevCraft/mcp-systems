# MCP System — Python SDK Usage Examples

All examples use `httpx` for async HTTP. Install: `pip install httpx`.

---

## Client Setup

```python
import httpx
from uuid import uuid4

BASE_URL = "http://localhost:8000"
API_KEY  = "mcp_your_key_here"
HEADERS  = {"X-API-Key": API_KEY}

client = httpx.AsyncClient(base_url=BASE_URL, headers=HEADERS)
```

---

## Complete Workflow Example

```python
import asyncio
import httpx
from uuid import uuid4


async def mcp_demo():
    async with httpx.AsyncClient(
        base_url="http://localhost:8000",
        headers={"X-API-Key": "mcp_dev_key"},
        timeout=30.0,
    ) as client:

        session_id = str(uuid4())

        # ── 1. Create a context ──────────────────────────────────
        r = await client.post("/api/v1/contexts/", json={
            "session_id": session_id,
            "system_prompt": "You are a helpful assistant.",
            "metadata": {"user": "alice"}
        })
        r.raise_for_status()
        context_id = r.json()["data"]["id"]
        print(f"Context created: {context_id}")

        # ── 2. Append a user message ─────────────────────────────
        r = await client.put(f"/api/v1/contexts/{context_id}/messages", json={
            "role": "user",
            "content": "Summarise the benefits of MCP for me.",
        })
        r.raise_for_status()
        token_count = r.json()["data"]["context_token_count"]
        print(f"Message appended — context tokens: {token_count}")

        # ── 3. Execute summarizer module ─────────────────────────
        r = await client.post("/api/v1/modules/text-summarizer/execute", json={
            "input": {
                "text": (
                    "The Model Context Protocol standardises how AI models connect "
                    "to external tools. It eliminates bespoke integrations, reduces "
                    "maintenance cost, and enables plug-and-play capability discovery."
                ),
                "style": "bullet",
                "max_words": 30,
            },
            "context_id": context_id,
            "session_id": session_id,
        })
        r.raise_for_status()
        data = r.json()["data"]
        print(f"Summarizer result [{data['latency_ms']:.1f}ms]:")
        print(data["output"]["summary"])

        # ── 4. Store a preference to long-term memory ────────────
        r = await client.post("/api/v1/memory/store", json={
            "content": "Alice prefers bullet-point summaries.",
            "tier": "long_term",
            "session_id": session_id,
            "tags": ["preference"],
        })
        r.raise_for_status()
        memory_id = r.json()["data"]["memory_id"]
        print(f"Memory stored: {memory_id}")

        # ── 5. Retrieve relevant memory ──────────────────────────
        r = await client.post("/api/v1/memory/retrieve", json={
            "query": "user preferences for response format",
            "tier": "long_term",
            "k": 3,
        })
        r.raise_for_status()
        results = r.json()["data"]["results"]
        print(f"Memory retrieved: {len(results)} items")

        # ── 6. Fork context for A/B comparison ───────────────────
        r = await client.post(f"/api/v1/contexts/{context_id}/fork")
        r.raise_for_status()
        child_id = r.json()["data"]["child_id"]
        print(f"Forked → child context: {child_id}")

        # ── 7. Seal the parent context ───────────────────────────
        r = await client.post(f"/api/v1/contexts/{context_id}/seal")
        r.raise_for_status()
        print(f"Context sealed: {r.json()['data']['status']}")

        # ── 8. System health ──────────────────────────────────────
        r = await client.get("/health/")
        health = r.json()["data"]
        print(f"System health: {health['status']}")

asyncio.run(mcp_demo())
```

---

## Executing Modules

```python
async def execute_echo(client: httpx.AsyncClient, text: str) -> str:
    r = await client.post("/api/v1/modules/echo/execute", json={
        "input": {"text": text, "uppercase": True}
    })
    return r.json()["data"]["output"]["text"]


async def execute_summarizer(
    client: httpx.AsyncClient,
    text: str,
    style: str = "paragraph",
    max_words: int = 100,
) -> dict:
    r = await client.post("/api/v1/modules/text-summarizer/execute", json={
        "input": {"text": text, "style": style, "max_words": max_words}
    })
    return r.json()["data"]["output"]


async def search_memory(
    client: httpx.AsyncClient,
    query: str,
    session_id: str | None = None,
    k: int = 5,
) -> list[dict]:
    payload = {"query": query, "tier": "long_term", "k": k}
    if session_id:
        payload["session_id"] = session_id
    r = await client.post("/api/v1/memory/retrieve", json=payload)
    return r.json()["data"]["results"]
```

---

## Writing a Custom Module (Python)

```python
# my_module.py — drop into src/modules/plugins/ for auto-discovery

from pydantic import BaseModel, Field
from src.modules.base import ExecutionContext, HealthStatus, MCPModule


class WordCountInput(BaseModel):
    text: str = Field(..., description="Text to analyse")
    include_spaces: bool = Field(default=False)


class WordCountOutput(BaseModel):
    words: int
    characters: int
    sentences: int
    average_word_length: float


class WordCountModule(MCPModule):
    name = "word-count"
    description = "Analyses text and returns linguistic statistics"
    version = "1.0.0"
    tags = ["nlp", "analysis", "utility"]
    input_schema = WordCountInput
    output_schema = WordCountOutput

    async def execute(self, input: WordCountInput, ctx: ExecutionContext) -> WordCountOutput:
        text = input.text
        chars = len(text) if input.include_spaces else len(text.replace(" ", ""))
        words = text.split()
        sentences = [s.strip() for s in text.split(".") if s.strip()]

        ctx.logger.info("word_count_executed", word_count=len(words))
        return WordCountOutput(
            words=len(words),
            characters=chars,
            sentences=len(sentences),
            average_word_length=sum(len(w) for w in words) / max(len(words), 1),
        )

    async def health_check(self) -> HealthStatus:
        return HealthStatus(healthy=True, message="No external deps")
```

After dropping this file in `src/modules/plugins/` and restarting the server
(or calling `registry.discover()` in dev mode), the module is instantly available:

```bash
curl -X POST http://localhost:8000/api/v1/modules/word-count/execute \
  -H "X-API-Key: mcp_dev_key" \
  -H "Content-Type: application/json" \
  -d '{"input": {"text": "The quick brown fox jumps over the lazy dog."}}'
```

---

## Error Handling Pattern

```python
import httpx
from typing import Any


class MCPClientError(Exception):
    def __init__(self, status: int, code: str, detail: str) -> None:
        self.status  = status
        self.code    = code
        self.detail  = detail
        super().__init__(f"[{status}] {code}: {detail}")


async def safe_execute(
    client: httpx.AsyncClient,
    module_name: str,
    input_data: dict[str, Any],
) -> Any:
    """Execute a module with structured error handling."""
    try:
        r = await client.post(
            f"/api/v1/modules/{module_name}/execute",
            json={"input": input_data},
        )
        if r.status_code >= 400:
            body = r.json()
            raise MCPClientError(
                status=r.status_code,
                code=body.get("type", "unknown"),
                detail=body.get("detail", str(body)),
            )
        return r.json()["data"]

    except httpx.TimeoutException:
        raise MCPClientError(504, "client.timeout", "Request timed out")
    except httpx.ConnectError:
        raise MCPClientError(503, "client.connect_error", "Cannot reach MCP API")


# Usage:
# try:
#     result = await safe_execute(client, "echo", {"text": "hello"})
# except MCPClientError as e:
#     if e.status == 404:
#         print("Module not found")
#     elif e.status == 429:
#         print(f"Rate limited — retry after {e.detail}")
#     else:
#         raise
```

---

## Streaming Module Output (SSE)

When a module supports streaming (future feature):

```python
import httpx

async def stream_module(module_name: str, input_data: dict) -> None:
    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        async with client.stream(
            "GET",
            f"/api/v1/modules/{module_name}/stream",
            params=input_data,
            headers={"Accept": "text/event-stream"},
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    payload = line[6:]
                    if payload == "[DONE]":
                        break
                    print(payload, end="", flush=True)
```
