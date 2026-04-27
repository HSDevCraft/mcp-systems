# MCP System — Architecture Reference

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Component Deep-Dive](#2-component-deep-dive)
3. [Data Flow](#3-data-flow)
4. [Control Flow](#4-control-flow)
5. [Memory Architecture](#5-memory-architecture)
6. [Module System Design](#6-module-system-design)
7. [API Design](#7-api-design)
8. [Security Architecture](#8-security-architecture)
9. [Observability Architecture](#9-observability-architecture)
10. [Deployment Architecture](#10-deployment-architecture)
11. [Scalability Design](#11-scalability-design)
12. [Design Decisions & Tradeoffs](#12-design-decisions--tradeoffs)

---

## 1. System Overview

The MCP System is a layered platform built around three core concerns:

- **Context Lifecycle** — create, maintain, fork, merge, serialize, and expire context windows
- **Memory Tiering** — route information to the appropriate memory tier based on access patterns and durability requirements
- **Modular Execution** — provide a plugin interface that lets any capability (tool, resource, prompt) integrate cleanly without core changes

```
┌─────────────────────────────────────────────────────────────────────────┐
│                             EXTERNAL CLIENTS                             │
│          (AI Model SDKs / REST / SSE / WebSocket / CLI)                  │
└──────────────────────────────────────┬──────────────────────────────────┘
                                       │ HTTPS / SSE
┌──────────────────────────────────────▼──────────────────────────────────┐
│                            API GATEWAY LAYER                             │
│  ┌────────────┐  ┌──────────────┐  ┌────────────┐  ┌────────────────┐  │
│  │  JWT Auth  │  │ Rate Limiter │  │ Validation │  │ Request Logger │  │
│  └────────────┘  └──────────────┘  └────────────┘  └────────────────┘  │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ /context │  │ /memory  │  │   /modules   │  │    /health       │   │
│  └──────────┘  └──────────┘  └──────────────┘  └──────────────────┘   │
└──────────────────────────────────────┬──────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼──────────────────────────────────┐
│                            MCP ENGINE (CORE)                             │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                        ORCHESTRATOR                               │   │
│  │   Coordinates: ContextManager ↔ MemoryManager ↔ ModuleRegistry  │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  ┌─────────────────┐   ┌──────────────────┐   ┌─────────────────────┐  │
│  │ Context Manager │   │  Memory Manager  │   │  Module Registry    │  │
│  │                 │   │                  │   │                     │  │
│  │ - create/fork   │   │ - route writes   │   │ - discover modules  │  │
│  │ - merge/expire  │   │ - cache reads    │   │ - validate contracts│  │
│  │ - token budget  │   │ - TTL management │   │ - lifecycle hooks   │  │
│  │ - serialization │   │ - consistency    │   │ - execute tools     │  │
│  └────────┬────────┘   └────────┬─────────┘   └──────────┬──────────┘  │
└───────────┼─────────────────────┼─────────────────────────┼────────────┘
            │                     │                          │
┌───────────▼──────┐   ┌──────────▼──────────────┐  ┌──────▼────────────┐
│  Context Store   │   │     MEMORY TIERS         │  │   MODULE POOL     │
│  (Redis/DB)      │   │  ┌───────────────────┐   │  │                   │
│                  │   │  │ Working (In-proc) │   │  │ Built-in modules: │
│ - Serialized     │   │  │ ← < 1ms, volatile │   │  │ - echo            │
│   context state  │   │  ├───────────────────┤   │  │ - summarizer      │
│ - Message        │   │  │ Short-term (Redis) │  │  │ - retriever       │
│   history        │   │  │ ← < 5ms, TTL-based│  │  │                   │
│ - Metadata       │   │  ├───────────────────┤   │  │ Plugin modules:   │
│                  │   │  │ Long-term (Qdrant) │  │  │ - user-defined    │
└──────────────────┘   │  │ ← semantic search │   │  └───────────────────┘
                       │  └───────────────────┘   │
                       └─────────────────────────-┘
```

---

## 2. Component Deep-Dive

### 2.1 Context Manager (`src/core/context_manager.py`)

**Responsibility**: Owns the full lifecycle of a *context* — the unit of stateful interaction between a client and the MCP system.

**Key design decisions**:
- A context is immutable once sealed; mutations produce new versions (append-only log of messages)
- Token budgeting is enforced at ingestion time using `tiktoken` — overflow triggers summarization or eviction
- Contexts can be *forked* (branching) for A/B testing and *merged* for multi-agent aggregation
- Context IDs are UUIDv4; session binding is handled by the API layer separately

**Data model**:
```
Context {
  id: UUID
  session_id: UUID
  created_at: datetime
  updated_at: datetime
  ttl_seconds: int
  token_count: int          # running token budget
  max_tokens: int           # cap from config
  messages: List[Message]
  metadata: Dict[str, Any]  # arbitrary client-supplied tags
  parent_id: UUID | None    # set when forked
  status: ContextStatus     # active | sealed | expired | archived
}

Message {
  id: UUID
  role: str                 # "user" | "assistant" | "system" | "tool"
  content: str | List[Part] # text or multimodal parts
  timestamp: datetime
  token_count: int
  metadata: Dict[str, Any]
}
```

### 2.2 Orchestrator (`src/core/orchestrator.py`)

**Responsibility**: Routes incoming requests to the appropriate subsystem and assembles the response. The orchestrator is the only component that holds references to all three managers.

**Execution flow**:
1. Validate request schema (Pydantic)
2. Resolve context (load from store or create new)
3. Apply memory retrieval (augment messages with long-term memories)
4. Resolve module/tool calls
5. Execute modules (with timeout + retry)
6. Persist updated context + write to memory tiers
7. Return assembled response

**Concurrency model**: All I/O paths are `async`. The orchestrator uses `asyncio.gather` for parallel memory retrieval + module pre-loading. Tool execution is sequential within a request but concurrent across requests.

### 2.3 Module Registry (`src/core/registry.py`)

**Responsibility**: Maintains a catalog of all available modules. Supports both static registration (import-time) and dynamic discovery (filesystem scan).

**Interface contract** (see `src/modules/base.py`): every module must implement:
```python
class MCPModule(ABC):
    name: str               # unique identifier
    description: str
    input_schema: Type[BaseModel]
    output_schema: Type[BaseModel]

    async def execute(self, input: BaseModel, ctx: ExecutionContext) -> BaseModel: ...
    async def on_load(self) -> None: ...
    async def on_unload(self) -> None: ...
    async def health_check(self) -> HealthStatus: ...
```

### 2.4 Memory Manager (`src/memory/manager.py`)

Routes all memory reads/writes across three tiers. The routing policy:

| Tier | Backend | Latency | Durability | Capacity | Use |
|------|---------|---------|------------|---------|-----|
| Working | Python dict | < 0.1ms | None | Small | In-flight request state |
| Short-term | Redis | 1–5ms | TTL-based | Medium | Session context, recent turns |
| Long-term | Qdrant | 20–100ms | Permanent | Large | Semantic memory, facts, history |

---

## 3. Data Flow

### 3.1 Inbound Request (tool call)

```
Client
  │  POST /api/v1/modules/{name}/execute
  ▼
Auth Middleware (validate JWT / API key)
  ▼
Rate Limit Middleware (check token bucket)
  ▼
Request Logger (structured log entry)
  ▼
Router → ModulesRouter.execute_module()
  ▼
Orchestrator.execute_module_in_context()
  │
  ├─ 1. ContextManager.get_or_create(context_id)
  │     └─ Redis GET → deserialize Context
  │
  ├─ 2. MemoryManager.retrieve(query, tier=long_term)
  │     └─ Qdrant vector search → inject into context
  │
  ├─ 3. ModuleRegistry.get_module(name)
  │     └─ Validate input schema
  │
  ├─ 4. Module.execute(input, execution_context)
  │     └─ Async with timeout guard
  │
  ├─ 5. ContextManager.append_message(context, result)
  │     └─ Token count → Redis SET
  │
  └─ 6. MemoryManager.write(result, tier=short_term)
        └─ Redis SET with TTL

  ▼
Response (JSON)
  ▼
Response Logger (latency, status, context_id)
```

### 3.2 Context Retrieval and Stitching

Memory stitching combines context tiers into a coherent window:

```
[System Prompt]
[Long-term memories] ← semantic search results, summarized
[Short-term context] ← recent N messages from Redis
[Working context]    ← current request message(s)
[Instructions/Tools] ← available module schemas
```

Budget management: total token count is enforced. If budget would be exceeded, long-term results are trimmed first, then short-term (oldest messages), preserving the system prompt and current request.

---

## 4. Control Flow

### 4.1 Module Execution State Machine

```
         ┌─────────┐
         │ PENDING │
         └────┬────┘
              │ registry.execute()
         ┌────▼─────┐
         │ LOADING  │  ← on_load() hook
         └────┬─────┘
              │ success
         ┌────▼──────┐
         │ EXECUTING │ ── timeout ──► FAILED
         └────┬──────┘
              │                        │
        ┌─────▼──────┐          ┌──────▼──────┐
        │  COMPLETED │          │   FAILED     │
        └────────────┘          └──────────────┘
```

### 4.2 Context Lifecycle

```
CREATE ──► ACTIVE ──► SEALED ──► ARCHIVED
                │
                └──► EXPIRED (TTL exceeded, GC deletes)
                │
                └──► FORKED (creates child context, parent stays ACTIVE)
```

---

## 5. Memory Architecture

### 5.1 Working Memory

- Implemented as `dict` on the `ExecutionContext` object
- Scoped to a single request; discarded after response
- Used for: intermediate tool results, computed values, state between pipeline stages

### 5.2 Short-term Memory (Redis)

- Redis Hashes for structured context storage (O(1) field access)
- Redis Lists for message history (LPUSH + LTRIM for rolling window)
- TTL set on context key — no explicit deletion required
- Keyspace: `mcp:ctx:{context_id}`, `mcp:session:{session_id}`, `mcp:mem:st:{key}`

### 5.3 Long-term Memory (Qdrant)

- Collection: `mcp_memory`, HNSW index
- Vectors: embeddings of message content (OpenAI or sentence-transformers)
- Payload: `{context_id, session_id, role, content, timestamp, metadata}`
- Retrieval: top-k semantic search filtered by `session_id` or global
- Write path: async background task — does not block response

### 5.4 Memory Retrieval Chain

```python
async def retrieve_relevant_memory(query: str, session_id: UUID, k: int = 5):
    # 1. Check working memory (synchronous dict lookup)
    # 2. Check Redis short-term (hash scan)
    # 3. Vector search Qdrant (async)
    # 4. Merge, deduplicate, rank by relevance score
    # 5. Return top-k within token budget
```

---

## 6. Module System Design

### 6.1 Module Interface

Every module is a Python class implementing `MCPModule`. The registry enforces the interface at load time using `inspect` + Pydantic schema validation.

### 6.2 Discovery

Two discovery modes:
1. **Static**: explicit `registry.register(MyModule)` in application startup
2. **Dynamic**: filesystem scan of `MODULES_DIR` — any `.py` file with a class inheriting `MCPModule` is auto-loaded

### 6.3 Lifecycle Hooks

| Hook | When Called | Use Case |
|------|------------|---------|
| `on_load()` | After registration | Connect to external APIs, warm caches |
| `before_execute()` | Before each execution | Input sanitization, auth |
| `after_execute()` | After each execution | Logging, metrics, side effects |
| `on_error()` | On execution failure | Custom error handling, alerting |
| `on_unload()` | Before deregistration | Clean up resources |

### 6.4 Versioning

Modules are versioned via `version: str` attribute. The registry supports multiple versions of the same module name simultaneously; clients can pin via `module_name@version` syntax.

---

## 7. API Design

### 7.1 Design Principles

- RESTful resource-based routing under `/api/v1/`
- All responses follow `{data, meta, error}` envelope
- Errors follow RFC 7807 Problem Details (`type`, `title`, `status`, `detail`)
- Server-Sent Events (SSE) for streaming tool output at `/stream`
- Idempotency keys supported on write operations via `X-Idempotency-Key` header

### 7.2 Route Groups

```
/api/v1/
  contexts/               Context CRUD
    POST   /              Create context
    GET    /{id}          Get context
    PUT    /{id}/messages Append message
    DELETE /{id}          Expire context
    POST   /{id}/fork     Fork context
    POST   /{id}/seal     Seal context

  memory/                 Memory operations
    POST   /store         Store memory item
    POST   /retrieve      Semantic retrieval
    DELETE /{id}          Delete memory item
    GET    /stats         Memory tier statistics

  modules/                Module operations
    GET    /              List all modules
    GET    /{name}        Get module schema
    POST   /{name}/execute Execute module
    GET    /health        Module health summary

  health/                 System health
    GET    /              Overall health
    GET    /ready         Readiness probe
    GET    /live          Liveness probe
```

---

## 8. Security Architecture

### 8.1 Auth Flow

```
Client ──► API Gateway
             │
             ├─ X-API-Key header present?
             │    └─ validate against hashed key store (Redis)
             │
             └─ Authorization: Bearer <JWT> present?
                  └─ decode + validate (algorithm, expiry, issuer)
                  └─ extract user_id, roles, tenant_id
                  └─ inject into request.state
```

### 8.2 Threat Model

| Threat | Mitigation |
|--------|-----------|
| Prompt injection via module input | Input validation (Pydantic), content filtering layer |
| Token theft | Short JWT expiry (60min), refresh token rotation |
| DoS / abuse | Rate limiting (sliding window per API key) |
| Data leakage | Context isolation by tenant_id in all storage keys |
| SSRF via module | Module execution sandboxed; outbound allowlist |
| Secret exposure | Secrets loaded from env only, never logged, masked in errors |

---

## 9. Observability Architecture

### 9.1 Structured Logging

Every log entry is JSON with mandatory fields:
```json
{
  "timestamp": "ISO8601",
  "level": "INFO",
  "service": "mcp-system",
  "trace_id": "uuid",
  "context_id": "uuid",
  "module": "src.core.orchestrator",
  "event": "module_executed",
  "latency_ms": 42,
  "status": "success"
}
```

### 9.2 Prometheus Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `mcp_requests_total` | Counter | Requests by endpoint + status |
| `mcp_request_duration_seconds` | Histogram | Request latency |
| `mcp_context_count` | Gauge | Active context count |
| `mcp_memory_operations_total` | Counter | Memory reads/writes by tier |
| `mcp_module_executions_total` | Counter | Module calls by name + status |
| `mcp_module_duration_seconds` | Histogram | Module execution latency |
| `mcp_token_budget_used` | Histogram | Token utilization per context |

### 9.3 Tracing (OpenTelemetry)

Spans created for:
- Inbound HTTP request (root span)
- Context load/save
- Memory retrieval (per tier)
- Module execution
- Outbound HTTP calls (from modules)

---

## 10. Deployment Architecture

### 10.1 Single-Node (Docker Compose)

Suitable for development and small deployments (<100 RPS):
```
[Nginx] → [mcp-api] → [Redis] + [Qdrant]
                    ↗ [Prometheus] → [Grafana]
```

### 10.2 Production (Kubernetes)

```
[Ingress/ALB]
      │
[mcp-api Deployment]    ← HPA (CPU/RPS based, 2–20 replicas)
      │
[Redis Cluster]         ← Managed (ElastiCache / Upstash)
[Qdrant StatefulSet]    ← 3-node cluster with replication
[PostgreSQL]            ← Managed (RDS / CloudSQL) for metadata
      │
[Prometheus Stack]
[OpenTelemetry Collector]
```

---

## 11. Scalability Design

### 11.1 Stateless API Layer

The API server is fully stateless — all state lives in Redis/Qdrant. Horizontal scaling is trivial: add replicas behind a load balancer.

### 11.2 Multi-tenancy

All storage keys are namespaced by `tenant_id`:
- Redis: `mcp:{tenant_id}:ctx:{id}`
- Qdrant: filter on `tenant_id` payload field
- Rate limits: per `(tenant_id, api_key)` pair

### 11.3 Bottlenecks and Mitigations

| Bottleneck | Mitigation |
|------------|-----------|
| Redis hot keys | Key sharding + Redis Cluster |
| Qdrant query latency | Pre-filter by tenant + HNSW tuning |
| Large context serialization | Delta compression, chunked storage |
| Module cold start | Pre-warm pool via `on_load()` at startup |

---

## 12. Design Decisions & Tradeoffs

### 12.1 Why Async Python (asyncio) over threads?

MCP workloads are I/O-bound (Redis, Qdrant, LLM APIs). asyncio gives ~10× better throughput than threading for I/O-bound workloads without the GIL contention overhead. CPU-bound modules run in `ProcessPoolExecutor`.

### 12.2 Why Redis for short-term memory over local dict?

Local dict would not survive pod restarts or scale horizontally. Redis provides shared state across replicas with sub-millisecond latency — the tradeoff of a network hop is acceptable and the persistence requirement makes it non-negotiable.

### 12.3 Why Qdrant over Pinecone/Weaviate?

- Qdrant is self-hostable (no vendor lock-in), open-source, and has excellent async client support
- Payload filtering is more expressive than Pinecone (critical for multi-tenant queries)
- HNSW implementation is state-of-the-art
- Pinecone stub is included for teams that need managed hosting

### 12.4 Why FastAPI over Django/Flask?

- Async-native (ASGI via Starlette)
- Pydantic v2 schema validation is shared with domain models
- Auto-generated OpenAPI docs reduce documentation maintenance burden
- Performance (close to Node.js in benchmarks)

### 12.5 Why Plugin/Module pattern over direct tool calls?

Direct tool calls couple the orchestrator to tool implementations. The module system decouples them: adding a new capability requires zero changes to core — implement the interface and register. This enables external contributors to ship modules without core team review of every PR.
