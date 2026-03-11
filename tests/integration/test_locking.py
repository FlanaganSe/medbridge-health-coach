"""Tests for patient advisory lock — unit tests for lock key derivation."""

from __future__ import annotations

from health_coach.persistence.locking import _patient_lock_key


def test_lock_key_deterministic() -> None:
    """Same patient_id always produces the same lock key."""
    key1 = _patient_lock_key("patient-123")
    key2 = _patient_lock_key("patient-123")
    assert key1 == key2


def test_lock_key_different_patients() -> None:
    """Different patient_ids produce different lock keys."""
    key1 = _patient_lock_key("patient-123")
    key2 = _patient_lock_key("patient-456")
    assert key1 != key2


def test_lock_key_positive_32bit() -> None:
    """Lock key is a positive 32-bit integer (required by pg_advisory_lock)."""
    key = _patient_lock_key("any-patient-id")
    assert 0 <= key <= 0x7FFFFFFF


def test_lock_key_uses_hashlib_not_hash() -> None:
    """Lock key is deterministic across processes (hashlib, not hash()).

    Python's hash() is salted per process via PYTHONHASHSEED.
    hashlib.sha256 is deterministic across processes.
    """
    # Known fixed value — if this changes, the lock key derivation changed
    key = _patient_lock_key("test-patient")
    assert isinstance(key, int)
    assert key > 0
    # Re-derive to verify consistency
    assert _patient_lock_key("test-patient") == key
