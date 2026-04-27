# 07 — Observability

## Three Pillars

The MCP System ships with full observability out of the box:

| Pillar | Implementation | Where |
|--------|---------------|-------|
| **Logs** | structlog → JSON | stdout / log file |
| **Metrics** | Prometheus | `/metrics` (port 9091) |
| **Traces** | OpenTelemetry | OTLP exporter (opt-in) |

---

## Structured Logging

All logs are emitted as JSON lines (production) or pretty-printed text (dev).
Every entry carries mandatory context fields injected automatically.

### Log Entry Structure

```json
{
  "timestamp": "2025-01-15T10:23:45.123Z",
  "level": "info",
  "service": "mcp-system",
  "version": "0.1.0",
  "request_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "tenant_id": "acme-corp",
  "user_id": "user-123",
  "context_id": "a1b2c3d4-...",
  "module": "src.core.orchestrator",
  "event": "module_executed",
  "module_name": "echo",
  "status": "success",
  "latency_ms": 1.42
}
```

### Key Events Logged

| Event | Level | Component | Key Fields |
|-------|-------|-----------|-----------|
| `request_complete` | INFO/WARN | RequestLoggingMiddleware | method, path, status_code, latency_ms |
| `context_created` | INFO | ContextManager | context_id, session_id, tenant_id |
| `context_forked` | INFO | ContextManager | parent_id, child_id |
| `context_expired` | INFO | ContextManager | context_id |
| `context_overflow_evicted` | WARN | ContextManager | context_id, remaining_tokens |
| `module_registered` | INFO | ModuleRegistry | module_name, module_version |
| `module_executed` | INFO | Orchestrator | module_name, status, latency_ms |
| `module_execution_failed` | ERROR | Orchestrator | module_name, error |
| `memory_retrieve_degraded` | WARN | Orchestrator | error |
| `auth_rejected` | WARN | AuthMiddleware | path, method |
| `rate_limit_exceeded` | WARN | RateLimitMiddleware | tenant_id, current, limit |
| `redis_unavailable` | WARN | main | error |
| `qdrant_unavailable` | WARN | main | error |

### Log Level Guidelines

| Level | When to use |
|-------|------------|
| `DEBUG` | Fine-grained operation details (not emitted in production) |
| `INFO` | Normal lifecycle events (request complete, module executed) |
| `WARNING` | Degraded behaviour (Redis unavailable, memory retrieval fallback) |
| `ERROR` | Failures that affect the request but system continues |
| `CRITICAL` | Failures that may require restart |

### Configuring Log Output

```bash
# Production (JSON, stdout only)
LOG_LEVEL=WARNING
LOG_FORMAT=json

# Development (colored text)
LOG_LEVEL=DEBUG
LOG_FORMAT=text

# Log to file
LOG_FILE=/app/logs/mcp.log
```

---

## Prometheus Metrics

Exposed at `GET /metrics` (or port 9091 if `METRICS_PORT` is set).

### Full Metric Catalog

#### HTTP Layer
```
mcp_requests_total{method, endpoint, status_code}          counter
mcp_request_duration_seconds{method, endpoint}             histogram
  Buckets: 5ms 10ms 25ms 50ms 100ms 250ms 500ms 1s 2.5s 5s 10s
```

#### Context Management
```
mcp_active_contexts_total{tenant_id}                       gauge
mcp_context_operations_total{operation, status}            counter
  operations: create | get | append | fork | seal | expire
  status: success | error
mcp_context_token_usage                                    histogram
  Buckets: 100 500 1K 5K 10K 32K 64K 128K tokens
```

#### Memory System
```
mcp_memory_operations_total{operation, tier, status}       counter
  operations: read | write
  tiers: working | short_term | long_term
mcp_memory_operation_duration_seconds{operation, tier}     histogram
  Buckets: 0.1ms 1ms 5ms 10ms 25ms 50ms 100ms 500ms 1s
```

#### Module Execution
```
mcp_module_executions_total{module_name, module_version, status}  counter
  status: success | error | timeout
mcp_module_execution_duration_seconds{module_name}         histogram
  Buckets: 10ms 50ms 100ms 500ms 1s 5s 15s 30s 60s
mcp_module_health{module_name, module_version}             gauge
  1 = healthy, 0 = unhealthy
mcp_registered_modules_total                               gauge
```

