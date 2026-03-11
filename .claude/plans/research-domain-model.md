# Research: Domain Model and Data Architecture

**Date:** 2026-03-10
**Scope:** Domain model, SQLAlchemy 2.0 async ORM patterns, Pydantic v2 schemas, LangGraph state integration, consent, audit, multi-tenancy, and idempotency for Milestones M2–M4
**Input:** FINAL_CONSOLIDATED_RESEARCH.md §18, §11, §17; research-fastapi-sqlalchemy.md; PRD v1.6; external documentation verified against live sources

---

## 1. Current State

The consolidated research established the full entity list and high-level data-model rules but stopped short of implementation-ready patterns. The following is already decided and must not be relitigated:

- 16-entity relational domain model; append-only `audit_events` (`FINAL_CONSOLIDATED_RESEARCH.md:1350-1377`)
- SQLAlchemy 2.0 async + Pydantic v2 + Alembic; `Mapped[T]` / `mapped_column()` style (`FINAL_CONSOLIDATED_RESEARCH.md:56-58`)
- `REVOKE UPDATE, DELETE` on the audit table at the PostgreSQL level (`FINAL_CONSOLIDATED_RESEARCH.md:928`)
- All tables include `tenant_id`; RLS for multi-tenancy isolation (`FINAL_CONSOLIDATED_RESEARCH.md:1371, 1392`)
- Two independent connection pools; LangGraph checkpointer pool must not be shared with the app ORM pool (`research-fastapi-sqlalchemy.md:514-570`)
- `expire_on_commit=False`, `pool_pre_ping=True`, `lazy="raise"` are mandatory (`research-fastapi-sqlalchemy.md:949-964`)
- Naming convention on `Base.metadata` is mandatory for deterministic Alembic constraint names (`research-fastapi-sqlalchemy.md:65-78`)
- Phase transitions are deterministic application code; the LLM never decides them (`.claude/rules/immutable.md:3`)
- Consent is verified per-interaction, never cached across invocations (`.claude/rules/immutable.md:2`)
- Domain DB is source of truth; LangGraph checkpointer stores thread replay state only (`prd.md:218-219`)

---

## 2. Constraints

The following cannot change without an explicit ADR:

