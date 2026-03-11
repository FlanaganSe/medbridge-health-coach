"""SSE chat endpoint for patient conversations.

POST /v1/chat — accepts patient message, invokes graph, streams response via SSE.
Acquires patient_advisory_lock before graph invocation (Plan Invariant #3).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, Request
from langchain_core.messages import HumanMessage
from starlette.responses import StreamingResponse

from health_coach.api.dependencies import AuthContext, get_auth_context
from health_coach.persistence.locking import patient_advisory_lock

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = structlog.stdlib.get_logger()
router = APIRouter(prefix="/v1", tags=["chat"])


class ChatRequest:
    """Chat request body."""

    def __init__(self, message: str) -> None:
        self.message = message


@router.post("/chat")
async def chat(
    request: Request,
    auth: AuthContext = Depends(get_auth_context),  # noqa: B008
) -> StreamingResponse:
    """Accept a patient message and stream the coaching response via SSE."""
    body = await request.json()
    message = str(body.get("message", ""))
    if not message:
        return StreamingResponse(
            _error_event("Message is required"),
            media_type="text/event-stream",
            status_code=400,
        )

    graph = request.app.state.graph
    engine = request.app.state.engine
    ctx = request.app.state.ctx_factory(
        request.app.state.session_factory,
        engine,
    )

    thread_id = f"patient-{auth.patient_id}"

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            async with patient_advisory_lock(engine, auth.patient_id):
                async for event in graph.astream(
                    {
                        "patient_id": auth.patient_id,
                        "tenant_id": auth.tenant_id,
                        "messages": [HumanMessage(content=message)],
                        "invocation_source": "patient",
                    },
                    config={
                        "configurable": {
                            "ctx": ctx,
                            "thread_id": thread_id,
                        }
                    },
                    stream_mode="updates",
                ):
                    yield _format_sse(event)

            yield _format_sse({"type": "done"})
        except Exception:
            logger.exception("chat_stream_error", patient_id=auth.patient_id)
            yield _format_sse({"type": "error", "message": "Internal error"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _format_sse(data: dict[str, Any]) -> str:
    """Format a dict as an SSE event."""
    return f"data: {json.dumps(data)}\n\n"


async def _error_event(message: str) -> AsyncGenerator[str, None]:
    """Yield a single error SSE event."""
    yield _format_sse({"type": "error", "message": message})
