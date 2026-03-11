"""structlog configuration for PHI-safe structured logging."""

from __future__ import annotations

import logging
from typing import Literal

import structlog


def _otel_trace_processor(
    _logger: object, _method_name: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """Inject OpenTelemetry trace and span IDs if available."""
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.trace_id:
            event_dict["trace_id"] = format(ctx.trace_id, "032x")
            event_dict["span_id"] = format(ctx.span_id, "016x")
    except ImportError:
        pass
    return event_dict


def configure_logging(
    log_format: Literal["json", "console"] = "console",
    log_level: str = "INFO",
    service: str = "health-coach",
    environment: str = "dev",
) -> None:
    """Configure structlog with PHI-safe processors."""
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        _otel_trace_processor,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: structlog.types.Processor
    if log_format == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # Bind standard fields
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(service=service, environment=environment)
