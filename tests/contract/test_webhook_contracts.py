"""Contract tests for the MedBridge Go webhook interface."""

from __future__ import annotations

from health_ally.integrations.medbridge import verify_webhook_signature


def test_webhook_signature_valid() -> None:
    """Valid HMAC signature passes verification."""
    import hashlib
    import hmac

    secret = "test-secret"  # noqa: S105
    payload = b'{"event_type": "test"}'
    sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

    assert verify_webhook_signature(payload, sig, secret) is True


def test_webhook_signature_invalid() -> None:
    """Invalid signature fails verification."""
    assert verify_webhook_signature(b"payload", "bad-sig", "secret") is False


def test_webhook_signature_tampered_payload() -> None:
    """Tampered payload fails verification."""
    import hashlib
    import hmac

    secret = "test-secret"  # noqa: S105
    original = b'{"event_type": "test"}'
    sig = hmac.new(secret.encode(), original, hashlib.sha256).hexdigest()

    tampered = b'{"event_type": "malicious"}'
    assert verify_webhook_signature(tampered, sig, secret) is False
