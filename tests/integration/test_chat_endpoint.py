"""Integration tests for the SSE chat endpoint."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from health_ally.main import create_app
from health_ally.settings import Settings

if TYPE_CHECKING:
    from fastapi import FastAPI


@pytest.fixture
def app() -> FastAPI:
    """Create a test app with mocked graph."""
    settings = Settings(app_mode="api")
    app = create_app(settings)

    # Mock graph for testing (bypass actual LLM calls)
    mock_graph = AsyncMock()

    async def mock_stream(*args, **kwargs):  # type: ignore[no-untyped-def]
        yield ("custom", {"type": "token", "content": "Hello"})
        yield ("custom", {"type": "token", "content": "!"})
        yield ("updates", {"save_patient_context": {"outbound_message": "Hello!"}})

    mock_graph.astream = mock_stream

    app.state.graph = mock_graph
    app.state.engine = MagicMock()
    app.state.session_factory = AsyncMock()
    app.state.ctx_factory = MagicMock(return_value=MagicMock())

    return app


@pytest.mark.asyncio
async def test_chat_streams_response(app: FastAPI) -> None:
    """Chat endpoint streams SSE events."""
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/chat",
            json={"message": "Hi there"},
            headers={
                "X-Patient-ID": "p1",
                "X-Tenant-ID": "t1",
            },
        )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_chat_requires_auth_headers(app: FastAPI) -> None:
    """Chat endpoint returns 422 without auth headers."""
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/chat",
            json={"message": "Hi"},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_chat_sse_event_shape(app: FastAPI) -> None:
    """SSE response contains JSON data events and ends with a done event."""
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/chat",
            json={"message": "Hi there"},
            headers={
                "X-Patient-ID": "p1",
                "X-Tenant-ID": "t1",
            },
        )

    assert response.status_code == 200

    # Parse SSE events from response body
    body = response.text
    events = []
    for chunk in body.split("\n\n"):
        chunk = chunk.strip()
        if chunk.startswith("data: "):
            events.append(json.loads(chunk[len("data: ") :]))

    assert len(events) >= 2  # at least one data event + done

    # Separate token events from node update events
    token_events = [e for e in events if e.get("type") == "token"]
    update_events = [e for e in events if e.get("type") not in ("done", "token")]

    # All events should be dicts with string keys
    for event in events:
        assert isinstance(event, dict)
        assert all(isinstance(k, str) for k in event)

    # Token events have correct shape
    for te in token_events:
        assert "content" in te
        assert isinstance(te["content"], str)

    # At least one update event contains outbound_message
    has_outbound = any(
        "outbound_message" in v
        for e in update_events
        for v in (e.values() if isinstance(e, dict) else [])
        if isinstance(v, dict)
    )
    assert has_outbound

    # Last event is the done marker
    assert events[-1] == {"type": "done"}


@pytest.mark.asyncio
async def test_chat_streams_token_events(app: FastAPI) -> None:
    """Chat endpoint emits token events from custom stream mode."""
    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/chat",
            json={"message": "Hi there"},
            headers={
                "X-Patient-ID": "p1",
                "X-Tenant-ID": "t1",
            },
        )

    assert response.status_code == 200

    body = response.text
    events = []
    for chunk in body.split("\n\n"):
        chunk = chunk.strip()
        if chunk.startswith("data: "):
            events.append(json.loads(chunk[len("data: ") :]))

    token_events = [e for e in events if e.get("type") == "token"]
    assert len(token_events) == 2
    assert token_events[0] == {"type": "token", "content": "Hello"}
    assert token_events[1] == {"type": "token", "content": "!"}

    # Updates event still present with correct shape
    update_events = [e for e in events if e.get("type") not in ("done", "token")]
    assert len(update_events) == 1
    assert "save_patient_context" in update_events[0]
    assert update_events[0]["save_patient_context"]["outbound_message"] == "Hello!"
