"""Tests for demo API endpoints — seed, reset, trigger followup."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

if TYPE_CHECKING:
    from fastapi import FastAPI


async def test_seed_patient_returns_patient_id(app: FastAPI) -> None:
    """POST /v1/demo/seed-patient returns 200 with patient_id."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/demo/seed-patient",
            json={"tenant_id": "demo-tenant", "external_patient_id": str(uuid.uuid4())},
        )

    assert response.status_code == 200
    data = response.json()
    assert "patient_id" in data
    assert data["phase"] == "pending"


async def test_seed_patient_is_idempotent(app: FastAPI) -> None:
    """POST /v1/demo/seed-patient twice with same external_id returns same patient_id."""
    ext_id = str(uuid.uuid4())
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        r1 = await client.post(
            "/v1/demo/seed-patient",
            json={"tenant_id": "demo-tenant", "external_patient_id": ext_id},
        )
        r2 = await client.post(
            "/v1/demo/seed-patient",
            json={"tenant_id": "demo-tenant", "external_patient_id": ext_id},
        )

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["patient_id"] == r2.json()["patient_id"]


async def test_reset_patient_sets_phase_to_pending(app: FastAPI) -> None:
    """Seed then reset returns phase='pending'."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        seed_resp = await client.post(
            "/v1/demo/seed-patient",
            json={"tenant_id": "demo-tenant"},
        )
        patient_id = seed_resp.json()["patient_id"]

        reset_resp = await client.post(f"/v1/demo/reset-patient/{patient_id}")

    assert reset_resp.status_code == 200
    assert reset_resp.json()["phase"] == "pending"


async def test_reset_patient_clears_checkpoint(app: FastAPI) -> None:
    """Reset patient should clear LangGraph conversation history."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        seed_resp = await client.post(
            "/v1/demo/seed-patient",
            json={"tenant_id": "demo-tenant"},
        )
        patient_id = seed_resp.json()["patient_id"]

    # Write a fake checkpoint to the MemorySaver for this patient's thread
    checkpointer = app.state.graph.checkpointer
    thread_id = f"patient-{patient_id}"
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    checkpoint = {
        "v": 1,
        "id": "ckpt-test",
        "ts": "2024-01-01T00:00:00+00:00",
        "channel_values": {},
        "channel_versions": {},
        "versions_seen": {},
        "pending_sends": [],
    }
    metadata = {"source": "input", "step": 0, "writes": {}, "parents": {}}
    await checkpointer.aput(config, checkpoint, metadata, {})

    # Verify checkpoint exists
    assert thread_id in checkpointer.storage

    # Reset the patient
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        reset_resp = await client.post(f"/v1/demo/reset-patient/{patient_id}")

    assert reset_resp.status_code == 200
    # Checkpoint should be cleared
    assert thread_id not in checkpointer.storage


async def test_get_conversation_history_empty(app: FastAPI) -> None:
    """Conversation history returns empty list for patient with no chat."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        seed_resp = await client.post(
            "/v1/demo/seed-patient",
            json={"tenant_id": "demo-tenant"},
        )
        patient_id = seed_resp.json()["patient_id"]

        resp = await client.get(f"/v1/demo/conversation/{patient_id}")

    assert resp.status_code == 200
    assert resp.json()["messages"] == []


async def test_get_conversation_history_with_messages(app: FastAPI) -> None:
    """Conversation history returns serialized messages from checkpoint."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        seed_resp = await client.post(
            "/v1/demo/seed-patient",
            json={"tenant_id": "demo-tenant"},
        )
        patient_id = seed_resp.json()["patient_id"]

    # Mock aget_state to return messages
    mock_snapshot = MagicMock()
    mock_snapshot.values = {
        "messages": [
            HumanMessage(content="Hello", id="msg-1"),
            AIMessage(content="Hi there! How can I help?", id="msg-2"),
            ToolMessage(content="tool result", name="get_goals", tool_call_id="tc-1", id="msg-3"),
        ],
    }
    original_aget_state = app.state.graph.aget_state
    app.state.graph.aget_state = AsyncMock(return_value=mock_snapshot)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.get(f"/v1/demo/conversation/{patient_id}")
    finally:
        app.state.graph.aget_state = original_aget_state

    assert resp.status_code == 200
    messages = resp.json()["messages"]
    assert len(messages) == 3
    assert messages[0]["role"] == "human"
    assert messages[0]["content"] == "Hello"
    assert messages[0]["message_id"] == "msg-1"
    assert messages[1]["role"] == "ai"
    assert messages[1]["content"] == "Hi there! How can I help?"
    assert messages[2]["role"] == "tool"
    assert messages[2]["tool_name"] == "get_goals"


