"""Active phase agent node — stub for M3, replaced in M5."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from health_coach.agent.state import PatientState  # noqa: TC001


async def active_agent(
    state: PatientState,
    **_kwargs: object,
) -> dict[str, object]:
    """Stub active phase agent — returns placeholder message."""
    return {
        "messages": [AIMessage(content="Keep up the great work with your exercises!")],
        "outbound_message": "Keep up the great work with your exercises!",
    }
