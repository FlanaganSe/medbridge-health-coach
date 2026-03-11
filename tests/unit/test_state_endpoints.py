"""Tests for state query endpoints (/v1/patients/{id}/*)."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from health_coach.main import create_app
from health_coach.persistence.models import (
    Base,
    ClinicianAlert,
    Patient,
    PatientGoal,
    SafetyDecisionRecord,
)
from health_coach.settings import Settings

TENANT = "test-tenant"


@pytest.fixture
async def test_app():
    """Create app with real in-memory SQLite and tables."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(database_url="sqlite+aiosqlite://", environment="dev")
    app = create_app(settings)
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.langgraph_pool = None

    yield app, factory

    await engine.dispose()


@pytest.fixture
async def client(test_app):
    app, _ = test_app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest.fixture
async def seeded_patient(test_app):
    """Seed a patient and return its UUID."""
    _, factory = test_app
    patient_id = uuid.uuid4()
    async with factory() as session, session.begin():
        session.add(
            Patient(
                id=patient_id,
                tenant_id=TENANT,
                external_patient_id="ext-1",
                phase="onboarding",
            )
        )
    return patient_id


def _headers(patient_id: uuid.UUID | str = "unused") -> dict[str, str]:
    return {"X-Patient-ID": str(patient_id), "X-Tenant-ID": TENANT}


# --- Phase endpoint ---


async def test_get_phase_returns_phase(client: AsyncClient, seeded_patient: uuid.UUID) -> None:
    resp = await client.get(
        f"/v1/patients/{seeded_patient}/phase",
        headers=_headers(seeded_patient),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["phase"] == "onboarding"
    assert data["patient_id"] == str(seeded_patient)


async def test_get_phase_unknown_patient_404(client: AsyncClient) -> None:
    unknown = uuid.uuid4()
    resp = await client.get(
        f"/v1/patients/{unknown}/phase",
        headers=_headers(unknown),
    )
    assert resp.status_code == 404


async def test_get_phase_invalid_uuid_400(client: AsyncClient) -> None:
    resp = await client.get(
        "/v1/patients/not-a-uuid/phase",
        headers=_headers(),
    )
    assert resp.status_code == 400


# --- Goals endpoint ---


async def test_get_goals_empty(client: AsyncClient, seeded_patient: uuid.UUID) -> None:
    resp = await client.get(
        f"/v1/patients/{seeded_patient}/goals",
        headers=_headers(seeded_patient),
    )
    assert resp.status_code == 200
    assert resp.json()["goals"] == []


async def test_get_goals_returns_goals(
    client: AsyncClient, seeded_patient: uuid.UUID, test_app: tuple
) -> None:
    _, factory = test_app
    async with factory() as session, session.begin():
        session.add(
            PatientGoal(
                tenant_id=TENANT,
                patient_id=seeded_patient,
                goal_text="Walk 30 min daily",
                idempotency_key="goal-1",
            )
        )

    resp = await client.get(
        f"/v1/patients/{seeded_patient}/goals",
        headers=_headers(seeded_patient),
    )
    assert resp.status_code == 200
    goals = resp.json()["goals"]
    assert len(goals) == 1
    assert goals[0]["goal_text"] == "Walk 30 min daily"


# --- Alerts endpoint ---


async def test_get_alerts_returns_alerts(
    client: AsyncClient, seeded_patient: uuid.UUID, test_app: tuple
) -> None:
    _, factory = test_app
    async with factory() as session, session.begin():
        session.add(
            ClinicianAlert(
                tenant_id=TENANT,
                patient_id=seeded_patient,
                reason="Crisis language detected",
                priority="urgent",
                idempotency_key="alert-1",
            )
        )

    resp = await client.get(
        f"/v1/patients/{seeded_patient}/alerts",
        headers=_headers(seeded_patient),
    )
    assert resp.status_code == 200
    alerts = resp.json()["alerts"]
    assert len(alerts) == 1
    assert alerts[0]["reason"] == "Crisis language detected"
    assert alerts[0]["priority"] == "urgent"


# --- Safety decisions endpoint ---


async def test_get_safety_decisions_returns_decisions(
    client: AsyncClient, seeded_patient: uuid.UUID, test_app: tuple
) -> None:
    _, factory = test_app
    async with factory() as session, session.begin():
        session.add(
            SafetyDecisionRecord(
                tenant_id=TENANT,
                patient_id=seeded_patient,
                decision="allow",
                source="classifier",
                confidence=0.95,
            )
        )

    resp = await client.get(
        f"/v1/patients/{seeded_patient}/safety-decisions",
        headers=_headers(seeded_patient),
    )
    assert resp.status_code == 200
    decisions = resp.json()["decisions"]
    assert len(decisions) == 1
    assert decisions[0]["decision"] == "allow"
    assert decisions[0]["confidence"] == 0.95
