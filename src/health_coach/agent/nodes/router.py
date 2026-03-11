"""Phase router — pure function, deterministic routing by phase."""

from __future__ import annotations

from health_coach.agent.state import PatientState  # noqa: TC001
from health_coach.domain.phases import PatientPhase


def phase_router(state: PatientState) -> str:
    """Route to the appropriate phase-specific node.

    Pure function — no I/O, no LLM. Reads state["phase"] and returns
    the node name string for conditional edge routing.
    """
    phase = state.get("phase", PatientPhase.PENDING.value)

    routing: dict[str, str] = {
        PatientPhase.PENDING.value: "pending_node",
        PatientPhase.ONBOARDING.value: "onboarding_agent",
        PatientPhase.ACTIVE.value: "active_agent",
        PatientPhase.RE_ENGAGING.value: "reengagement_agent",
        PatientPhase.DORMANT.value: "dormant_node",
    }

    return routing.get(phase, "pending_node")
