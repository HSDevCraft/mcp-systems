# 12 — Security

Security in MCP requires thinking about three layers: transport security, authentication, and
trust boundaries between host, client, and server.

---

## Trust Model

```
FULLY TRUSTED                    PARTIALLY TRUSTED          UNTRUSTED
─────────────────────────────    ───────────────────────    ──────────────────────
Host process                     Server process             External APIs called
  │                                  │                      by the server
  └─ Client (same process)           └─ May expose malicious
     └─ LLM context window             tool results
     └─ User credentials              └─ May lie about resources
     └─ System prompt                 └─ Cannot see host's context
```

**The host is the trust anchor.** It controls:
- Which servers are connected
- What data is shared with which server
- Whether tool calls are approved
- What appears in the LLM context window

---

## Transport Security

### stdio
- Trust is inherited from OS process ownership
- **Never** pass secrets as CLI arguments (visible in `ps aux`)
- Use `env` in `StdioServerParameters` for secrets

```jsonc
// Claude Desktop — secure secret injection
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "<your_pat>" }
    }
  }
}
```

### SSE / HTTP
- **Always use HTTPS** in production (TLS 1.2+ minimum)
- Validate TLS certificates; never disable certificate verification
- Use HTTP Strict Transport Security (HSTS) headers

```python
# Client — enforce TLS
import ssl, httpx

ssl_ctx = ssl.create_default_context()
# ssl_ctx.check_hostname = False  ← NEVER do this in production

async with sse_client(
    "https://api.example.com/mcp/sse",
    headers={"Authorization": "Bearer <token>"},
) as streams:
    ...
```

---

## Authentication Patterns

### Pattern 1 — API Key (simplest)
```python
# Server-side middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
import secrets

VALID_KEYS = {"sk-abc123", "sk-def456"}   # load from env in practice

class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        key = request.headers.get("X-API-Key") or request.headers.get("Authorization", "").removeprefix("Bearer ")
        if key not in VALID_KEYS:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)
```

```python
# Client-side
async with sse_client(
    url,
    headers={"X-API-Key": os.environ["MCP_API_KEY"]},
) as streams:
    ...
```

### Pattern 2 — JWT Bearer Token
```python
from jose import JWTError, jwt
from datetime import datetime, timedelta

SECRET_KEY = os.environ["JWT_SECRET"]
ALGORITHM  = "HS256"

def create_token(subject: str, scopes: list[str]) -> str:
    payload = {
        "sub": subject,
        "scopes": scopes,
        "exp": datetime.utcnow() + timedelta(hours=1),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError as e:
        raise PermissionError(f"Invalid token: {e}")

# In request handler:
class JWTMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse({"error": "Missing Bearer token"}, status_code=401)
        try:
            payload = verify_token(auth.removeprefix("Bearer "))
            request.state.user  = payload["sub"]
            request.state.scopes = payload["scopes"]
        except PermissionError as e:
            return JSONResponse({"error": str(e)}, status_code=401)
        return await call_next(request)
```

### Pattern 3 — OAuth 2.1 (recommended for production remote servers)
```
Client (Host)                    MCP Server                    Auth Server (e.g. Auth0)
     │                               │                               │
     │  1. GET /.well-known/mcp      │                               │
     ├──────────────────────────────►│                               │
     │◄── { authorization_endpoint } ┤                               │
     │                               │                               │
     │  2. Redirect to auth server   │                               │
     ├──────────────────────────────────────────────────────────────►│
     │◄─────────────────────── auth code ────────────────────────────┤
     │                               │                               │
     │  3. POST /token (code)        │                               │
     ├──────────────────────────────────────────────────────────────►│
     │◄─────────────────────── access_token ─────────────────────────┤
     │                               │                               │
     │  4. GET /sse  Bearer <token>  │                               │
     ├──────────────────────────────►│                               │
     │                   verifies token with auth server             │
     │◄───── SSE stream ─────────────┤                               │
```

```python
# OAuth 2.1 PKCE flow (client-side simplified)
import secrets, hashlib, base64, urllib.parse, httpx

def generate_pkce():
    verifier  = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge

async def oauth_flow(auth_url: str, client_id: str, redirect_uri: str) -> str:
    verifier, challenge = generate_pkce()
    params = {
        "response_type":         "code",
        "client_id":             client_id,
        "redirect_uri":          redirect_uri,
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
        "scope":                 "mcp:read mcp:write",
    }
    auth_redirect = f"{auth_url}?{urllib.parse.urlencode(params)}"
    # Open browser, wait for redirect with code...
    code = await wait_for_callback_code()  # host-specific implementation
    return await exchange_code(code, verifier, client_id, redirect_uri)
```

---

## Tool Call Authorization

