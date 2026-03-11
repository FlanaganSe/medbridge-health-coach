"""Tests for repositories — CRUD operations and audit immutability."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from health_ally.persistence.models import AuditEvent, Base, Patient
from health_ally.persistence.repositories.audit import AuditRepository
from health_ally.persistence.repositories.patient import PatientRepository


@pytest.fixture
async def db_engine():
    """Create a fresh in-memory SQLite engine with tables."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine: AsyncEngine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


async def test_create_patient(db_session: AsyncSession) -> None:
    repo = PatientRepository(db_session)
    patient = Patient(
        tenant_id="t1",
        external_patient_id="ext-1",
        phase="pending",
    )
    created = await repo.create(patient)
    assert created.id is not None
    assert created.tenant_id == "t1"
    await db_session.commit()


async def test_get_patient_by_id(db_session: AsyncSession) -> None:
    repo = PatientRepository(db_session)
    patient = Patient(tenant_id="t1", external_patient_id="ext-2", phase="pending")
    await repo.create(patient)
    await db_session.commit()

    found = await repo.get_by_id(patient.id)
    assert found is not None
    assert found.external_patient_id == "ext-2"


async def test_get_patient_by_external_id(db_session: AsyncSession) -> None:
    repo = PatientRepository(db_session)
    patient = Patient(tenant_id="t1", external_patient_id="ext-3", phase="pending")
    await repo.create(patient)
    await db_session.commit()

    found = await repo.get_by_external_id("t1", "ext-3")
    assert found is not None
    assert found.id == patient.id


async def test_get_patient_by_external_id_not_found(db_session: AsyncSession) -> None:
    repo = PatientRepository(db_session)
    found = await repo.get_by_external_id("t1", "nonexistent")
    assert found is None


async def test_update_patient(db_session: AsyncSession) -> None:
    repo = PatientRepository(db_session)
    patient = Patient(tenant_id="t1", external_patient_id="ext-4", phase="pending")
    await repo.create(patient)
    await db_session.commit()

    updated = await repo.update(patient, phase="onboarding")
    assert updated.phase == "onboarding"
    await db_session.commit()


async def test_create_audit_event(db_session: AsyncSession) -> None:
    repo = AuditRepository(db_session)
    event = AuditEvent(
        tenant_id="t1",
        patient_id=uuid.uuid4(),
        event_type="consent_check",
        outcome="denied",
        metadata_={"reason": "not_logged_in"},
    )
    created = await repo.create(event)
    assert created.id is not None
    await db_session.commit()


async def test_audit_update_raises() -> None:
    """Audit events must not be updatable."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine("sqlite+aiosqlite://", connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        repo = AuditRepository(session)
        event = AuditEvent(
            tenant_id="t1",
            patient_id=uuid.uuid4(),
            event_type="test",
            outcome="test",
        )
        await repo.create(event)
        await session.commit()

        with pytest.raises(NotImplementedError, match="immutable"):
            await repo.update(event, outcome="modified")

    await engine.dispose()


async def test_list_audit_by_patient(db_session: AsyncSession) -> None:
    repo = AuditRepository(db_session)
    pid = uuid.uuid4()

    for event_type in ["consent_check", "safety_decision", "consent_check"]:
        event = AuditEvent(
            tenant_id="t1",
            patient_id=pid,
            event_type=event_type,
            outcome="test",
        )
        await repo.create(event)
    await db_session.commit()

    all_events = await repo.list_by_patient(pid)
    assert len(all_events) == 3

    consent_events = await repo.list_by_patient(pid, event_type="consent_check")
    assert len(consent_events) == 2
