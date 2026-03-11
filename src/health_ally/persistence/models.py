"""SQLAlchemy ORM models.

All models use:
- UUID primary keys
- tenant_id on every table
- lazy="raise" on relationships
- created_at / updated_at timestamps
- StrEnum + String(20) for phase columns (SQLite compat)
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    WriteOnlyMapped,
    mapped_column,
    relationship,
)


class Base(DeclarativeBase):
    """Declarative base with naming convention."""

    type_annotation_map = {
        dict: JSON,  # type: ignore[type-arg]
    }


# Set naming convention for consistent constraint names
Base.metadata.naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(50), index=True)
    external_patient_id: Mapped[str] = mapped_column(String(100))
    phase: Mapped[str] = mapped_column(String(20), default="pending")
    timezone: Mapped[str] = mapped_column(String(50), default="America/New_York")
    unanswered_count: Mapped[int] = mapped_column(Integer, default=0)
    last_outreach_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_patient_response_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    goals: WriteOnlyMapped[list[PatientGoal]] = relationship(
        back_populates="patient",
    )
    consent_snapshots: WriteOnlyMapped[list[PatientConsentSnapshot]] = relationship(
        back_populates="patient",
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "external_patient_id", name="uq_patients_tenant_external"),
    )


class PatientGoal(Base):
    __tablename__ = "patient_goals"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(50), index=True)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"))
    goal_text: Mapped[str] = mapped_column(Text)
    raw_patient_text: Mapped[str] = mapped_column(Text, default="")
    structured_goal: Mapped[dict | None] = mapped_column()  # type: ignore[type-arg]
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    patient: Mapped[Patient] = relationship(back_populates="goals", lazy="raise")


class PatientConsentSnapshot(Base):
    __tablename__ = "patient_consent_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(50), index=True)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"))
    consented: Mapped[bool]
    reason: Mapped[str] = mapped_column(String(200))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    patient: Mapped[Patient] = relationship(back_populates="consent_snapshots", lazy="raise")


class AuditEvent(Base):
    """Append-only audit log. No FK to patients (survives deletion)."""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(50), index=True)
    patient_id: Mapped[uuid.UUID]
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    outcome: Mapped[str] = mapped_column(String(50))
    metadata_: Mapped[dict | None] = mapped_column("metadata")  # type: ignore[type-arg]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(50), index=True)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patients.id"))
    job_type: Mapped[str] = mapped_column(String(50))
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    metadata_: Mapped[dict | None] = mapped_column("metadata")  # type: ignore[type-arg]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index(
            "ix_scheduled_jobs_pending",
            "status",
            "scheduled_at",
            postgresql_where="status = 'pending'",
        ),
    )


class OutboxEntry(Base):
    """Outbound message intent table (AD-6)."""

    __tablename__ = "outbox_entries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(50), index=True)
    patient_id: Mapped[uuid.UUID]
    delivery_key: Mapped[str] = mapped_column(String(200), unique=True)
    message_type: Mapped[str] = mapped_column(String(30))  # patient_message | clinician_alert
    priority: Mapped[int] = mapped_column(Integer, default=0)
    channel: Mapped[str] = mapped_column(String(50), default="default")
    payload: Mapped[dict | None] = mapped_column()  # type: ignore[type-arg]
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    delivery_attempts: WriteOnlyMapped[list[DeliveryAttempt]] = relationship(
        back_populates="outbox_entry",
    )


class DeliveryAttempt(Base):
    """Transport execution history — one row per actual transport attempt."""

    __tablename__ = "delivery_attempts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(50), index=True)
    outbox_entry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("outbox_entries.id"))
    attempt_number: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(20))
    delivery_receipt: Mapped[dict | None] = mapped_column()  # type: ignore[type-arg]
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    outbox_entry: Mapped[OutboxEntry] = relationship(
        back_populates="delivery_attempts", lazy="raise"
    )


class ClinicianAlert(Base):
    __tablename__ = "clinician_alerts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(50), index=True)
    patient_id: Mapped[uuid.UUID]
    reason: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(20), default="routine")
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SafetyDecisionRecord(Base):
    """Per-message safety classifier outcome."""

    __tablename__ = "safety_decisions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(50), index=True)
    patient_id: Mapped[uuid.UUID]
    decision: Mapped[str] = mapped_column(String(30))
    source: Mapped[str] = mapped_column(String(30), default="classifier")
    confidence: Mapped[float | None]
    reasoning: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ProcessedEvent(Base):
    """Inbound event deduplication."""

    __tablename__ = "processed_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(50), index=True)
    source_event_key: Mapped[str] = mapped_column(String(200), unique=True)
    event_type: Mapped[str] = mapped_column(String(50))
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
