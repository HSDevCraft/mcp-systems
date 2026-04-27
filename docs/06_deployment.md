# 06 — Deployment

## Local Development (no Docker)

```bash
# Prerequisites: Python 3.11+, Redis running on localhost:6379
make setup          # installs all deps
cp .env.example .env
make api            # http://localhost:8000
```

The API server falls back to an in-memory Redis substitute when Redis is
unavailable, so it starts without any infrastructure in dev mode.

---

## Docker Compose (recommended for dev/staging)

```bash
cp .env.example .env   # fill in API keys
docker-compose up --build -d
```

| Service | URL | Notes |
|---------|-----|-------|
| MCP API | http://localhost:8000 | API gateway + docs |
| Redis | localhost:6379 | Short-term memory |
| Qdrant | http://localhost:6333 | Vector memory |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3000 | Dashboards (admin/admin) |

**Scaling the API locally:**
```bash
docker-compose up --scale mcp-api=3 -d
```

Redis and Qdrant are shared; the API instances are stateless.

---

## Production: Kubernetes

### Namespace setup
```bash
kubectl create namespace mcp-system
```

### Secrets (never store plaintext in repo)
```bash
kubectl create secret generic mcp-secrets \
  --namespace=mcp-system \
  --from-literal=MCP_SECRET_KEY="$(openssl rand -base64 32)" \
  --from-literal=JWT_SECRET_KEY="$(openssl rand -base64 32)" \
  --from-literal=OPENAI_API_KEY="sk-..." \
  --from-literal=REDIS_URL="redis://redis-master:6379/0" \
  --from-literal=QDRANT_URL="http://qdrant:6333"
```

### Apply manifests
```bash
kubectl apply -f deploy/kubernetes/configmap.yaml
kubectl apply -f deploy/kubernetes/deployment.yaml
kubectl apply -f deploy/kubernetes/service.yaml
```

### Verify
```bash
kubectl get pods -n mcp-system
kubectl logs -f deployment/mcp-api -n mcp-system
kubectl port-forward svc/mcp-api 8000:80 -n mcp-system
```

---

## Infrastructure Dependencies

### Redis

**Development:** Docker Compose service
**Production:** Use managed Redis:
- AWS ElastiCache (Redis 7, cluster mode)
- Upstash Redis (serverless, pay-per-request)
- Redis Enterprise Cloud

Minimum config:
```
maxmemory: 512mb
maxmemory-policy: allkeys-lru
appendonly: yes          # AOF persistence
```

### Qdrant

**Development:** Docker Compose service
**Production:** Options:
- Self-hosted StatefulSet (3 nodes, replication factor=2)
- Qdrant Cloud (managed)

Collection must be created before the API starts:
```python
# Handled automatically by LongTermMemoryStore.ensure_collection()
# Called in _lifespan() at startup
```

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MCP_ENV` | No | `development` | `development\|production\|test` |
| `MCP_SECRET_KEY` | **Yes (prod)** | — | App secret (32+ chars) |
| `MCP_PORT` | No | `8000` | API listen port |
| `MCP_WORKERS` | No | `4` | Uvicorn worker count |
| `REDIS_URL` | No | `redis://localhost:6379/0` | Redis connection string |
| `QDRANT_URL` | No | `http://localhost:6333` | Qdrant connection |
| `QDRANT_API_KEY` | No | — | Qdrant Cloud API key |
| `OPENAI_API_KEY` | No | — | Required if using OpenAI embedding |
| `JWT_SECRET_KEY` | **Yes (prod)** | — | JWT signing key (32+ chars) |
| `JWT_EXPIRE_MINUTES` | No | `60` | JWT token lifetime |
| `LOG_LEVEL` | No | `INFO` | Log level |
| `LOG_FORMAT` | No | `json` | `json\|text` |
| `ENABLE_METRICS` | No | `true` | Prometheus metrics |
| `RATE_LIMIT_ENABLED` | No | `true` | Enable rate limiting |
| `RATE_LIMIT_REQUESTS` | No | `100` | Requests per window |
| `RATE_LIMIT_WINDOW` | No | `60` | Window in seconds |
| `CONTEXT_MAX_TOKENS` | No | `128000` | Max tokens per context |
| `CONTEXT_TTL_SECONDS` | No | `86400` | Context TTL (24h) |
| `OTEL_ENABLED` | No | `false` | OpenTelemetry tracing |
| `OTEL_ENDPOINT` | No | `http://localhost:4317` | OTLP exporter endpoint |

---

## CI/CD Pipeline

The provided GitHub Actions workflows (`.github/workflows/`) implement:

### `ci.yml` — runs on every push/PR
```
Push/PR
  └── quality (ruff + black + mypy)
       └── unit-tests (Python 3.11, 3.12)
            └── integration-tests (with Redis service)
            └── docker build + health check
```

### `release.yml` — runs on version tag `v*.*.*`
```
git tag v1.0.0 && git push --tags
  └── Build distribution packages
  └── Create GitHub Release (auto-generated notes)
  └── Push Docker image to GHCR
  └── Publish to PyPI (non-alpha tags)
```

### Deployment to Kubernetes (add to release.yml)
```yaml
- name: Deploy to Kubernetes
  run: |
    kubectl set image deployment/mcp-api \
      mcp-api=ghcr.io/${{ github.repository }}:${{ steps.version.outputs.VERSION }} \
      -n mcp-system
    kubectl rollout status deployment/mcp-api -n mcp-system
```

---

## Zero-Downtime Deployment

The Kubernetes `Deployment` uses `RollingUpdate` with `maxUnavailable: 0`:

1. New pod starts, passes readiness probe (`/health/ready`)
2. Traffic shifted to new pod by Service
3. Old pod receives `SIGTERM`, Uvicorn gracefully drains in-flight requests
4. Old pod terminated after `terminationGracePeriodSeconds: 30`

No database migrations are required (stateless API, Redis state is compatible
across versions, Qdrant payload schema is additive).

---

## Resource Sizing

| Component | Minimum | Recommended (100 RPS) | Notes |
|-----------|---------|----------------------|-------|
| API pods | 2 × 250m CPU / 512Mi | 4 × 500m CPU / 1Gi | HPA to 20 |
| Redis | 1 vCPU / 1Gi | 2 vCPU / 4Gi | 512MB maxmemory |
| Qdrant | 2 vCPU / 4Gi | 4 vCPU / 16Gi | Depends on vector count |
