# 08 — Security & Production Hardening

## Threat Model

| Threat | Vector | Mitigation |
|--------|--------|-----------|
| Unauthorized access | Missing/stolen credentials | JWT + API key dual-auth |
| Token theft | Network interception | HTTPS only in production, short JWT TTL (30min prod) |
| Prompt injection | Malicious module input | Input sanitization + content validation |
| DoS / resource exhaustion | High-volume requests | Sliding-window rate limiting per tenant |
| Data leakage across tenants | Shared Redis/Qdrant | Tenant-namespaced storage keys |
| SSRF from modules | Module making arbitrary outbound calls | Module outbound allowlist policy |
| Secret exposure | Env var leakage, log scraping | Secrets never logged, masked in errors |
| Dependency vulnerabilities | Outdated packages | Dependabot + weekly `pip audit` in CI |
| Container breakout | Privileged container | Non-root user (UID 1001) in Dockerfile |
| Path traversal | Module discovery scan | Restricted to configured `MODULES_DIR` |

---

## Authentication

### JWT Tokens

**Algorithm**: HS256 (symmetric). For higher assurance, switch to RS256 (asymmetric) by changing `JWT_ALGORITHM=RS256` and providing RSA key pair.

**Token payload**:
```json
{
  "sub": "user-id",
  "tenant_id": "acme-corp",
  "roles": ["admin", "reader"],
  "iat": 1704067200,
  "exp": 1704070800,
  "iss": "mcp-system",
  "jti": "unique-token-id"
}
```

**jti (JWT ID)**: Unique per token. Used for token revocation — store revoked JTIs in Redis with TTL matching token expiry.

**Rotation**: Implement refresh token rotation in production:
```
Client: POST /api/v1/auth/refresh  {refresh_token: "..."}
Server: Invalidate old refresh token, issue new access + refresh pair
```

### API Keys

**Key format**: `mcp_{32-char-urlsafe-base64}` (e.g. `mcp_abc123...`)

**Storage**: Only the HMAC-SHA256 hash of the key is stored. The raw key is shown exactly once at creation and never stored.

**Validation**: Constant-time comparison (`hmac.compare_digest`) prevents timing attacks.

**Rotation**: Keys can be rotated by generating a new key and revoking the old hash. Zero-downtime rotation: add new key, update client, revoke old key.

**Scoped keys** (recommended extension): Add `scopes` field to key metadata:
```python
{"key_hash": "...", "tenant_id": "acme", "scopes": ["modules:execute", "context:read"]}
```

---

## Authorization (RBAC)

Roles are embedded in the JWT `roles` claim. The API layer checks roles for sensitive operations:

| Role | Permissions |
|------|------------|
| `admin` | All operations including module management |
| `writer` | Context CRUD, module execution, memory write |
| `reader` | Context read, memory retrieve, module list |
| `module_admin` | Register/unregister modules |

Implementing role checks (add to routers as needed):

```python
def require_role(required_role: str):
    def dependency(request: Request):
        roles = getattr(request.state, "roles", [])
        if required_role not in roles and "admin" not in roles:
            raise AuthorizationError(action="execute", resource="module")
    return Depends(dependency)
```

---

## Rate Limiting

### Algorithm: Sliding Window Counter

```
Window = 60s (configurable)
Limit  = 100 requests (configurable)
Key    = mcp:ratelimit:{tenant_id}:{user_id}:{window_start_epoch_bucket}

On each request:
  1. INCR the counter key
  2. If new key: EXPIRE it at window * 2
  3. If counter > limit: return 429 with Retry-After header
```

### Per-Tenant Limits

For multi-tenant deployments, set per-tenant limits in a configuration store:
```python
# Redis hash: mcp:ratelimit:config:{tenant_id}
# Fields: requests_per_window, window_seconds
tenant_limit = await redis.hgetall(f"mcp:ratelimit:config:{tenant_id}")
limit = int(tenant_limit.get("requests_per_window", settings.rate_limit_requests))
```

### Rate Limit Headers

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 42
Content-Type: application/json

