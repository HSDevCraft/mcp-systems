"""Application configuration via Pydantic Settings.

All values can be overridden by environment variables or a .env file.
"""

from __future__ import annotations

import os
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"
    TEST = "test"


class LogFormat(str, Enum):
    JSON = "json"
    TEXT = "text"


class EmbeddingProvider(str, Enum):
    OPENAI = "openai"
    SENTENCE_TRANSFORMERS = "sentence-transformers"
    COHERE = "cohere"


class StorageBackend(str, Enum):
    REDIS = "redis"
    MEMORY = "memory"


class Settings(BaseSettings):
    """Central configuration for the MCP System.

    Loaded from environment variables + optional .env file.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Core ──────────────────────────────────────────────────────────────────
    mcp_env: Environment = Field(default=Environment.DEVELOPMENT, alias="MCP_ENV")
    mcp_host: str = Field(default="0.0.0.0", alias="MCP_HOST")
    mcp_port: int = Field(default=8000, alias="MCP_PORT")
    mcp_workers: int = Field(default=4, alias="MCP_WORKERS")
    mcp_secret_key: str = Field(default="changeme", alias="MCP_SECRET_KEY")
    mcp_debug: bool = Field(default=False, alias="MCP_DEBUG")

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    redis_max_connections: int = Field(default=20, alias="REDIS_MAX_CONNECTIONS")
    redis_socket_timeout: float = Field(default=5.0, alias="REDIS_SOCKET_TIMEOUT")
    redis_ttl_seconds: int = Field(default=3600, alias="REDIS_TTL_SECONDS")

    # ── Qdrant ────────────────────────────────────────────────────────────────
    qdrant_url: str = Field(default="http://localhost:6333", alias="QDRANT_URL")
    qdrant_api_key: str | None = Field(default=None, alias="QDRANT_API_KEY")
    qdrant_collection: str = Field(default="mcp_memory", alias="QDRANT_COLLECTION")
    qdrant_vector_size: int = Field(default=1536, alias="QDRANT_VECTOR_SIZE")

    # ── Embeddings ────────────────────────────────────────────────────────────
    embedding_provider: EmbeddingProvider = Field(
        default=EmbeddingProvider.OPENAI, alias="EMBEDDING_PROVIDER"
    )
    embedding_model: str = Field(
        default="text-embedding-3-small", alias="EMBEDDING_MODEL"
    )
    embedding_batch_size: int = Field(default=64, alias="EMBEDDING_BATCH_SIZE")

    # ── LLM ───────────────────────────────────────────────────────────────────
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_org_id: str | None = Field(default=None, alias="OPENAI_ORG_ID")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")

    # ── Auth ──────────────────────────────────────────────────────────────────
    jwt_secret_key: str = Field(default="changeme-jwt-secret", alias="JWT_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_expire_minutes: int = Field(default=60, alias="JWT_EXPIRE_MINUTES")
    jwt_refresh_expire_days: int = Field(default=7, alias="JWT_REFRESH_EXPIRE_DAYS")
    api_key_header: str = Field(default="X-API-Key", alias="API_KEY_HEADER")

    # ── Rate Limiting ─────────────────────────────────────────────────────────
    rate_limit_enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    rate_limit_requests: int = Field(default=100, alias="RATE_LIMIT_REQUESTS")
    rate_limit_window: int = Field(default=60, alias="RATE_LIMIT_WINDOW")

    # ── Observability ─────────────────────────────────────────────────────────
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: LogFormat = Field(default=LogFormat.JSON, alias="LOG_FORMAT")
    log_file: str | None = Field(default=None, alias="LOG_FILE")
    enable_metrics: bool = Field(default=True, alias="ENABLE_METRICS")
    metrics_port: int = Field(default=9091, alias="METRICS_PORT")
    otel_enabled: bool = Field(default=False, alias="OTEL_ENABLED")
    otel_endpoint: str = Field(default="http://localhost:4317", alias="OTEL_ENDPOINT")
    otel_service_name: str = Field(default="mcp-system", alias="OTEL_SERVICE_NAME")

    # ── Module System ─────────────────────────────────────────────────────────
    modules_dir: Path = Field(
        default=Path("src/modules/plugins"), alias="MODULES_DIR"
    )
    modules_hot_reload: bool = Field(default=False, alias="MODULES_HOT_RELOAD")
    modules_timeout_seconds: float = Field(
        default=30.0, alias="MODULES_TIMEOUT_SECONDS"
    )

    # ── Context Management ────────────────────────────────────────────────────
    context_max_tokens: int = Field(default=128000, alias="CONTEXT_MAX_TOKENS")
    context_max_messages: int = Field(default=1000, alias="CONTEXT_MAX_MESSAGES")
    context_ttl_seconds: int = Field(default=86400, alias="CONTEXT_TTL_SECONDS")

    # ── Storage ───────────────────────────────────────────────────────────────
    storage_backend: StorageBackend = Field(
        default=StorageBackend.REDIS, alias="STORAGE_BACKEND"
    )
    storage_encryption_key: str | None = Field(
        default=None, alias="STORAGE_ENCRYPTION_KEY"
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    cors_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:8080"],
        alias="CORS_ORIGINS",
    )
    cors_allow_credentials: bool = Field(
        default=True, alias="CORS_ALLOW_CREDENTIALS"
    )

    # ── Database (optional, for metadata) ────────────────────────────────────
    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    database_pool_size: int = Field(default=10, alias="DATABASE_POOL_SIZE")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"log_level must be one of {valid}, got {v!r}")
        return upper

    @model_validator(mode="after")
    def warn_weak_secrets(self) -> "Settings":
        if self.mcp_env == Environment.PRODUCTION:
            if self.mcp_secret_key == "changeme":
                raise ValueError("MCP_SECRET_KEY must be set in production")
            if self.jwt_secret_key == "changeme-jwt-secret":
                raise ValueError("JWT_SECRET_KEY must be set in production")
        return self

    @property
    def is_development(self) -> bool:
        return self.mcp_env == Environment.DEVELOPMENT

    @property
    def is_production(self) -> bool:
        return self.mcp_env == Environment.PRODUCTION

    @property
    def is_test(self) -> bool:
        return self.mcp_env == Environment.TEST

    def get_redis_key_prefix(self, tenant_id: str) -> str:
        return f"mcp:{tenant_id}"

    def get_context_key(self, tenant_id: str, context_id: str) -> str:
        return f"mcp:{tenant_id}:ctx:{context_id}"

    def get_messages_key(self, tenant_id: str, context_id: str) -> str:
        return f"mcp:{tenant_id}:ctx:{context_id}:messages"

    def get_session_key(self, tenant_id: str, session_id: str) -> str:
        return f"mcp:{tenant_id}:session:{session_id}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached Settings instance (singleton)."""
    return Settings()


def override_settings(**kwargs: Any) -> Settings:
    """Create a new Settings instance with overrides (for testing)."""
    return Settings(**kwargs)
