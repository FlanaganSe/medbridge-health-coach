"""Input-side crisis pre-check — stub for M3, replaced in M4."""

from __future__ import annotations

from health_coach.agent.state import PatientState  # noqa: TC001


async def crisis_check(
    state: PatientState,
    **_kwargs: object,
) -> dict[str, object]:
    """Stub crisis check — always returns no crisis.

    Real implementation in M4 will use safety classifier.
    Skipped when invocation_source != "patient".
    """
    return {"crisis_detected": False}
