"""Manage history node — conditional message trimming."""

from __future__ import annotations

from health_coach.agent.state import PatientState  # noqa: TC001

MESSAGE_THRESHOLD = 20


async def manage_history(
    state: PatientState,
    **_kwargs: object,
) -> dict[str, object]:
    """Conditionally trim message history if above threshold.

    M3 stub: always passes through (no-op).
    Real implementation will LLM-summarize and use RemoveMessage.
    """
    # No-op in M3 — messages below threshold
    return {}
