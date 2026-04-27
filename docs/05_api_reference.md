# 05 — API Reference

## Design Principles

- **Resource-based routing** under `/api/v1/`
- **Standard envelope**: every response is `{data, meta, error}`
- **RFC 7807 errors**: `{type, title, status, detail}` — machine-readable and human-readable
- **Idempotency**: write operations accept `X-Idempotency-Key` header
- **Versioning**: URL prefix (`/api/v1/`) — bumped to `/api/v2/` on breaking changes

---

## Authentication

Every endpoint (except public health/docs paths) requires one of:

### API Key
```http
X-API-Key: mcp_your_key_here
```

### JWT Bearer Token
```http
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

**Obtain a JWT** (when user auth is configured):
```http
POST /api/v1/auth/token
Content-Type: application/json

{"username": "user@example.com", "password": "..."}
```

Response:
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "Bearer",
  "expires_in": 3600
}
```

---

## Response Envelope

All successful responses:
```json
{
  "data": { ... },
  "meta": { "request_id": "uuid", "latency_ms": 42 },
  "error": null
}
```

All error responses (RFC 7807):
```json
{
  "type": "https://errors.mcp-system.io/context/not_found",
  "title": "ContextNotFoundError",
  "status": 404,
  "detail": "Context 'abc-123' not found"
}
```

---

## Context Endpoints

### POST `/api/v1/contexts/`
Create a new context.

**Request:**
```json
{
  "session_id": "uuid",
  "max_tokens": 128000,
  "ttl_seconds": 86400,
  "system_prompt": "You are a helpful assistant.",
  "metadata": {"project": "demo"}
}
```

**Response `201`:**
```json
{
  "data": {
    "id": "uuid",
    "session_id": "uuid",
    "status": "active",
    "token_count": 12,
    "max_tokens": 128000,
    "remaining_tokens": 127988,
    "message_count": 1,
    "created_at": "2025-01-01T00:00:00Z",
    "updated_at": "2025-01-01T00:00:00Z",
    "parent_id": null,
    "metadata": {"project": "demo"}
  }
}
```

---

### GET `/api/v1/contexts/{id}`
Retrieve context metadata.

| Status | Meaning |
|--------|---------|
| 200 | Context returned |
| 404 | Context not found |
| 410 | Context expired |

---

### PUT `/api/v1/contexts/{id}/messages`
Append a message to a context.

**Request:**
```json
{
  "role": "user",
  "content": "What is the capital of France?",
  "metadata": {"source": "web-chat"}
}
```

**Response `200`:**
```json
{
  "data": {
    "context_id": "uuid",
    "message_id": "uuid",
    "role": "user",
    "token_count": 9,
    "context_token_count": 21,
    "context_remaining_tokens": 127979
  }
}
```

**Error `422` (overflow with REJECT strategy):**
```json
{
  "type": "https://errors.mcp-system.io/context/overflow",
  "title": "ContextOverflowError",
  "status": 422,
  "detail": "Context token budget exceeded",
  "current_tokens": 127990,
  "needed_tokens": 50,
  "token_limit": 128000
}
```

---

### GET `/api/v1/contexts/{id}/messages`
List messages in chronological order.

**Query params:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int | 50 | Max messages to return (1–500) |
| `offset` | int | 0 | Skip first N messages |

---

### POST `/api/v1/contexts/{id}/fork`
Fork a context — creates a child with a copy of the parent's message history.

**Response `201`:**
```json
{
  "data": {
    "parent_id": "uuid",
    "child_id": "uuid",
    "child_context": { ... }
  }
}
```

---

### POST `/api/v1/contexts/{id}/seal`
Seal a context (make it immutable). No further messages can be appended.

---

### DELETE `/api/v1/contexts/{id}`
Immediately expire a context. Returns `204 No Content`.

---

## Memory Endpoints

### POST `/api/v1/memory/store`
Store a memory item in a specified tier.

**Request:**
```json
{
  "content": "The user prefers dark mode interfaces",
  "tier": "long_term",
  "session_id": "uuid",
  "role": "user",
  "tags": ["preference", "ui"],
  "metadata": {"source": "feedback-form"}
}
```

**Tiers:** `short_term` | `long_term`

**Response `201`:**
```json
{
  "data": {
    "memory_id": "abc123",
    "tier": "long_term",
    "content_preview": "The user prefers dark mode...",
    "token_count": 8
  }
}
```

---

### POST `/api/v1/memory/retrieve`
Semantic search over memory.

