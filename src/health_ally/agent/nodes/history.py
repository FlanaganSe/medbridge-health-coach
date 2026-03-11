"""Manage history node — pass-through for demo.

Demo conversations are short-lived, so no trimming is needed.
If history trimming is needed later, implement here with RemoveMessage.
"""

from __future__ import annotations

from health_ally.agent.state import PatientState  # noqa: TC001


async def manage_history(
    state: PatientState,
    **_kwargs: object,
) -> dict[str, object]:
    """Pass-through — no history management needed for demo conversations."""
    return {}
