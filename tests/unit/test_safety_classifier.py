"""Tests for the safety classifier — prompts, classifier output, and routing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from health_ally.agent.nodes.safety import safety_route
from health_ally.domain.safety_types import SafetyDecision

if TYPE_CHECKING:
    from health_ally.agent.state import PatientState


def test_safety_route_safe() -> None:
    """SAFE decision routes to save_patient_context."""
    state: PatientState = {
        "patient_id": "p1",
        "tenant_id": "t1",
        "safety_decision": SafetyDecision.SAFE.value,
    }
    assert safety_route(state) == "save_patient_context"


def test_safety_route_clinical_boundary_is_advisory() -> None:
    """CLINICAL_BOUNDARY is advisory — routes to save (no retry/fallback)."""
    state: PatientState = {
        "patient_id": "p1",
        "tenant_id": "t1",
        "safety_decision": SafetyDecision.CLINICAL_BOUNDARY.value,
        "safety_retry_count": 0,
    }
    assert safety_route(state) == "save_patient_context"


def test_safety_route_crisis_no_retry() -> None:
    """CRISIS always routes to fallback_response — never retry."""
    state: PatientState = {
        "patient_id": "p1",
        "tenant_id": "t1",
        "safety_decision": SafetyDecision.CRISIS.value,
        "safety_retry_count": 0,
    }
    assert safety_route(state) == "fallback_response"


def test_safety_route_jailbreak_no_retry() -> None:
    """JAILBREAK always routes to fallback_response — never retry."""
    state: PatientState = {
        "patient_id": "p1",
        "tenant_id": "t1",
        "safety_decision": SafetyDecision.JAILBREAK.value,
    }
    assert safety_route(state) == "fallback_response"


def test_safety_route_default_is_safe() -> None:
    """Missing safety_decision defaults to SAFE routing."""
    state: PatientState = {
        "patient_id": "p1",
        "tenant_id": "t1",
    }
    assert safety_route(state) == "save_patient_context"
