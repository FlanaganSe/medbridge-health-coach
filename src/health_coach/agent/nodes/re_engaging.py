"""Re-engagement agent node — stub for M3, replaced in M5."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from health_coach.agent.state import PatientState  # noqa: TC001


async def reengagement_agent(
    state: PatientState,
    **_kwargs: object,
) -> dict[str, object]:
    """Stub re-engagement agent — returns placeholder message."""
    return {
        "messages": [
            AIMessage(content="Welcome back! I'm glad to hear from you. Let's get back on track.")
        ],
        "outbound_message": "Welcome back! I'm glad to hear from you. Let's get back on track.",
    }
