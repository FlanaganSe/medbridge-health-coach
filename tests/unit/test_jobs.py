"""Tests for job handlers — FollowupJobHandler and OnboardingTimeoutHandler."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from health_ally.orchestration.jobs import (
    JobDispatcher,
    OnboardingTimeoutHandler,
    ReminderJobHandler,
)


def _make_job(
    *,
    job_type: str = "day_2_followup",
    patient_id: uuid.UUID | None = None,
    tenant_id: str = "t1",
) -> MagicMock:
    """Create a mock ScheduledJob."""
    job = MagicMock()
    job.id = uuid.uuid4()
    job.patient_id = patient_id or uuid.uuid4()
    job.tenant_id = tenant_id
    job.job_type = job_type
    job.scheduled_at = datetime.now(UTC)
    job.attempts = 0
    job.max_attempts = 3
    job.metadata_ = {}
    return job


async def test_dispatcher_routes_to_followup_handler() -> None:
    """Dispatcher routes follow-up job types to followup handler."""
    followup = AsyncMock()
    timeout = AsyncMock()
    reminder = AsyncMock()
    dispatcher = JobDispatcher(
        followup_handler=followup, timeout_handler=timeout, reminder_handler=reminder
    )

    job = _make_job(job_type="day_2_followup")
    session_factory = MagicMock()
    engine = MagicMock()

    await dispatcher.dispatch(job, session_factory, engine)

    followup.handle.assert_awaited_once_with(job, session_factory, engine)
    timeout.handle.assert_not_awaited()


async def test_dispatcher_routes_to_timeout_handler() -> None:
    """Dispatcher routes onboarding_timeout to timeout handler."""
    followup = AsyncMock()
    timeout = AsyncMock()
    reminder = AsyncMock()
    dispatcher = JobDispatcher(
        followup_handler=followup, timeout_handler=timeout, reminder_handler=reminder
    )

    job = _make_job(job_type="onboarding_timeout")
    session_factory = MagicMock()
    engine = MagicMock()

    await dispatcher.dispatch(job, session_factory, engine)

    timeout.handle.assert_awaited_once_with(job, session_factory, engine)
    followup.handle.assert_not_awaited()


async def test_dispatcher_handles_unknown_job_type() -> None:
    """Unknown job types are logged and skipped."""
    followup = AsyncMock()
    timeout = AsyncMock()
    reminder = AsyncMock()
    dispatcher = JobDispatcher(
        followup_handler=followup, timeout_handler=timeout, reminder_handler=reminder
    )

    job = _make_job(job_type="unknown_type")
    session_factory = MagicMock()
    engine = MagicMock()

    await dispatcher.dispatch(job, session_factory, engine)

    followup.handle.assert_not_awaited()
    timeout.handle.assert_not_awaited()


async def test_onboarding_timeout_skips_non_onboarding() -> None:
    """OnboardingTimeoutHandler skips if patient is no longer in ONBOARDING."""
    handler = OnboardingTimeoutHandler()

    patient_id = uuid.uuid4()
    job = _make_job(job_type="onboarding_timeout", patient_id=patient_id)

    mock_patient = MagicMock()
    mock_patient.phase = "active"  # Already transitioned

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_patient)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock(return_value=AsyncMock())
    mock_session.begin().__aenter__ = AsyncMock(return_value=None)
    mock_session.begin().__aexit__ = AsyncMock(return_value=None)

    session_factory = MagicMock(return_value=mock_session)
    engine = MagicMock()
    engine.url = "sqlite:///test.db"  # Skip advisory lock

    with patch(
        "health_ally.orchestration.jobs.patient_advisory_lock",
        return_value=AsyncMock(),
    ) as mock_lock:
        mock_lock.return_value.__aenter__ = AsyncMock(return_value=None)
        mock_lock.return_value.__aexit__ = AsyncMock(return_value=None)
        await handler.handle(job, session_factory, engine)

    # Patient phase not changed (still "active")
    assert mock_patient.phase == "active"


async def test_dispatcher_routes_reminder_to_handler() -> None:
    """Dispatcher routes 'reminder' job type to reminder handler."""
    followup = AsyncMock()
    timeout = AsyncMock()
    reminder = AsyncMock()
    dispatcher = JobDispatcher(
        followup_handler=followup, timeout_handler=timeout, reminder_handler=reminder
    )

    job = _make_job(job_type="reminder")
    session_factory = MagicMock()
    engine = MagicMock()

    await dispatcher.dispatch(job, session_factory, engine)

    reminder.handle.assert_awaited_once_with(job, session_factory, engine)
    followup.handle.assert_not_awaited()
    timeout.handle.assert_not_awaited()


async def test_reminder_handler_creates_outbox_entry() -> None:
    """ReminderJobHandler creates an OutboxEntry with message from job metadata."""
    handler = ReminderJobHandler()

    patient_id = uuid.uuid4()
    job = _make_job(job_type="reminder", patient_id=patient_id)
    job.metadata_ = {"message": "Time for your morning stretches!"}

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock(return_value=AsyncMock())
    mock_session.begin().__aenter__ = AsyncMock(return_value=None)
    mock_session.begin().__aexit__ = AsyncMock(return_value=None)

    session_factory = MagicMock(return_value=mock_session)
    engine = MagicMock()

    with patch(
        "health_ally.orchestration.jobs.patient_advisory_lock",
        return_value=AsyncMock(),
    ) as mock_lock:
        mock_lock.return_value.__aenter__ = AsyncMock(return_value=None)
        mock_lock.return_value.__aexit__ = AsyncMock(return_value=None)
        await handler.handle(job, session_factory, engine)

    # Verify session.execute was called with an insert statement
    mock_session.execute.assert_awaited_once()
    stmt = mock_session.execute.call_args[0][0]
    # The insert statement should target outbox_entries
    assert "outbox_entries" in str(stmt)
