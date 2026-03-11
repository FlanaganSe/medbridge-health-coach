"""Consent gate node — verifies patient login + outreach consent."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import structlog

from health_coach.agent.context import get_coach_context
from health_coach.agent.state import PatientState  # noqa: TC001
from health_coach.persistence.models import AuditEvent

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig


logger = structlog.stdlib.get_logger()


def consent_route(state: PatientState) -> str:
    """Route based on consent_verified flag set by consent_gate."""
    if state.get("consent_verified"):
        return "load_patient_context"
    return "__end__"


async def consent_gate(
    state: PatientState,
    config: RunnableConfig,
) -> dict[str, object]:
    """Check consent before any LLM activity.

    If denied: writes consent audit event directly to DB
    (Plan Invariant #1 exception b) and exits graph via consent_route.
    """
    ctx = get_coach_context(config)
    patient_id = state["patient_id"]
    tenant_id = state["tenant_id"]

    result = await ctx.consent_service.check(patient_id, tenant_id)

    if result.allowed:
        return {"consent_verified": True}

    # Consent denied — write audit event directly (exception to AD-2)
    async with ctx.session_factory() as session:
        audit = AuditEvent(
            tenant_id=tenant_id,
            patient_id=uuid.UUID(patient_id),
            event_type="consent_check",
            outcome="denied",
            metadata_={
                "logged_in": result.logged_in,
                "consented": result.consented_to_outreach,
                "reason": result.reason,
                "checked_at": result.checked_at.isoformat(),
            },
        )
        session.add(audit)
        await session.commit()

    logger.info(
        "consent_denied",
        patient_id=patient_id,
        reason=result.reason,
    )

    return {"consent_verified": False}
