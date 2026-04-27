"""Echo module — simplest possible MCPModule implementation.

Returns the input text, optionally transformed. Primary use:
- Testing module registration and execution pipeline
- Demonstrating the minimal implementation required
- Smoke-testing API endpoints
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from src.modules.base import ExecutionContext, HealthStatus, MCPModule


class EchoInput(BaseModel):
    text: str = Field(..., description="Text to echo back", min_length=1, max_length=10000)
    uppercase: bool = Field(default=False, description="Return text uppercased")
    repeat: int = Field(default=1, description="Number of times to repeat", ge=1, le=10)
    prefix: str = Field(default="", description="Optional prefix to prepend")


class EchoOutput(BaseModel):
    text: str = Field(description="Output text")
    char_count: int = Field(description="Character count of output")
    word_count: int = Field(description="Word count of output")
    transformations: list[str] = Field(description="List of applied transformations")


class EchoModule(MCPModule):
    """Returns input text with optional transformations.

    This is the canonical minimal MCPModule implementation. It has no
    external dependencies and always succeeds (unless input fails validation),
    making it ideal for integration tests.
    """

    name = "echo"
    description = "Returns input text with optional transformations (uppercase, repeat, prefix)"
    version = "1.0.0"
    tags = ["utility", "testing", "debug"]
    input_schema = EchoInput
    output_schema = EchoOutput

    async def execute(self, input: EchoInput, ctx: ExecutionContext) -> EchoOutput:
        transformations: list[str] = []

        text = input.text

        if input.uppercase:
            text = text.upper()
            transformations.append("uppercase")

        if input.repeat > 1:
            text = (text + " ") * input.repeat
            text = text.rstrip()
            transformations.append(f"repeat:{input.repeat}")

        if input.prefix:
            text = f"{input.prefix}{text}"
            transformations.append("prefix")

        ctx.logger.info(
            "echo_executed",
            char_count=len(text),
            transformations=transformations,
        )

        return EchoOutput(
            text=text,
            char_count=len(text),
            word_count=len(text.split()),
            transformations=transformations,
        )

    async def health_check(self) -> HealthStatus:
        return HealthStatus(
            healthy=True,
            message="Echo module is always healthy",
        )
