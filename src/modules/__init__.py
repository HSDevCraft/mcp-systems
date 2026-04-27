"""Module/plugin system: base interface, lifecycle, loader, and built-in modules."""

from src.core.types import ExecutionContext  # canonical definition
from src.modules.base import HealthStatus, MCPModule

__all__ = ["MCPModule", "ExecutionContext", "HealthStatus"]
