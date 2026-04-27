"""Security utilities: JWT handling, API key hashing, input sanitization."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from src.utils.config import get_settings
from src.utils.exceptions import AuthenticationError, TokenExpiredError

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ── JWT ───────────────────────────────────────────────────────────────────────


def create_access_token(
    subject: str,
    tenant_id: str,
    roles: list[str] | None = None,
    extra_claims: dict[str, Any] | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a signed JWT access token.

    Args:
        subject: The user identifier (sub claim).
        tenant_id: Tenant namespace for multi-tenancy.
        roles: List of role strings for RBAC.
        extra_claims: Additional custom claims to embed.
        expires_delta: Token lifetime; defaults to JWT_EXPIRE_MINUTES.

    Returns:
        Encoded JWT string.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    expire = now + (
        expires_delta or timedelta(minutes=settings.jwt_expire_minutes)
    )

    payload: dict[str, Any] = {
        "sub": subject,
        "tenant_id": tenant_id,
        "roles": roles or [],
        "iat": now,
        "exp": expire,
        "iss": "mcp-system",
        "jti": secrets.token_urlsafe(16),
    }
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(subject: str, tenant_id: str) -> str:
    """Create a longer-lived refresh token."""
    settings = get_settings()
    return create_access_token(
        subject=subject,
        tenant_id=tenant_id,
        expires_delta=timedelta(days=settings.jwt_refresh_expire_days),
        extra_claims={"token_type": "refresh"},
    )


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT token.

    Args:
        token: Raw JWT string.

    Returns:
        Decoded payload dict.

    Raises:
        TokenExpiredError: If the token is expired.
        AuthenticationError: If the token is invalid for any other reason.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"verify_exp": True},
        )
        return payload
    except JWTError as e:
        if "expired" in str(e).lower():
            raise TokenExpiredError() from e
        raise AuthenticationError(f"Invalid token: {e}") from e


def extract_token_claims(token: str) -> dict[str, Any]:
    """Extract claims without verifying signature (for logging only)."""
    return jwt.get_unverified_claims(token)


# ── API Keys ──────────────────────────────────────────────────────────────────


def generate_api_key() -> tuple[str, str]:
    """Generate a new API key and its HMAC-SHA256 hash.

    Returns:
        Tuple of (raw_key, hashed_key). Store only the hash.
        The raw key is shown to the user once and never stored.

    Example:
        raw_key, hashed_key = generate_api_key()
        # Store hashed_key in DB/Redis
        # Return raw_key to user
    """
    raw_key = f"mcp_{secrets.token_urlsafe(32)}"
    hashed = _hash_api_key(raw_key)
    return raw_key, hashed


def verify_api_key(raw_key: str, stored_hash: str) -> bool:
    """Constant-time comparison of API key against stored hash.

    Args:
        raw_key: The key presented by the client.
        stored_hash: The HMAC-SHA256 hash stored in the backend.

    Returns:
        True if the key matches, False otherwise.
    """
    expected = _hash_api_key(raw_key)
    return hmac.compare_digest(expected, stored_hash)


def _hash_api_key(key: str) -> str:
    settings = get_settings()
    return hmac.new(
        settings.mcp_secret_key.encode(),
        key.encode(),
        hashlib.sha256,
    ).hexdigest()


# ── Password Hashing ──────────────────────────────────────────────────────────


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return pwd_context.verify(plain_password, hashed_password)


# ── Input Sanitization ────────────────────────────────────────────────────────

_DANGEROUS_PATTERNS = [
    "{{",
    "}}",
    "<script",
    "javascript:",
    "data:text/html",
    "\x00",  # null byte
]


def sanitize_string(value: str, max_length: int = 100_000) -> str:
    """Sanitize a string input for safe storage and logging.

    - Truncates to max_length
    - Strips null bytes
    - Does NOT HTML-escape (that is the output layer's job)

    Args:
        value: Raw input string.
        max_length: Maximum allowed length.

    Returns:
        Sanitized string.
    """
    if len(value) > max_length:
        value = value[:max_length]
    value = value.replace("\x00", "")
    return value


def mask_secret(value: str, visible_chars: int = 4) -> str:
    """Mask a secret string for safe logging.

    Args:
        value: The secret string.
        visible_chars: How many trailing characters to reveal.

    Returns:
        String like "mcp_****abcd".

    Example:
        mask_secret("mcp_abc123xyz", 4) → "mcp_*****xyz"
    """
    if len(value) <= visible_chars:
        return "****"
    return "*" * (len(value) - visible_chars) + value[-visible_chars:]


# ── Token Counter ─────────────────────────────────────────────────────────────


def count_tokens(text: str, model: str = "gpt-4o") -> int:
    """Count tokens in a string using tiktoken.

    Falls back to a simple word-count heuristic if tiktoken is unavailable.

    Args:
        text: Input text.
        model: Model name for tiktoken encoding selection.

    Returns:
        Approximate token count.
    """
    try:
        import tiktoken

        enc = tiktoken.encoding_for_model(model)
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text.split()) + len(text) // 4)
