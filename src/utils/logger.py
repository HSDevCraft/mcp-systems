"""Structured logging via structlog.

Provides JSON-formatted logs in production and pretty-printed logs in dev.
All loggers are pre-bound with service metadata and support trace_id injection.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, WrappedLogger


def _add_service_info(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Inject static service metadata into every log entry."""
    event_dict.setdefault("service", "mcp-system")
    event_dict.setdefault("version", "0.1.0")
    return event_dict


def _drop_color_message_key(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Remove uvicorn color codes injected by access logger."""
    event_dict.pop("color_message", None)
    return event_dict


def configure_logging(log_level: str = "INFO", log_format: str = "json") -> None:
    """Configure structlog + stdlib logging.

    Call once at application startup before any loggers are used.

    Args:
        log_level: One of DEBUG, INFO, WARNING, ERROR, CRITICAL.
        log_format: "json" for production, "text" for development.
    """
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        _add_service_info,
        _drop_color_message_key,
    ]

    if log_format == "json":
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(log_level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level.upper())

    # Silence noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("qdrant_client").setLevel(logging.WARNING)


def get_logger(name: str, **initial_values: Any) -> structlog.BoundLogger:
    """Get a structlog bound logger pre-seeded with context values.

    Args:
        name: Logger name (typically __name__).
        **initial_values: Context key-value pairs bound to all log entries.

    Returns:
        A structlog BoundLogger instance.

    Example:
        logger = get_logger(__name__, component="context_manager")
        logger.info("context_created", context_id=str(ctx.id))
    """
    return structlog.get_logger(name).bind(**initial_values)


def bind_request_context(
    request_id: str,
    tenant_id: str,
    user_id: str | None = None,
    context_id: str | None = None,
) -> None:
    """Bind request-level context variables (persists for the current async task).

    Call at the start of each request handler. Uses structlog.contextvars
    which are automatically propagated across async boundaries within the same task.

    Args:
        request_id: Unique request identifier.
        tenant_id: Tenant namespace.
        user_id: Authenticated user identifier.
        context_id: MCP context ID if applicable.
    """
    ctx: dict[str, Any] = {
        "request_id": request_id,
        "tenant_id": tenant_id,
    }
    if user_id:
        ctx["user_id"] = user_id
    if context_id:
        ctx["context_id"] = context_id
    structlog.contextvars.bind_contextvars(**ctx)


def clear_request_context() -> None:
    """Clear request-level context variables."""
    structlog.contextvars.clear_contextvars()