### Scope-based tool authorization
```python
TOOL_SCOPES = {
    "read_file":   "files:read",
    "write_file":  "files:write",
    "delete_file": "files:delete",
    "exec_code":   "code:execute",
}

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    required_scope = TOOL_SCOPES.get(name)
    if required_scope:
        user_scopes = server.request_context.meta.get("scopes", [])
        if required_scope not in user_scopes:
            return [types.TextContent(
                type="text",
                text=f"Permission denied: requires scope '{required_scope}'"
            )]
    return await execute_tool(name, arguments)
```

### Human-in-the-loop for destructive tools
```python
REQUIRES_CONFIRMATION = {"delete_file", "exec_code", "send_email", "write_db"}

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name in REQUIRES_CONFIRMATION:
        # Signal to the host that confirmation is needed
        # Convention: return a special marker that the host UI intercepts
        return [types.TextContent(
            type="text",
            text=f"⚠️ CONFIRM_REQUIRED: About to {name} with args: {arguments}",
        )]
    return await execute_tool(name, arguments)
```

---

## Input Validation & Sanitisation

### JSON Schema validation (always done by SDK, but add business logic)
```python
import re
from pathlib import Path

def validate_file_path(path: str, allowed_root: Path) -> Path:
    """Validate and resolve a file path, preventing traversal attacks."""
    if not path or ".." in path:
        raise ValueError(f"Invalid path: {path}")
    resolved = (allowed_root / path).resolve()
    if not resolved.is_relative_to(allowed_root):
        raise PermissionError(f"Path traversal detected: {path}")
    return resolved

def sanitise_sql(query: str) -> str:
    """Basic SQL injection prevention (prefer parameterised queries)."""
    dangerous = ["DROP", "DELETE", "TRUNCATE", "ALTER", "CREATE", "INSERT", "UPDATE"]
    upper = query.upper()
    for kw in dangerous:
        if kw in upper:
            raise ValueError(f"Potentially dangerous SQL keyword: {kw}")
    return query

def sanitise_shell_arg(arg: str) -> str:
    """Prevent shell injection in subprocess arguments."""
    if re.search(r'[;&|`$<>\\]', arg):
        raise ValueError(f"Invalid characters in shell argument: {arg!r}")
    return arg
```

### Prompt injection prevention
```python
def sanitise_for_llm(text: str) -> str:
    """
    Remove patterns that could hijack LLM instructions
    when embedding external content in prompts/sampling calls.
    """
    # Remove common prompt injection patterns
    patterns = [
        r"ignore (all |previous |above )?instructions?",
        r"you are now",
        r"disregard (your |all )?",
        r"</?system>",
        r"\[INST\]|\[/INST\]",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "[FILTERED]", text, flags=re.IGNORECASE)
    return text
```

---

## Secret Management

### Never hardcode secrets
```python
# BAD
API_KEY = "sk-abc123"

# GOOD — load from environment
import os
API_KEY = os.environ["EXTERNAL_API_KEY"]  # raises if missing

# BETTER — use pydantic-settings with validation
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    external_api_key: str
    db_password:      str
    jwt_secret:       str

    class Config:
        env_file = ".env"

settings = Settings()
```

### Secret rotation
- Use short-lived tokens (< 1 hour) and refresh them
- Support multiple valid API keys simultaneously during rotation
- Log key usage (not key values) for audit

---

## Rate Limiting

```python
from collections import defaultdict
from time import time
import asyncio

class RateLimiter:
    def __init__(self, max_calls: int = 60, window_seconds: float = 60.0):
        self.max_calls = max_calls
        self.window    = window_seconds
        self._calls: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> bool:
        async with self._lock:
            now = time()
            calls = self._calls[key]
            # Remove old calls outside the window
            self._calls[key] = [t for t in calls if now - t < self.window]
            if len(self._calls[key]) >= self.max_calls:
                return False
            self._calls[key].append(now)
            return True

rate_limiter = RateLimiter(max_calls=60, window_seconds=60)

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    client_id = server.request_context.meta.get("client_id", "anonymous")
    if not await rate_limiter.check(client_id):
        return [types.TextContent(type="text", text="Rate limit exceeded. Try again in 60 seconds.")]
    return await execute_tool(name, arguments)
```

---

## Security Checklist

| Item | Priority |
|------|----------|
| Use HTTPS for all remote transports | Critical |
| Validate all tool inputs (types + business rules) | Critical |
| Sanitise file paths (prevent traversal) | Critical |
| Never log secret values | Critical |
| Load secrets from environment, not source code | Critical |
| Implement rate limiting per client/tenant | High |
| Validate JWT expiry and signature | High |
| Sanitise content before embedding in LLM prompts | High |
| Implement human approval for destructive tools | High |
| Log all tool calls with user/tenant ID | High |
| Scope tools to minimum required permissions | Medium |
| Rotate secrets regularly | Medium |
| Implement request size limits | Medium |
| Audit logs for security events | Medium |
