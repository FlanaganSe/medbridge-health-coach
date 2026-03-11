"""Request logging middleware with structlog contextvars.

Clears contextvars per request (mandatory for async), binds request metadata,
and logs request duration. Never logs request/response bodies (PHI safety).
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

logger = structlog.stdlib.get_logger()


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs request metadata and duration. Never logs bodies (PHI safety)."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Clear and bind per-request context (mandatory for async structlog)
        structlog.contextvars.clear_contextvars()

        request_id = str(uuid.uuid4())
        patient_id = request.headers.get("X-Patient-ID", "")

        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            path=request.url.path,
            method=request.method,
            patient_id=patient_id if patient_id else None,
        )

        start = time.monotonic()
        try:
            response = await call_next(request)
            duration_ms = int((time.monotonic() - start) * 1000)
            await logger.ainfo(
                "request_completed",
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.exception("request_error", duration_ms=duration_ms)
            raise
        finally:
            structlog.contextvars.clear_contextvars()
