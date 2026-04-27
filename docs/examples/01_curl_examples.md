# MCP System — cURL Usage Examples

Base URL: `http://localhost:8000`
All authenticated endpoints require either `X-API-Key` or `Authorization: Bearer <jwt>`.

## Generate a JWT (dev shortcut)

```bash
# Generate a dev token (make api must be running)
TOKEN=$(python -c "
from src.utils.security import create_access_token
print(create_access_token('dev-user', 'default', ['admin']))
")
```

---

## Context Operations

### Create a context

```bash
curl -s -X POST http://localhost:8000/api/v1/contexts/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "system_prompt": "You are a helpful AI assistant.",
    "max_tokens": 32000,
    "metadata": {"project": "demo"}
  }' | python -m json.tool
```

### Append a message

```bash
CONTEXT_ID="<uuid-from-create-response>"

curl -s -X PUT http://localhost:8000/api/v1/contexts/${CONTEXT_ID}/messages \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "role": "user",
    "content": "What is the Model Context Protocol?"
  }' | python -m json.tool
```

### Fork a context

```bash
curl -s -X POST http://localhost:8000/api/v1/contexts/${CONTEXT_ID}/fork \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

### Seal a context (make immutable)

```bash
curl -s -X POST http://localhost:8000/api/v1/contexts/${CONTEXT_ID}/seal \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

### Get messages

```bash
curl -s "http://localhost:8000/api/v1/contexts/${CONTEXT_ID}/messages?limit=20&offset=0" \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

### Delete a context

```bash
curl -s -X DELETE http://localhost:8000/api/v1/contexts/${CONTEXT_ID} \
  -H "Authorization: Bearer $TOKEN" -w "%{http_code}"
# Expect: 204
```

---

## Module Operations

### List all modules

```bash
curl -s http://localhost:8000/api/v1/modules/ \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

### Get module schema

```bash
curl -s http://localhost:8000/api/v1/modules/echo \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

### Execute echo module

```bash
curl -s -X POST http://localhost:8000/api/v1/modules/echo/execute \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "input": {
      "text": "Hello, MCP System!",
      "uppercase": true,
      "repeat": 2
    },
    "metadata": {"source": "curl-example"}
  }' | python -m json.tool
```

### Execute text summarizer

```bash
curl -s -X POST http://localhost:8000/api/v1/modules/text-summarizer/execute \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "input": {
      "text": "The Model Context Protocol is an open standard... (long text here)",
      "style": "bullet",
      "max_words": 50
    }
  }' | python -m json.tool
```

### Execute module with context binding

```bash
curl -s -X POST http://localhost:8000/api/v1/modules/echo/execute \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"input\": {\"text\": \"context-bound call\"},
    \"context_id\": \"${CONTEXT_ID}\",
    \"session_id\": \"550e8400-e29b-41d4-a716-446655440000\"
  }" | python -m json.tool
```

### Pin to a specific module version

```bash
curl -s -X POST http://localhost:8000/api/v1/modules/echo@1.0.0/execute \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"input": {"text": "pinned version"}}' | python -m json.tool
```

### Module health

```bash
curl -s http://localhost:8000/api/v1/modules/health \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

---

## Memory Operations

### Store a short-term memory item

```bash
curl -s -X POST http://localhost:8000/api/v1/memory/store \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "The user prefers concise bullet-point responses.",
    "tier": "short_term",
    "session_id": "550e8400-e29b-41d4-a716-446655440000",
    "role": "user",
    "tags": ["preference", "style"]
  }' | python -m json.tool
```

### Store a long-term memory item (vector-indexed)

```bash
curl -s -X POST http://localhost:8000/api/v1/memory/store \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "MCP stands for Model Context Protocol, an open standard by Anthropic.",
    "tier": "long_term",
    "tags": ["fact", "definition"]
  }' | python -m json.tool
```

### Semantic memory retrieval

```bash
curl -s -X POST http://localhost:8000/api/v1/memory/retrieve \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What does MCP stand for?",
    "tier": "long_term",
    "k": 5,
    "score_threshold": 0.6
  }' | python -m json.tool
```

### Memory statistics

```bash
curl -s http://localhost:8000/api/v1/memory/stats \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

---

## Health Endpoints (no auth required)

```bash
# Liveness probe
curl -s http://localhost:8000/health/live

# Readiness probe
curl -s http://localhost:8000/health/ready

# Deep health check
curl -s http://localhost:8000/health/ | python -m json.tool

# Prometheus metrics
curl -s http://localhost:8000/metrics | grep mcp_
```
