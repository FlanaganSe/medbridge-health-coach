"""Context loading and saving nodes."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import update

from health_coach.agent.context import get_coach_context
from health_coach.agent.state import PatientState  # noqa: TC001
from health_coach.domain.errors import PhaseTransitionError
from health_coach.domain.phase_machine import transition
from health_coach.domain.phases import PatientPhase
from health_coach.persistence.models import (
    AuditEvent,
    ClinicianAlert,
    OutboxEntry,
    PatientGoal,
    SafetyDecisionRecord,
    ScheduledJob,
)

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

    from health_coach.agent.state import PendingEffects

logger = structlog.stdlib.get_logger()

EMPTY_EFFECTS: PendingEffects = {
    "goal": None,
    "alerts": [],
    "phase_event": None,
    "scheduled_jobs": [],
    "safety_decisions": [],
    "outbox_entries": [],
    "audit_events": [],
}


async def load_patient_context(
    state: PatientState,
    config: RunnableConfig,
) -> dict[str, object]:
    """Load patient data from domain DB into graph state."""
    from health_coach.persistence.models import Patient

    ctx = get_coach_context(config)
    patient_id = state["patient_id"]

    async with ctx.session_factory() as session:
        patient = await session.get(Patient, uuid.UUID(patient_id))

    if patient is None:
        logger.warning("patient_not_found", patient_id=patient_id)
        return {
            "phase": PatientPhase.PENDING.value,
            "unanswered_count": 0,
            "pending_effects": dict(EMPTY_EFFECTS),
        }

    return {
        "phase": patient.phase,
        "unanswered_count": patient.unanswered_count,
        "last_outreach_at": (
            patient.last_outreach_at.isoformat() if patient.last_outreach_at else None
        ),
        "last_patient_response_at": (
            patient.last_patient_response_at.isoformat()
            if patient.last_patient_response_at
            else None
        ),
        "pending_effects": dict(EMPTY_EFFECTS),
    }


async def save_patient_context(
    state: PatientState,
    config: RunnableConfig,
) -> dict[str, object]:
    """Flush all accumulated pending_effects to the domain DB atomically.

    This is the ONLY node (besides crisis_check and consent_gate)
    that writes to the domain DB. Contains zero LLM calls.
    """
    from health_coach.persistence.models import Patient

    ctx = get_coach_context(config)
    effects = state.get("pending_effects") or {}
    patient_id = state["patient_id"]
    tenant_id = state["tenant_id"]
    pid = uuid.UUID(patient_id)

    async with ctx.session_factory() as session, session.begin():
        patient = await session.get(Patient, pid)
        if patient is None:
            logger.error("save_patient_context_no_patient", patient_id=patient_id)
            return {}

        # Apply phase transition if requested
        phase_event = effects.get("phase_event")
        if phase_event:
            current_phase = PatientPhase(patient.phase)
            try:
                new_phase = transition(current_phase, phase_event)
                patient.phase = new_phase.value

                # Cancel pending jobs on phase transition
                await session.execute(
                    update(ScheduledJob)
                    .where(
                        ScheduledJob.patient_id == pid,
                        ScheduledJob.status == "pending",
                    )
                    .values(status="cancelled")
                )

                session.add(
                    AuditEvent(
                        tenant_id=tenant_id,
                        patient_id=pid,
                        event_type="phase_transition",
                        outcome=new_phase.value,
                        metadata_={
                            "from": current_phase.value,
                            "event": phase_event,
                        },
                    )
                )
            except PhaseTransitionError:
                # Replay safety: if already at target, skip
                target = _expected_target(phase_event)
                if target and patient.phase == target:
                    logger.info(
                        "phase_transition_already_applied",
                        patient_id=patient_id,
                        phase=patient.phase,
                    )
                else:
                    raise

        # Reset unanswered count on patient response
        if state.get("invocation_source") == "patient":
            patient.unanswered_count = 0

        # Persist goal
        goal_data = effects.get("goal")
        if goal_data:
            goal = PatientGoal(
                tenant_id=tenant_id,
                patient_id=pid,
                goal_text=str(goal_data.get("goal_text", "")),
                raw_patient_text=str(goal_data.get("raw_patient_text", "")),
                structured_goal=goal_data.get("structured_goal"),  # type: ignore[arg-type]
                idempotency_key=str(goal_data.get("idempotency_key", "")),
            )
            session.add(goal)

        # Write safety decisions
        for sd in effects.get("safety_decisions", []):
            session.add(
                SafetyDecisionRecord(
                    tenant_id=tenant_id,
                    patient_id=pid,
                    decision=str(sd.get("decision", "")),
                    source=str(sd.get("source", "classifier")),
                    confidence=sd.get("confidence"),  # type: ignore[arg-type]
                    reasoning=sd.get("reasoning"),  # type: ignore[arg-type]
                )
            )

        # Write clinician alerts + outbox entries for delivery
        for alert_data in effects.get("alerts", []):
            idempotency_key = str(alert_data.get("idempotency_key", ""))
            session.add(
                ClinicianAlert(
                    tenant_id=tenant_id,
                    patient_id=pid,
                    reason=str(alert_data.get("reason", "")),
                    priority=str(alert_data.get("priority", "routine")),
                    idempotency_key=idempotency_key,
                )
            )
            # Alert delivery via outbox (clinician alerts skip consent re-check)
            session.add(
                OutboxEntry(
                    tenant_id=tenant_id,
                    patient_id=pid,
                    delivery_key=idempotency_key,
                    message_type="clinician_alert",
                    priority=1 if alert_data.get("priority") == "urgent" else 0,
                    channel="default",
                    payload={
                        "reason": str(alert_data.get("reason", "")),
                        "priority": str(alert_data.get("priority", "routine")),
                    },
                    status="pending",
                )
            )

        # Write outbox entries
        for entry in effects.get("outbox_entries", []):
            session.add(
                OutboxEntry(
                    tenant_id=tenant_id,
                    patient_id=pid,
                    delivery_key=str(entry.get("delivery_key", "")),
                    message_type=str(entry.get("message_type", "patient_message")),
                    priority=int(entry.get("priority", 0)),  # type: ignore[arg-type]
                    channel=str(entry.get("channel", "default")),
                    payload=entry.get("payload"),  # type: ignore[arg-type]
                    status="pending",
                )
            )

        # Write scheduled jobs
        for job in effects.get("scheduled_jobs", []):
            session.add(
                ScheduledJob(
                    tenant_id=tenant_id,
                    patient_id=pid,
                    job_type=str(job.get("job_type", "")),
                    idempotency_key=str(job.get("idempotency_key", "")),
                    scheduled_at=job.get("scheduled_at"),  # type: ignore[arg-type]
                    metadata_=job.get("metadata"),  # type: ignore[arg-type]
                )
            )

        # Write audit events
        for ae in effects.get("audit_events", []):
            session.add(
                AuditEvent(
                    tenant_id=tenant_id,
                    patient_id=pid,
                    event_type=str(ae.get("event_type", "")),
                    outcome=str(ae.get("outcome", "")),
                    metadata_=ae.get("metadata"),  # type: ignore[arg-type]
                )
            )

        # Update last_outreach_at if outreach outbox entry created
        if effects.get("outbox_entries") and state.get("invocation_source") == "scheduler":
            from datetime import UTC, datetime

            patient.last_outreach_at = datetime.now(UTC)

    logger.info("patient_context_saved", patient_id=patient_id)
    return {"pending_effects": None}


def _expected_target(event: str) -> str | None:
    """Map phase events to their expected target phase for replay safety."""
    targets: dict[str, str] = {
        "onboarding_initiated": "onboarding",
        "goal_confirmed": "active",
        "no_response_timeout": "dormant",
        "unanswered_outreach": "re_engaging",
        "missed_third_message": "dormant",
        "patient_responded": "active",
        "patient_returned": "re_engaging",
    }
    return targets.get(event)
