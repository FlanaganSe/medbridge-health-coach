"""Request logging middleware — pure ASGI (no BaseHTTPMiddleware).

Clears contextvars per request (mandatory for async), binds request metadata,
and logs request duration. Never logs request/response bodies (PHI safety).

Using raw ASGI avoids the SSE buffering issue caused by BaseHTTPMiddleware.
"""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = structlog.stdlib.get_logger()


class RequestLoggingMiddleware:
    """Logs request metadata and duration. Never logs bodies (PHI safety)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        structlog.contextvars.clear_contextvars()

        request_id = str(uuid.uuid4())
        path = scope.get("path", "")
        method = scope.get("method", "")

        # Extract patient ID from headers
        patient_id = ""
        for header_name, header_value in scope.get("headers", []):
            if header_name == b"x-patient-id":
                patient_id = header_value.decode("utf-8", errors="replace")
                break

        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            path=path,
            method=method,
            patient_id=patient_id or None,
        )

        start = time.monotonic()
        status_code = 500  # Default if response_start never fires

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)
                # Inject X-Request-ID header
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
            duration_ms = int((time.monotonic() - start) * 1000)
            await logger.ainfo(
                "request_completed",
                status_code=status_code,
                duration_ms=duration_ms,
            )
        except Exception:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.exception("request_error", duration_ms=duration_ms)
            raise
        finally:
            structlog.contextvars.clear_contextvars()
