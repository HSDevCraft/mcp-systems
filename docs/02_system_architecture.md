# 02 — System Architecture

## Layer Model

The MCP System is organized into five horizontal layers, each with a single well-defined responsibility:

```
┌──────────────────────────────────────────────────────┐
│  Layer 5: Client Interface (REST / SSE / SDK)         │
├──────────────────────────────────────────────────────┤
│  Layer 4: API Gateway (Auth, Rate Limit, Routing)     │
├──────────────────────────────────────────────────────┤
│  Layer 3: MCP Engine (Context, Orchestrator, Registry)│
├──────────────────────────────────────────────────────┤
│  Layer 2: Service Layer (Memory, Modules, Storage)    │
├──────────────────────────────────────────────────────┤
│  Layer 1: Infrastructure (Redis, Qdrant, DB, Metrics) │
└──────────────────────────────────────────────────────┘
```

Dependency rule: each layer depends only on layers below it. The API Gateway never calls the infrastructure directly — it goes through the engine.

---

## Component Breakdown

### Context Manager

**Purpose**: Manages the complete lifecycle of a context object.

**Inputs**: context ID, messages, fork requests, seal requests
**Outputs**: serialized/deserialized Context objects, token counts

**Key operations**:
- `create(session_id, max_tokens)` — create a new empty context
- `get(context_id)` — load from Redis, deserialize
- `append_message(context_id, message)` — add message, check token budget
- `fork(context_id)` — create child context with shared parent pointer
- `merge(source_id, target_id)` — merge two context message sets
- `seal(context_id)` — mark as sealed (immutable)
- `expire(context_id)` — delete from store

**Token budget enforcement**:
```
on append_message:
  new_tokens = count_tokens(message.content)
  if context.token_count + new_tokens > context.max_tokens:
    trigger overflow_strategy:
      SUMMARIZE: summarize oldest N messages → replace with summary
      EVICT:     remove oldest messages until budget satisfied
      REJECT:    raise ContextOverflowError
```

---

### Orchestrator

**Purpose**: The central coordinator. The only component that references all three managers. Routes requests to the right subsystem and assembles responses.

**Key responsibilities**:
1. Parse and validate incoming tool call requests
2. Load the context for the session
3. Retrieve relevant memories (augment context)
4. Resolve and execute the module
5. Persist updated state to memory tiers
6. Return assembled response

**Concurrency**: Uses `asyncio.gather` to parallelize context load + memory pre-fetch. Module execution is sequential within a request (respects tool call ordering) but fully concurrent across independent requests.

---

### Module Registry

**Purpose**: Catalog, validate, version, and dispatch module calls.

**Module states**:
```
REGISTERED → LOADED → HEALTHY → (EXECUTING) → HEALTHY
                    ↓
                  UNHEALTHY → UNLOADED
```

**Registration** (at startup):
```python
registry = ModuleRegistry()
registry.register(EchoModule())
registry.register(SummarizerModule())
# Dynamic scan:
await registry.discover(path="src/modules/plugins")
```

**Execution** (at request time):
```python
module = registry.get("echo", version="1.0.0")
result = await registry.execute("echo", input_data, exec_ctx)
```

---

### Memory Manager

**Purpose**: Route reads and writes to the correct memory tier. Abstract over backend differences.

**Write routing**:
```
High-frequency, ephemeral → Working Memory (dict)
Session-level, TTL-based   → Short-term (Redis)
Persistent, semantic       → Long-term (Qdrant, async background)
```

**Read path (retrieval)**:
```
1. Check working memory → O(1) dict lookup
2. Check Redis short-term → O(1) hash get
3. Qdrant semantic search → ANN query
4. Merge + rank → return top-k within token budget
```

---

## Data Flow Diagrams

### Tool Execution Flow

