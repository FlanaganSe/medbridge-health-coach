"""Retry generation node — stub for M3, replaced in M4."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from health_coach.agent.state import PatientState  # noqa: TC001


async def retry_generation(
    state: PatientState,
    **_kwargs: object,
) -> dict[str, object]:
    """Stub retry — increments counter and returns safe placeholder.

    Real implementation in M4 appends augmented HumanMessage
    and re-invokes the LLM with tighter constraints.
    """
    return {
        "safety_retry_count": state.get("safety_retry_count", 0) + 1,
        "messages": [
            AIMessage(content="I want to make sure I'm being helpful. Let me try again.")
        ],
        "outbound_message": "I want to make sure I'm being helpful. Let me try again.",
    }
