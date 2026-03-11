"""Factory for creating the appropriate ConsentService implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from health_ally.domain.consent import ConsentService
    from health_ally.settings import Settings


def create_consent_service(settings: Settings) -> ConsentService:
    """Create the appropriate ConsentService based on environment.

    - Dev/SQLite: FakeConsentService (always allows)
    - Staging/Prod with MedBridge URL: MedBridgeClient wrapped in FailSafe
    - Staging/Prod without MedBridge URL: FakeConsentService (with warning)
    """
    from health_ally.domain.consent import FailSafeConsentService, FakeConsentService

    if settings.environment == "dev" or settings.is_sqlite:
        return FakeConsentService(logged_in=True, consented=True)

    if settings.medbridge_api_url:
        from health_ally.integrations.medbridge import MedBridgeClient

        real_client = MedBridgeClient.from_settings(settings)
        return FailSafeConsentService(real_client)

    # Non-dev without MedBridge URL configured — use fake with warning
    import structlog

    structlog.stdlib.get_logger().warning(
        "consent_service_using_fake",
        environment=settings.environment,
        reason="medbridge_api_url not configured",
    )
    return FakeConsentService(logged_in=True, consented=True)
