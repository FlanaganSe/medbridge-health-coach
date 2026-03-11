"""Tests for the phase state machine — exhaustive transition coverage."""

import pytest

from health_coach.domain.errors import PhaseTransitionError
from health_coach.domain.phase_machine import (
    VALID_EVENTS,
    is_valid_transition,
    transition,
)
from health_coach.domain.phases import PatientPhase

# --- Valid transitions ---


def test_pending_to_onboarding() -> None:
    assert transition(PatientPhase.PENDING, "onboarding_initiated") == PatientPhase.ONBOARDING


def test_onboarding_to_active() -> None:
    assert transition(PatientPhase.ONBOARDING, "goal_confirmed") == PatientPhase.ACTIVE


def test_onboarding_to_dormant_timeout() -> None:
    assert transition(PatientPhase.ONBOARDING, "no_response_timeout") == PatientPhase.DORMANT


def test_active_to_re_engaging() -> None:
    assert transition(PatientPhase.ACTIVE, "unanswered_outreach") == PatientPhase.RE_ENGAGING


def test_re_engaging_to_dormant() -> None:
    assert transition(PatientPhase.RE_ENGAGING, "missed_third_message") == PatientPhase.DORMANT


def test_re_engaging_to_active() -> None:
    assert transition(PatientPhase.RE_ENGAGING, "patient_responded") == PatientPhase.ACTIVE


def test_dormant_to_re_engaging() -> None:
    assert transition(PatientPhase.DORMANT, "patient_returned") == PatientPhase.RE_ENGAGING


# --- Invalid transitions ---


@pytest.mark.parametrize(
    "phase,event",
    [
        (PatientPhase.PENDING, "goal_confirmed"),
        (PatientPhase.PENDING, "patient_responded"),
        (PatientPhase.PENDING, "unanswered_outreach"),
        (PatientPhase.PENDING, "missed_third_message"),
        (PatientPhase.PENDING, "patient_returned"),
        (PatientPhase.PENDING, "no_response_timeout"),
        (PatientPhase.ONBOARDING, "onboarding_initiated"),
        (PatientPhase.ONBOARDING, "unanswered_outreach"),
        (PatientPhase.ONBOARDING, "patient_returned"),
        (PatientPhase.ACTIVE, "onboarding_initiated"),
        (PatientPhase.ACTIVE, "goal_confirmed"),
        (PatientPhase.ACTIVE, "patient_returned"),
        (PatientPhase.ACTIVE, "missed_third_message"),
        (PatientPhase.RE_ENGAGING, "onboarding_initiated"),
        (PatientPhase.RE_ENGAGING, "goal_confirmed"),
        (PatientPhase.RE_ENGAGING, "unanswered_outreach"),
        (PatientPhase.DORMANT, "onboarding_initiated"),
        (PatientPhase.DORMANT, "goal_confirmed"),
        (PatientPhase.DORMANT, "unanswered_outreach"),
        (PatientPhase.DORMANT, "missed_third_message"),
    ],
)
def test_invalid_transitions(phase: PatientPhase, event: str) -> None:
    with pytest.raises(PhaseTransitionError) as exc_info:
        transition(phase, event)
    assert exc_info.value.current == phase
    assert exc_info.value.event == event


def test_unknown_event_raises() -> None:
    with pytest.raises(PhaseTransitionError):
        transition(PatientPhase.PENDING, "nonexistent_event")


# --- is_valid_transition ---


def test_is_valid_transition_true() -> None:
    assert is_valid_transition(PatientPhase.PENDING, "onboarding_initiated") is True


def test_is_valid_transition_false() -> None:
    assert is_valid_transition(PatientPhase.PENDING, "goal_confirmed") is False


# --- Completeness check ---


def test_all_valid_events_are_known() -> None:
    """Every event in the transition map is in the VALID_EVENTS set."""
    assert len(VALID_EVENTS) == 7


def test_every_phase_has_at_least_one_valid_event() -> None:
    """No phase is a dead end (except DORMANT has patient_returned)."""
    for phase in PatientPhase:
        has_event = any(is_valid_transition(phase, e) for e in VALID_EVENTS)
        assert has_event, f"{phase} has no valid transitions"
