"""Shared domain types used across core, modules, and tests.

Placed here to break the potential import cycle:
  src/core/orchestrator.py  ← needs ExecutionContext
  src/modules/base.py       ← needs ExecutionContext
  src/core/registry.py      ← imports from src/modules/base.py

By defining ExecutionContext here (in core/types.py, which imports only
from utils), both orchestrator and modules can import it without cycles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from src.utils.logger import get_logger
from src.utils.metrics import get_metrics


@dataclass
class ExecutionContext:
    """Runtime context passed to every module.execute() call and held by the Orchestrator.

    Modules use `ctx.logger` (not a module-level logger) to ensure all their log
    entries automatically carry the request_id, tenant_id, and user_id.

    Attributes:
        request_id:     Unique per API call — correlates logs across subsystems.
        context_id:     MCP context being operated on (None for stateless calls).
        session_id:     Session namespace for memory retrieval.
        user_id:        Authenticated user identifier.
        tenant_id:      Tenant namespace — used to scope all storage keys.
        metadata:       Arbitrary client-supplied key-value pairs.
        working_memory: In-request KV store — discarded after response.
        timeout:        Remaining execution budget in seconds.
        logger:         Pre-bound structlog BoundLogger.
        metrics:        MetricsClient for emitting custom Prometheus metrics.
    """

    request_id: UUID = field(default_factory=uuid4)
    context_id: UUID | None = None
    session_id: UUID | None = None
    user_id: str = "anonymous"
    tenant_id: str = "default"
    metadata: dict[str, Any] = field(default_factory=dict)
    working_memory: dict[str, Any] = field(default_factory=dict)
    timeout: float = 30.0
    logger: Any = field(
        default_factory=lambda: get_logger("execution_context")
    )
    metrics: Any = field(default_factory=get_metrics)

    def bind_log(self, **kwargs: Any) -> None:
        """Bind additional key-value pairs to the logger for this context."""
        self.logger = self.logger.bind(**kwargs)

    def set_working(self, key: str, value: Any) -> None:
        """Store a value in working (in-request) memory."""
        self.working_memory[key] = value

    def get_working(self, key: str, default: Any = None) -> Any:
        """Retrieve a value from working memory."""
        return self.working_memory.get(key, default)


@dataclass
class ModuleResult:
    """Result of a module execution returned by the Orchestrator."""

    module_name: str
    module_version: str
    output: Any
    latency_ms: float
    status: str            # "success" | "error" | "timeout"
    error: str | None = None
    request_id: UUID = field(default_factory=uuid4)
