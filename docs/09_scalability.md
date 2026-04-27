# 09 — Scalability & Future Extensions

## Current Architecture Scalability Ceiling

| Component | Single-node ceiling | Horizontal ceiling |
|-----------|--------------------|--------------------|
| API layer | ~2K RPS (4 workers) | ~40K RPS (20 pods × 4 workers) |
| Redis | ~100K ops/s | ~1M ops/s (Redis Cluster) |
| Qdrant | ~500 QPS (HNSW k=5) | ~10K QPS (3-node cluster) |
| Context store | Redis-bound | Redis Cluster shards by tenant |

The primary bottleneck under load is **Qdrant vector search latency** (20–100ms).
The Redis layer handles short-term state at sub-millisecond latency.

---

## Horizontal Scaling

### API Layer (stateless)

The API server is fully stateless — add replicas behind any L7 load balancer.
The `Deployment` HPA scales from 2 to 20 replicas on CPU (70%) and memory (80%).

**Session affinity**: NOT required. Context state lives in Redis, not in-process.

**Connection pooling**: Each replica maintains its own Redis connection pool
(`REDIS_MAX_CONNECTIONS=20` per replica). With 10 replicas, Redis sees 200 total connections — well within the `maxclients` limit of 10,000.

### Redis Scaling

For > 50K ops/s or > 4GB active context data:

**Option 1: Redis Cluster** (recommended)
```
6 nodes: 3 primaries + 3 replicas
Sharding key: {tenant_id} — ensures all tenant keys on same shard
Avoids cross-slot multi-key ops
```

**Option 2: Managed Redis** (ElastiCache, Upstash)
```
ElastiCache Cluster Mode Enabled
- Auto-failover
- Multi-AZ replication
- Automatic sharding
```

Key design constraint: all Redis operations are single-key — no MULTI/EXEC transactions
span across keys, making cluster migration transparent.

### Qdrant Scaling

**Replication** (availability, not throughput):
```yaml
# 3-node Qdrant cluster
replication_factor: 2    # 2 copies of each shard
write_consistency: 1     # Write confirmed on 1 replica
```

**Sharding** (throughput + capacity):
```python
# Collection with 4 shards
await client.create_collection(
    collection_name="mcp_memory",
    shard_number=4,
    ...
)
```

**Read scaling**: Qdrant allows routing read requests to replicas, relieving the
primary for writes. Set `read_consistency_type=MAJORITY` for strong consistency.

---

## Multi-Tenancy

All storage is namespaced by `tenant_id` at the key level:

```
Redis:  mcp:{tenant_id}:ctx:{context_id}
Redis:  mcp:{tenant_id}:session:{session_id}
Qdrant: payload filter on tenant_id field (indexed)
```

### Tenant Isolation Levels

| Level | What's Shared | Isolation Guarantee |
|-------|--------------|---------------------|
| **Logical** (current) | Redis, Qdrant, API | Key-level isolation |
| **Pool** | API pods per tenant group | Namespace-level Redis |
| **Silo** | Dedicated Redis + Qdrant | Full isolation |

For regulated industries (banking, healthcare), silo isolation is required.
Implement by deploying a separate `docker-compose.yml` stack per tenant
or separate Kubernetes namespaces with dedicated Redis/Qdrant StatefulSets.

### Per-Tenant Rate Limiting

```python
# Redis hash: mcp:ratelimit:config:{tenant_id}
limits = {
    "acme-corp": 1000,      # High-volume tenant
    "startup-xyz": 50,      # Free tier
    "enterprise-abc": 5000, # Enterprise SLA
}
```

### Per-Tenant Token Budgets

```python
# Override context.max_tokens per tenant from tenant config
tenant_config = await get_tenant_config(tenant_id)
max_tokens = tenant_config.get("context_max_tokens", settings.context_max_tokens)
```

---

## Event-Driven Architecture Extensions

The current request-response model can be extended with an event bus for:
- Async module execution (fire-and-forget long-running tools)
- Cross-service context sharing
- Audit event streaming
- Multi-agent coordination

### Adding a Message Queue (Kafka / Redis Streams)

