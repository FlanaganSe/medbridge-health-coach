"""Safety gate node — stub for M3, replaced in M4."""

from __future__ import annotations

from health_coach.agent.state import PatientState  # noqa: TC001


async def safety_gate(
    state: PatientState,
    **_kwargs: object,
) -> dict[str, object]:
    """Stub safety gate — always returns SAFE.

    Real implementation in M4 will use safety classifier LLM call.
    """
    return {"safety_decision": "safe"}


def safety_route(state: PatientState) -> str:
    """Route based on safety decision."""
    decision = state.get("safety_decision", "safe")

    if decision == "safe":
        return "save_patient_context"
    if decision == "clinical_boundary":
        retry_count = state.get("safety_retry_count", 0)
        if retry_count < 1:
            return "retry_generation"
        return "fallback_response"
    # crisis or jailbreak — never retry
    return "fallback_response"
