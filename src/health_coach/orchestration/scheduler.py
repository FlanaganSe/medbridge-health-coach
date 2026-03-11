"""Scheduler worker — polls for due jobs and dispatches to handlers.

Uses SELECT ... FOR UPDATE SKIP LOCKED to safely claim jobs across
multiple worker instances without duplicate processing.
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select, update

from health_coach.persistence.models import ScheduledJob

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

    from health_coach.orchestration.jobs import JobDispatcher

logger = structlog.stdlib.get_logger()

_DEFAULT_BATCH_SIZE = 10
_JITTER_FRACTION = 0.2  # ±20% on poll interval


class SchedulerWorker:
    """Background worker that polls for and processes due scheduled jobs.

    Jobs are claimed using SELECT ... FOR UPDATE SKIP LOCKED for safe
    concurrent access across multiple worker instances.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        engine: AsyncEngine,
        dispatcher: JobDispatcher,
        poll_interval_seconds: int = 30,
        batch_size: int = _DEFAULT_BATCH_SIZE,
    ) -> None:
        self._session_factory = session_factory
        self._engine = engine
        self._dispatcher = dispatcher
        self._poll_interval = poll_interval_seconds
        self._batch_size = batch_size
        self._shutdown_event = asyncio.Event()

    @property
    def shutdown_event(self) -> asyncio.Event:
        """Event to signal graceful shutdown."""
        return self._shutdown_event

    async def run(self) -> None:
        """Main poll loop — runs until shutdown_event is set."""
        await logger.ainfo("scheduler_started", poll_interval=self._poll_interval)

        while not self._shutdown_event.is_set():
            try:
                processed = await self._poll_and_process()
                if processed > 0:
                    await logger.ainfo("scheduler_batch_processed", count=processed)
            except Exception:
                logger.exception("scheduler_poll_error")

            # Jittered sleep
            jitter = self._poll_interval * random.uniform(  # noqa: S311
                1 - _JITTER_FRACTION, 1 + _JITTER_FRACTION
            )
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=jitter)
                break  # Shutdown signalled
            except TimeoutError:
                continue

        await logger.ainfo("scheduler_stopped")

    async def _poll_and_process(self) -> int:
        """Claim due jobs and dispatch them, grouped by patient."""
        now = datetime.now(UTC)

        async with self._session_factory() as session, session.begin():
            stmt = (
                select(ScheduledJob)
                .where(
                    ScheduledJob.status == "pending",
                    ScheduledJob.scheduled_at <= now,
                )
                .order_by(ScheduledJob.scheduled_at)
                .limit(self._batch_size)
                .with_for_update(skip_locked=True)
            )
            result = await session.execute(stmt)
            jobs = list(result.scalars().all())

            if not jobs:
                return 0

            # Mark all as processing
            job_ids = [j.id for j in jobs]
            await session.execute(
                update(ScheduledJob)
                .where(ScheduledJob.id.in_(job_ids))
                .values(status="processing")
            )

        # Group by patient for sequential processing per patient
        patient_groups: dict[str, list[ScheduledJob]] = {}
        for job in jobs:
            pid = str(job.patient_id)
            patient_groups.setdefault(pid, []).append(job)

        # Process different patients concurrently, same patient sequentially
        tasks = [
            self._process_patient_jobs(patient_id, patient_jobs)
            for patient_id, patient_jobs in patient_groups.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result_item in enumerate(results):
            if isinstance(result_item, Exception):
                patient_id = list(patient_groups.keys())[i]
                logger.exception(
                    "scheduler_patient_group_error",
                    patient_id=patient_id,
                    error=str(result_item),
                )

        return len(jobs)

    async def _process_patient_jobs(
        self,
        patient_id: str,
        jobs: list[ScheduledJob],
    ) -> None:
        """Process jobs for a single patient sequentially."""
        for job in jobs:
            await self._process_single_job(job, patient_id)

    async def _process_single_job(
        self,
        job: ScheduledJob,
        patient_id: str,
    ) -> None:
        """Process a single job with error handling and status updates."""
        try:
            await self._dispatcher.dispatch(
                job=job,
                session_factory=self._session_factory,
                engine=self._engine,
            )
            await self._mark_job(job.id, "completed")
            await logger.ainfo(
                "job_completed",
                job_id=str(job.id),
                job_type=job.job_type,
                patient_id=patient_id,
            )
        except Exception:
            logger.exception(
                "job_failed",
                job_id=str(job.id),
                job_type=job.job_type,
                patient_id=patient_id,
            )
            await self._handle_job_failure(job)

    async def _mark_job(self, job_id: object, status: str) -> None:
        """Update job status."""
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(ScheduledJob).where(ScheduledJob.id == job_id).values(status=status)
            )

    async def _handle_job_failure(self, job: ScheduledJob) -> None:
        """Increment attempts and mark as failed or dead."""
        new_attempts = job.attempts + 1
        new_status = "dead" if new_attempts >= job.max_attempts else "failed"

        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(ScheduledJob)
                .where(ScheduledJob.id == job.id)
                .values(
                    status=new_status,
                    attempts=new_attempts,
                )
            )

        if new_status == "dead":
            await logger.awarning(
                "job_dead",
                job_id=str(job.id),
                job_type=job.job_type,
                attempts=new_attempts,
            )
