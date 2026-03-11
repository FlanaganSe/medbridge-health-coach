"""Startup reconciliation and periodic sweep for job health.

- On startup: resets jobs stuck in 'processing' (crashed worker recovery)
- Periodic sweep: finds patients missing expected scheduled jobs
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select, update

from health_coach.domain.phases import PatientPhase
from health_coach.domain.scheduling import add_jitter, calculate_send_time
from health_coach.persistence.models import Patient, ScheduledJob

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from health_coach.domain.scheduling import CoachConfig

logger = structlog.stdlib.get_logger()


async def startup_recovery(
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Reset jobs stuck in 'processing' back to 'pending'.

    Called once at worker startup to recover from previous crashes.
    Returns the number of jobs reset.
    """
    async with session_factory() as session, session.begin():
        result = await session.execute(
            update(ScheduledJob)
            .where(ScheduledJob.status == "processing")
            .values(status="pending")
        )
        count = result.rowcount  # type: ignore[assignment]

    if count > 0:
        await logger.awarning("reconciliation_reset_processing", count=count)
    else:
        await logger.ainfo("reconciliation_no_stuck_jobs")

    return count  # type: ignore[return-value]


async def sweep_missing_jobs(
    session_factory: async_sessionmaker[AsyncSession],
    coach_config: CoachConfig,
) -> int:
    """Find patients missing expected scheduled jobs and create them.

    - ACTIVE patients with no pending follow-up → create next follow-up
    - ONBOARDING patients with no pending timeout → create timeout job

    Returns the number of jobs created. Uses ON CONFLICT DO NOTHING for
    idempotency (stable keys prevent duplicates).
    """
    created = 0
    now = datetime.now(UTC)

    async with session_factory() as session, session.begin():
        # Find ACTIVE patients with no pending scheduled jobs
        active_patients = await _patients_without_pending_jobs(session, PatientPhase.ACTIVE.value)
        for patient in active_patients:
            pid = str(patient.id)
            send_time = calculate_send_time(
                now + timedelta(days=coach_config.follow_up_days[0]),
                patient.timezone,
                coach_config.quiet_hours_start,
                coach_config.quiet_hours_end,
            )
            send_time = add_jitter(send_time, coach_config.max_jitter_minutes)
            idempotency_key = f"{pid}:reconciliation_followup:{now.date().isoformat()}"

            session.add(
                ScheduledJob(
                    tenant_id=patient.tenant_id,
                    patient_id=patient.id,
                    job_type="day_2_followup",
                    idempotency_key=idempotency_key,
                    scheduled_at=send_time,
                    metadata_={"source": "reconciliation", "follow_up_day": 2},
                )
            )
            created += 1

        # Find ONBOARDING patients with no pending timeout job
        onboarding_patients = await _patients_without_pending_jobs(
            session, PatientPhase.ONBOARDING.value
        )
        for patient in onboarding_patients:
            pid = str(patient.id)
            timeout_at = patient.created_at + timedelta(
                hours=coach_config.onboarding_timeout_hours
            )
            # Only create if timeout hasn't already passed (reconciliation
            # will catch it next sweep if it has)
            idempotency_key = f"{pid}:onboarding_timeout"
            session.add(
                ScheduledJob(
                    tenant_id=patient.tenant_id,
                    patient_id=patient.id,
                    job_type="onboarding_timeout",
                    idempotency_key=idempotency_key,
                    scheduled_at=timeout_at,
                    metadata_={"source": "reconciliation"},
                )
            )
            created += 1

    if created > 0:
        await logger.ainfo("reconciliation_jobs_created", count=created)

    return created


async def _patients_without_pending_jobs(
    session: AsyncSession,
    phase: str,
) -> list[Patient]:
    """Find patients in a given phase with no pending scheduled jobs."""
    # Subquery: patients who DO have pending jobs
    has_pending = (
        select(ScheduledJob.patient_id)
        .where(ScheduledJob.status == "pending")
        .distinct()
        .subquery()
    )

    stmt = select(Patient).where(
        Patient.phase == phase,
        Patient.id.notin_(select(has_pending.c.patient_id)),
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
