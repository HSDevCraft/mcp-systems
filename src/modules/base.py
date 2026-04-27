"""Abstract base class for all MCP modules.

Every capability in the system — tools, resources, prompt builders —
is implemented as an MCPModule subclass. This enforces a consistent
interface that the ModuleRegistry, API layer, and observability stack
can rely on without knowing implementation details.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from src.core.types import ExecutionContext  # noqa: F401 — re-exported for modules


class HealthStatus(BaseModel):
    """Module health check result."""

    healthy: bool
    message: str = ""
    latency_ms: float | None = None
    details: dict[str, Any] = {}


class MCPModule(ABC):
    """Abstract base class for all MCP modules.

    Subclasses MUST define:
      - name: str                    — unique kebab-case identifier
      - description: str             — human-readable purpose
      - version: str                 — semver (default "1.0.0")
      - input_schema: type[BaseModel]
      - output_schema: type[BaseModel]

    Subclasses MUST implement:
      - execute(input, ctx) → output
      - health_check() → HealthStatus

    Lifecycle hooks are optional but strongly recommended:
      - on_load()        — called once at registration
      - on_unload()      — called on deregistration / shutdown
      - before_execute() — called before every execute()
      - after_execute()  — called after every successful execute()
      - on_error()       — called when execute() raises

    Example minimal implementation:

        class MyInput(BaseModel):
            text: str

        class MyOutput(BaseModel):
            result: str

        class MyModule(MCPModule):
            name = "my-module"
            description = "Does something useful"
            version = "1.0.0"
            tags = ["utility"]
            input_schema = MyInput
            output_schema = MyOutput

            async def execute(self, input, ctx):
                return MyOutput(result=input.text.upper())

            async def health_check(self):
                return HealthStatus(healthy=True)
    """

    name: str
    description: str
    version: str = "1.0.0"
    tags: list[str] = []
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Skip validation for abstract intermediaries
        if ABC in cls.__bases__:
            return

    @abstractmethod
    async def execute(
        self, input: BaseModel, ctx: ExecutionContext
    ) -> BaseModel:
        """Execute the module with validated input.

        This is the primary execution method. It receives a validated
        Pydantic model (type defined by input_schema) and the
        ExecutionContext for the current request.

        Args:
            input: Validated input model instance.
            ctx: Execution context with logger, metrics, working memory.

        Returns:
            Output model instance (type defined by output_schema).

        Raises:
            Any exception — will be caught by the registry and wrapped
            in ModuleExecutionError with the original traceback.
        """
        ...

    @abstractmethod
    async def health_check(self) -> HealthStatus:
        """Check module health.

        Called periodically by the registry to update health metrics.
        Should verify that all external dependencies (APIs, DBs) are reachable.

        Returns:
            HealthStatus with healthy=True/False and a descriptive message.
        """
        ...

    # ── Lifecycle Hooks (optional, override as needed) ─────────────────────

    async def on_load(self) -> None:
        """Called once when the module is registered.

        Use to: connect to external services, load model weights,
        validate configuration, warm caches.

        Raise an exception here to prevent registration (fail fast).
        """

    async def on_unload(self) -> None:
        """Called when the module is deregistered or the server shuts down.

        Use to: close HTTP clients, flush buffers, release locks.
        Should not raise — errors are logged and ignored.
        """

    async def before_execute(
        self, input: BaseModel, ctx: ExecutionContext
    ) -> None:
        """Called before every execute() invocation.

        Use to: additional input validation, auth checks, rate limiting,
        request logging with input values.

        Raise an exception to abort execution before the module runs.
        """

    async def after_execute(
        self, output: BaseModel, ctx: ExecutionContext
    ) -> None:
        """Called after every successful execute() invocation.

        Use to: emit custom metrics, write side effects (cache, DB),
        transform/enrich output before returning to caller.

        Exceptions here are logged but do NOT affect the response.
        """

    async def on_error(
        self, error: Exception, ctx: ExecutionContext
    ) -> None:
        """Called when execute() raises an exception.

        Use to: custom error logging, alerting, graceful degradation logic.
        The error is re-raised by the registry regardless of this hook's result.
        """

    def __repr__(self) -> str:
        return f"<MCPModule name={self.name!r} version={self.version!r}>"
