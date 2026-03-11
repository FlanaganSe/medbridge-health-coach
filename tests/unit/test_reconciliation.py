"""Tests for reconciliation — startup recovery and sweep logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from health_coach.orchestration.reconciliation import startup_recovery


async def test_startup_recovery_resets_processing_jobs() -> None:
    """startup_recovery resets 'processing' jobs to 'pending'."""
    mock_result = MagicMock()
    mock_result.rowcount = 2

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock(return_value=AsyncMock())
    mock_session.begin().__aenter__ = AsyncMock(return_value=None)
    mock_session.begin().__aexit__ = AsyncMock(return_value=None)
    mock_session.execute = AsyncMock(return_value=mock_result)

    session_factory = MagicMock(return_value=mock_session)

    count = await startup_recovery(session_factory)
    assert count == 2


async def test_startup_recovery_no_stuck_jobs() -> None:
    """startup_recovery reports zero when no jobs are stuck."""
    mock_result = MagicMock()
    mock_result.rowcount = 0

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock(return_value=AsyncMock())
    mock_session.begin().__aenter__ = AsyncMock(return_value=None)
    mock_session.begin().__aexit__ = AsyncMock(return_value=None)
    mock_session.execute = AsyncMock(return_value=mock_result)

    session_factory = MagicMock(return_value=mock_session)

    count = await startup_recovery(session_factory)
    assert count == 0