#### Auth & Rate Limiting
```
mcp_auth_events_total{auth_type, result}                   counter
  auth_type: jwt | api_key | none
  result: success | failure
mcp_rate_limit_events_total{result}                        counter
  result: allowed | rejected
```

#### Embeddings
```
mcp_embedding_requests_total{provider, status}             counter
mcp_embedding_duration_seconds{provider}                   histogram
```

### Key Alerts (Prometheus rules)

```yaml
groups:
  - name: mcp-system
    rules:
      - alert: HighErrorRate
        expr: |
          sum(rate(mcp_requests_total{status_code=~"5.."}[5m])) /
          sum(rate(mcp_requests_total[5m])) > 0.05
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "MCP API error rate > 5%"

      - alert: HighModuleLatency
        expr: |
          histogram_quantile(0.95, rate(mcp_module_execution_duration_seconds_bucket[5m])) > 10
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Module p95 latency > 10s"

      - alert: RateLimitRejections
        expr: |
          sum(rate(mcp_rate_limit_events_total{result="rejected"}[1m])) > 10
        for: 1m
        labels:
          severity: warning

      - alert: ModuleUnhealthy
        expr: mcp_module_health == 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Module {{ $labels.module_name }} is unhealthy"
```

---

## OpenTelemetry Tracing (opt-in)

Enable with `OTEL_ENABLED=true` + `OTEL_ENDPOINT=http://jaeger:4317`.

### Spans Emitted

```
HTTP Request (root span)
├── context_manager.get
├── memory.retrieve{tier=long_term}
│   └── embed_text
├── module.execute{module=echo}
└── memory.write{tier=short_term}
    memory.write{tier=long_term}   ← background task, separate trace
```

### Trace Attributes

| Attribute | Value |
|-----------|-------|
| `service.name` | `mcp-system` |
| `mcp.request_id` | UUID |
| `mcp.tenant_id` | Tenant namespace |
| `mcp.context_id` | Context UUID |
| `mcp.module_name` | Executed module name |
| `http.method` | GET/POST/etc. |
| `http.target` | Request path |
| `http.status_code` | Response status |

---

## Grafana Dashboards

The monitoring stack includes Prometheus + Grafana (port 3000, admin/admin by default).

Key dashboard panels to create:

1. **Request Rate** — `rate(mcp_requests_total[1m])` grouped by status
2. **Request Latency p50/p95/p99** — histogram quantiles
3. **Module Execution Heatmap** — per-module latency distribution
4. **Active Contexts** — gauge by tenant
5. **Memory Operations** — read/write rate per tier
6. **Token Budget Distribution** — histogram of context token usage
7. **Auth Events** — success/failure rate
8. **Rate Limit Rejections** — rejection rate over time

---

## Debugging Runbook

### Request is slow (> 2s)
1. Check `mcp_module_execution_duration_seconds` — which module is slow?
2. Check `mcp_memory_operation_duration_seconds{tier=long_term}` — Qdrant latency?
3. Check Redis latency: `mcp_memory_operation_duration_seconds{tier=short_term}`
4. Enable `LOG_LEVEL=DEBUG` temporarily to see per-step timing

### 5xx error rate spike
1. Grep logs for `"level": "error"` — look for `event` field
2. Check `mcp_module_health` — is a module reporting unhealthy?
3. Check Redis connectivity: `mcp_memory_operations_total{status=error,tier=short_term}`
4. Check Qdrant connectivity: `mcp_memory_operations_total{status=error,tier=long_term}`

### Context operations failing
1. Check Redis: `redis-cli ping` from within the API container
2. Check `mcp_context_operations_total{status=error}` — which operation type?
3. Enable DEBUG logging to see raw Redis command errors

### Module timing out
1. Increase `MODULES_TIMEOUT_SECONDS` if the module is legitimately slow
2. Check if external API (LLM) is the bottleneck
3. Add `before_execute` hook to log input size
4. Use `ctx.metrics.record_module_execution()` for custom sub-span timing
