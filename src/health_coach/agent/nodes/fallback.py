"""Fallback response node — deterministic safe message."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from health_coach.agent.state import PatientState  # noqa: TC001

SAFE_FALLBACK_MESSAGE = (
    "I appreciate your patience. For any health-related questions, "
    "please reach out to your care team directly. "
    "I'm here to help you stay on track with your exercises!"
)


async def fallback_response(
    state: PatientState,
    **_kwargs: object,
) -> dict[str, object]:
    """Return a deterministic safe fallback message."""
    return {
        "messages": [AIMessage(content=SAFE_FALLBACK_MESSAGE)],
        "outbound_message": SAFE_FALLBACK_MESSAGE,
        "safety_decision": "fallback",
    }
