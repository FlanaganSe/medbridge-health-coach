"""MedBridge Go API client for consent verification and patient events.

Provides the real ConsentService implementation backed by MedBridge Go API,
plus HMAC signature verification for inbound webhooks.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import structlog

from health_ally.domain.consent import ConsentResult, ConsentService

if TYPE_CHECKING:
    from health_ally.settings import Settings

logger = structlog.stdlib.get_logger()


class MedBridgeClient(ConsentService):
    """HTTP client for MedBridge Go API.

    Checks patient consent and login status via the MedBridge Go backend.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    async def check(self, patient_id: str, tenant_id: str) -> ConsentResult:
        """Verify patient login and outreach consent via MedBridge Go API."""
        try:
            response = await self._client.get(
                f"/api/v1/patients/{patient_id}/consent",
                params={"tenant_id": tenant_id},
            )
            response.raise_for_status()
            data = response.json()

            return ConsentResult(
                logged_in=bool(data.get("logged_in", False)),
                consented_to_outreach=bool(data.get("consented_to_outreach", False)),
                reason=str(data.get("reason", "api_check")),
                checked_at=datetime.now(UTC),
            )
        except Exception as exc:
            await logger.awarning(
                "medbridge_consent_check_failed",
                patient_id=patient_id,
                error=str(exc),
            )
            return ConsentResult(
                logged_in=False,
                consented_to_outreach=False,
                reason=f"consent_check_error: {exc}",
                checked_at=datetime.now(UTC),
            )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    @classmethod
    def from_settings(cls, settings: Settings) -> MedBridgeClient:
        """Create a MedBridgeClient from application settings."""
        return cls(
            base_url=settings.medbridge_api_url,
            api_key=settings.medbridge_api_key.get_secret_value(),
        )


def verify_webhook_signature(
    payload: bytes,
    signature: str,
    secret: str,
) -> bool:
    """Verify HMAC-SHA256 signature on an inbound webhook payload."""
    expected = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


class FakeMedBridgeClient(ConsentService):
    """Fake MedBridge client for testing and local development."""

    def __init__(
        self,
        *,
        logged_in: bool = True,
        consented: bool = True,
    ) -> None:
        self._logged_in = logged_in
        self._consented = consented

    async def check(self, patient_id: str, tenant_id: str) -> ConsentResult:
        return ConsentResult(
            logged_in=self._logged_in,
            consented_to_outreach=self._consented,
            reason="fake_medbridge",
            checked_at=datetime.now(UTC),
        )
