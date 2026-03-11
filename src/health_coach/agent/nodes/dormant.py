"""Dormant node — handles patient return or logs no-op for scheduler."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from health_coach.agent.state import PatientState  # noqa: TC001

if TYPE_CHECKING:
    from health_coach.agent.state import PendingEffects

logger = structlog.stdlib.get_logger()


async def dormant_node(
    state: PatientState,
    **_kwargs: object,
) -> dict[str, object]:
    """Handle dormant patient interaction.

    - Patient-initiated: triggers patient_returned → RE_ENGAGING
    - Scheduler-initiated: no-op (no further proactive outreach)
    """
    patient_id = state.get("patient_id")
    invocation_source = state.get("invocation_source")

    if invocation_source == "patient":
        # Patient returned — transition to RE_ENGAGING
        current_effects: PendingEffects = state.get("pending_effects") or {}
        updated_effects: PendingEffects = {
            **current_effects,  # type: ignore[typeddict-item]
            "phase_event": "patient_returned",
            "audit_events": [
                *current_effects.get("audit_events", []),
                {
                    "event_type": "patient_returned",
                    "outcome": "re_engaging",
                    "metadata": {},
                },
            ],
        }
        logger.info(
            "dormant_patient_returned",
            patient_id=patient_id,
        )
        return {
            "pending_effects": updated_effects,
            "outbound_message": None,
        }

    logger.info(
        "dormant_no_outreach",
        patient_id=patient_id,
        invocation_source=invocation_source,
    )
    return {"outbound_message": None}
