"""Tests for the LangGraph state definition."""

from __future__ import annotations

from health_coach.agent.state import PatientState, PendingEffects


def test_patient_state_is_typeddict() -> None:
    """PatientState is a TypedDict with total=False (all optional except Required)."""
    state: PatientState = {"patient_id": "p1", "tenant_id": "t1"}
    assert state["patient_id"] == "p1"
    assert state["tenant_id"] == "t1"


def test_patient_state_optional_fields() -> None:
    """Optional fields can be omitted."""
    state: PatientState = {"patient_id": "p1", "tenant_id": "t1"}
    assert state.get("phase") is None
    assert state.get("consent_verified") is None
    assert state.get("safety_decision") is None


def test_pending_effects_defaults() -> None:
    """PendingEffects can be constructed with no fields (total=False)."""
    effects: PendingEffects = {}
    assert effects.get("goal") is None
    assert effects.get("alerts") is None
    assert effects.get("phase_event") is None


def test_pending_effects_full() -> None:
    """PendingEffects can be constructed with all fields."""
    effects: PendingEffects = {
        "goal": {"goal_text": "test"},
        "alerts": [{"reason": "test"}],
        "phase_event": "onboarding_initiated",
        "scheduled_jobs": [],
        "safety_decisions": [],
        "outbox_entries": [],
        "audit_events": [],
    }
    assert effects["phase_event"] == "onboarding_initiated"
    assert effects["goal"] == {"goal_text": "test"}
