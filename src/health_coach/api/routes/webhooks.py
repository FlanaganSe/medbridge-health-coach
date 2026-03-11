"""MedBridge Go webhook receiver.

POST /webhooks/medbridge — receives patient events (login, message, consent change).
Uses HMAC signature verification and ProcessedEvent deduplication.
"""

# pyright: reportUnknownVariableType=false
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from health_coach.integrations.medbridge import verify_webhook_signature
from health_coach.persistence.models import (
    Patient,
    PatientConsentSnapshot,
    ProcessedEvent,
)

logger = structlog.stdlib.get_logger()
router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/medbridge")
async def medbridge_webhook(
    request: Request,
    x_signature: str = Header("", alias="X-Webhook-Signature"),
) -> dict[str, str]:
    """Receive and process MedBridge Go webhook events."""
    body = await request.body()
    settings = request.app.state.settings

    # HMAC signature verification
    webhook_secret = getattr(settings, "medbridge_webhook_secret", "")
    if webhook_secret and not verify_webhook_signature(body, x_signature, webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload: dict[str, Any] = await request.json()
    event_type = str(payload.get("event_type", ""))
    event_key = str(payload.get("event_id", ""))
    tenant_id = str(payload.get("tenant_id", ""))

    if not event_type or not event_key:
        raise HTTPException(status_code=400, detail="Missing event_type or event_id")

    session_factory = request.app.state.session_factory

    # Deduplication via ProcessedEvent
    async with session_factory() as session:
        existing = await session.execute(
            select(ProcessedEvent).where(ProcessedEvent.source_event_key == event_key)
        )
        if existing.scalars().first() is not None:
            return {"status": "duplicate"}

    # Route to handler
    if event_type == "patient_message":
        await _handle_patient_message(request, payload)
    elif event_type == "consent_change":
        await _handle_consent_change(session_factory, payload, tenant_id)
    elif event_type == "patient_login":
        await logger.ainfo("webhook_patient_login", payload_keys=list(payload.keys()))
    else:
        await logger.ainfo("webhook_unknown_event", event_type=event_type)

    # Record processed event (idempotent)
    async with session_factory() as session, session.begin():
        stmt = sqlite_insert(ProcessedEvent).values(
            tenant_id=tenant_id,
            source_event_key=event_key,
            event_type=event_type,
        )
        # ON CONFLICT DO NOTHING for idempotency
        stmt = stmt.on_conflict_do_nothing(index_elements=["source_event_key"])
        await session.execute(stmt)

    return {"status": "processed"}


async def _handle_patient_message(
    request: Request,
    payload: dict[str, Any],
) -> None:
    """Process a patient message event — invokes the graph."""
    from langchain_core.messages import HumanMessage

    from health_coach.persistence.locking import patient_advisory_lock

    patient_id = str(payload.get("patient_id", ""))
    tenant_id = str(payload.get("tenant_id", ""))
    message_text = str(payload.get("message", ""))

    if not patient_id or not message_text:
        return

    graph = request.app.state.graph
    engine = request.app.state.engine
    ctx = request.app.state.ctx_factory(
        request.app.state.session_factory,
        engine,
    )

    thread_id = f"patient-{patient_id}"

    async with patient_advisory_lock(engine, patient_id):
        await graph.ainvoke(
            {
                "patient_id": patient_id,
                "tenant_id": tenant_id,
                "messages": [HumanMessage(content=message_text)],
                "invocation_source": "patient",
            },
            config={
                "configurable": {
                    "ctx": ctx,
                    "thread_id": thread_id,
                }
            },
        )


async def _handle_consent_change(
    session_factory: object,
    payload: dict[str, Any],
    tenant_id: str,
) -> None:
    """Process a consent change event — update domain DB directly (no graph)."""
    from datetime import UTC, datetime

    patient_id = str(payload.get("patient_id", ""))
    consented = bool(payload.get("consented", False))
    reason = str(payload.get("reason", "webhook_update"))

    if not patient_id:
        return

    async with session_factory() as session, session.begin():  # type: ignore[union-attr]
        # Verify patient exists
        patient = await session.execute(
            select(Patient).where(Patient.external_patient_id == patient_id)
        )
        patient_row = patient.scalars().first()
        if patient_row is None:
            await logger.awarning("webhook_patient_not_found", patient_id=patient_id)
            return

        session.add(
            PatientConsentSnapshot(
                tenant_id=tenant_id,
                patient_id=patient_row.id,
                consented=consented,
                reason=reason,
                checked_at=datetime.now(UTC),
            )
        )
