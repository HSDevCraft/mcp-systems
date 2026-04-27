# 04 — Memory System

## Why Tiered Memory?

A single memory backend cannot satisfy all access patterns simultaneously:

| Requirement | Implication |
|-------------|-------------|
| In-flight computation state | Must be < 0.1ms; in-process dict |
| Recent conversation turns | Must be < 5ms; shared across replicas → Redis |
| Semantic search over history | Must handle millions of records → Vector DB |
| Cross-session facts | Persistent; must survive restarts → Vector DB |

Tiered memory solves this by routing each type of data to the backend optimized for it.

---

## Tier 1: Working Memory

**Backend**: Python `dict` on `ExecutionContext`
**Latency**: < 0.1ms (no I/O)
**Durability**: None — discarded after request
**Capacity**: Bounded by request heap

**Use cases**:
- Intermediate tool results within a multi-step pipeline
- Computed values shared across lifecycle hooks
- Temporary flags (e.g., "already fetched external data this request")

**API**:
```python
ctx.working_memory["intermediate_result"] = result
value = ctx.working_memory.get("intermediate_result")
```

---

## Tier 2: Short-term Memory (Redis)

**Backend**: Redis 7+
**Latency**: 1–5ms (network hop)
**Durability**: TTL-based persistence (survives pod restart, not Redis restart without AOF)
**Capacity**: Configurable `maxmemory` (typically 512MB–4GB)
**Eviction**: `allkeys-lru` — oldest least-recently-used items evicted when full

### Storage Schema

```
Context object:
  Key:   mcp:{tenant_id}:ctx:{context_id}
  Type:  Redis Hash
  Fields: {id, session_id, status, token_count, max_tokens, metadata, created_at, updated_at}
  TTL:   CONTEXT_TTL_SECONDS (default 86400 = 24h)

Message history:
  Key:   mcp:{tenant_id}:ctx:{context_id}:messages
  Type:  Redis List (LPUSH for recency ordering)
  TTL:   Inherits context TTL

Session index:
  Key:   mcp:{tenant_id}:session:{session_id}
  Type:  Redis Set
  Value: Set of context_ids for this session

Short-term memory items:
  Key:   mcp:{tenant_id}:mem:st:{hash(content)}
  Type:  Redis Hash
  TTL:   REDIS_TTL_SECONDS (default 3600 = 1h)
```

### Read/Write Operations

```python
# Write context
await redis.hset(f"mcp:{tenant}:ctx:{ctx_id}", mapping=context.model_dump())
await redis.expire(f"mcp:{tenant}:ctx:{ctx_id}", settings.context_ttl)

# Append message (prepend for recency order)
await redis.lpush(f"mcp:{tenant}:ctx:{ctx_id}:messages", message.model_dump_json())
await redis.ltrim(f"mcp:{tenant}:ctx:{ctx_id}:messages", 0, MAX_MESSAGES - 1)

# Read recent N messages
raw = await redis.lrange(f"mcp:{tenant}:ctx:{ctx_id}:messages", 0, N - 1)
messages = [Message.model_validate_json(m) for m in raw]

# TTL refresh on access (sliding expiry)
await redis.expire(f"mcp:{tenant}:ctx:{ctx_id}", settings.context_ttl)
```

### Connection Pooling

```python
redis_pool = redis.asyncio.ConnectionPool.from_url(
    settings.redis_url,
    max_connections=settings.redis_max_connections,  # default 20
    socket_timeout=settings.redis_socket_timeout,    # default 5s
    retry_on_timeout=True,
    health_check_interval=30,
)
```

---

## Tier 3: Long-term Memory (Qdrant)

**Backend**: Qdrant vector database
**Latency**: 20–100ms (ANN search)
**Durability**: Persistent (WAL-backed)
**Capacity**: Scales to hundreds of millions of vectors
**Search**: Approximate Nearest Neighbor (HNSW index)

### Collection Schema

