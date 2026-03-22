"""Tests for save_patient_context — verifies atomic multi-table writes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from health_ally.agent.context import CoachContext
from health_ally.agent.nodes.context import save_patient_context
from health_ally.domain.consent import FakeConsentService
from health_ally.domain.scheduling import CoachConfig
from health_ally.integrations.model_gateway import FakeModelGateway
from health_ally.persistence.models import (
    AuditEvent,
    ClinicianAlert,
    OutboxEntry,
    PatientGoal,
    ScheduledJob,
)
from tests.conftest import make_mock_session


def _make_mock_patient(*, phase: str = "active") -> MagicMock:
    """Create a mock Patient for save_patient_context."""
    patient = MagicMock()
    patient.phase = phase
    patient.unanswered_count = 0
    patient.last_outreach_at = None
    patient.last_patient_response_at = None
    return patient


def _make_state(
    *,
    pending_effects: dict | None = None,  # type: ignore[type-arg]
    invocation_source: str = "scheduler",
    outbound_message: str | None = None,
) -> dict:  # type: ignore[type-arg]
    """Build a minimal PatientState dict for testing."""
    return {
        "patient_id": str(uuid.uuid4()),
        "tenant_id": "t1",
        "pending_effects": pending_effects
        or {
            "goal": None,
            "alerts": [],
            "phase_event": None,
            "scheduled_jobs": [],
            "safety_decisions": [],
            "outbox_entries": [],
            "audit_events": [],
        },
        "invocation_source": invocation_source,
        "outbound_message": outbound_message,
    }


def _make_config(mock_patient: MagicMock) -> dict:  # type: ignore[type-arg]
    """Build a LangGraph config dict with mocked CoachContext."""
    mock_session = make_mock_session(mock_patient)
    sf = MagicMock(return_value=mock_session)

    mock_engine = MagicMock()
    mock_engine.url = "sqlite://"

    ctx = CoachContext(
        session_factory=sf,  # type: ignore[arg-type]
        engine=mock_engine,  # type: ignore[arg-type]
        consent_service=FakeConsentService(logged_in=True, consented=True),
        settings=MagicMock(),  # type: ignore[arg-type]
        coach_config=CoachConfig(),
        model_gateway=FakeModelGateway(),
    )

    return {
        "configurable": {"ctx": ctx, "thread_id": str(uuid.uuid4())},
        "_mock_session": mock_session,  # stash for assertions
    }


def _get_added_instances(mock_session: AsyncMock, cls: type) -> list:  # type: ignore[type-arg]
    """Extract all instances of `cls` passed to session.add()."""
    return [c.args[0] for c in mock_session.add.call_args_list if isinstance(c.args[0], cls)]


async def test_save_goal_creates_patient_goal() -> None:
    """Pending effects with a goal dict creates a PatientGoal."""
    patient = _make_mock_patient()
    state = _make_state(
        pending_effects={
            "goal": {
                "goal_text": "Walk 30 minutes daily",
                "raw_patient_text": "I want to walk every day",
                "idempotency_key": "ik-1",
            },
            "alerts": [],
            "phase_event": None,
            "scheduled_jobs": [],
            "safety_decisions": [],
            "outbox_entries": [],
            "audit_events": [],
        },
    )
    config = _make_config(patient)
    mock_session = config.pop("_mock_session")

    await save_patient_context(state, config)

    goals = _get_added_instances(mock_session, PatientGoal)
    assert len(goals) == 1
    assert goals[0].goal_text == "Walk 30 minutes daily"
    assert goals[0].raw_patient_text == "I want to walk every day"


async def test_save_phase_transition_updates_patient() -> None:
    """Pending effects with phase_event transitions the patient phase."""
    patient = _make_mock_patient(phase="onboarding")
    state = _make_state(
        pending_effects={
            "goal": None,
            "alerts": [],
            "phase_event": "goal_confirmed",
            "scheduled_jobs": [],
            "safety_decisions": [],
            "outbox_entries": [],
            "audit_events": [],
        },
    )
    config = _make_config(patient)
    mock_session = config.pop("_mock_session")

    await save_patient_context(state, config)

    # goal_confirmed transitions ONBOARDING → ACTIVE
    assert patient.phase == "active"

    # Phase transition also creates an AuditEvent
    audit = _get_added_instances(mock_session, AuditEvent)
    assert len(audit) == 1
    assert audit[0].event_type == "phase_transition"
    assert audit[0].outcome == "active"


async def test_save_alerts_creates_clinician_alert_and_outbox() -> None:
    """Pending effects with alerts creates ClinicianAlert and OutboxEntry."""
    patient = _make_mock_patient()
    state = _make_state(
        pending_effects={
            "goal": None,
            "alerts": [
                {
                    "reason": "Patient reported increased pain",
                    "priority": "urgent",
                    "idempotency_key": "alert-1",
                },
            ],
            "phase_event": None,
            "scheduled_jobs": [],
            "safety_decisions": [],
            "outbox_entries": [],
            "audit_events": [],
        },
    )
    config = _make_config(patient)
    mock_session = config.pop("_mock_session")

    await save_patient_context(state, config)

    alerts = _get_added_instances(mock_session, ClinicianAlert)
    assert len(alerts) == 1
    assert alerts[0].reason == "Patient reported increased pain"
    assert alerts[0].priority == "urgent"

    outbox = _get_added_instances(mock_session, OutboxEntry)
    assert len(outbox) == 1
    assert outbox[0].message_type == "clinician_alert"
    assert outbox[0].priority == 1  # urgent → priority 1


async def test_save_scheduled_jobs() -> None:
    """Pending effects with scheduled_jobs creates ScheduledJob instances."""
    patient = _make_mock_patient()
    scheduled_at = datetime.now(UTC)
    state = _make_state(
        pending_effects={
            "goal": None,
            "alerts": [],
            "phase_event": None,
            "scheduled_jobs": [
                {
                    "job_type": "day_2_followup",
                    "idempotency_key": "job-1",
                    "scheduled_at": scheduled_at,
                    "metadata": {"source": "goal_set"},
                },
            ],
            "safety_decisions": [],
            "outbox_entries": [],
            "audit_events": [],
        },
    )
    config = _make_config(patient)
    mock_session = config.pop("_mock_session")

    await save_patient_context(state, config)

    jobs = _get_added_instances(mock_session, ScheduledJob)
    assert len(jobs) == 1
    assert jobs[0].job_type == "day_2_followup"
    assert jobs[0].scheduled_at == scheduled_at


async def test_save_patient_message_resets_unanswered_count() -> None:
    """When invocation_source is 'patient', unanswered_count is reset to 0."""
    patient = _make_mock_patient()
    patient.unanswered_count = 3
    state = _make_state(invocation_source="patient")
    config = _make_config(patient)
    config.pop("_mock_session")

    await save_patient_context(state, config)

    assert patient.unanswered_count == 0
    assert patient.last_patient_response_at is not None


async def test_save_empty_effects_is_noop() -> None:
    """Empty pending effects does not call session.add (except outbound_message handling)."""
    patient = _make_mock_patient()
    state = _make_state()
    config = _make_config(patient)
    mock_session = config.pop("_mock_session")

    await save_patient_context(state, config)

    # No models added for empty effects (no goal, alerts, jobs, etc.)
    goals = _get_added_instances(mock_session, PatientGoal)
    alerts = _get_added_instances(mock_session, ClinicianAlert)
    jobs = _get_added_instances(mock_session, ScheduledJob)
    audit = _get_added_instances(mock_session, AuditEvent)
    assert len(goals) == 0
    assert len(alerts) == 0
    assert len(jobs) == 0
    assert len(audit) == 0
