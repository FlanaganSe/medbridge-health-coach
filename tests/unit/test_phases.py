"""Tests for PatientPhase enum."""

from health_ally.domain.phases import PatientPhase


def test_all_phases_exist() -> None:
    phases = list(PatientPhase)
    assert len(phases) == 5
    assert PatientPhase.PENDING in phases
    assert PatientPhase.ONBOARDING in phases
    assert PatientPhase.ACTIVE in phases
    assert PatientPhase.RE_ENGAGING in phases
    assert PatientPhase.DORMANT in phases


def test_phase_string_values() -> None:
    assert str(PatientPhase.PENDING) == "pending"
    assert str(PatientPhase.ONBOARDING) == "onboarding"
    assert str(PatientPhase.ACTIVE) == "active"
    assert str(PatientPhase.RE_ENGAGING) == "re_engaging"
    assert str(PatientPhase.DORMANT) == "dormant"


def test_phase_from_string() -> None:
    assert PatientPhase("pending") == PatientPhase.PENDING
    assert PatientPhase("onboarding") == PatientPhase.ONBOARDING
