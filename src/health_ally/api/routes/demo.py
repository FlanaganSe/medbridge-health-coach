"""Demo-only API endpoints for patient seeding, follow-up triggering, and reset.

Only registered when settings.environment == "dev".
These endpoints bypass normal webhook/consent flows for demo convenience.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

import structlog
from fastapi import APIRouter, HTTPException, Request
from langchain_core.messages import BaseMessage  # noqa: TC002
from pydantic import BaseModel
from sqlalchemy import delete, select

from health_ally.persistence.models import (
    AuditEvent,
    OutboxEntry,
    Patient,
    PatientConsentSnapshot,
    PatientGoal,
    ScheduledJob,
)

logger = structlog.stdlib.get_logger()
router = APIRouter(prefix="/v1/demo", tags=["demo"])


# --- Request / Response models ---


class SeedPatientRequest(BaseModel):
    tenant_id: str = "demo-tenant"
    external_patient_id: str | None = None
    timezone: str = "America/New_York"


class SeedPatientResponse(BaseModel):
    patient_id: str
    external_patient_id: str
    phase: str


class TriggerFollowupResponse(BaseModel):
    job_id: str
    job_type: str
    original_scheduled_at: str
    status: str


class ScheduledJobItem(BaseModel):
    id: str
    job_type: str
    status: str
    scheduled_at: str
    attempts: int
    max_attempts: int
    created_at: str


class ScheduledJobsResponse(BaseModel):
    jobs: list[ScheduledJobItem]


class ResetPatientResponse(BaseModel):
    patient_id: str
    phase: str
    deleted_goals: int
    deleted_jobs: int
    deleted_outbox: int


class AuditEventItem(BaseModel):
    id: str
    event_type: str
    outcome: str
    created_at: str


class AuditEventsResponse(BaseModel):
    events: list[AuditEventItem]


class ConversationMessageItem(BaseModel):
    role: str
    content: str
    tool_name: str | None = None
    message_id: str


class ConversationHistoryResponse(BaseModel):
    messages: list[ConversationMessageItem]


# --- Helpers ---


def _serialize_message(
    msg: BaseMessage,
    tool_call_names: dict[str, str],
) -> ConversationMessageItem | None:
    """Serialize a LangChain message to API response, filtering noise."""
    raw = cast("str | list[dict[str, Any]]", msg.content)  # type: ignore[reportUnknownMemberType]
    if isinstance(raw, list):
        parts: list[str] = []
        for block in raw:
            if block.get("type") == "text" and block.get("text"):
                parts.append(str(block["text"]))
        content = " ".join(parts)
    else:
        content = str(raw) if raw else ""

    # Filter empty sentinels and tool-invoking AIMessages with no visible text
    if not content:
        return None

    role = {"human": "human", "ai": "ai", "tool": "tool"}.get(msg.type, "ai")

    # Recover tool name: prefer msg.name, then look up from preceding AIMessage tool_calls
    tool_name: str | None = None
    if role == "tool":
        tool_name = getattr(msg, "name", None) or None
        if not tool_name:
            tc_id = getattr(msg, "tool_call_id", None)
            if tc_id:
                tool_name = tool_call_names.get(tc_id)

    return ConversationMessageItem(
        role=role,
        content=content,
        tool_name=tool_name,
        message_id=str(msg.id) if msg.id else str(uuid.uuid4()),
    )


# --- Endpoints ---


@router.post("/seed-patient", response_model=SeedPatientResponse)
async def seed_patient(
    request: Request,
    body: SeedPatientRequest,
) -> SeedPatientResponse:
    """Create a patient record with consent, transitioning to ONBOARDING.

    Equivalent to webhook patient_login + consent_granted, but in one call.
    """
    session_factory = request.app.state.session_factory

    ext_id = body.external_patient_id or str(uuid.uuid4())

    async with session_factory() as session, session.begin():
        # Check if patient already exists
        existing = await session.execute(
            select(Patient).where(
                Patient.tenant_id == body.tenant_id,
                Patient.external_patient_id == ext_id,
            )
        )
        patient = existing.scalars().first()

        if patient is not None:
            return SeedPatientResponse(
                patient_id=str(patient.id),
                external_patient_id=patient.external_patient_id,
                phase=patient.phase,
            )

        # Create new patient in PENDING phase
        patient = Patient(
            tenant_id=body.tenant_id,
            external_patient_id=ext_id,
            phase="pending",
            timezone=body.timezone,
        )
        session.add(patient)
        await session.flush()

        # Grant consent
        session.add(
            PatientConsentSnapshot(
                tenant_id=body.tenant_id,
                patient_id=patient.id,
                consented=True,
                reason="demo_seed",
                checked_at=datetime.now(UTC),
            )
        )

        # Record audit event
        session.add(
            AuditEvent(
                tenant_id=body.tenant_id,
                patient_id=patient.id,
                event_type="demo_seed",
                outcome="patient_created",
            )
        )

    return SeedPatientResponse(
        patient_id=str(patient.id),
        external_patient_id=ext_id,
        phase="pending",
    )


@router.post(
    "/trigger-followup/{patient_id}",
    response_model=TriggerFollowupResponse,
)
async def trigger_followup(
    request: Request,
    patient_id: str,
) -> TriggerFollowupResponse:
    """Make the next pending scheduled job immediately due.

    Finds the earliest pending ScheduledJob for this patient and sets
    scheduled_at to now. The scheduler will pick it up on the next poll.
    """
    session_factory = request.app.state.session_factory

    try:
        pid = uuid.UUID(patient_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail="Invalid patient_id format") from err

    async with session_factory() as session, session.begin():
        result = await session.execute(
            select(ScheduledJob)
            .where(
                ScheduledJob.patient_id == pid,
                ScheduledJob.status == "pending",
            )
            .order_by(ScheduledJob.scheduled_at.asc())
            .limit(1)
        )
        job = result.scalars().first()

        if job is None:
            raise HTTPException(
                status_code=404,
                detail="No pending scheduled jobs for this patient",
            )

        original_time = job.scheduled_at.isoformat()
        job.scheduled_at = datetime.now(UTC)
        await session.flush()

    return TriggerFollowupResponse(
        job_id=str(job.id),
        job_type=job.job_type,
        original_scheduled_at=original_time,
        status="pending",
    )


@router.post(
    "/reset-patient/{patient_id}",
    response_model=ResetPatientResponse,
)
async def reset_patient(
    request: Request,
    patient_id: str,
) -> ResetPatientResponse:
    """Reset a patient to PENDING phase for re-running demos.

    Deletes goals, jobs, outbox entries, and resets engagement counters.
    Preserves the patient record and consent history.
    """
    session_factory = request.app.state.session_factory

    try:
        pid = uuid.UUID(patient_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail="Invalid patient_id format") from err

    async with session_factory() as session, session.begin():
        patient = await session.get(Patient, pid)
        if patient is None:
            raise HTTPException(status_code=404, detail="Patient not found")

        # Delete related records
        goals_result = await session.execute(
            delete(PatientGoal).where(PatientGoal.patient_id == pid)
        )
        jobs_result = await session.execute(
            delete(ScheduledJob).where(ScheduledJob.patient_id == pid)
        )
        outbox_result = await session.execute(
            delete(OutboxEntry).where(OutboxEntry.patient_id == pid)
        )

        # Reset patient state
        patient.phase = "pending"
        patient.unanswered_count = 0
        patient.last_outreach_at = None
        patient.last_patient_response_at = None

    # Clear LangGraph checkpoint so next chat starts fresh
    try:
        checkpointer = request.app.state.graph.checkpointer
        if checkpointer is not None and checkpointer is not False:
            await checkpointer.adelete_thread(f"patient-{pid}")  # type: ignore[union-attr]
    except Exception:
        logger.exception("Failed to clear checkpoint for patient %s", pid)

    return ResetPatientResponse(
        patient_id=str(pid),
        phase="pending",
        deleted_goals=goals_result.rowcount,  # type: ignore[union-attr]
        deleted_jobs=jobs_result.rowcount,  # type: ignore[union-attr]
        deleted_outbox=outbox_result.rowcount,  # type: ignore[union-attr]
    )


@router.get(
    "/scheduled-jobs/{patient_id}",
    response_model=ScheduledJobsResponse,
)
async def get_scheduled_jobs(
    request: Request,
    patient_id: str,
) -> ScheduledJobsResponse:
    """List all scheduled jobs for a patient (any status)."""
    session_factory = request.app.state.session_factory

    try:
        pid = uuid.UUID(patient_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail="Invalid patient_id format") from err

    async with session_factory() as session:
        result = await session.execute(
            select(ScheduledJob)
            .where(ScheduledJob.patient_id == pid)
            .order_by(ScheduledJob.created_at.desc())
        )
        jobs = result.scalars().all()

    return ScheduledJobsResponse(
        jobs=[
            ScheduledJobItem(
                id=str(j.id),
                job_type=j.job_type,
                status=j.status,
                scheduled_at=j.scheduled_at.isoformat(),
                attempts=j.attempts,
                max_attempts=j.max_attempts,
                created_at=j.created_at.isoformat(),
            )
            for j in jobs
        ]
    )


@router.get(
    "/audit-events/{patient_id}",
    response_model=AuditEventsResponse,
)
async def get_audit_events(
    request: Request,
    patient_id: str,
) -> AuditEventsResponse:
    """List audit events for a patient, newest first (limit 100)."""
    session_factory = request.app.state.session_factory

    try:
        pid = uuid.UUID(patient_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail="Invalid patient_id format") from err

    async with session_factory() as session:
        result = await session.execute(
            select(AuditEvent)
            .where(AuditEvent.patient_id == pid)
            .order_by(AuditEvent.created_at.desc())
            .limit(100)
        )
        events = result.scalars().all()

    return AuditEventsResponse(
        events=[
            AuditEventItem(
                id=str(e.id),
                event_type=e.event_type,
                outcome=e.outcome,
                created_at=e.created_at.isoformat(),
            )
            for e in events
        ]
    )


@router.get(
    "/conversation/{patient_id}",
    response_model=ConversationHistoryResponse,
)
async def get_conversation_history(
    request: Request,
    patient_id: str,
) -> ConversationHistoryResponse:
    """Return conversation history from the LangGraph checkpoint."""
    try:
        pid = uuid.UUID(patient_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail="Invalid patient_id format") from err

    graph = request.app.state.graph
    thread_id = f"patient-{pid}"
    config = {"configurable": {"thread_id": thread_id}}

    snapshot = await graph.aget_state(config)

    # Handle no checkpoint (patient never chatted)
    if snapshot is None or not snapshot.values:
        return ConversationHistoryResponse(messages=[])

    raw_messages: list[BaseMessage] = snapshot.values.get("messages", [])

    # Build tool_call_id → tool_name lookup from AIMessage.tool_calls
    tool_call_names: dict[str, str] = {}
    for msg in raw_messages:
        tool_calls: list[dict[str, Any]] = getattr(msg, "tool_calls", [])  # type: ignore[reportUnknownMemberType]
        for tc in tool_calls:
            tc_id = tc.get("id")
            tc_name = tc.get("name")
            if isinstance(tc_id, str) and isinstance(tc_name, str):
                tool_call_names[tc_id] = tc_name

    items: list[ConversationMessageItem] = []
    for msg in raw_messages:
        item = _serialize_message(msg, tool_call_names)
        if item is not None:
            items.append(item)

    return ConversationHistoryResponse(messages=items[:100])