async def test_get_conversation_history_filters_sentinels(app: FastAPI) -> None:
    """Empty AIMessage sentinels and tool-invoking AIMessages are excluded."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        seed_resp = await client.post(
            "/v1/demo/seed-patient",
            json={"tenant_id": "demo-tenant"},
        )
        patient_id = seed_resp.json()["patient_id"]

    mock_snapshot = MagicMock()
    mock_snapshot.values = {
        "messages": [
            HumanMessage(content="Set a goal", id="msg-1"),
            # Empty sentinel — should be filtered
            AIMessage(content="", id="msg-2"),
            # Tool-invoking AIMessage with no text — should be filtered
            AIMessage(
                content="",
                tool_calls=[{"name": "set_goal", "args": {}, "id": "tc-1"}],
                id="msg-3",
            ),
            # Tool result — should be included
            ToolMessage(content="Goal set!", name="set_goal", tool_call_id="tc-1", id="msg-4"),
            # Normal AI response — should be included
            AIMessage(content="I've set your goal.", id="msg-5"),
        ],
    }
    original_aget_state = app.state.graph.aget_state
    app.state.graph.aget_state = AsyncMock(return_value=mock_snapshot)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.get(f"/v1/demo/conversation/{patient_id}")
    finally:
        app.state.graph.aget_state = original_aget_state

    assert resp.status_code == 200
    messages = resp.json()["messages"]
    assert len(messages) == 3
    assert messages[0]["role"] == "human"
    assert messages[0]["content"] == "Set a goal"
    assert messages[1]["role"] == "tool"
    assert messages[1]["tool_name"] == "set_goal"
    assert messages[2]["role"] == "ai"
    assert messages[2]["content"] == "I've set your goal."


async def test_get_conversation_history_recovers_tool_name(app: FastAPI) -> None:
    """Tool name is recovered from AIMessage.tool_calls when ToolMessage has no name."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        seed_resp = await client.post(
            "/v1/demo/seed-patient",
            json={"tenant_id": "demo-tenant"},
        )
        patient_id = seed_resp.json()["patient_id"]

    mock_snapshot = MagicMock()
    mock_snapshot.values = {
        "messages": [
            HumanMessage(content="Set a goal"),
            AIMessage(
                content="",
                tool_calls=[{"name": "set_goal", "args": {}, "id": "tc-1"}],
            ),
            # ToolMessage without name= (matches real agent behavior)
            ToolMessage(content="Goal set!", tool_call_id="tc-1"),
            AIMessage(content="Done!"),
        ],
    }
    original_aget_state = app.state.graph.aget_state
    app.state.graph.aget_state = AsyncMock(return_value=mock_snapshot)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.get(f"/v1/demo/conversation/{patient_id}")
    finally:
        app.state.graph.aget_state = original_aget_state

    assert resp.status_code == 200
    messages = resp.json()["messages"]
    assert len(messages) == 3  # HumanMessage, ToolMessage, AIMessage (tool-invoking filtered)
    assert messages[1]["role"] == "tool"
    assert messages[1]["tool_name"] == "set_goal"
    # All message_ids should be non-empty (UUID fallback for messages without id)
    for m in messages:
        assert m["message_id"] != ""


async def test_trigger_followup_with_no_jobs_returns_404(app: FastAPI) -> None:
    """Trigger followup for patient with no pending jobs returns 404."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        seed_resp = await client.post(
            "/v1/demo/seed-patient",
            json={"tenant_id": "demo-tenant"},
        )
        patient_id = seed_resp.json()["patient_id"]

        trigger_resp = await client.post(
            f"/v1/demo/trigger-followup/{patient_id}",
        )

    assert trigger_resp.status_code == 404
