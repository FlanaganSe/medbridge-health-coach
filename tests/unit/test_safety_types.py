"""Tests for safety types."""

from health_ally.domain.safety_types import (
    ClassifierOutput,
    CrisisLevel,
    SafetyDecision,
)


def test_safety_decision_values() -> None:
    assert SafetyDecision.SAFE == "safe"
    assert SafetyDecision.CLINICAL_BOUNDARY == "clinical_boundary"
    assert SafetyDecision.CRISIS == "crisis"
    assert SafetyDecision.JAILBREAK == "jailbreak"


def test_crisis_level_values() -> None:
    assert CrisisLevel.NONE == "none"
    assert CrisisLevel.POSSIBLE == "possible"
    assert CrisisLevel.EXPLICIT == "explicit"


def test_classifier_output_valid() -> None:
    output = ClassifierOutput(
        decision=SafetyDecision.SAFE,
        crisis_level=CrisisLevel.NONE,
        confidence=0.95,
        reasoning="No clinical content detected",
    )
    assert output.decision == SafetyDecision.SAFE
    assert output.confidence == 0.95


def test_classifier_output_defaults() -> None:
    output = ClassifierOutput(
        decision=SafetyDecision.SAFE,
        confidence=0.9,
    )
    assert output.crisis_level == CrisisLevel.NONE
    assert output.reasoning == ""


def test_classifier_output_crisis() -> None:
    output = ClassifierOutput(
        decision=SafetyDecision.CRISIS,
        crisis_level=CrisisLevel.EXPLICIT,
        confidence=0.99,
        reasoning="Explicit crisis language detected",
    )
    assert output.decision == SafetyDecision.CRISIS
    assert output.crisis_level == CrisisLevel.EXPLICIT
