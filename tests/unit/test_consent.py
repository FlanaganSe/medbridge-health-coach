"""Tests for consent service contract."""

import pytest

from health_coach.domain.consent import (
    ConsentResult,
    FailSafeConsentService,
    FakeConsentService,
)


async def test_fake_consent_service_allowed() -> None:
    service = FakeConsentService(logged_in=True, consented=True)
    result = await service.check("patient-1", "tenant-1")
    assert result.allowed
    assert result.logged_in
    assert result.consented_to_outreach


async def test_fake_consent_service_denied_not_logged_in() -> None:
    service = FakeConsentService(logged_in=False, consented=True)
    result = await service.check("patient-1", "tenant-1")
    assert not result.allowed
    assert not result.logged_in


async def test_fake_consent_service_denied_not_consented() -> None:
    service = FakeConsentService(logged_in=True, consented=False)
    result = await service.check("patient-1", "tenant-1")
    assert not result.allowed
    assert not result.consented_to_outreach


async def test_consent_result_requires_both() -> None:
    """Both logged_in AND consented must be true."""
    result = ConsentResult(
        logged_in=True,
        consented_to_outreach=False,
        reason="test",
        checked_at=pytest.importorskip("datetime").datetime.now(
            tz=pytest.importorskip("datetime").UTC
        ),
    )
    assert not result.allowed


async def test_fail_safe_returns_denied_on_error() -> None:
    """FailSafeConsentService returns denied on any exception."""

    class ExplodingService(FakeConsentService):
        async def check(self, patient_id: str, tenant_id: str) -> ConsentResult:
            msg = "boom"
            raise RuntimeError(msg)

    inner = ExplodingService()
    safe = FailSafeConsentService(inner)
    result = await safe.check("patient-1", "tenant-1")
    assert not result.allowed
    assert result.reason == "consent_check_error"


async def test_fail_safe_passes_through_on_success() -> None:
    inner = FakeConsentService(logged_in=True, consented=True)
    safe = FailSafeConsentService(inner)
    result = await safe.check("patient-1", "tenant-1")
    assert result.allowed
