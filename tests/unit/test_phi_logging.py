"""Tests for PHI scrubbing in structlog processors."""

from __future__ import annotations

from health_coach.observability.logging import scrub_phi_fields


def test_scrub_known_phi_fields() -> None:
    """Known PHI field names are redacted."""
    event: dict[str, object] = {
        "event": "test",
        "patient_name": "John Doe",
        "email": "john@example.com",
        "phone": "555-1234",
        "patient_id": "abc-123",  # NOT PHI — this is an opaque ID
    }
    result = scrub_phi_fields(None, "", event)
    assert result["patient_name"] == "[REDACTED]"
    assert result["email"] == "[REDACTED]"
    assert result["phone"] == "[REDACTED]"
    assert result["patient_id"] == "abc-123"  # Preserved


def test_scrub_message_content() -> None:
    """Message content field is redacted."""
    event: dict[str, object] = {
        "event": "test",
        "message_content": "Patient said something private",
    }
    result = scrub_phi_fields(None, "", event)
    assert result["message_content"] == "[REDACTED]"


def test_scrub_ssn_pattern() -> None:
    """SSN patterns in values are redacted."""
    event: dict[str, object] = {
        "event": "test",
        "some_field": "SSN is 123-45-6789",
    }
    result = scrub_phi_fields(None, "", event)
    assert result["some_field"] == "[REDACTED]"


def test_scrub_email_pattern_in_value() -> None:
    """Email patterns in arbitrary values are redacted."""
    event: dict[str, object] = {
        "event": "test",
        "info": "contact patient@example.com for details",
    }
    result = scrub_phi_fields(None, "", event)
    assert result["info"] == "[REDACTED]"


def test_safe_fields_preserved() -> None:
    """Non-PHI fields pass through unchanged."""
    event: dict[str, object] = {
        "event": "safety_gate_result",
        "patient_id": "uuid-value",
        "decision": "safe",
        "confidence": 0.95,
        "status_code": 200,
    }
    result = scrub_phi_fields(None, "", event)
    assert result == event


def test_scrub_body_fields() -> None:
    """Request/response body fields are redacted."""
    event: dict[str, object] = {
        "event": "test",
        "body": '{"message": "hello"}',
        "request_body": "raw data",
        "response_body": "raw response",
    }
    result = scrub_phi_fields(None, "", event)
    assert result["body"] == "[REDACTED]"
    assert result["request_body"] == "[REDACTED]"
    assert result["response_body"] == "[REDACTED]"


def test_scrub_medical_fields() -> None:
    """Medical-related fields are redacted."""
    event: dict[str, object] = {
        "event": "test",
        "diagnosis": "ACL tear",
        "medication": "ibuprofen",
        "treatment": "physical therapy",
        "symptoms": "knee pain",
    }
    result = scrub_phi_fields(None, "", event)
    assert result["diagnosis"] == "[REDACTED]"
    assert result["medication"] == "[REDACTED]"
    assert result["treatment"] == "[REDACTED]"
    assert result["symptoms"] == "[REDACTED]"


def test_non_string_values_not_pattern_matched() -> None:
    """Non-string values are not checked for patterns."""
    event: dict[str, object] = {
        "event": "test",
        "count": 42,
        "active": True,
    }
    result = scrub_phi_fields(None, "", event)
    assert result == event


def test_scrub_nested_dict_phi_fields() -> None:
    """PHI fields inside nested dicts are redacted."""
    event: dict[str, object] = {
        "event": "test",
        "metadata": {
            "patient_name": "Jane Doe",
            "safe_field": "ok",
            "email": "jane@example.com",
        },
    }
    result = scrub_phi_fields(None, "", event)
    metadata = result["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["patient_name"] == "[REDACTED]"
    assert metadata["email"] == "[REDACTED]"
    assert metadata["safe_field"] == "ok"


def test_scrub_deeply_nested_dict() -> None:
    """PHI scrubbing works on deeply nested dicts."""
    event: dict[str, object] = {
        "event": "test",
        "outer": {
            "inner": {
                "diagnosis": "torn ACL",
                "count": 5,
            },
        },
    }
    result = scrub_phi_fields(None, "", event)
    outer = result["outer"]
    assert isinstance(outer, dict)
    inner = outer["inner"]
    assert isinstance(inner, dict)
    assert inner["diagnosis"] == "[REDACTED]"
    assert inner["count"] == 5


def test_scrub_nested_email_pattern() -> None:
    """Email patterns in nested dict values are redacted."""
    event: dict[str, object] = {
        "event": "test",
        "details": {"contact": "reach out to user@hospital.org"},
    }
    result = scrub_phi_fields(None, "", event)
    details = result["details"]
    assert isinstance(details, dict)
    assert details["contact"] == "[REDACTED]"
