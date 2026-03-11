"""Alert channels for clinician-facing alert delivery."""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Any

import structlog

from health_ally.integrations.notification import DeliveryResult

if TYPE_CHECKING:
    from health_ally.persistence.models import ClinicianAlert

logger = structlog.stdlib.get_logger()


class AlertChannel(abc.ABC):
    """Abstract transport for clinician alerts."""

    @abc.abstractmethod
    async def send_alert(self, alert: ClinicianAlert) -> DeliveryResult:
        """Deliver a clinician alert. Returns delivery outcome."""


class MockAlertChannel(AlertChannel):
    """In-memory alert channel for dev and testing."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_alert(self, alert: ClinicianAlert) -> DeliveryResult:
        entry = {
            "alert_id": str(alert.id),
            "patient_id": str(alert.patient_id),
            "reason": alert.reason,
            "priority": alert.priority,
        }
        self.sent.append(entry)
        await logger.ainfo(
            "mock_alert_sent",
            patient_id=str(alert.patient_id),
            priority=alert.priority,
        )
        return DeliveryResult(success=True, receipt={"channel": "mock_alert"})


class WebhookAlertChannel(AlertChannel):
    """Delivers clinician alerts via HTTP webhook POST."""

    def __init__(self, webhook_url: str, *, timeout: float = 10.0) -> None:
        import httpx

        self._client = httpx.AsyncClient(timeout=timeout)
        self._webhook_url = webhook_url

    async def send_alert(self, alert: ClinicianAlert) -> DeliveryResult:
        import httpx

        payload = {
            "alert_id": str(alert.id),
            "tenant_id": alert.tenant_id,
            "patient_id": str(alert.patient_id),
            "reason": alert.reason,
            "priority": alert.priority,
        }
        try:
            response = await self._client.post(self._webhook_url, json=payload)
            response.raise_for_status()
            return DeliveryResult(
                success=True,
                receipt={"status_code": response.status_code},
            )
        except httpx.HTTPError as exc:
            await logger.awarning(
                "webhook_alert_failed",
                patient_id=str(alert.patient_id),
                error=str(exc),
            )
            return DeliveryResult(success=False, error=str(exc))

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
