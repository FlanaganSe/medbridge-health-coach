"""Fallback response node — deterministic safe message based on context."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from health_ally.agent.state import PatientState  # noqa: TC001
from health_ally.domain.safety import (
    CLINICAL_REDIRECT_MESSAGE,
    CRISIS_RESPONSE_MESSAGE,
    SAFE_FALLBACK_MESSAGE,
)
from health_ally.domain.safety_types import SafetyDecision


async def fallback_response(
    state: PatientState,
    **_kwargs: object,
) -> dict[str, object]:
    """Return a deterministic safe fallback message.

    Selects message based on context:
    - Crisis detected → 988 crisis response
    - Clinical boundary → redirect to care team
    - Default → generic safe fallback
    """
    if state.get("crisis_detected"):
        message = CRISIS_RESPONSE_MESSAGE
    elif state.get("safety_decision") == SafetyDecision.CLINICAL_BOUNDARY.value:
        message = CLINICAL_REDIRECT_MESSAGE
    else:
        message = SAFE_FALLBACK_MESSAGE

    return {
        "messages": [AIMessage(content=message)],
        "outbound_message": message,
        "safety_decision": "fallback",
    }
