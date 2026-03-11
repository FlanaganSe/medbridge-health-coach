"""Safety tests — clinical boundary detection and fallback behavior."""

from __future__ import annotations

from typing import TYPE_CHECKING

from health_coach.agent.nodes.fallback import fallback_response
from health_coach.domain.safety import (
    CLINICAL_REDIRECT_MESSAGE,
    CRISIS_RESPONSE_MESSAGE,
    SAFE_FALLBACK_MESSAGE,
)
from health_coach.domain.safety_types import SafetyDecision

if TYPE_CHECKING:
    from health_coach.agent.state import PatientState


async def test_fallback_crisis_message() -> None:
    """Crisis detected → 988 response message."""
    state: PatientState = {
        "patient_id": "p1",
        "tenant_id": "t1",
        "crisis_detected": True,
    }
    result = await fallback_response(state)
    assert result["outbound_message"] == CRISIS_RESPONSE_MESSAGE


async def test_fallback_clinical_boundary_message() -> None:
    """Clinical boundary → redirect to care team."""
    state: PatientState = {
        "patient_id": "p1",
        "tenant_id": "t1",
        "safety_decision": SafetyDecision.CLINICAL_BOUNDARY.value,
    }
    result = await fallback_response(state)
    assert result["outbound_message"] == CLINICAL_REDIRECT_MESSAGE


async def test_fallback_default_message() -> None:
    """No crisis, no clinical boundary → generic safe fallback."""
    state: PatientState = {
        "patient_id": "p1",
        "tenant_id": "t1",
    }
    result = await fallback_response(state)
    assert result["outbound_message"] == SAFE_FALLBACK_MESSAGE


async def test_fallback_crisis_overrides_clinical() -> None:
    """Crisis takes precedence over clinical_boundary."""
    state: PatientState = {
        "patient_id": "p1",
        "tenant_id": "t1",
        "crisis_detected": True,
        "safety_decision": SafetyDecision.CLINICAL_BOUNDARY.value,
    }
    result = await fallback_response(state)
    assert result["outbound_message"] == CRISIS_RESPONSE_MESSAGE
