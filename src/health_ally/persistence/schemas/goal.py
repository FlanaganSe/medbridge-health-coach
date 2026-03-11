"""Goal schemas for API serialization and LLM extraction."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class GoalCreate(BaseModel):
    """Schema for creating a patient goal."""

    goal_text: str
    raw_patient_text: str = ""
    structured_goal: dict[str, object] | None = None


class GoalRead(BaseModel):
    """Schema for reading goal data. Excludes raw_patient_text (PHI)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    goal_text: str
    structured_goal: dict[str, object] | None
    confirmed_at: datetime | None
    created_at: datetime


class ExtractedGoal(BaseModel):
    """Structured output model for LLM goal extraction."""

    activity: str = Field(description="The exercise or activity type")
    frequency: str = Field(description="How often (e.g., '3 times per week')")
    duration: str = Field(description="Duration per session (e.g., '30 minutes')")
    specific_target: str = Field(
        default="",
        description="Any specific target mentioned (e.g., 'walk a mile without pain')",
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Extraction confidence score")
