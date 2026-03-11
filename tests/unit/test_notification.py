"""Unit tests for notification and alert channels."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from health_ally.integrations.alert_channel import MockAlertChannel
from health_ally.integrations.notification import (
    DeliveryResult,
    MockNotificationChannel,
)


@pytest.mark.asyncio
async def test_mock_notification_sends_message() -> None:
    """MockNotificationChannel records sent messages."""
    channel = MockNotificationChannel()
    result = await channel.send("Hello!", patient_id="p1")

    assert result.success is True
    assert len(channel.sent) == 1
    assert channel.sent[0]["message"] == "Hello!"
    assert channel.sent[0]["patient_id"] == "p1"


@pytest.mark.asyncio
async def test_mock_notification_with_metadata() -> None:
    """MockNotificationChannel preserves metadata."""
    channel = MockNotificationChannel()
    await channel.send("Test", patient_id="p2", metadata={"key": "val"})

    assert channel.sent[0]["metadata"] == {"key": "val"}


@pytest.mark.asyncio
async def test_mock_alert_channel_sends_alert() -> None:
    """MockAlertChannel records sent alerts."""
    channel = MockAlertChannel()
    alert = MagicMock()
    alert.id = uuid.uuid4()
    alert.patient_id = uuid.uuid4()
    alert.reason = "Test alert"
    alert.priority = "urgent"

    result = await channel.send_alert(alert)

    assert result.success is True
    assert len(channel.sent) == 1
    assert channel.sent[0]["reason"] == "Test alert"
    assert channel.sent[0]["priority"] == "urgent"


def test_delivery_result_fields() -> None:
    """DeliveryResult has correct defaults."""
    result = DeliveryResult(success=True)
    assert result.receipt == {}
    assert result.error is None

    failed = DeliveryResult(success=False, error="timeout")
    assert failed.error == "timeout"