```
Client
  │
  POST /api/v1/modules/{name}/execute
  {context_id, session_id, input, metadata}
  │
  ▼
[Auth Middleware]
  Validate JWT → extract user_id, tenant_id
  │
  ▼
[Rate Limit Middleware]
  Check sliding window counter (Redis)
  │
  ▼
[Validation Middleware]
  Pydantic schema validation
  │
  ▼
[Modules Router]
  │
  ▼
[Orchestrator.execute_module()]
  │
  ├─── asyncio.gather ───────────────────────────────┐
  │    ContextManager.get(context_id)                │
  │    MemoryManager.retrieve(query, session_id)      │
  │    ModuleRegistry.get(name)                      │
  └──────────────────────────────────────────────────┘
  │
  ▼
[Context + Memory stitched into ExecutionContext]
  │
  ▼
[Module.execute(input, execution_context)]
  (with timeout guard: asyncio.wait_for)
  │
  ▼
[Orchestrator: persist results]
  ├─ ContextManager.append_message(result_message)
  │   └─ Redis HSET + LPUSH (synchronous path)
  │
  └─ MemoryManager.write(result, tier=LONG_TERM)
      └─ Qdrant upsert (async background task)
  │
  ▼
[Response Assembly]
  {data: ModuleOutput, meta: {context_id, latency_ms}, error: null}
  │
  ▼
Client
```

### Context Lifecycle Flow

```
CREATE(session_id)
  │
  context = Context(id=uuid4(), session_id=..., status=ACTIVE)
  Redis: SET mcp:{tenant}:ctx:{id} → serialized JSON, EX=TTL
  │
APPEND_MESSAGE(context_id, message)
  │
  check token budget
  ├── OK: LPUSH mcp:{tenant}:ctx:{id}:messages
  └── OVERFLOW: apply strategy → retry or raise
  │
[Periodic TTL refresh on access — prevents premature expiry]
  │
FORK(context_id)
  │
  child = Context(id=uuid4(), parent_id=context_id)
  child.messages = copy(parent.messages)
  Redis: SET child context
  │
SEAL(context_id)
  │
  context.status = SEALED
  Redis: SET updated context (no further appends allowed)
  │
EXPIRE / GC
  │
  Redis TTL fires → key deleted (no manual cleanup needed)
  Qdrant: soft-delete (mark payload expired=true)
```

---

## Failure Handling

| Component | Failure Mode | Behavior |
|-----------|-------------|---------|
| Redis down | Context not loadable | Return 503; request cached in local working memory for retry |
| Qdrant down | Long-term retrieval fails | Log warning, proceed without long-term augmentation |
| Module timeout | Execution exceeds limit | Return `{error: "module_timeout"}`, context still updated with error record |
| Token overflow | Context window full | Apply configured strategy (summarize/evict/reject) |
| Auth failure | Invalid token | Return 401, log event, increment abuse counter |
| Rate limit hit | Too many requests | Return 429 with `Retry-After` header |

### Circuit Breaker Pattern

Redis and Qdrant clients are wrapped with `tenacity` retry logic:
- 3 retries with exponential backoff (0.1s, 0.5s, 2s)
- After 5 consecutive failures: circuit opens, returns degraded response
- Circuit re-checks every 30s

---

## Scalability Considerations

### Horizontal Scaling

The API layer is fully stateless — every stateful artifact (context, memory) lives in Redis or Qdrant. Adding more API replicas behind a load balancer requires zero configuration.

```
Load Balancer (ALB / Nginx)
    │
    ├─ mcp-api-1
    ├─ mcp-api-2
    ├─ mcp-api-N
    │
    ├─ Redis Cluster (shared state)
    └─ Qdrant Cluster (shared vector store)
```

### Redis Cluster Considerations

For high-throughput deployments (>10K concurrent contexts):
- Use Redis Cluster with consistent hashing
- Shard key: `{tenant_id}` — ensures all tenant data on same shard
- Avoid cross-slot operations (MULTI/EXEC spans must be per-shard)

### Qdrant Optimization

- HNSW parameters: `m=16, ef_construction=100` (balance recall vs. index time)
- Payload index on `tenant_id` and `session_id` for fast pre-filtering
- Background indexing — writes don't block reads
- Replication factor=2 for production redundancy
