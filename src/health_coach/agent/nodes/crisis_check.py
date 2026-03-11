"""Input-side crisis pre-check — classifies patient messages for crisis signals."""

# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false

from __future__ import annotations

import hashlib
import uuid
from typing import TYPE_CHECKING

import structlog

from health_coach.agent.context import get_coach_context
from health_coach.agent.prompts.safety import CRISIS_CHECK_PROMPT
from health_coach.agent.state import PatientState  # noqa: TC001
from health_coach.domain.safety_types import ClassifierOutput, CrisisLevel
from health_coach.persistence.models import AuditEvent, ClinicianAlert, OutboxEntry

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

    from health_coach.agent.context import CoachContext

logger = structlog.stdlib.get_logger()


async def crisis_check(
    state: PatientState,
    config: RunnableConfig,
) -> dict[str, object]:
    """Check patient input for crisis signals before main generation.

    Skipped when invocation_source != "patient" (no patient message to check).
    EXPLICIT crisis writes durable alert immediately (Plan Invariant #1 exception a).
    POSSIBLE crisis creates routine alert via pending_effects.
    """
    # Skip on proactive outreach — no patient message to check
    if state.get("invocation_source") != "patient":
        return {"crisis_detected": False}

    # Get the last patient message
    messages = state.get("messages", [])
    if not messages:
        return {"crisis_detected": False}

    last_message = messages[-1]
    patient_text = getattr(last_message, "content", "")
    if not patient_text:
        return {"crisis_detected": False}

    ctx = get_coach_context(config)
    patient_id = state["patient_id"]
    tenant_id = state["tenant_id"]

    # Classify using safety classifier model (Haiku)
    classifier_model = ctx.model_gateway.get_chat_model("classifier")
    structured_model = classifier_model.with_structured_output(ClassifierOutput)

    try:
        result: ClassifierOutput = await structured_model.ainvoke(  # type: ignore[assignment]
            [
                {"role": "system", "content": CRISIS_CHECK_PROMPT},
                {"role": "user", "content": patient_text},
            ]
        )
    except Exception:
        logger.exception("crisis_check_classifier_error", patient_id=patient_id)
        # Fail-safe: escalate — a missed crisis is worse than a false alarm.
        # Accumulate a routine alert so clinicians are notified of the failure.
        current_effects = state.get("pending_effects") or {}
        existing_alerts: list[dict[str, object]] = list(current_effects.get("alerts", []))
        content_hash = hashlib.sha256(patient_text.encode()).hexdigest()[:16]
        existing_alerts.append(
            {
                "reason": "Crisis classifier failed — manual review recommended",
                "priority": "urgent",
                "idempotency_key": f"{patient_id}:crisis_error:{content_hash}",
            }
        )
        updated_effects = {**current_effects, "alerts": existing_alerts}
        return {
            "crisis_detected": False,
            "pending_effects": updated_effects,
        }

    logger.info(
        "crisis_check_result",
        patient_id=patient_id,
        crisis_level=result.crisis_level,
        confidence=result.confidence,
    )

    if result.crisis_level == CrisisLevel.EXPLICIT:
        # Write durable alert immediately — must survive crashes
        await _write_crisis_alert(ctx, patient_id, tenant_id, patient_text, result)
        return {"crisis_detected": True}

    if result.crisis_level == CrisisLevel.POSSIBLE:
        # Accumulate routine alert via pending_effects
        current_effects = state.get("pending_effects") or {}
        existing_alerts: list[dict[str, object]] = list(current_effects.get("alerts", []))
        content_hash = hashlib.sha256(patient_text.encode()).hexdigest()[:16]
        existing_alerts.append(
            {
                "reason": f"Possible crisis detected: {result.reasoning}",
                "priority": "routine",
                "idempotency_key": f"{patient_id}:crisis_possible:{content_hash}",
            }
        )
        updated_effects = {
            **current_effects,
            "alerts": existing_alerts,
        }
        return {
            "crisis_detected": False,
            "pending_effects": updated_effects,
        }

    return {"crisis_detected": False}


async def _write_crisis_alert(
    ctx: CoachContext,
    patient_id: str,
    tenant_id: str,
    patient_text: str,
    result: ClassifierOutput,
) -> None:
    """Write durable crisis alert + outbox entry immediately.

    Plan Invariant #1 exception a: crisis alerts must survive crashes
    and be deliverable. Written outside the normal save_patient_context path.
    """
    pid = uuid.UUID(patient_id)
    content_hash = hashlib.sha256(patient_text.encode()).hexdigest()[:16]
    idempotency_key = f"{patient_id}:crisis_alert:{content_hash}"

    async with ctx.session_factory() as session, session.begin():
        # Write clinician alert
        session.add(
            ClinicianAlert(
                tenant_id=tenant_id,
                patient_id=pid,
                reason=f"EXPLICIT crisis detected: {result.reasoning}",
                priority="urgent",
                idempotency_key=idempotency_key,
            )
        )

        # Write outbox entry for delivery (skip consent for clinician alerts)
        session.add(
            OutboxEntry(
                tenant_id=tenant_id,
                patient_id=pid,
                delivery_key=idempotency_key,
                message_type="clinician_alert",
                priority=1,
                channel="default",
                payload={
                    "reason": f"EXPLICIT crisis detected: {result.reasoning}",
                    "priority": "urgent",
                    "patient_text_snippet": patient_text[:200],
                },
                status="pending",
            )
        )

        # Write audit event
        session.add(
            AuditEvent(
                tenant_id=tenant_id,
                patient_id=pid,
                event_type="crisis_detected",
                outcome="explicit",
                metadata_={
                    "crisis_level": result.crisis_level.value,
                    "confidence": result.confidence,
                    "reasoning": result.reasoning,
                },
            )
        )

    logger.warning(
        "crisis_alert_written",
        patient_id=patient_id,
        crisis_level=result.crisis_level,
    )
