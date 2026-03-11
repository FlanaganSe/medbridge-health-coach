"""Tests for the scheduler worker — poll loop and job processing."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from health_ally.orchestration.scheduler import SchedulerWorker


async def test_scheduler_stops_on_shutdown() -> None:
    """Scheduler exits cleanly when shutdown_event is set."""
    session_factory = MagicMock()
    engine = MagicMock()
    dispatcher = MagicMock()

    worker = SchedulerWorker(
        session_factory=session_factory,
        engine=engine,
        dispatcher=dispatcher,
        poll_interval_seconds=1,
    )

    # Set shutdown immediately
    worker.shutdown_event.set()

    # Should return quickly without errors
    await asyncio.wait_for(worker.run(), timeout=5.0)


async def test_scheduler_processes_batch() -> None:
    """Scheduler claims and processes due jobs."""
    session_factory = MagicMock()
    engine = MagicMock()
    dispatcher = AsyncMock()

    worker = SchedulerWorker(
        session_factory=session_factory,
        engine=engine,
        dispatcher=dispatcher,
        poll_interval_seconds=1,
    )

    # Patch _poll_and_process to return 1 on first call, then set shutdown
    call_count = 0

    async def _mock_poll() -> int:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            worker.shutdown_event.set()
        return 1 if call_count == 1 else 0

    with patch.object(worker, "_poll_and_process", side_effect=_mock_poll):
        await asyncio.wait_for(worker.run(), timeout=5.0)

    assert call_count >= 2