{
  "type": "https://errors.mcp-system.io/rate_limit/exceeded",
  "title": "RateLimitError",
  "status": 429,
  "detail": "Rate limit of 100 requests per 60s exceeded",
  "retry_after_seconds": 42
}
```

---

## Input Validation

### Pydantic Schema Validation (primary)

All API inputs are validated by Pydantic v2 models before reaching business logic:
- Type coercion and strict type checks
- Min/max length for strings
- Numeric bounds (`ge`, `le`)
- Regex patterns where needed

### String Sanitization (secondary)

`src/utils/security.py::sanitize_string()` applies:
- Truncation to `max_length` (default 100KB)
- Null byte stripping (`\x00`)
- Applied to all free-text inputs before storage

### Content-length Guard

FastAPI + Uvicorn enforce a max request body size. For production, set:
```yaml
# Nginx ingress annotation
nginx.ingress.kubernetes.io/proxy-body-size: "10m"
```

### Module Input Isolation

Module inputs never leave Python process memory during execution. Modules that make outbound HTTP calls should validate URLs against an allowlist:

```python
ALLOWED_HOSTS = {"api.openai.com", "api.anthropic.com"}

def validate_url(url: str) -> None:
    from urllib.parse import urlparse
    host = urlparse(url).netloc
    if host not in ALLOWED_HOSTS:
        raise ValidationError(f"Outbound URL not allowed: {host}")
```

---

## Secrets Management

### Environment Variables (minimum)

- Secrets are loaded from environment variables or `.env` (never committed)
- Validation at startup: `Settings.warn_weak_secrets()` raises if default secrets are used in production
- Secrets are never logged (masked via `mask_secret()` if they appear in log context)

### Fernet Encryption (context content)

For regulatory environments (HIPAA, GDPR), context content can be encrypted at rest:
```bash
# Generate key
make generate-fernet
# Set in environment
STORAGE_ENCRYPTION_KEY=your_fernet_key_here
```

The `STORAGE_ENCRYPTION_KEY` is used to encrypt context JSON before writing to Redis and decrypt on read.

### External Secret Stores (production)

For Kubernetes, use:
- **AWS Secrets Manager** + External Secrets Operator
- **HashiCorp Vault** + Vault Agent Injector
- **Kubernetes Secrets** (encrypted at rest with KMS)

Never put plaintext secrets in `configmap.yaml` — use `Secret` objects with RBAC restricting access to the service account only.

---

## HTTPS / TLS

**Development**: HTTP acceptable (local only)

**Production**: TLS must be terminated at:
- Nginx Ingress (Kubernetes) with cert-manager + Let's Encrypt
- AWS ALB with ACM certificate
- Cloudflare Tunnel

The application itself does not handle TLS — terminate at the load balancer.

```yaml
# cert-manager ClusterIssuer
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: ops@your-org.com
    privateKeySecretRef:
      name: letsencrypt-prod
    solvers:
      - http01:
          ingress:
            class: nginx
```

---

## Security Headers

Add to Nginx/ALB configuration:
```
Strict-Transport-Security: max-age=31536000; includeSubDomains
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
X-XSS-Protection: 1; mode=block
Referrer-Policy: strict-origin-when-cross-origin
Content-Security-Policy: default-src 'self'
```

---

## Audit Logging

Every authenticated API call is logged with:
```json
{
  "event": "request_complete",
  "user_id": "user-123",
  "tenant_id": "acme-corp",
  "auth_type": "jwt",
  "method": "POST",
  "path": "/api/v1/modules/echo/execute",
  "status_code": 200,
  "request_id": "uuid"
}
```

Ship these to an immutable log store (CloudWatch Logs, Splunk, Datadog) for compliance audit trails. Use `LOG_LEVEL=INFO` in production to capture all requests.

---

## Dependency Security

```bash
# Audit installed packages for known vulnerabilities
pip audit

# Check for outdated packages
pip list --outdated

# Auto-update security patches (in CI)
pip install --upgrade $(pip list --outdated --format=freeze | cut -d= -f1)
```

Configure Dependabot in `.github/dependabot.yml`:
```yaml
version: 2
updates:
  - package-ecosystem: pip
    directory: "/"
    schedule:
      interval: weekly
    labels:
      - security
```

---

## Container Security

The production `Dockerfile` applies:
- **Non-root user** (UID 1001) — prevents container privilege escalation
- **Read-only filesystem** for source code (mounted as ConfigMap in k8s)
- **No shell** — `CMD` uses exec form
- **Minimal base image** (`python:3.12-slim` — no build tools in runtime)
- **Pinned base image digest** (recommended for supply-chain security)

Kubernetes Pod Security:
```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 1001
  readOnlyRootFilesystem: true
  allowPrivilegeEscalation: false
  capabilities:
    drop: ["ALL"]
```