```python
from qdrant_client.models import VectorParams, Distance, PayloadSchemaType

collection_config = {
    "collection_name": settings.qdrant_collection,
    "vectors_config": VectorParams(
        size=settings.qdrant_vector_size,    # 1536 for OpenAI ada-002
        distance=Distance.COSINE,
    ),
    "optimizers_config": {
        "indexing_threshold": 10000,          # Index after 10K vectors
    },
    "hnsw_config": {
        "m": 16,                              # Connections per node
        "ef_construct": 100,                  # Build-time search width
        "full_scan_threshold": 10000,         # Use HNSW above this count
    },
}
```

### Payload Structure (per vector)

```json
{
  "memory_id": "uuid",
  "tenant_id": "string",
  "session_id": "uuid",
  "context_id": "uuid",
  "role": "user|assistant|system|tool",
  "content": "original text content",
  "timestamp": "ISO8601",
  "importance_score": 0.85,
  "expired": false,
  "tags": ["fact", "preference", "conversation"]
}
```

### Semantic Search

```python
async def retrieve(
    query: str,
    tenant_id: str,
    session_id: UUID | None = None,
    k: int = 5,
    score_threshold: float = 0.7,
) -> list[MemoryItem]:
    embedding = await self._embed(query)

    filters = Filter(
        must=[
            FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
            FieldCondition(key="expired", match=MatchValue(value=False)),
        ]
    )
    if session_id:
        filters.must.append(
            FieldCondition(key="session_id", match=MatchValue(value=str(session_id)))
        )

    results = await self._client.search(
        collection_name=self._collection,
        query_vector=embedding,
        query_filter=filters,
        limit=k,
        score_threshold=score_threshold,
        with_payload=True,
    )
    return [MemoryItem.from_qdrant(r) for r in results]
```

### Write Path (Async Background)

Long-term writes are non-blocking — they are dispatched as background tasks so they don't add latency to the API response:

```python
async def write_to_long_term(self, content: str, payload: dict) -> None:
    embedding = await self._embed(content)
    await self._client.upsert(
        collection_name=self._collection,
        points=[PointStruct(id=str(uuid4()), vector=embedding, payload=payload)],
    )

# In orchestrator — fire and forget:
asyncio.create_task(
    memory_manager.write(content, payload, tier=MemoryTier.LONG_TERM)
)
```

---

## Context Stitching

Context stitching assembles the final context window from all memory tiers:

```python
async def stitch_context(
    context: Context,
    query: str,
    memory_manager: MemoryManager,
    token_budget: int,
) -> list[Message]:
    messages = []
    remaining_budget = token_budget

    # 1. System prompt (always included, high priority)
    system_msg = context.get_system_message()
    if system_msg:
        messages.append(system_msg)
        remaining_budget -= system_msg.token_count

    # 2. Current user message (always included)
    current_msg = context.messages[-1]
    remaining_budget -= current_msg.token_count

    # 3. Long-term relevant memories (semantic)
    lt_memories = await memory_manager.retrieve(
        query=query,
        session_id=context.session_id,
        tier=MemoryTier.LONG_TERM,
        k=5,
    )
    for mem in lt_memories:
        if remaining_budget - mem.token_count < 0:
            break
        messages.append(mem.to_message())
        remaining_budget -= mem.token_count

    # 4. Recent short-term messages (recency order)
    recent = await memory_manager.retrieve(
        query=None,
        context_id=context.id,
        tier=MemoryTier.SHORT_TERM,
        limit=50,
    )
    for msg in reversed(recent):
        if remaining_budget - msg.token_count < 0:
            break
        messages.insert(1, msg)  # Insert after system, before current
        remaining_budget -= msg.token_count

    # 5. Append current message at the end
    messages.append(current_msg)
    return messages
```

---

## Memory Manager Interface

```python
class MemoryManager:
    async def write(
        self,
        content: str,
        metadata: dict,
        tier: MemoryTier = MemoryTier.SHORT_TERM,
        ttl: int | None = None,
    ) -> str: ...                                    # Returns memory_id

    async def retrieve(
        self,
        query: str | None,
        tier: MemoryTier,
        session_id: UUID | None = None,
        context_id: UUID | None = None,
        k: int = 10,
    ) -> list[MemoryItem]: ...

    async def delete(self, memory_id: str, tier: MemoryTier) -> bool: ...
    async def get_stats(self) -> MemoryStats: ...
```
