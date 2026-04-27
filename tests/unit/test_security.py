"""Unit tests for security utilities."""

from __future__ import annotations

import pytest

from src.utils.exceptions import AuthenticationError, TokenExpiredError
from src.utils.security import (
    count_tokens,
    create_access_token,
    decode_token,
    generate_api_key,
    hash_password,
    mask_secret,
    sanitize_string,
    verify_api_key,
    verify_password,
)

pytestmark = pytest.mark.unit


class TestJWTTokens:
    def test_create_and_decode_token(self, test_settings):
        token = create_access_token(
            subject="user-123",
            tenant_id="acme-corp",
            roles=["admin"],
        )
        payload = decode_token(token)
        assert payload["sub"] == "user-123"
        assert payload["tenant_id"] == "acme-corp"
        assert "admin" in payload["roles"]

    def test_decoded_token_has_expiry(self, test_settings):
        token = create_access_token(subject="u1", tenant_id="t1")
        payload = decode_token(token)
        assert "exp" in payload
        assert "iat" in payload
        assert "jti" in payload

    def test_decoded_token_has_issuer(self, test_settings):
        token = create_access_token(subject="u1", tenant_id="t1")
        payload = decode_token(token)
        assert payload["iss"] == "mcp-system"

    def test_invalid_token_raises_auth_error(self, test_settings):
        with pytest.raises(AuthenticationError):
            decode_token("not.a.valid.jwt")

    def test_tampered_token_raises_auth_error(self, test_settings):
        token = create_access_token(subject="u1", tenant_id="t1")
        tampered = token[:-5] + "XXXXX"
        with pytest.raises(AuthenticationError):
            decode_token(tampered)

    def test_expired_token_raises_token_expired_error(self, test_settings):
        from datetime import timedelta

        token = create_access_token(
            subject="u1",
            tenant_id="t1",
            expires_delta=timedelta(seconds=-1),
        )
        with pytest.raises(TokenExpiredError):
            decode_token(token)

    def test_extra_claims_embedded(self, test_settings):
        token = create_access_token(
            subject="u1",
            tenant_id="t1",
            extra_claims={"custom_field": "custom_value"},
        )
        payload = decode_token(token)
        assert payload["custom_field"] == "custom_value"

    def test_each_token_has_unique_jti(self, test_settings):
        t1 = create_access_token(subject="u1", tenant_id="t1")
        t2 = create_access_token(subject="u1", tenant_id="t1")
        p1 = decode_token(t1)
        p2 = decode_token(t2)
        assert p1["jti"] != p2["jti"]


class TestAPIKeys:
    def test_generate_api_key_returns_raw_and_hash(self):
        raw, hashed = generate_api_key()
        assert raw.startswith("mcp_")
        assert len(raw) > 10
        assert hashed != raw

    def test_verify_api_key_correct(self):
        raw, hashed = generate_api_key()
        assert verify_api_key(raw, hashed) is True

    def test_verify_api_key_wrong_key(self):
        _, hashed = generate_api_key()
        raw_other, _ = generate_api_key()
        assert verify_api_key(raw_other, hashed) is False

    def test_verify_api_key_tampered_hash(self):
        raw, hashed = generate_api_key()
        tampered = hashed[:-4] + "XXXX"
        assert verify_api_key(raw, tampered) is False

    def test_each_key_is_unique(self):
        raw1, _ = generate_api_key()
        raw2, _ = generate_api_key()
        assert raw1 != raw2


class TestPasswordHashing:
    def test_hash_and_verify_password(self):
        hashed = hash_password("mysecretpassword")
        assert verify_password("mysecretpassword", hashed) is True

    def test_wrong_password_fails(self):
        hashed = hash_password("correct")
        assert verify_password("wrong", hashed) is False

    def test_hash_is_not_plaintext(self):
        plain = "plaintext"
        hashed = hash_password(plain)
        assert plain not in hashed

    def test_same_password_different_hashes(self):
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2  # bcrypt uses random salt


class TestInputSanitization:
    def test_sanitize_string_truncates(self):
        long_str = "x" * 200_000
        result = sanitize_string(long_str, max_length=100)
        assert len(result) == 100

    def test_sanitize_string_removes_null_bytes(self):
        with_null = "hello\x00world"
        result = sanitize_string(with_null)
        assert "\x00" not in result
        assert "helloworld" == result

    def test_sanitize_string_preserves_normal_text(self):
        text = "Hello, world! This is a normal string."
        assert sanitize_string(text) == text

    def test_mask_secret_hides_most_chars(self):
        key = "mcp_abc123def456ghi789"
        masked = mask_secret(key, visible_chars=4)
        assert masked.endswith("i789")
        assert "*" in masked

    def test_mask_secret_short_string(self):
        assert mask_secret("ab", visible_chars=4) == "****"


class TestTokenCounting:
    def test_count_tokens_returns_positive(self):
        count = count_tokens("Hello, world!")
        assert count > 0

    def test_count_tokens_longer_text_more_tokens(self):
        short = count_tokens("Hi")
        long = count_tokens("This is a much longer piece of text with many more words")
        assert long > short

    def test_count_tokens_empty_string(self):
        count = count_tokens("")
        assert count >= 0

    def test_count_tokens_consistent(self):
        text = "The quick brown fox"
        assert count_tokens(text) == count_tokens(text)
