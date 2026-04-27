"""Text Summarizer module — demonstrates LLM-backed module implementation."""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field

from src.modules.base import ExecutionContext, HealthStatus, MCPModule


class SummarizerInput(BaseModel):
    text: str = Field(..., description="Text to summarize", min_length=10)
    max_words: int = Field(default=100, description="Target summary word count", ge=10, le=500)
    style: Literal["bullet", "paragraph", "tldr"] = Field(
        default="paragraph", description="Summary output style"
    )
    language: str = Field(default="en", description="Output language (ISO 639-1)")


class SummarizerOutput(BaseModel):
    summary: str = Field(description="Generated summary")
    original_word_count: int = Field(description="Word count of input text")
    summary_word_count: int = Field(description="Word count of summary")
    compression_ratio: float = Field(description="Ratio of summary to original length")
    style: str = Field(description="Style used for summarization")


class SummarizerModule(MCPModule):
    """Summarizes text using configurable style.

    In production, this module calls an LLM. In this reference implementation,
    it uses a simple extractive summarization approach (no external deps) to
    keep the module runnable without API keys.

    To connect a real LLM, override _call_llm() or inject an LLM client
    via __init__.
    """

    name = "text-summarizer"
    description = "Summarizes text into bullet points, paragraphs, or TL;DR format"
    version = "1.0.0"
    tags = ["nlp", "text", "summarization", "llm"]
    input_schema = SummarizerInput
    output_schema = SummarizerOutput

    def __init__(self, llm_client: object | None = None) -> None:
        self._llm = llm_client

    async def on_load(self) -> None:
        if self._llm is not None and hasattr(self._llm, "health_check"):
            await self._llm.health_check()  # type: ignore[union-attr]

    async def execute(
        self, input: SummarizerInput, ctx: ExecutionContext
    ) -> SummarizerOutput:
        ctx.logger.info(
            "summarizer_started",
            input_words=len(input.text.split()),
            style=input.style,
        )

        if self._llm is not None:
            summary = await self._call_llm(input)
        else:
            summary = self._extractive_summarize(input)

        original_words = len(input.text.split())
        summary_words = len(summary.split())

        ctx.logger.info(
            "summarizer_complete",
            original_words=original_words,
            summary_words=summary_words,
        )

        return SummarizerOutput(
            summary=summary,
            original_word_count=original_words,
            summary_word_count=summary_words,
            compression_ratio=round(summary_words / max(original_words, 1), 3),
            style=input.style,
        )

    async def health_check(self) -> HealthStatus:
        if self._llm is not None and hasattr(self._llm, "health_check"):
            try:
                start = time.perf_counter()
                await self._llm.health_check()  # type: ignore[union-attr]
                latency = (time.perf_counter() - start) * 1000
                return HealthStatus(
                    healthy=True, message="LLM reachable", latency_ms=latency
                )
            except Exception as exc:
                return HealthStatus(healthy=False, message=str(exc))
        return HealthStatus(healthy=True, message="Running in extractive mode (no LLM)")

    async def _call_llm(self, input: SummarizerInput) -> str:
        """Delegate to injected LLM client."""
        prompt = self._build_prompt(input)
        return await self._llm.complete(prompt, max_tokens=input.max_words * 2)  # type: ignore[union-attr]

    def _build_prompt(self, input: SummarizerInput) -> str:
        style_map = {
            "bullet": "as 3-5 concise bullet points starting with •",
            "paragraph": f"as a concise paragraph of about {input.max_words} words",
            "tldr": "as a single sentence TL;DR",
        }
        return (
            f"Summarize the following text {style_map[input.style]}. "
            f"Respond in {input.language}.\n\n{input.text}"
        )

    def _extractive_summarize(self, input: SummarizerInput) -> str:
        """Simple extractive summarization without LLM (for testing/demo)."""
        sentences = [s.strip() for s in input.text.replace("\n", " ").split(".") if s.strip()]
        if not sentences:
            return input.text[: input.max_words * 6]

        target = max(1, input.max_words // 20)
        selected = sentences[:target]

        if input.style == "bullet":
            return "\n".join(f"• {s}." for s in selected)
        elif input.style == "tldr":
            return f"TL;DR: {selected[0]}."
        else:
            return ". ".join(selected) + "."