```python
# Publish module result event
await event_bus.publish(
    topic="mcp.module.executed",
    payload={
        "tenant_id": tenant_id,
        "module_name": module_name,
        "request_id": str(request_id),
        "output": output.model_dump(),
        "timestamp": datetime.now(UTC).isoformat(),
    }
)
```

**Consumer pattern** for async module execution:
```
Client POST /api/v1/modules/{name}/execute?async=true
  → Returns {job_id: "uuid", status_url: "/api/v1/jobs/uuid"}
  → Publishes to Kafka topic
  → Worker consumes, executes module, writes result to Redis
  → Client polls /api/v1/jobs/uuid for completion
```

### Server-Sent Events (SSE) for Streaming

Already available via FastAPI's `EventSourceResponse`:

```python
@router.get("/{name}/stream")
async def execute_module_stream(name: str, ...):
    async def event_generator():
        async for chunk in module.execute_stream(input, ctx):
            yield {"data": chunk.model_dump_json(), "event": "chunk"}
        yield {"data": "{}", "event": "done"}

    return EventSourceResponse(event_generator())
```

---

## AI Agent Integrations

### MCP Standard Protocol Compatibility

This system is compatible with the Anthropic MCP standard. External MCP clients
(Claude Desktop, custom agents) can connect via:

```python
# SSE transport endpoint (MCP spec)
GET /api/v1/mcp/sse        # Server-sent events stream
POST /api/v1/mcp/messages  # Client → server messages
```

Module types map to MCP primitives:
- **Tools** → `MCPModule` with `execute()` returning structured output
- **Resources** → `MCPModule` with `read()` returning file/data content
- **Prompts** → `MCPModule` returning formatted prompt templates

### Multi-Agent Patterns

**Agent-per-context** (current): Each agent maintains its own context.
Fork/merge enables agent collaboration:

```
Planner Agent → creates parent context
  ├── forks → Researcher Agent (context child A)
  ├── forks → Writer Agent (context child B)
  └── merges A + B → Reviewer Agent (merged context)
```

**Shared memory pool**: Multiple agents writing to the same `session_id` in
long-term memory creates a shared knowledge base visible to all agents in the session.

---

## Performance Optimization Roadmap

### Near-term (weeks)

| Optimization | Impact | Effort |
|-------------|--------|--------|
| Redis pipelining for multi-message appends | 3× throughput | Low |
| Batch embedding (process N texts in one API call) | 5× embedding throughput | Low |
| Context compression (zlib on Redis stored JSON) | 40% memory reduction | Medium |
| Qdrant payload index on `timestamp` for range queries | 2× retrieval speed | Low |

### Medium-term (months)

| Optimization | Impact | Effort |
|-------------|--------|--------|
| Async module execution queue (Celery/ARQ) | Unblocks long-running tools | Medium |
| Context summarization (automatic overflow strategy) | Infinite effective context | High |
| Qdrant binary quantization | 32× memory reduction, 40× faster search | Low |
| Module output caching (Redis, keyed by input hash) | Eliminates redundant LLM calls | Medium |

### Long-term (quarters)

| Feature | Description |
|---------|-------------|
| Multi-modal context | Store image/audio embeddings alongside text |
| Federated memory | Cross-tenant knowledge sharing with consent |
| Context versioning | Full git-like history of context mutations |
| Distributed tracing mesh | Agent-to-agent trace propagation |
| Adaptive rate limiting | ML-based anomaly detection for abuse patterns |

---

## Capacity Planning Formula

```
Required API replicas = ceil(peak_rps / throughput_per_replica)

Where:
  throughput_per_replica ≈ 200 RPS (echo module, no LLM)
                         ≈  20 RPS (LLM-backed module, 500ms p50)

Required Redis memory = active_contexts × avg_context_size_bytes
                      + short_term_items × avg_item_size_bytes
  Example: 10K contexts × 50KB + 100K items × 2KB = 700MB

Required Qdrant memory ≈ vector_count × (4 × vector_dim + payload_bytes)
                        × 1.5 (HNSW graph overhead)
  Example: 1M vectors × (4 × 1536 + 500) × 1.5 ≈ 9.9 GB
```

Use the `/api/v1/memory/stats` endpoint to monitor actual growth rates.
