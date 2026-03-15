"""Job type handlers — dispatches scheduled jobs to appropriate logic.

FollowupJobHandler invokes the graph for follow-up conversations.
OnboardingTimeoutHandler performs a pure lifecycle transition (no graph).
"""

# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
# pyright: reportMissingTypeArgument=false
# pyright: reportUnknownParameterType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportAttributeAccessIssue=false

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import structlog
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from health_ally.domain.phase_machine import transition
from health_ally.domain.phases import PatientPhase
from health_ally.persistence.locking import patient_advisory_lock
from health_ally.persistence.models import (
    AuditEvent,
    ClinicianAlert,
    OutboxEntry,
    ScheduledJob,
)

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

    from health_ally.agent.context import ContextFactory

logger = structlog.stdlib.get_logger()


class JobHandler(Protocol):
    """Protocol for job handlers."""

    async def handle(
        self,
        job: ScheduledJob,
        session_factory: async_sessionmaker[AsyncSession],
        engine: AsyncEngine,
    ) -> None: ...


class JobDispatcher:
    """Routes jobs to the appropriate handler by job_type."""

    def __init__(
        self,
        followup_handler: FollowupJobHandler,
        timeout_handler: OnboardingTimeoutHandler,
        reminder_handler: ReminderJobHandler,
    ) -> None:
        self._handlers: dict[str, JobHandler] = {
            "day_2_followup": followup_handler,
            "day_5_followup": followup_handler,
            "day_7_followup": followup_handler,
            "backoff_followup": followup_handler,
            "onboarding_timeout": timeout_handler,
            "reminder": reminder_handler,
        }

    async def dispatch(
        self,
        job: ScheduledJob,
        session_factory: async_sessionmaker[AsyncSession],
        engine: AsyncEngine,
    ) -> None:
        """Dispatch a job to its handler."""
        handler = self._handlers.get(job.job_type)
        if handler is None:
            logger.warning("unknown_job_type", job_type=job.job_type, job_id=str(job.id))
            return
        await handler.handle(job, session_factory, engine)


class FollowupJobHandler:
    """Invokes the graph for follow-up conversations.

    Acquires patient advisory lock, then invokes the graph with the patient's
    persistent thread. Sets invocation_source="scheduler".
    """

    def __init__(
        self,
        graph: CompiledStateGraph,
        ctx_factory: ContextFactory,
    ) -> None:
        self._graph = graph
        self._ctx_factory = ctx_factory

    async def handle(
        self,
        job: ScheduledJob,
        session_factory: async_sessionmaker[AsyncSession],
        engine: AsyncEngine,
    ) -> None:
        """Process a follow-up job by invoking the graph."""
        patient_id = str(job.patient_id)
        thread_id = f"patient-{patient_id}"

        ctx = self._ctx_factory(session_factory, engine)

        async with patient_advisory_lock(engine, patient_id):
            await self._graph.ainvoke(
                {
                    "patient_id": patient_id,
                    "tenant_id": job.tenant_id,
                    "messages": [],
                    "invocation_source": "scheduler",
                    "_job_metadata": job.metadata_ or {},
                },
                config={
                    "configurable": {
                        "ctx": ctx,
                        "thread_id": thread_id,
                    }
                },
            )

        await logger.ainfo(
            "followup_graph_invoked",
            patient_id=patient_id,
            job_type=job.job_type,
        )


class OnboardingTimeoutHandler:
    """Handles 72h onboarding timeout — pure lifecycle transition, no graph.

    Acquires patient advisory lock, checks phase is still ONBOARDING,
    transitions to DORMANT, creates clinician alert, and writes audit event.
    """

    async def handle(
        self,
        job: ScheduledJob,
        session_factory: async_sessionmaker[AsyncSession],
        engine: AsyncEngine,
    ) -> None:
        """Process an onboarding timeout."""
        from health_ally.persistence.models import Patient

        patient_id = str(job.patient_id)

        async with patient_advisory_lock(engine, patient_id):  # noqa: SIM117
            async with session_factory() as session, session.begin():
                patient = await session.get(Patient, job.patient_id)
                if patient is None:
                    logger.warning("timeout_patient_not_found", patient_id=patient_id)
                    return

                # Idempotency: skip if no longer in ONBOARDING
                if patient.phase != PatientPhase.ONBOARDING.value:
                    await logger.ainfo(
                        "timeout_skipped_phase_changed",
                        patient_id=patient_id,
                        current_phase=patient.phase,
                    )
                    return

                # Transition ONBOARDING → DORMANT
                new_phase = transition(PatientPhase.ONBOARDING, "no_response_timeout")
                patient.phase = new_phase.value

                # Cancel any pending jobs
                await session.execute(
                    update(ScheduledJob)
                    .where(
                        ScheduledJob.patient_id == job.patient_id,
                        ScheduledJob.status == "pending",
                    )
                    .values(status="cancelled")
                )

                # Clinician alert — routine priority (unresponsive, not crisis)
                idempotency_key = f"{patient_id}:onboarding_timeout"
                session.add(
                    ClinicianAlert(
                        tenant_id=job.tenant_id,
                        patient_id=job.patient_id,
                        reason="Patient unresponsive during onboarding — timed out after 72h",
                        priority="routine",
                        idempotency_key=idempotency_key,
                    )
                )
                session.add(
                    OutboxEntry(
                        tenant_id=job.tenant_id,
                        patient_id=job.patient_id,
                        delivery_key=idempotency_key,
                        message_type="clinician_alert",
                        priority=0,
                        channel="default",
                        payload={
                            "reason": "Onboarding timeout — patient unresponsive",
                            "priority": "routine",
                        },
                        status="pending",
                    )
                )

                session.add(
                    AuditEvent(
                        tenant_id=job.tenant_id,
                        patient_id=job.patient_id,
                        event_type="onboarding_timeout",
                        outcome="dormant",
                        metadata_={
                            "from_phase": "onboarding",
                            "event": "no_response_timeout",
                        },
                    )
                )

        await logger.ainfo(
            "onboarding_timeout_processed",
            patient_id=patient_id,
        )


class ReminderJobHandler:
    """Delivers reminder messages via the outbox — no graph invocation needed.

    The LLM composed the message at set_reminder time; this handler simply
    creates an OutboxEntry so the delivery worker can send it.
    """

    async def handle(
        self,
        job: ScheduledJob,
        session_factory: async_sessionmaker[AsyncSession],
        engine: AsyncEngine,
    ) -> None:
        """Create an outbox entry for the reminder message."""
        message = (job.metadata_ or {}).get("message", "Time for your exercises!")
        patient_id = str(job.patient_id)
        delivery_key = f"{patient_id}:reminder:{job.id}"

        async with patient_advisory_lock(engine, patient_id):  # noqa: SIM117
            async with session_factory() as session, session.begin():
                await session.execute(
                    insert(OutboxEntry)
                    .values(
                        tenant_id=job.tenant_id,
                        patient_id=job.patient_id,
                        delivery_key=delivery_key,
                        message_type="patient_message",
                        priority=0,
                        channel="default",
                        payload={"message": message},
                        status="pending",
                    )
                    .on_conflict_do_nothing(index_elements=["delivery_key"])
                )

        await logger.ainfo(
            "reminder_outbox_created",
            patient_id=patient_id,
            job_type=job.job_type,
        )
