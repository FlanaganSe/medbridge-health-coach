"""Notification channels for patient-facing message delivery."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.stdlib.get_logger()


@dataclass(frozen=True)
class DeliveryResult:
    """Outcome of a delivery attempt."""

    success: bool
    receipt: dict[str, object] = field(default_factory=lambda: {})
    error: str | None = None


class NotificationChannel(abc.ABC):
    """Abstract transport for patient-facing messages."""

    @abc.abstractmethod
    async def send(
        self,
        message: str,
        patient_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DeliveryResult:
        """Deliver a message to a patient. Returns delivery outcome."""


class MockNotificationChannel(NotificationChannel):
    """In-memory notification channel for dev and testing."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(
        self,
        message: str,
        patient_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DeliveryResult:
        entry = {
            "message": message,
            "patient_id": patient_id,
            "metadata": metadata or {},
        }
        self.sent.append(entry)
        await logger.ainfo(
            "mock_notification_sent",
            patient_id=patient_id,
            message_length=len(message),
        )
        return DeliveryResult(success=True, receipt={"channel": "mock"})


class MedBridgePushChannel(NotificationChannel):
    """Push notification via MedBridge Go API.

    Stub implementation — to be completed when API contract is defined.
    """

    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url
        self._api_key = api_key

    async def send(
        self,
        message: str,
        patient_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> DeliveryResult:
        # TODO: Implement when MedBridge Go push API contract is defined
        await logger.awarning(
            "medbridge_push_not_implemented",
            patient_id=patient_id,
        )
        return DeliveryResult(
            success=False,
            error="MedBridge push channel not yet implemented",
        )
