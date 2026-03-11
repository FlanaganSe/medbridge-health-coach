"""Channel factory — settings-driven notification and alert channel creation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from health_ally.integrations.alert_channel import AlertChannel, MockAlertChannel
from health_ally.integrations.notification import (
    MockNotificationChannel,
    NotificationChannel,
)

if TYPE_CHECKING:
    from health_ally.settings import Settings


def create_notification_channel(settings: Settings) -> NotificationChannel:
    """Create a notification channel based on settings.

    For demo/dev, "mock" logs delivery without external transport.
    """
    _ = settings  # Reserved for future channel_type setting
    return MockNotificationChannel()


def create_alert_channel(settings: Settings) -> AlertChannel:
    """Create an alert channel based on settings.

    For demo/dev, "mock" logs alerts without external transport.
    """
    _ = settings  # Reserved for future channel_type setting
    return MockAlertChannel()
