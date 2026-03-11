"""Dormant node — logs interaction, no outbound message."""

from __future__ import annotations

import structlog

from health_coach.agent.state import PatientState  # noqa: TC001

logger = structlog.stdlib.get_logger()


async def dormant_node(
    state: PatientState,
    **_kwargs: object,
) -> dict[str, object]:
    """Handle dormant patient — no proactive outreach."""
    logger.info(
        "dormant_patient_interaction",
        patient_id=state.get("patient_id"),
        invocation_source=state.get("invocation_source"),
    )
    return {"outbound_message": None}
