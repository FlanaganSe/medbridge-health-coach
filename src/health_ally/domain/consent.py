"""Consent verification service contract.

PRD §5.5: Every interaction must verify login + outreach consent.
Consent is checked per interaction, not per thread.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class ConsentResult:
    """Result of a consent verification check."""

    logged_in: bool
    consented_to_outreach: bool
    reason: str
    checked_at: datetime

    @property
    def allowed(self) -> bool:
        """Both conditions must be true for consent to be valid."""
        return self.logged_in and self.consented_to_outreach


class ConsentService(abc.ABC):
    """Abstract consent verification service.

    Implementations check MedBridge Go for login and outreach consent.
    """

    @abc.abstractmethod
    async def check(self, patient_id: str, tenant_id: str) -> ConsentResult:
        """Verify patient login and outreach consent.

        Must fail safe: any exception → denied result.
        """


class FakeConsentService(ConsentService):
    """Fake consent service for testing."""

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
            reason="fake" if self.allowed else "fake_denied",
            checked_at=datetime.now(UTC),
        )

    @property
    def allowed(self) -> bool:
        return self._logged_in and self._consented


class FailSafeConsentService(ConsentService):
    """Wraps a real consent service with fail-safe error handling."""

    def __init__(self, inner: ConsentService) -> None:
        self._inner = inner

    async def check(self, patient_id: str, tenant_id: str) -> ConsentResult:
        try:
            return await self._inner.check(patient_id, tenant_id)
        except Exception:
            return ConsentResult(
                logged_in=False,
                consented_to_outreach=False,
                reason="consent_check_error",
                checked_at=datetime.now(UTC),
            )