**Request:**
```json
{
  "query": "What are the user's UI preferences?",
  "tier": "long_term",
  "session_id": "uuid",
  "k": 5,
  "score_threshold": 0.6
}
```

**Response `200`:**
```json
{
  "data": {
    "results": [
      {
        "id": "abc123",
        "content": "The user prefers dark mode interfaces",
        "tier": "long_term",
        "role": "user",
        "timestamp": "2025-01-01T00:00:00Z",
        "importance_score": 0.91,
        "token_count": 8,
        "tags": ["preference", "ui"],
        "metadata": {}
      }
    ],
    "total": 1,
    "query": "What are the user's UI preferences?",
    "tier": "long_term"
  }
}
```

---

### DELETE `/api/v1/memory/{id}`
Soft-delete a memory item. Returns `204 No Content`.

**Query params:** `tier=long_term` (default)

---

### GET `/api/v1/memory/stats`
Return memory tier statistics.

```json
{
  "data": {
    "short_term_memory_bytes": 2048000,
    "long_term_vectors": 15420,
    "redis_connected": true,
    "qdrant_connected": true
  }
}
```

---

## Module Endpoints

### GET `/api/v1/modules/`
List all registered modules.

```json
{
  "data": {
    "modules": [
      {
        "name": "echo",
        "version": "1.0.0",
        "description": "Returns input text with optional transformations",
        "tags": ["utility", "testing"],
        "input_schema": { ... },
        "output_schema": { ... }
      }
    ],
    "total": 3
  }
}
```

---

### GET `/api/v1/modules/{name}`
Get module schema. Supports `name@version` syntax.

```http
GET /api/v1/modules/echo
GET /api/v1/modules/echo@1.0.0
```

---

### POST `/api/v1/modules/{name}/execute`
Execute a module.

**Request:**
```json
{
  "input": {
    "text": "Hello from MCP!",
    "uppercase": true
  },
  "context_id": "uuid",
  "session_id": "uuid",
  "version": null,
  "metadata": {"source": "web"}
}
```

**Response `200`:**
```json
{
  "data": {
    "module_name": "echo",
    "module_version": "1.0.0",
    "output": {
      "text": "HELLO FROM MCP!",
      "char_count": 15,
      "word_count": 3,
      "transformations": ["uppercase"]
    },
    "latency_ms": 1.24,
    "status": "success",
    "request_id": "uuid",
    "context_id": "uuid",
    "error": null
  },
  "meta": { "latency_ms": 1.24 }
}
```

**Version pinning via path:**
```http
POST /api/v1/modules/echo@1.0.0/execute
```

---

### GET `/api/v1/modules/health`
Health status of all registered modules.

```json
{
  "data": {
    "overall": "healthy",
    "modules": {
      "echo@1.0.0": { "healthy": true, "message": "Echo module is always healthy" },
      "text-summarizer@1.0.0": { "healthy": true, "message": "Running in extractive mode" }
    },
    "total": 2,
    "unhealthy_count": 0
  }
}
```

---

## Health Endpoints

| Endpoint | Auth | Description |
|----------|------|-------------|
| `GET /health/live` | No | Liveness probe — always 200 if process running |
| `GET /health/ready` | No | Readiness probe — checks dependencies |
| `GET /health/` | No | Deep health check — all subsystems |

Deep health response:
```json
{
  "data": {
    "status": "healthy",
    "version": "0.1.0",
    "environment": "production",
    "checks": {
      "redis": { "status": "healthy", "message": "" },
      "qdrant": { "status": "healthy", "message": "" },
      "modules": { "status": "healthy", "message": "" }
    }
  }
}
```

---

## HTTP Status Code Reference

| Code | Meaning | When |
|------|---------|------|
| 200 | OK | Successful GET / action |
| 201 | Created | Successful POST (create) |
| 204 | No Content | Successful DELETE |
| 400 | Bad Request | Malformed request body |
| 401 | Unauthorized | Missing or invalid credentials |
| 403 | Forbidden | Valid credentials, insufficient permissions |
| 404 | Not Found | Context/module/memory not found |
| 409 | Conflict | Context sealed; duplicate resource |
| 410 | Gone | Context expired |
| 422 | Unprocessable | Validation error (input schema) |
| 429 | Too Many Requests | Rate limit exceeded |
| 500 | Internal Server Error | Unexpected server error |
| 503 | Service Unavailable | Dependency (Redis/Qdrant) unreachable |
| 504 | Gateway Timeout | Module execution timeout |
