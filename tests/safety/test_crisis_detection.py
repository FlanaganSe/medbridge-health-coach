"""Safety tests — crisis detection routing and alert creation."""

from __future__ import annotations

from health_ally.domain.safety_types import (
    ClassifierOutput,
    CrisisLevel,
    SafetyDecision,
)


def test_explicit_crisis_classification() -> None:
    """EXPLICIT crisis maps to SafetyDecision.CRISIS."""
    output = ClassifierOutput(
        decision=SafetyDecision.CRISIS,
        crisis_level=CrisisLevel.EXPLICIT,
        confidence=0.95,
        reasoning="Self-harm ideation detected",
    )
    assert output.crisis_level == CrisisLevel.EXPLICIT
    assert output.decision == SafetyDecision.CRISIS


def test_possible_crisis_can_be_safe() -> None:
    """POSSIBLE crisis can have SAFE decision (routine alert, continue flow)."""
    output = ClassifierOutput(
        decision=SafetyDecision.SAFE,
        crisis_level=CrisisLevel.POSSIBLE,
        confidence=0.6,
        reasoning="Vague distress, not immediately dangerous",
    )
    assert output.crisis_level == CrisisLevel.POSSIBLE
    assert output.decision == SafetyDecision.SAFE


def test_confidence_validation() -> None:
    """Confidence must be between 0.0 and 1.0."""
    import pytest

    with pytest.raises(ValueError):
        ClassifierOutput(
            decision=SafetyDecision.SAFE,
            confidence=1.5,
            reasoning="invalid",
        )

    with pytest.raises(ValueError):
        ClassifierOutput(
            decision=SafetyDecision.SAFE,
            confidence=-0.1,
            reasoning="invalid",
        )
