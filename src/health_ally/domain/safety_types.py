"""Safety decision types for the clinical boundary and crisis pipeline."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class SafetyDecision(StrEnum):
    """Outcome of the safety classifier for an outbound message."""

    SAFE = "safe"
    CLINICAL_BOUNDARY = "clinical_boundary"
    CRISIS = "crisis"
    JAILBREAK = "jailbreak"


class CrisisLevel(StrEnum):
    """Crisis severity level from input-side pre-check."""

    NONE = "none"
    POSSIBLE = "possible"
    EXPLICIT = "explicit"


class ClassifierOutput(BaseModel):
    """Structured output from the safety classifier LLM call."""

    decision: SafetyDecision
    crisis_level: CrisisLevel = CrisisLevel.NONE
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""
