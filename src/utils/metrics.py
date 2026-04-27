"""Prometheus metrics for the MCP System.

All metrics are registered once at module import and exposed via
the /metrics endpoint (or a dedicated port via prometheus_client HTTP server).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    Summary,
    CollectorRegistry,
    REGISTRY,
)

# ── Metric Definitions ────────────────────────────────────────────────────────

REQUEST_COUNT = Counter(
    "mcp_requests_total",
    "Total HTTP requests by endpoint and status",
    ["method", "endpoint", "status_code"],
)

REQUEST_LATENCY = Histogram(
    "mcp_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

ACTIVE_CONTEXTS = Gauge(
    "mcp_active_contexts_total",
    "Number of active (non-expired) contexts",
    ["tenant_id"],
)

CONTEXT_OPERATIONS = Counter(
    "mcp_context_operations_total",
    "Context lifecycle operations",
    ["operation", "status"],  # operation: create|get|append|fork|seal|expire
)

CONTEXT_TOKEN_USAGE = Histogram(
    "mcp_context_token_usage",
    "Token usage per context (at time of measurement)",
    buckets=[100, 500, 1000, 5000, 10000, 32000, 64000, 128000],
)

MEMORY_OPERATIONS = Counter(
    "mcp_memory_operations_total",
    "Memory read/write operations by tier",
    ["operation", "tier", "status"],  # tier: working|short_term|long_term
)

MEMORY_OPERATION_LATENCY = Histogram(
    "mcp_memory_operation_duration_seconds",
    "Memory operation latency by tier",
    ["operation", "tier"],
    buckets=[0.0001, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.5, 1.0],
)

MODULE_EXECUTIONS = Counter(
    "mcp_module_executions_total",
    "Module execution count by module name and status",
    ["module_name", "module_version", "status"],
)

MODULE_EXECUTION_LATENCY = Histogram(
    "mcp_module_execution_duration_seconds",
    "Module execution latency in seconds",
    ["module_name"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 15.0, 30.0, 60.0],
)

MODULE_HEALTH = Gauge(
    "mcp_module_health",
    "Module health status (1=healthy, 0=unhealthy)",
    ["module_name", "module_version"],
)

REGISTERED_MODULES = Gauge(
    "mcp_registered_modules_total",
    "Total number of registered modules",
)

AUTH_EVENTS = Counter(
    "mcp_auth_events_total",
    "Authentication events by type and result",
    ["auth_type", "result"],  # auth_type: jwt|api_key; result: success|failure
)

RATE_LIMIT_EVENTS = Counter(
    "mcp_rate_limit_events_total",
    "Rate limit events by result",
    ["result"],  # result: allowed|rejected
)

EMBEDDING_REQUESTS = Counter(
    "mcp_embedding_requests_total",
    "Embedding API calls by provider and status",
    ["provider", "status"],
)

EMBEDDING_LATENCY = Histogram(
    "mcp_embedding_duration_seconds",
    "Embedding request latency",
    ["provider"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)


# ── MetricsClient ─────────────────────────────────────────────────────────────


class MetricsClient:
    """High-level wrapper around raw Prometheus metrics.

    Provides ergonomic methods for common recording patterns so module
    code does not need to import Prometheus types directly.
    """

    def record_request(
        self, method: str, endpoint: str, status_code: int, latency_seconds: float
    ) -> None:
        REQUEST_COUNT.labels(
            method=method, endpoint=endpoint, status_code=str(status_code)
        ).inc()
        REQUEST_LATENCY.labels(method=method, endpoint=endpoint).observe(latency_seconds)

    def record_module_execution(
        self,
        module_name: str,
        module_version: str,
        status: str,
        latency_seconds: float,
    ) -> None:
        MODULE_EXECUTIONS.labels(
            module_name=module_name,
            module_version=module_version,
            status=status,
        ).inc()
        MODULE_EXECUTION_LATENCY.labels(module_name=module_name).observe(latency_seconds)

    def set_module_health(
        self, module_name: str, module_version: str, healthy: bool
    ) -> None:
        MODULE_HEALTH.labels(
            module_name=module_name, module_version=module_version
        ).set(1 if healthy else 0)

    def set_registered_modules(self, count: int) -> None:
        REGISTERED_MODULES.set(count)

    def record_memory_operation(
        self,
        operation: str,
        tier: str,
        status: str,
        latency_seconds: float,
    ) -> None:
        MEMORY_OPERATIONS.labels(
            operation=operation, tier=tier, status=status
        ).inc()
        MEMORY_OPERATION_LATENCY.labels(
            operation=operation, tier=tier
        ).observe(latency_seconds)

    def record_context_operation(self, operation: str, status: str) -> None:
        CONTEXT_OPERATIONS.labels(operation=operation, status=status).inc()

    def observe_context_tokens(self, token_count: int) -> None:
        CONTEXT_TOKEN_USAGE.observe(token_count)

    def set_active_contexts(self, tenant_id: str, count: int) -> None:
        ACTIVE_CONTEXTS.labels(tenant_id=tenant_id).set(count)

    def record_auth_event(self, auth_type: str, result: str) -> None:
        AUTH_EVENTS.labels(auth_type=auth_type, result=result).inc()

    def record_rate_limit_event(self, result: str) -> None:
        RATE_LIMIT_EVENTS.labels(result=result).inc()

    def record_embedding_request(
        self, provider: str, status: str, latency_seconds: float
    ) -> None:
        EMBEDDING_REQUESTS.labels(provider=provider, status=status).inc()
        EMBEDDING_LATENCY.labels(provider=provider).observe(latency_seconds)


@lru_cache(maxsize=1)
def get_metrics() -> MetricsClient:
    """Return the singleton MetricsClient."""
    return MetricsClient()
