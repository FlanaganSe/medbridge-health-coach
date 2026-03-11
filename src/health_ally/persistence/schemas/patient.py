"""Patient schemas for API serialization."""

from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, field_validator


class PatientCreate(BaseModel):
    """Schema for creating a new patient."""

    tenant_id: str
    external_patient_id: str
    timezone: str = "America/New_York"

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        """Validate IANA timezone string."""
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, KeyError) as err:
            msg = f"Invalid IANA timezone: {v}"
            raise ValueError(msg) from err
        return v


class PatientRead(BaseModel):
    """Schema for reading patient data."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: str
    external_patient_id: str
    phase: str
    timezone: str
    unanswered_count: int
    last_outreach_at: datetime | None
    last_patient_response_at: datetime | None
    created_at: datetime
    updated_at: datetime
