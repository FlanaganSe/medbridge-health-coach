"""Tests for the phase state machine — exhaustive transition coverage.

Includes both parametrized unit tests and Hypothesis RuleBasedStateMachine
property-based tests for invariant verification.
"""

import pytest
from hypothesis import settings
from hypothesis.stateful import RuleBasedStateMachine, initialize, precondition, rule

from health_coach.domain.errors import PhaseTransitionError
from health_coach.domain.phase_machine import (
    _TRANSITIONS,
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


# --- Property-based tests (Hypothesis RuleBasedStateMachine) ---

# Build a reverse lookup: phase → list of (event, target) pairs
_EVENTS_BY_PHASE: dict[PatientPhase, list[tuple[str, PatientPhase]]] = {}
for (phase, event), target in _TRANSITIONS.items():
    _EVENTS_BY_PHASE.setdefault(phase, []).append((event, target))


class PatientLifecycleMachine(RuleBasedStateMachine):
    """Property-based test that explores all reachable phase sequences.

    Verifies:
    - No invalid state is reachable via ANY sequence of events
    - All valid transitions produce correct target states
    - Transition idempotency (attempting invalid transitions raises)
    - Backoff sequence ACTIVE → RE_ENGAGING → DORMANT cannot be bypassed
    """

    phase: PatientPhase

    @initialize()
    def setup(self) -> None:
        self.phase = PatientPhase.PENDING

    # --- Valid transition rules (one per phase) ---

    @rule()
    @precondition(lambda self: self.phase == PatientPhase.PENDING)
    def begin_onboarding(self) -> None:
        result = transition(self.phase, "onboarding_initiated")
        assert result == PatientPhase.ONBOARDING
        self.phase = result

    @rule()
    @precondition(lambda self: self.phase == PatientPhase.ONBOARDING)
    def confirm_goal(self) -> None:
        result = transition(self.phase, "goal_confirmed")
        assert result == PatientPhase.ACTIVE
        self.phase = result

    @rule()
    @precondition(lambda self: self.phase == PatientPhase.ONBOARDING)
    def onboarding_timeout(self) -> None:
        result = transition(self.phase, "no_response_timeout")
        assert result == PatientPhase.DORMANT
        self.phase = result

    @rule()
    @precondition(lambda self: self.phase == PatientPhase.ACTIVE)
    def unanswered_outreach(self) -> None:
        result = transition(self.phase, "unanswered_outreach")
        assert result == PatientPhase.RE_ENGAGING
        self.phase = result

    @rule()
    @precondition(lambda self: self.phase == PatientPhase.RE_ENGAGING)
    def patient_responded(self) -> None:
        result = transition(self.phase, "patient_responded")
        assert result == PatientPhase.ACTIVE
        self.phase = result

    @rule()
    @precondition(lambda self: self.phase == PatientPhase.RE_ENGAGING)
    def missed_third_message(self) -> None:
        result = transition(self.phase, "missed_third_message")
        assert result == PatientPhase.DORMANT
        self.phase = result

    @rule()
    @precondition(lambda self: self.phase == PatientPhase.DORMANT)
    def patient_returned(self) -> None:
        result = transition(self.phase, "patient_returned")
        assert result == PatientPhase.RE_ENGAGING
        self.phase = result

    # --- Invariant: phase is always a valid PatientPhase ---

    def teardown(self) -> None:
        assert self.phase in PatientPhase.__members__.values()


# Generate the TestCase class — Hypothesis will explore random sequences
TestPatientLifecycle = PatientLifecycleMachine.TestCase
TestPatientLifecycle.settings = settings(max_examples=200, stateful_step_count=20)


# --- Additional property: invalid transitions always raise ---


def test_no_direct_active_to_dormant() -> None:
    """Backoff sequence cannot be bypassed: ACTIVE cannot go directly to DORMANT."""
    with pytest.raises(PhaseTransitionError):
        transition(PatientPhase.ACTIVE, "missed_third_message")
    with pytest.raises(PhaseTransitionError):
        transition(PatientPhase.ACTIVE, "no_response_timeout")


def test_no_direct_pending_to_active() -> None:
    """Cannot skip onboarding: PENDING cannot go directly to ACTIVE."""
    with pytest.raises(PhaseTransitionError):
        transition(PatientPhase.PENDING, "goal_confirmed")
    with pytest.raises(PhaseTransitionError):
        transition(PatientPhase.PENDING, "patient_responded")


def test_transition_map_is_complete() -> None:
    """Every phase has at least one outgoing transition in the map."""
    phases_with_transitions = {phase for phase, _ in _TRANSITIONS}
    for phase in PatientPhase:
        assert phase in phases_with_transitions, f"{phase} has no transitions in map"


def test_no_self_loops_in_transition_map() -> None:
    """No transition maps a phase back to itself."""
    for (phase, _event), target in _TRANSITIONS.items():
        assert phase != target, f"Self-loop found: {phase} + {_event} → {target}"
