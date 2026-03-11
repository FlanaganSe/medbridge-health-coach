"""Integration tests for backoff sequence and dormant transition."""

from __future__ import annotations

from health_coach.agent.nodes.re_engaging import (
    _accumulate_backoff_job,
    _accumulate_patient_return,
    _handle_dormant_transition,
)
from health_coach.domain.scheduling import CoachConfig


def test_handle_dormant_transition_sets_phase_event() -> None:
    """Dormant transition sets missed_third_message phase_event."""
    state = {
        "patient_id": "p1",
        "tenant_id": "t1",
        "pending_effects": {},
    }
    result = _handle_dormant_transition(state, unanswered=3)

    effects = result.get("pending_effects", {})
    assert effects.get("phase_event") == "missed_third_message"
    assert result.get("unanswered_count") == 3


def test_handle_dormant_creates_clinician_alert() -> None:
    """Dormant transition creates routine clinician alert."""
    state = {
        "patient_id": "p1",
        "tenant_id": "t1",
        "pending_effects": {},
    }
    result = _handle_dormant_transition(state, unanswered=3)

    effects = result.get("pending_effects", {})
    alerts = effects.get("alerts", [])
    assert len(alerts) == 1
    assert alerts[0]["priority"] == "routine"
    assert "unresponsive" in alerts[0]["reason"].lower()


def test_accumulate_backoff_job_creates_job() -> None:
    """Backoff job accumulation creates a scheduled job."""
    state = {
        "patient_id": "p1",
        "tenant_id": "t1",
        "pending_effects": {},
    }
    config = CoachConfig()
    effects = _accumulate_backoff_job(state, unanswered=2, coach_config=config)

    assert effects is not None
    jobs = effects.get("scheduled_jobs", [])
    assert len(jobs) == 1
    assert jobs[0]["job_type"] == "backoff_followup"
    assert "backoff_followup" in jobs[0]["idempotency_key"]


def test_patient_return_sets_phase_event() -> None:
    """Patient return sets patient_responded phase_event."""
    state = {
        "patient_id": "p1",
        "tenant_id": "t1",
        "pending_effects": {},
    }
    config = CoachConfig()
    effects = _accumulate_patient_return(state, coach_config=config)

    assert effects.get("phase_event") == "patient_responded"


def test_patient_return_schedules_followup() -> None:
    """Patient return schedules new follow-up cadence."""
    state = {
        "patient_id": "p1",
        "tenant_id": "t1",
        "pending_effects": {},
    }
    config = CoachConfig()
    effects = _accumulate_patient_return(state, coach_config=config)

    jobs = effects.get("scheduled_jobs", [])
    assert len(jobs) == 1
    assert jobs[0]["job_type"] == "day_2_followup"


def test_dormant_node_patient_returns() -> None:
    """Dormant node triggers patient_returned on patient invocation."""
    import asyncio

    from health_coach.agent.nodes.dormant import dormant_node

    state = {
        "patient_id": "p1",
        "tenant_id": "t1",
        "invocation_source": "patient",
        "pending_effects": {},
    }
    result = asyncio.get_event_loop().run_until_complete(dormant_node(state))

    effects = result.get("pending_effects", {})
    assert effects.get("phase_event") == "patient_returned"


def test_dormant_node_scheduler_noop() -> None:
    """Dormant node does nothing on scheduler invocation."""
    import asyncio

    from health_coach.agent.nodes.dormant import dormant_node

    state = {
        "patient_id": "p1",
        "tenant_id": "t1",
        "invocation_source": "scheduler",
        "pending_effects": {},
    }
    result = asyncio.get_event_loop().run_until_complete(dormant_node(state))

    effects = result.get("pending_effects")
    assert effects is None  # No pending_effects set
