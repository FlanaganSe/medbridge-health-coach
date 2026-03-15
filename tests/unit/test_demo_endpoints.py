"""Tests for demo API endpoints — seed, reset, trigger followup."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from httpx import ASGITransport, AsyncClient

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
