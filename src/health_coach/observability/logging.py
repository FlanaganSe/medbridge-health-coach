"""structlog configuration for PHI-safe structured logging."""

from __future__ import annotations

import logging
import re
from typing import Literal

import structlog

# Fields that must never appear in logs (defense-in-depth)
_PHI_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "message_content",
        "patient_name",
        "patient_email",
        "email",
        "phone",
        "phone_number",
        "address",
        "ssn",
        "date_of_birth",
        "dob",
        "diagnosis",
        "medication",
        "treatment",
        "symptoms",
        "medical_record_number",
        "mrn",
        "insurance_id",
        "body",
        "request_body",
        "response_body",
    }
)

# Patterns that look like PHI values even under unknown keys
_PHI_VALUE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # SSN
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),  # Email
]


def scrub_phi_fields(
    _logger: object, _method_name: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """Remove known PHI fields from log events.

    Defense-in-depth: even if a developer accidentally binds a PHI field,
    this processor strips it before the log is emitted.
    """
    for field in _PHI_FIELD_NAMES:
        if field in event_dict:
            event_dict[field] = "[REDACTED]"

    # Scrub values that match PHI patterns (SSN, email in string values)
    for key, value in list(event_dict.items()):
        if isinstance(value, str):
            for pattern in _PHI_VALUE_PATTERNS:
                if pattern.search(value):
                    event_dict[key] = "[REDACTED]"
                    break

    return event_dict


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
        scrub_phi_fields,
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
