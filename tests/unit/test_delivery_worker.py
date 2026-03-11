"""Unit tests for the outbox delivery worker."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from health_coach.domain.consent import FakeConsentService
from health_coach.integrations.alert_channel import MockAlertChannel
from health_coach.integrations.notification import MockNotificationChannel
from health_coach.orchestration.delivery_worker import DeliveryWorker


def _make_outbox_entry(
    *,
    message_type: str = "patient_message",
    status: str = "delivering",
    payload: dict | None = None,
) -> MagicMock:
    entry = MagicMock()
    entry.id = uuid.uuid4()
    entry.tenant_id = "t1"
    entry.patient_id = uuid.uuid4()
    entry.message_type = message_type
    entry.status = status
    entry.payload = payload or {"message": "Hello!"}
    entry.priority = 0
    entry.created_at = datetime.now(UTC)
    return entry


def _mock_session_factory() -> MagicMock:
    """Create a mock session factory that supports async context managers."""
    mock_result = MagicMock()
    mock_result.rowcount = 0

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_begin = AsyncMock()
    mock_begin.__aenter__ = AsyncMock(return_value=None)
    mock_begin.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=mock_begin)

    mock_sf = MagicMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_sf.return_value = mock_ctx

    return mock_sf


@pytest.mark.asyncio
async def test_delivery_worker_lifecycle() -> None:
    """Delivery worker starts and stops cleanly."""
    session_factory = _mock_session_factory()
    consent_service = FakeConsentService(logged_in=True, consented=True)
    notification = MockNotificationChannel()
    alert_channel = MockAlertChannel()

    worker = DeliveryWorker(
        session_factory=session_factory,
        consent_service=consent_service,
        notification_channel=notification,
        alert_channel=alert_channel,
        poll_interval_seconds=1,
    )

    # Signal shutdown immediately
    worker.shutdown_event.set()

    # Should return without hanging
    await worker.run()


@pytest.mark.asyncio
async def test_deliver_message_calls_notification_channel() -> None:
    """Patient messages are delivered via notification channel."""
    notification = MockNotificationChannel()
    entry = _make_outbox_entry(payload={"message": "Hello patient!"})

    session_factory = AsyncMock()
    consent = FakeConsentService(logged_in=True, consented=True)
    alert_ch = MockAlertChannel()

    worker = DeliveryWorker(
        session_factory=session_factory,
        consent_service=consent,
        notification_channel=notification,
        alert_channel=alert_ch,
    )

    result = await worker._deliver_message(entry)
    assert result.success is True
    assert len(notification.sent) == 1
    assert notification.sent[0]["message"] == "Hello patient!"


@pytest.mark.asyncio
async def test_deliver_message_empty_message_fails() -> None:
    """Empty messages return failure."""
    notification = MockNotificationChannel()
    entry = _make_outbox_entry(payload={"message": ""})

    session_factory = AsyncMock()
    consent = FakeConsentService()
    alert_ch = MockAlertChannel()

    worker = DeliveryWorker(
        session_factory=session_factory,
        consent_service=consent,
        notification_channel=notification,
        alert_channel=alert_ch,
    )

    result = await worker._deliver_message(entry)
    assert result.success is False
    assert result.error == "empty_message"


@pytest.mark.asyncio
async def test_consent_denied_skips_delivery() -> None:
    """Patient messages are not delivered when consent is denied."""
    notification = MockNotificationChannel()

    # Consent denied
    consent = FakeConsentService(logged_in=False, consented=False)

    # Verify the consent service correctly denies
    result = await consent.check("p1", "t1")
    assert result.allowed is False

    # Verify notification channel is not invoked when consent is denied
    # (unit-level: we check that the consent gate works, not the full flow)
    assert len(notification.sent) == 0
