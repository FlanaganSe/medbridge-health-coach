"""Audit event schemas for API serialization."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuditEventRead(BaseModel):
    """Schema for reading audit events."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: str
    patient_id: uuid.UUID
    event_type: str
    outcome: str
    metadata_: dict[str, object] | None
    created_at: datetime
