"""Deterministic phase state machine.

All transitions are defined here as a pure adjacency map.
No I/O, no LLM — fully testable in isolation.
"""

from __future__ import annotations

from health_ally.domain.errors import PhaseTransitionError
from health_ally.domain.phases import PatientPhase

_TRANSITIONS: dict[tuple[PatientPhase, str], PatientPhase] = {
    # PENDING → ONBOARDING: coach initiates onboarding
    (PatientPhase.PENDING, "onboarding_initiated"): PatientPhase.ONBOARDING,
    # ONBOARDING → ACTIVE: patient confirms goal
    (PatientPhase.ONBOARDING, "goal_confirmed"): PatientPhase.ACTIVE,
    # ONBOARDING → DORMANT: no response within timeout
    (PatientPhase.ONBOARDING, "no_response_timeout"): PatientPhase.DORMANT,
    # ACTIVE → RE_ENGAGING: first unanswered outreach detected
    (PatientPhase.ACTIVE, "unanswered_outreach"): PatientPhase.RE_ENGAGING,
    # RE_ENGAGING → DORMANT: third unanswered message
    (PatientPhase.RE_ENGAGING, "missed_third_message"): PatientPhase.DORMANT,
    # RE_ENGAGING → ACTIVE: patient responded
    (PatientPhase.RE_ENGAGING, "patient_responded"): PatientPhase.ACTIVE,
    # DORMANT → RE_ENGAGING: dormant patient returns
    (PatientPhase.DORMANT, "patient_returned"): PatientPhase.RE_ENGAGING,
}

VALID_EVENTS: frozenset[str] = frozenset(event for _, event in _TRANSITIONS)


def transition(current: PatientPhase, event: str) -> PatientPhase:
    """Apply a phase transition event, returning the new phase.

    Raises PhaseTransitionError if the transition is invalid.
    """
    key = (current, event)
    target = _TRANSITIONS.get(key)
    if target is None:
        raise PhaseTransitionError(current, event)
    return target


def is_valid_transition(current: PatientPhase, event: str) -> bool:
    """Check if a transition is valid without raising."""
    return (current, event) in _TRANSITIONS


def transition_target(event: str) -> str | None:
    """Look up the target phase for a given event (any source phase).

    Returns the target phase value, or None if the event is unknown.
    Used for replay safety in save_patient_context.
    """
    for (_, evt), target in _TRANSITIONS.items():
        if evt == event:
            return target.value
    return None
