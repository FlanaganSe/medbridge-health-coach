"""Onboarding agent node — stub for M3, replaced in M4."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from health_coach.agent.state import PatientState  # noqa: TC001


async def onboarding_agent(
    state: PatientState,
    **_kwargs: object,
) -> dict[str, object]:
    """Stub onboarding agent — returns placeholder message.

    Real implementation in M4 will use LLM with tool binding.
    """
    return {
        "messages": [
            AIMessage(
                content=(
                    "That's a great goal! Let me help you set that up. "
                    "I'll make sure to check in with you regularly."
                ),
            )
        ],
        "outbound_message": (
            "That's a great goal! Let me help you set that up. "
            "I'll make sure to check in with you regularly."
        ),
    }