1. **pyright strict** — all `Mapped[T]` annotations, `StrEnum`, and Pydantic v2 models must pass without `# type: ignore` except for the two known upstream open issues: `add_conditional_edges` (`# type: ignore[arg-type]`, issue #6540) and ORM model constructor argument inference (issue #12268).
2. **Python 3.12+** — `StrEnum` is available natively without a backport. `enum.StrEnum` is the correct type.
3. **`postgresql+psycopg://` scheme** — the URL scheme must be explicit throughout; `postgresql://` silently selects the wrong dialect.
4. **No dual-write between domain DB and LangGraph checkpointer** — the domain DB governs all regulated state (phase, consent, goals, alerts, audit). The checkpointer stores only the conversation replay blob. `load_patient_context` → agent nodes → `save_patient_context` is the synchronization boundary.
5. **Append-only audit** — `AuditEvent` rows must never be updated or deleted. PostgreSQL-level `REVOKE` and the `write_only=True` SQLAlchemy relationship enforce this structurally.
6. **Idempotency on all side effects** — inbound events, scheduled jobs, tool calls, and delivery attempts each carry a stable key. This is not optional for HIPAA-safe re-execution.

---

## 3. Topic 1: Patient Lifecycle State Machine

### 3.1 StrEnum for Phases

Use `enum.StrEnum` (Python 3.11+; stable in 3.12+). `StrEnum` members are `str` instances, so they compare equal to their string values and serialize to JSON without a `.value` call. This is preferable to `(str, Enum)` mixin, which has subtle SQLAlchemy storage behaviors that differ between psycopg2 and psycopg3 (see: [SQLAlchemy discussion #13052](https://github.com/sqlalchemy/sqlalchemy/discussions/13052)).

```python
# src/health_coach/domain/phases.py
from enum import StrEnum


class PatientPhase(StrEnum):
    PENDING = "PENDING"
    ONBOARDING = "ONBOARDING"
    ACTIVE = "ACTIVE"
    RE_ENGAGING = "RE_ENGAGING"
    DORMANT = "DORMANT"
```

**Storage in SQLAlchemy:** Store as `String(20)` with explicit enum validation in the domain layer rather than as a native PostgreSQL `ENUM` type. Native `ENUM` requires a schema ALTER to add values and behaves inconsistently with Alembic autogenerate across database dialects. `String` is portable (SQLite for local dev, PostgreSQL for prod) and avoids the psycopg3 StrEnum storage edge case.

```python
# In the ORM model (see §7 for full model):
phase: Mapped[PatientPhase] = mapped_column(
    String(20),
    default=PatientPhase.PENDING,
    nullable=False,
)
```

### 3.2 Deterministic Transition Rules

The state machine lives in pure Python with no I/O. It accepts the current phase and an event, returns either the next phase or raises `PhaseTransitionError`. The LLM never touches this code.

```python
# src/health_coach/domain/phase_machine.py
from health_coach.domain.phases import PatientPhase
from health_coach.domain.errors import PhaseTransitionError

# Adjacency map: (current_phase, event) -> next_phase
_TRANSITIONS: dict[tuple[PatientPhase, str], PatientPhase] = {
    (PatientPhase.PENDING, "onboarding_initiated"): PatientPhase.ONBOARDING,
    (PatientPhase.ONBOARDING, "goal_confirmed"): PatientPhase.ACTIVE,
    (PatientPhase.ONBOARDING, "no_response_timeout"): PatientPhase.DORMANT,
    (PatientPhase.ACTIVE, "missed_third_message"): PatientPhase.RE_ENGAGING,
    (PatientPhase.ACTIVE, "patient_disengaged"): PatientPhase.DORMANT,
    (PatientPhase.RE_ENGAGING, "patient_responded"): PatientPhase.ACTIVE,
    (PatientPhase.RE_ENGAGING, "missed_third_message"): PatientPhase.DORMANT,
    (PatientPhase.DORMANT, "patient_returned"): PatientPhase.RE_ENGAGING,
}

# Phases from which re-entry is permitted at all
_REENTERABLE: frozenset[PatientPhase] = frozenset(
    {PatientPhase.DORMANT, PatientPhase.RE_ENGAGING}
)


def transition(current: PatientPhase, event: str) -> PatientPhase:
    """
    Return the next phase given current phase and event name.
    Raises PhaseTransitionError for invalid combinations.
    """
    key = (current, event)
    if key not in _TRANSITIONS:
        raise PhaseTransitionError(
            f"No transition defined for phase={current!r} event={event!r}"
        )
    return _TRANSITIONS[key]


def allowed_events(phase: PatientPhase) -> frozenset[str]:
    """Return the set of events valid in the given phase."""
    return frozenset(
        event for (p, event) in _TRANSITIONS if p == phase
    )
```

**Design notes:**
- The adjacency map is the complete truth table. Any unmapped `(phase, event)` pair raises immediately — there is no silent no-op.
- The transition function is pure (no side effects). Callers in the repository layer record the transition as an audit event after calling this.
- `allowed_events()` supports testing invariants with `hypothesis` property-based tests.

### 3.3 Transition Validation in the Repository

```python
# In PatientRepository.apply_phase_transition():
async def apply_phase_transition(
    self,
    patient_id: uuid.UUID,
    event: str,
    actor: str,
    metadata: dict[str, object] | None = None,
) -> PatientPhase:
    patient = await self.get_by_id_for_update(patient_id)
    if patient is None:
        raise PatientNotFoundError(patient_id)
    next_phase = transition(patient.phase, event)  # raises PhaseTransitionError if invalid
    patient.phase = next_phase
    patient.updated_at = func.now()
    # Emit audit event in same transaction
    self._session.add(
        AuditEvent(
            patient_id=patient_id,
            tenant_id=patient.tenant_id,
            event_type=AuditEventType.PHASE_TRANSITION,
            actor=actor,
            outcome="success",
            metadata={
                "from_phase": str(patient.phase),
                "to_phase": str(next_phase),
                "event": event,
                **(metadata or {}),
            },
        )
    )
    await self._session.flush()
    return next_phase
```

---

## 4. Topic 2: Consent Verification Pattern

### 4.1 Per-Interaction Check

Consent is verified once per graph invocation, before any LLM call. The result is stored on `PatientState.consent_verified` for the duration of the invocation but is never persisted back to the domain DB as a boolean "consent is OK" field — doing so would allow it to be read as a cached value in a later invocation.

```python
# src/health_coach/domain/consent.py
from dataclasses import dataclass
from datetime import datetime, UTC


@dataclass(frozen=True)
class ConsentResult:
    """Immutable snapshot of a consent check outcome."""
    patient_id: str
    checked_at: datetime
    logged_in: bool
    outreach_consented: bool

    @property
    def is_valid(self) -> bool:
        return self.logged_in and self.outreach_consented


class ConsentService:
    """
    Checks MedBridge Go for login and outreach consent.
    Fails safe: if the upstream is unavailable, consent is denied.
    """

    def __init__(self, medbridge_client: "MedBridgeClient") -> None:
        self._client = medbridge_client

    async def check(self, patient_id: str) -> ConsentResult:
        try:
            status = await self._client.get_consent_status(patient_id)
            return ConsentResult(
                patient_id=patient_id,
                checked_at=datetime.now(UTC),
                logged_in=status.logged_in,
                outreach_consented=status.outreach_consented,
            )
        except Exception:
            # Fail safe: unknown consent = no consent
            return ConsentResult(
                patient_id=patient_id,
                checked_at=datetime.now(UTC),
                logged_in=False,
                outreach_consented=False,
            )
```

### 4.2 Immutable Consent Snapshots

`PatientConsentSnapshot` is an append-only table recording the result of every consent check. It is never updated. The application queries it only for audit, not for caching consent decisions.

```python
class PatientConsentSnapshot(Base):
    __tablename__ = "patient_consent_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("patients.id"), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False, server_default=func.now())
    logged_in: Mapped[bool] = mapped_column(nullable=False)
    outreach_consented: Mapped[bool] = mapped_column(nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)  # "pass" | "fail"
    invocation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # ^ Links to the graph invocation that triggered the check
```

### 4.3 Fail-Safe Behavior in the Graph

The `consent_gate` node is the first node in the graph. It must always:
1. Call `ConsentService.check()` and persist a `PatientConsentSnapshot`
2. If `not result.is_valid`: emit an `AuditEvent(event_type=CONSENT_CHECK, outcome="fail")`, set `state["consent_verified"] = False`, route to `END`
3. If `result.is_valid`: emit `AuditEvent(outcome="pass")`, set `state["consent_verified"] = True`, proceed

The graph must never bypass this node. The conditional router after it must verify `state["consent_verified"]` before any downstream node executes.

---

## 5. Topic 3: Goal Extraction and Storage

### 5.1 Pydantic Schema for a Structured Goal

```python
# src/health_coach/domain/goals.py
from pydantic import BaseModel, Field


class ExtractedGoal(BaseModel):
    """
    Structured representation of a patient exercise goal extracted from
    free-form conversation. Used as the output schema for LLM extraction.
    All fields have explicit descriptions — these appear in the JSON schema
    and are included in the tool prompt the LLM receives.
    """
    activity: str = Field(
        description="The exercise or activity the patient wants to do, e.g. 'walk', 'swim'."
    )
    frequency: str = Field(
        description="How often, e.g. '3 times a week', 'daily'."
    )
    duration: str = Field(
        description="How long each session, e.g. '20 minutes', '1 hour'."
    )
    goal_summary: str = Field(
        description=(
            "A one-sentence plain-language summary of the complete goal "
            "as the patient would say it, e.g. 'I want to walk for 20 minutes 3 times a week.'"
        )
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Model's confidence that the extraction is accurate (0.0–1.0).",
    )
```

### 5.2 LLM Extraction with `with_structured_output(method="json_schema")`

Anthropic structured outputs are GA as of March 2026. The correct call site uses `method="json_schema"` which maps to `tool_choice={"type": "tool", "name": ...}` under the hood via `langchain-anthropic`. This is confirmed in the project memory (`MEMORY.md`: "Anthropic structured outputs are GA — use `method='json_schema'`").

```python
# src/health_coach/agent/goal_extractor.py
from langchain_anthropic import ChatAnthropic
from health_coach.domain.goals import ExtractedGoal


def build_goal_extractor(model: ChatAnthropic) -> object:
    """
    Returns a runnable that takes a free-form patient message and
    returns an ExtractedGoal instance.
    """
    return model.with_structured_output(
        ExtractedGoal,
        method="json_schema",
        strict=True,
    )


async def extract_goal(
    patient_message: str,
    extractor: object,
) -> ExtractedGoal:
    prompt = (
        "The patient said the following about their exercise goal. "
        "Extract a structured goal from it.\n\n"
        f"Patient: {patient_message}"
    )
    result = await extractor.ainvoke(prompt)
    assert isinstance(result, ExtractedGoal)  # guaranteed by with_structured_output
    return result
```

**Notes on `method` parameter:**
- `method="json_schema"` (Anthropic) sends the schema via tool definition; the model is forced to return valid JSON matching that schema.
- `method="function_calling"` is the OpenAI path — same interface, different wire protocol. Both are handled by LangChain's abstraction.
- Always pass `strict=True` to prevent the model from adding extra fields or omitting required ones.
- `max_tokens` must be set explicitly on `ChatAnthropic` instances (project `MEMORY.md`).

### 5.3 PatientGoal ORM Model

```python
class PatientGoal(Base):
    __tablename__ = "patient_goals"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("patients.id"), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    activity: Mapped[str] = mapped_column(String(255), nullable=False)
    frequency: Mapped[str] = mapped_column(String(255), nullable=False)
    duration: Mapped[str] = mapped_column(String(255), nullable=False)
    goal_summary: Mapped[str] = mapped_column(Text, nullable=False)
    raw_patient_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ^ Store the original text for audit; never expose in logs
    extraction_confidence: Mapped[float] = mapped_column(nullable=False)
    confirmed_by_patient: Mapped[bool] = mapped_column(default=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    # ^ Only one goal is active at a time; prior goals are kept for audit
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())

    patient: Mapped["Patient"] = relationship("Patient", back_populates="goals", lazy="raise")
```

---

## 6. Topic 4: Audit Event Design

### 6.1 Event Type Taxonomy

```python
# src/health_coach/domain/audit.py
from enum import StrEnum


class AuditEventType(StrEnum):
    # Consent
    CONSENT_CHECK = "consent_check"

    # Safety pipeline
    SAFETY_INPUT_CHECK = "safety_input_check"
    SAFETY_OUTPUT_CHECK = "safety_output_check"
    SAFETY_CRISIS_DETECTED = "safety_crisis_detected"

    # Message delivery
    MESSAGE_GENERATED = "message_generated"
    MESSAGE_SENT = "message_sent"
    MESSAGE_BLOCKED = "message_blocked"
    MESSAGE_FALLBACK_USED = "message_fallback_used"

    # Phase transitions
    PHASE_TRANSITION = "phase_transition"

    # Tools
    TOOL_INVOKED = "tool_invoked"
    TOOL_FAILED = "tool_failed"

    # Goals
    GOAL_EXTRACTED = "goal_extracted"
    GOAL_CONFIRMED = "goal_confirmed"

    # Clinician
    CLINICIAN_ALERT_CREATED = "clinician_alert_created"
    CLINICIAN_ALERT_DELIVERED = "clinician_alert_delivered"

    # Scheduling
    JOB_SCHEDULED = "job_scheduled"
    JOB_COMPLETED = "job_completed"
    JOB_FAILED = "job_failed"
    JOB_DEAD_LETTERED = "job_dead_lettered"
```

### 6.2 Append-Only Schema

```python
class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    # patient_id is NOT a FK — audit events must survive patient record deletion
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    event_type: Mapped[AuditEventType] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    # ^ "system", "coach_agent", "patient", "scheduler", specific user ID
    outcome: Mapped[str] = mapped_column(String(50), nullable=False)
    # ^ "pass", "fail", "blocked", "escalated", "success", "error"
    conversation_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    invocation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    # ^ Event-specific structured data. Never raw message content, never PII fields.
    occurred_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now(), index=True
    )

    # Append-only: no update_at, no relationships that load back into memory
    # The relationship on Patient uses write_only=True so it can never be
    # iterated over accidentally in an ORM query.
```

**No FK from `patient_id` to `patients.id`:** If a patient record is later purged per a retention policy, the audit trail must remain intact for HIPAA 6-year retention. An FK would block the delete.

**`write_only=True` on `Patient.audit_events`:**

```python
# On the Patient model:
audit_events: Mapped[list["AuditEvent"]] = relationship(
    "AuditEvent",
    foreign_keys="[AuditEvent.patient_id]",
    primaryjoin="Patient.id == AuditEvent.patient_id",
    write_only=True,  # append-only; cannot be read as a collection
)
```

### 6.3 PostgreSQL-Level Immutability

The `REVOKE UPDATE, DELETE` statements belong in the Alembic migration that creates the table, not in application code:

```python
# In the Alembic migration file (versions/XXXX_create_audit_events.py):
from alembic import op

def upgrade() -> None:
    op.create_table(
        "audit_events",
        # ... column definitions ...
    )
    # Enforce append-only at the database level.
    # The app service role (e.g., "healthcoach_app") must exist.
    op.execute(
        "REVOKE UPDATE, DELETE ON audit_events FROM healthcoach_app"
    )

def downgrade() -> None:
    # Restore before dropping
    op.execute(
        "GRANT UPDATE, DELETE ON audit_events TO healthcoach_app"
    )
    op.drop_table("audit_events")
```

**Note:** `REVOKE` applies to a named database role. The Alembic migration must run as a superuser or the table owner; the application role (`healthcoach_app`) is the one being restricted. In Cloud SQL / RDS, this is typically the default service account vs. the application user. Document the role topology in `docs/phi-data-flow.md`.

---

## 7. Topic 5: Multi-Tenancy

### 7.1 `tenant_id` Column Pattern

Every table (except `audit_events`, which has it for query purposes) carries `tenant_id` as a non-null UUID. This is a denormalization for query efficiency — it avoids a join through `patients` to determine tenant ownership on every row.

```python
# Included in every table as a standard column:
tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
```

### 7.2 PostgreSQL Row-Level Security

RLS is enforced at the PostgreSQL level so application bugs cannot accidentally leak cross-tenant data. The pattern uses a session-local configuration variable set on each connection before queries run.

**Migration:**

```sql
-- Enable RLS on a table (example: patients)
ALTER TABLE patients ENABLE ROW LEVEL SECURITY;
ALTER TABLE patients FORCE ROW LEVEL SECURITY;

-- Policy: the app role may only see rows matching the current_setting value
CREATE POLICY tenant_isolation ON patients
    USING (tenant_id = current_setting('app.current_tenant_id')::uuid);
```

Apply equivalent `ENABLE ROW LEVEL SECURITY` + policy to every tenant-scoped table. The migration superuser role is exempt (it sees all rows).

**Setting tenant context per session** (FastAPI middleware layer, not SQLAlchemy layer):

```python
# src/health_coach/api/middleware.py
from contextvars import ContextVar
import uuid

_current_tenant_id: ContextVar[uuid.UUID | None] = ContextVar(
    "current_tenant_id", default=None
)


async def get_session_with_tenant(
    tenant_id: uuid.UUID,
    session: AsyncSession,
) -> None:
    """
    Set the PostgreSQL session variable so RLS policies fire correctly.
    Must be called before any query that touches tenant-scoped tables.
    """
    await session.execute(
        text("SET LOCAL app.current_tenant_id = :tid"),
        {"tid": str(tenant_id)},
    )
```

**Trade-off:** `SET LOCAL` (versus `SET`) scopes the variable to the current transaction, which is safer for connection pooling. When the session is returned to the pool, the next borrower does not inherit a stale tenant context.

**Source:** [PostgreSQL docs on Row Security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html); [FastAPI + RLS example](https://adityamattos.com/multi-tenancy-in-python-fastapi-and-sqlalchemy-using-postgres-row-level-security); [RLS with SQLAlchemy](https://personal-web-9c834.web.app/blog/pg-tenant-isolation/)

---

## 8. Topic 6: SQLAlchemy Model Patterns

### 8.1 Base with Common Columns

```python
# src/health_coach/persistence/models/base.py
import uuid
from datetime import datetime
from sqlalchemy import MetaData, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID as PGUUID, TIMESTAMPTZ

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
```

**Note:** There is no shared `TimestampMixin` here by design. `created_at` and `updated_at` are per-table decisions — audit events have only `occurred_at`, and goals have `created_at` but no `updated_at` because goal records are replaced, not updated in place.

### 8.2 Full Patient Model

```python
# src/health_coach/persistence/models/patient.py
import uuid
from datetime import datetime
from sqlalchemy import String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID, TIMESTAMPTZ
from sqlalchemy.orm import Mapped, mapped_column, relationship
from health_coach.domain.phases import PatientPhase
from health_coach.persistence.models.base import Base


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    external_patient_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # ^ Opaque ID from MedBridge Go — our system does not store names or contact info

    phase: Mapped[PatientPhase] = mapped_column(
        String(20), nullable=False, default=PatientPhase.PENDING
    )
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    unanswered_count: Mapped[int] = mapped_column(nullable=False, default=0)
    last_contact_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMPTZ, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # lazy="raise" on all relationships — prevents accidental implicit I/O in async context
    goals: Mapped[list["PatientGoal"]] = relationship(
        "PatientGoal", back_populates="patient", lazy="raise"
    )
    consent_snapshots: Mapped[list["PatientConsentSnapshot"]] = relationship(
        "PatientConsentSnapshot", back_populates="patient", lazy="raise"
    )
    scheduled_jobs: Mapped[list["ScheduledJob"]] = relationship(
        "ScheduledJob", back_populates="patient", lazy="raise"
    )
    audit_events: Mapped[list["AuditEvent"]] = relationship(
        "AuditEvent",
        foreign_keys="[AuditEvent.patient_id]",
        primaryjoin="Patient.id == AuditEvent.patient_id",
        write_only=True,  # append-only; never iterate this collection
    )
    clinician_alerts: Mapped[list["ClinicianAlert"]] = relationship(
        "ClinicianAlert", back_populates="patient", lazy="raise"
    )
```

### 8.3 JSONB Usage

```python
# For structured blobs where the schema evolves or varies per event type:
metadata_: Mapped[dict[str, object] | None] = mapped_column(
    "metadata", JSONB, nullable=True
)
```

Use `JSONB` (not `JSON`) — `JSONB` is indexable and supports operators like `@>`. Use it for:
- `AuditEvent.metadata_` — event-specific key/value details
- `ScheduledJob.metadata_` — job parameters
- `SafetyDecision.classifier_output` — raw classifier output blob

Do not use JSONB for structured data that will be queried by field — use proper columns instead.

---

## 9. Topic 7: Repository Pattern

### 9.1 Base Repository

```python
# src/health_coach/persistence/repositories/base.py
from typing import Generic, TypeVar
from sqlalchemy.ext.asyncio import AsyncSession

ModelT = TypeVar("ModelT")


class BaseRepository(Generic[ModelT]):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
```

### 9.2 Patient Repository

```python
# src/health_coach/persistence/repositories/patient.py
import uuid
from sqlalchemy import select, update, func
from sqlalchemy.orm import selectinload
from health_coach.domain.phases import PatientPhase
from health_coach.domain.errors import PatientNotFoundError
from health_coach.persistence.models import Patient
from health_coach.persistence.repositories.base import BaseRepository


class PatientRepository(BaseRepository[Patient]):

    async def get_by_id(self, patient_id: uuid.UUID) -> Patient | None:
        stmt = select(Patient).where(Patient.id == patient_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id_with_goals(self, patient_id: uuid.UUID) -> Patient | None:
        """Load patient + active goals in one query (avoids N+1)."""
        stmt = (
            select(Patient)
            .where(Patient.id == patient_id)
            .options(selectinload(Patient.goals))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_external_id(self, external_id: str) -> Patient | None:
        stmt = select(Patient).where(Patient.external_patient_id == external_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, patient: Patient) -> Patient:
        self._session.add(patient)
        await self._session.flush()  # materialize id, created_at without committing
        return patient

    async def update_phase(
        self,
        patient_id: uuid.UUID,
        new_phase: PatientPhase,
    ) -> None:
        """
        Direct column update — does not require loading the full ORM object.
        Use apply_phase_transition() for validated state machine transitions.
        """
        stmt = (
            update(Patient)
            .where(Patient.id == patient_id)
            .values(phase=new_phase, updated_at=func.now())
        )
        await self._session.execute(stmt)

    async def increment_unanswered_count(self, patient_id: uuid.UUID) -> int:
        """Returns the new count."""
        stmt = (
            update(Patient)
            .where(Patient.id == patient_id)
            .values(unanswered_count=Patient.unanswered_count + 1, updated_at=func.now())
            .returning(Patient.unanswered_count)
        )
        result = await self._session.execute(stmt)
        row = result.one()
        return int(row[0])

    async def reset_unanswered_count(self, patient_id: uuid.UUID) -> None:
        stmt = (
            update(Patient)
            .where(Patient.id == patient_id)
            .values(unanswered_count=0, updated_at=func.now())
        )
        await self._session.execute(stmt)
```

### 9.3 Transaction Boundaries

The `session.commit()` is owned by the FastAPI dependency (`get_session`) for HTTP request paths. For background worker paths (scheduler), each job executes inside its own `async with session.begin()` block. The pattern is:

```
HTTP path:  FastAPI dependency opens session → handler calls repo methods → flush inside repo → dependency commits
Worker path: Worker opens session → session.begin() context → job logic → commit on exit
```

Never call `session.commit()` inside a repository method — that decision belongs to the caller.

---

## 10. Topic 8: Pydantic Schema Patterns

### 10.1 Create / Read / Update Separation

```python
# src/health_coach/persistence/schemas/patient.py
import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict
from health_coach.domain.phases import PatientPhase


class PatientBase(BaseModel):
    external_patient_id: str
    timezone: str = "UTC"


class PatientCreate(PatientBase):
    tenant_id: uuid.UUID


class PatientRead(PatientBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    phase: PatientPhase
    unanswered_count: int
    last_contact_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PatientUpdate(BaseModel):
    """All fields optional — only provided fields are updated."""
    phase: PatientPhase | None = None
    unanswered_count: int | None = None
    timezone: str | None = None
    last_contact_at: datetime | None = None
```

**Rules:**
- `PatientCreate` contains only what the caller can set — no server-generated fields.
- `PatientRead` has `from_attributes=True` for `model_validate(orm_obj)` conversion.
- `PatientUpdate` uses `None` defaults — the repository layer applies only non-None fields.
- Never return `PatientCreate` or raw ORM objects from API routes; always return `PatientRead` or a purpose-built response schema.

### 10.2 Goal Schemas

```python
# src/health_coach/persistence/schemas/goal.py
import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class GoalCreate(BaseModel):
    patient_id: uuid.UUID
    tenant_id: uuid.UUID
    activity: str
    frequency: str
    duration: str
    goal_summary: str
    raw_patient_text: str | None = None
    extraction_confidence: float = Field(ge=0.0, le=1.0)


class GoalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    activity: str
    frequency: str
    duration: str
    goal_summary: str
    extraction_confidence: float
    confirmed_by_patient: bool
    confirmed_at: datetime | None
    is_active: bool
    created_at: datetime
    # NOTE: raw_patient_text is intentionally excluded from GoalRead —
    # it is PHI and should not be returned to callers by default.
```

### 10.3 ORM → Pydantic Conversion Pattern

```python
# In a route handler:
patient_orm = await repo.get_by_id(patient_id)
if patient_orm is None:
    raise HTTPException(status_code=404)
return PatientRead.model_validate(patient_orm)  # replaces v1 .from_orm()
```

`model_validate()` with `from_attributes=True` reads directly from ORM object attributes. This is the only correct Pydantic v2 pattern — `from_orm()` was removed in v2.

---

## 11. Topic 9: LangGraph State vs. Domain DB

### 11.1 What Lives Where

| Data | Where | Rationale |
|------|-------|-----------|
| Patient phase | Domain DB (`patients.phase`) | Source of truth; auditable; tested by domain logic |
| Consent verification result | LangGraph state (`PatientState.consent_verified`) | Current-invocation only; never persisted |
| Active goal | Domain DB (`patient_goals`) | Source of truth; confirmed by patient |
| Conversation messages | LangGraph checkpointer blob | Thread replay; controlled by `thread_id` |
| Message content for context | LangGraph state (`PatientState.messages`) | Ephemeral for the current invocation |
| Unanswered count | Domain DB (`patients.unanswered_count`) | Durable; drives backoff policy |
| Safety decisions | Domain DB (`safety_decisions`) | Auditable; required by HIPAA |
| Scheduled jobs | Domain DB (`scheduled_jobs`) | Durable; recoverable after crash |
| Clinician alerts | Domain DB (`clinician_alerts`) | Durable; outbox pattern for delivery |
| Phase for current routing | LangGraph state (`PatientState.phase`) | Copied from DB in `load_patient_context`; read-only in graph |

### 11.2 `load_patient_context` / `save_patient_context` Pattern

These two nodes are the synchronization boundary. They are the only nodes that touch the domain DB; all agent nodes between them work only on `PatientState`.

```python
# src/health_coach/agent/nodes/context.py
from health_coach.agent.state import PatientState
from health_coach.persistence.repositories import PatientRepository, GoalRepository
from health_coach.domain.phases import PatientPhase


async def load_patient_context(
    state: PatientState,
    *,
    session: AsyncSession,      # injected via LangGraph Runtime
) -> dict[str, object]:
    """
    Load authoritative domain state into LangGraph state.
    This is the only node that reads from the domain DB.
    Returns only the fields that are safe to include in graph state.
    """
    repo = PatientRepository(session)
    patient = await repo.get_by_id_with_goals(uuid.UUID(state["patient_id"]))
    if patient is None:
        raise PatientNotFoundError(state["patient_id"])

    active_goal = next((g for g in patient.goals if g.is_active), None)  # type: ignore[union-attr]
    # goals is already loaded via selectinload; safe to iterate
    # ^ patient.goals access requires eager load; lazy="raise" enforces this

    return {
        "phase": patient.phase,
        "unanswered_count": patient.unanswered_count,
        "current_goal": active_goal.goal_summary if active_goal else None,
        "last_contact_at": patient.last_contact_at,
    }


async def save_patient_context(
    state: PatientState,
    *,
    session: AsyncSession,
) -> dict[str, object]:
    """
    Persist any domain state changes made during the invocation.
    This is the only node that writes to the domain DB.
    Phase transitions must have already been validated by transition().
    """
    repo = PatientRepository(session)

    # If a phase transition was flagged during the invocation, apply it
    if state.get("pending_phase_transition"):
        await repo.apply_phase_transition(
            uuid.UUID(state["patient_id"]),
            event=state["pending_phase_transition"],
            actor="coach_agent",
        )

    # Update operational counters
    if state.get("unanswered_count") is not None:
        # direct update; transition logic already validated
        pass  # handled inside apply_phase_transition or separate repo method

    return {}  # no state mutation; side effects are the purpose of this node
```

### 11.3 Avoiding Dual-Write

The key rule: the domain DB is the source of truth for all regulated state. The checkpointer is the source of truth for conversation replay only. These do not overlap.

- Phase in `PatientState` is a copy for routing, not the master. It is read in `load_patient_context` and treated as read-only by all agent nodes.
- `save_patient_context` writes domain state ONCE at the end of each invocation. It does not write back to the checkpointer — LangGraph handles that automatically.
- If a crash occurs between `save_patient_context` and the checkpointer commit, the invocation replays from the last checkpoint. `save_patient_context` is idempotent by design (upsert / `ON CONFLICT DO NOTHING` where applicable).

---

## 12. Topic 10: Idempotency Patterns

### 12.1 Stable Key Design

Every side-effecting operation carries a deterministic, stable key. The key is computed from inputs that do not change on retry.

| Operation | Key construction | Example |
|-----------|-----------------|---------|
| Inbound webhook event | Provided by MedBridge Go; stored in `processed_events.event_key` | `"medbridge:{event_id}"` |
| Scheduled job | `"{patient_id}:{job_type}:{scheduled_date}"` | `"a3b4...:day_2_followup:2026-03-12"` |
| Tool call | `"{thread_id}:{run_id}:{tool_name}:{call_index}"` | `"thread_123:run_456:set_goal:0"` |
| Message delivery | `"{outbox_row_id}:{attempt_number}"` | `"outbox_789:1"` |
| Clinician alert | `"{patient_id}:{alert_reason}:{date}"` | `"a3b4...:crisis:2026-03-10"` |

### 12.2 Inbound Event Deduplication

```python
class ProcessedEvent(Base):
    """
    Tracks inbound events to prevent duplicate processing.
    Uses INSERT ... ON CONFLICT DO NOTHING as the idempotency primitive.
    """
    __tablename__ = "processed_events"

    event_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())
    source: Mapped[str] = mapped_column(String(100), nullable=False)
```

```python
async def process_inbound_event_idempotently(
    session: AsyncSession,
    event_key: str,
    source: str,
    handler: Callable[[], Awaitable[None]],
) -> bool:
    """
    Returns True if the event was processed, False if already seen.
    Uses INSERT ON CONFLICT DO NOTHING so concurrent workers are safe.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = pg_insert(ProcessedEvent).values(
        event_key=event_key, source=source
    ).on_conflict_do_nothing(index_elements=["event_key"])
    result = await session.execute(stmt)
    if result.rowcount == 0:
        return False  # Already processed
    await handler()
    return True
```

### 12.3 Scheduled Job Idempotency

```python
async def schedule_job_idempotently(
    session: AsyncSession,
    patient_id: uuid.UUID,
    tenant_id: uuid.UUID,
    job_type: str,
    scheduled_at: datetime,
    idempotency_key: str,
    metadata: dict[str, object] | None = None,
) -> bool:
    """
    Returns True if the job was created, False if the key already exists.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = pg_insert(ScheduledJob).values(
        patient_id=patient_id,
        tenant_id=tenant_id,
        job_type=job_type,
        scheduled_at=scheduled_at,
        idempotency_key=idempotency_key,
        status="pending",
        metadata_=metadata,
    ).on_conflict_do_nothing(index_elements=["idempotency_key"])
    result = await session.execute(stmt)
    return result.rowcount > 0
```

### 12.4 Tool Call Idempotency

For side-effecting tools (`set_goal`, `alert_clinician`), the tool implementation checks whether the operation was already applied:

```python
async def set_goal_tool(
    patient_id: str,
    goal_summary: str,
    tool_call_key: str,  # "{thread_id}:{run_id}:set_goal:0"
    session: AsyncSession,
) -> dict[str, object]:
    """
    Idempotent goal setting. If the tool_call_key already exists in
    ToolInvocation, return the previously recorded result.
    """
    existing = await tool_invocation_repo.get_by_key(tool_call_key)
    if existing is not None:
        return existing.output  # replay safe
    # ... proceed with goal extraction and persistence
    await tool_invocation_repo.record(
        key=tool_call_key,
        tool_name="set_goal",
        patient_id=patient_id,
        input_={"goal_summary": goal_summary},
        output={"status": "ok", "goal_id": str(goal.id)},
    )
    return {"status": "ok", "goal_id": str(goal.id)}
```

### 12.5 Delivery Idempotency

The outbox table carries a unique `idempotency_key` per outbound message. The delivery worker uses `ON CONFLICT DO NOTHING` when the notification channel returns a duplicate-delivery response:

```python
class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    outbox_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("outbox.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    channel: Mapped[str] = mapped_column(String(50), nullable=False)  # "sms", "push", "mock"
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempted_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
```

---

## 13. Options and Trade-offs

Three areas had meaningful design choices:

### 13.1 Enum Storage: `String(20)` vs. Native PostgreSQL `ENUM`

**Option A: `String(20)` with Python-side validation (recommended)**
- Portable across PostgreSQL and SQLite (local dev)
- Adding new phase values requires only a code change, no schema ALTER
- psycopg3 has a known behavior difference with `StrEnum` vs. `Enum` in storage (Discussion #13052) — `String` avoids this entirely
- Trade-off: no database-level constraint; application must validate

**Option B: Native PostgreSQL `ENUM` type**
- Database enforces valid values
- Alembic cannot autogenerate ENUM modifications — requires manual migration
- Breaks on SQLite (local dev)
- Not recommended for this project

### 13.2 RLS vs. Application-Layer Tenant Filtering

**Option A: PostgreSQL RLS (recommended)**
- Tenant isolation enforced at DB level — survives application bugs
- No `WHERE tenant_id = ?` in every repository query
- Requires `SET LOCAL app.current_tenant_id` on each session before queries
- Trade-off: requires `SET LOCAL` discipline in all code paths; migration superuser needs care

**Option B: Application-layer `tenant_id` filter in every query**
- Simpler connection management
- One missed `WHERE` clause leaks cross-tenant data
- Appropriate for single-tenant v1 launch with future migration path
- Trade-off: relies entirely on developer discipline

**Option C: Schema-per-tenant**
- Strongest isolation; simpler queries within a tenant
- Operational complexity multiplies by tenant count
- Not appropriate for a SaaS model with many tenants

**Given PRD §8.2 ("Single-tenant launch with tenant-ready schema"), Option A** is the right preparation posture. Start with `tenant_id` columns everywhere and enable RLS in a post-launch migration once the tenant model is confirmed.

### 13.3 LangGraph State ↔ Domain DB Sync: Load-Once vs. Pass-Through

**Option A: `load_patient_context` / `save_patient_context` boundary nodes (recommended)**
- Single synchronization point; predictable; testable in isolation
- Graph nodes between them are pure LangGraph state transformations
- Easier to replay: if `save_patient_context` fails, the domain DB is unchanged and replay is safe

**Option B: Repository calls inside individual agent nodes**
- More granular updates (phase written as soon as known)
- Risk of dual-write divergence; harder to test; harder to reason about replay safety
- Breaks the PRD principle: "Avoid unnecessary dual-write paths" (prd.md:219)

**Option C: LangGraph Store as the domain state layer**
- Eliminates the load/save boundary
- Store is not queryable as a relational DB; loses SQL expressiveness for audit queries
- PRD §8.2 says "Keep optional. Introduce only if cross-thread memory emerges that is not better modeled in relational application state"
- Not appropriate for regulated state (consent, phase, audit)

---

## 14. Recommendation

### Adopt the Following Patterns for M2

1. **`PatientPhase` as `StrEnum`** stored in `String(20)` columns. Do not use native PostgreSQL `ENUM`. The `transition()` function in `domain/phase_machine.py` is the single truth table for all valid transitions.

2. **`ConsentService.check()` fails safe** — `except Exception: return ConsentResult(logged_in=False, outreach_consented=False)`. Every check is persisted as a `PatientConsentSnapshot`. The `consent_gate` node emits an `AuditEvent` regardless of outcome.

3. **`ExtractedGoal` Pydantic model** with `with_structured_output(method="json_schema", strict=True)` for goal extraction. Store raw patient text in `PatientGoal.raw_patient_text` for audit but exclude it from `GoalRead` API schema.

4. **`AuditEvent` has no FK to `patients`**, uses `write_only=True` on the ORM relationship, and is protected by `REVOKE UPDATE, DELETE` in the Alembic migration.

5. **`tenant_id` on every table** with `SET LOCAL app.current_tenant_id` per session. Enable RLS in a migration once the tenant model is confirmed (PRD §8.2: "single-tenant launch, tenant-ready schema").

6. **`load_patient_context` / `save_patient_context` as the sync boundary** between LangGraph state and the domain DB. No repository calls from individual agent nodes.

7. **Stable idempotency keys** computed deterministically from inputs that do not change across retries. `INSERT ... ON CONFLICT DO NOTHING` is the primitive for inbound events and scheduled jobs.

8. **Avoid `total=False` on `PatientState`** — use `T | None` fields with `total=True` to avoid pyright partial-return issues (confirmed in project `MEMORY.md` and `research-fastapi-sqlalchemy.md:973`).

---

## Sources

- `FINAL_CONSOLIDATED_RESEARCH.md:1346-1392` — Entity list and data model rules
- `FINAL_CONSOLIDATED_RESEARCH.md:884-960` — Audit logging and consent gate design
- `research-fastapi-sqlalchemy.md:54-264` — SQLAlchemy 2.0 async ORM patterns
- `prd.md:55-56, 129-143, 214-219` — Phase lifecycle, consent, dual-write avoidance
- `.claude/rules/immutable.md` — Consent, phase, clinical boundary invariants
- [PostgreSQL RLS Documentation](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)
- [FastAPI + RLS Multi-tenancy](https://adityamattos.com/multi-tenancy-in-python-fastapi-and-sqlalchemy-using-postgres-row-level-security)
- [SQLAlchemy RLS Pattern](https://personal-web-9c834.web.app/blog/pg-tenant-isolation/)
- [Atlas Guides: RLS with SQLAlchemy](https://atlasgo.io/guides/orms/sqlalchemy/row-level-security)
- [SQLAlchemy StrEnum vs Enum Discussion #13052](https://github.com/sqlalchemy/sqlalchemy/discussions/13052) — psycopg3 storage behavior difference
- [SQLAlchemy Discussion #12123](https://github.com/sqlalchemy/sqlalchemy/discussions/12123) — StrEnum enum processing
- [LangChain Structured Output Docs](https://docs.langchain.com/oss/python/langchain/structured-output)
- [Anthropic Structured Outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
- [SQLAlchemy issue #12268](https://github.com/sqlalchemy/sqlalchemy/issues/12268) — pyright ORM constructor inference (known, non-blocking)
- [AWS Backoff and Jitter](https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/)
- [HIPAA Audit Log Requirements — 45 CFR 164.316](https://www.law.cornell.edu/cfr/text/45/164.316)
