"""Tests for demo API endpoints — seed, reset, trigger followup, list, delete, run-checkin."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import update

from health_ally.persistence.models import Patient

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


# --- Seed with display_name ---


async def test_seed_patient_with_display_name(app: FastAPI) -> None:
    """Seed patient with display_name stores and returns it."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/demo/seed-patient",
            json={
                "tenant_id": "demo-tenant",
                "external_patient_id": str(uuid.uuid4()),
                "display_name": "Alice W. — Hip Recovery",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["display_name"] == "Alice W. — Hip Recovery"


async def test_seed_patient_without_display_name(app: FastAPI) -> None:
    """Seed patient without display_name returns null."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/demo/seed-patient",
            json={"tenant_id": "demo-tenant", "external_patient_id": str(uuid.uuid4())},
        )

    assert response.status_code == 200
    assert response.json()["display_name"] is None


# --- List patients ---


async def test_list_patients_returns_seeded(app: FastAPI) -> None:
    """GET /v1/demo/patients returns patients seeded in this tenant."""
    ext_id = str(uuid.uuid4())
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        await client.post(
            "/v1/demo/seed-patient",
            json={
                "tenant_id": "demo-tenant",
                "external_patient_id": ext_id,
                "display_name": "Test Patient",
            },
        )

        resp = await client.get("/v1/demo/patients?tenant_id=demo-tenant")

    assert resp.status_code == 200
    patients = resp.json()["patients"]
    assert len(patients) >= 1
    ext_ids = [p["external_patient_id"] for p in patients]
    assert ext_id in ext_ids


async def test_list_patients_empty_tenant(app: FastAPI) -> None:
    """GET /v1/demo/patients for unknown tenant returns empty list."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/v1/demo/patients?tenant_id=nonexistent-tenant")

    assert resp.status_code == 200
    assert resp.json()["patients"] == []


# --- Delete patient ---


async def test_delete_patient_removes_record(app: FastAPI) -> None:
    """DELETE /v1/demo/patients/{id} removes the patient."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        seed_resp = await client.post(
            "/v1/demo/seed-patient",
            json={"tenant_id": "demo-tenant", "external_patient_id": str(uuid.uuid4())},
        )
        patient_id = seed_resp.json()["patient_id"]

        del_resp = await client.delete(f"/v1/demo/patients/{patient_id}")

    assert del_resp.status_code == 200
    assert del_resp.json()["deleted"] is True

    # Verify patient no longer exists via list
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        list_resp = await client.get("/v1/demo/patients?tenant_id=demo-tenant")

    patient_ids = [p["patient_id"] for p in list_resp.json()["patients"]]
    assert patient_id not in patient_ids


async def test_delete_nonexistent_patient_returns_404(app: FastAPI) -> None:
    """DELETE /v1/demo/patients/{id} for unknown patient returns 404."""
    fake_id = str(uuid.uuid4())
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.delete(f"/v1/demo/patients/{fake_id}")

    assert resp.status_code == 404


# --- Run check-in ---


async def test_run_checkin_rejects_pending_phase(app: FastAPI) -> None:
    """POST /v1/demo/run-checkin rejects patient in PENDING phase."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        seed_resp = await client.post(
            "/v1/demo/seed-patient",
            json={"tenant_id": "demo-tenant", "external_patient_id": str(uuid.uuid4())},
        )
        patient_id = seed_resp.json()["patient_id"]

        resp = await client.post(f"/v1/demo/run-checkin/{patient_id}")

    assert resp.status_code == 409
    assert "ACTIVE or RE_ENGAGING" in resp.json()["detail"]


async def test_run_checkin_rejects_onboarding_phase(app: FastAPI) -> None:
    """POST /v1/demo/run-checkin rejects patient in ONBOARDING phase."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        seed_resp = await client.post(
            "/v1/demo/seed-patient",
            json={"tenant_id": "demo-tenant", "external_patient_id": str(uuid.uuid4())},
        )
        patient_id = seed_resp.json()["patient_id"]

    # Manually set phase to onboarding
    async with app.state.session_factory() as session, session.begin():
        await session.execute(
            update(Patient)
            .where(Patient.id == uuid.UUID(patient_id))
            .values(phase="onboarding")
        )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.post(f"/v1/demo/run-checkin/{patient_id}")

    assert resp.status_code == 409


async def test_run_checkin_succeeds_for_active_patient(app: FastAPI) -> None:
    """POST /v1/demo/run-checkin invokes graph for ACTIVE patient."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        seed_resp = await client.post(
            "/v1/demo/seed-patient",
            json={"tenant_id": "demo-tenant", "external_patient_id": str(uuid.uuid4())},
        )
        patient_id = seed_resp.json()["patient_id"]

    # Set phase to active
    async with app.state.session_factory() as session, session.begin():
        await session.execute(
            update(Patient)
            .where(Patient.id == uuid.UUID(patient_id))
            .values(phase="active")
        )

    # Mock ctx_factory and graph.ainvoke
    mock_ctx = MagicMock()
    app.state.ctx_factory = MagicMock(return_value=mock_ctx)
    original_ainvoke = app.state.graph.ainvoke
    app.state.graph.ainvoke = AsyncMock(return_value=None)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.post(f"/v1/demo/run-checkin/{patient_id}")
    finally:
        app.state.graph.ainvoke = original_ainvoke

    assert resp.status_code == 200
    data = resp.json()
    assert data["patient_id"] == patient_id
    assert data["status"] == "completed"


async def test_run_checkin_nonexistent_patient_returns_404(app: FastAPI) -> None:
    """POST /v1/demo/run-checkin for unknown patient returns 404."""
    fake_id = str(uuid.uuid4())
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.post(f"/v1/demo/run-checkin/{fake_id}")

    assert resp.status_code == 404
