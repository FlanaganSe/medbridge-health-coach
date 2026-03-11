# Implementation Plan: MedBridge AI Health Coach MVP

**Status:** Draft v5 — revised after fourth review (hash determinism, AUTOCOMMIT on lock, template safety policy, consent_gate text)
**Date:** 2026-03-10
**Input:** `prd.md` (v1.6), `RESEARCH_INDEX.md`, 6 research files (~5,800 lines)
**Contract:** Every step maps to a PRD acceptance criterion. Every file maps to the project structure in `FINAL_CONSOLIDATED_RESEARCH.md` §9. Every pattern maps to a verified research finding.

---

## Summary

Build the MVP as 7 milestones, each independently testable and CI-green. The architecture is a regulated workflow engine — not a chatbot shell. Deterministic policy lives in pure Python; the LLM handles bounded generation, extraction, and tool selection. The domain database is the source of truth; LangGraph checkpointing stores conversation replay state only. Every outbound message passes a multi-layer safety pipeline before delivery.

The plan follows the PRD's milestone sequence (M1–M7) but expands each into concrete, ordered steps with file paths, verification commands, and research references. Steps within a milestone are sequential — each step's verification must pass before proceeding to the next.

---

## Architectural Decisions (Cross-Cutting)

These decisions affect multiple milestones and must be understood before implementation begins.

### AD-1: Thread Strategy — One Persistent Thread Per Patient

**Decision:** Use `thread_id = f"patient-{patient_id}"` for all interactions with a given patient. All conversations (onboarding, follow-ups, re-engagement) accumulate in a single thread.

**Why:** Preserves conversational continuity. When a patient responds to a follow-up, the LLM has the full prior conversation history. The alternative (new thread per check-in) requires loading context from the domain DB, which is lossy for conversational coherence.

**Consequence:** Unbounded message growth. Mitigated by a `manage_history` node that runs between `load_patient_context` and `phase_router`. When message count exceeds a threshold (e.g., 20), it generates a summary via LLM, stores it, and trims older messages via `RemoveMessage`. This keeps the LLM call out of `save_patient_context` (which must contain zero LLM calls).

**Research ref:** `research.md` §10 (Thread Management — Option A recommended)
**ADR trigger:** This contradicts `FINAL_CONSOLIDATED_RESEARCH.md` §6.5 which recommended new threads per check-in. Requires explicit ADR-002 before M4.

### AD-2: Intent Accumulation — save_patient_context Boundary

**Decision:** Graph nodes between `load_patient_context` and `save_patient_context` do NOT write to the domain DB directly. Tools validate inputs and return results but accumulate side effects as "intents" in state. `save_patient_context` flushes all intents to the DB atomically.

**Why:** Replay safety. If `save_patient_context` fails, the domain DB is unchanged and the graph can be replayed. Direct writes from tools/nodes create dual-write divergence that is hard to reason about under retries.

**Exceptions:** Two narrowly-scoped eager writes are permitted:
- `crisis_check` writes `ClinicianAlert` + its `OutboxEntry` immediately for durability (PRD §5.4 requires durable alert intent BEFORE patient-facing message).
- `consent_gate` writes a consent audit event on denial (PRD §5.6 requires consent failures to be auditable; consent-denied path exits before `save_patient_context` runs).

No other node, tool, or agent writes to the domain DB.

**Implementation mechanism for tools:** Side-effecting tools (`set_goal`, `set_reminder`, `alert_clinician`) return `Command(update={"pending_effects": updated_dict, "messages": [ToolMessage(...)]})` to propagate state changes. `InjectedState` is read-only for tools — mutations to the injected dict are silently discarded by `ToolNode`. Read-only tools (`get_program_summary`, `get_adherence_summary`) return plain strings.

**Research ref:** `research-domain-model.md` §13.3 (Option A recommended), §14.6, `research-injectedstate-tool-mutation.md`

### AD-3: Invocation Modes — Proactive vs Reactive

**Decision:** Add `invocation_source: Literal["patient", "scheduler"] | None` to `PatientState`. Set explicitly at all call sites (chat endpoint, webhook handler, and scheduler). Agent nodes and prompt templates use this to select behavior.

**Why:** The graph has two fundamentally different semantic modes. Proactive outreach (scheduler) has no patient message to respond to. Reactive (patient action) does. Without this distinction, nodes must infer invocation type from message list inspection, which is implicit and fragile. Webhook is a transport mechanism, not a semantic mode — a patient message arriving via webhook is semantically a patient interaction and sets `invocation_source="patient"`.

### AD-4: Worker Topology — Separate Deployability

**Decision:** API and worker processes must be deployable separately (PRD §9.4). Local development runs both in one process via FastAPI lifespan background tasks. Production uses separate containers or process commands.

**Implementation:** `__main__.py` accepts `--mode api|worker|all` flag. `worker` mode runs scheduler + delivery workers without starting the HTTP server. `api` mode starts HTTP only. `all` mode (default for local dev) runs everything.

**Why:** PRD explicitly requires always-on background processing that survives scale-to-zero, replica churn, and staged rollouts (PRD §9.4 lines 255-256).

### AD-5: Consent Re-check at Delivery

**Decision:** The outbox delivery worker must re-verify consent before each delivery attempt. Consent checked at graph entry may be stale by the time the delivery worker processes the outbox entry.

**Why:** PRD §5.5 requires consent verification "before any outbound delivery attempt" and §3.1 calls out "same-invocation verification." If consent is revoked between generation and delivery, the worker must cancel the delivery (not retry) and emit an audit event.

**Implementation:** Delivery worker calls `ConsentService.check()` before transport **for patient-facing messages only** (`message_type="patient_message"`). Clinician alerts (`message_type="clinician_alert"`) skip consent verification — a clinician escalation must be delivered regardless of patient consent status (PRD §5.4: "preserve alert delivery through retries"). If patient-facing consent check fails: update outbox status to `cancelled`, emit `consent_check` + `delivery_cancelled` audit events.

### AD-6: Outbox vs DeliveryAttempt Separation

**Decision:** Two tables, not one:
- `outbox` — intent table. Written atomically with domain state. Tracks pending/delivering/delivered/cancelled/dead status.
- `delivery_attempts` — history table. One row per actual transport attempt. Captures receipt, error, latency.

**Why:** The outbox row is the durable intent. Delivery attempts are the execution history. Conflating them makes retry counting fragile and audit queries ambiguous.

**Research ref:** `research-scheduling-observability.md` §3.2–3.4

---

## Plan Invariants

These four properties must hold across all milestones. Any step that would violate them is a plan defect.

1. **`save_patient_context` is the only domain writer for patient state.** No graph node, tool, or agent writes to the domain DB except through `save_patient_context`. Two narrowly-scoped exceptions exist: (a) `crisis_check` writes `ClinicianAlert` + its `OutboxEntry` immediately for durability (PRD §5.4); (b) `consent_gate` writes a consent audit event on denial (PRD §5.6). Neither exception touches patient domain state (phase, goals, unanswered_count). `save_patient_context` contains zero LLM calls.

2. **All outbound messages and clinician alerts become outbox intents.** Every patient-facing message is written to the `outbox` table inside `save_patient_context`'s transaction, alongside the domain state change. Crisis clinician alerts are written to the `outbox` table by `crisis_check` immediately (the AD-2 durability exception). No message is delivered without an outbox intent. The delivery worker is the only transport path.

3. **Every graph invocation that enters the patient workflow acquires a patient-scoped lock.** The lock is acquired at the **call site** (chat endpoint, webhook handler, scheduler job handler) using `pg_advisory_lock` (session-level) on a dedicated connection with `isolation_level="AUTOCOMMIT"`, held for the duration of `graph.ainvoke()`. This serializes concurrent graph invocations for the same patient across all processes. The lock key is derived from `hashlib.sha256(patient_id.encode())` — NOT Python's `hash()`, which is salted per process and would defeat cross-process serialization. The consent-denied early exit (before `load_patient_context`) does not require the lock because it does not read or write patient domain state. SQLite tests are unaffected (global write lock provides equivalent serialization). The lock connection is idle (not idle-in-transaction) during LLM calls because AUTOCOMMIT prevents SQLAlchemy 2.x autobegin — it holds only the advisory lock, not an open transaction.

4. **Local dev and CI testing modes are explicit.** Unit tests: SQLite for app queries + `InMemorySaver` for LangGraph. Local dev: `docker-compose` provides PostgreSQL (both pools active). Integration tests: PostgreSQL service container in CI. The psycopg3 pool for LangGraph is only instantiated when `DATABASE_URL` points to PostgreSQL.

---

## Notation

- **Research ref** — `research-*.md` file in `.claude/plans/` containing the implementation pattern
- **PRD ref** — section in `prd.md` being satisfied
- **AC** — acceptance criteria number from PRD §10
- **FR/NFR** — functional/non-functional requirement from PRD §6/§7
- **Verify** — command(s) that must pass before moving to the next step
- **Migration note** — Any step that modifies ORM models must be followed by `uv run alembic revision --autogenerate -m "description"` + manual review before proceeding

---

## Milestone 1: Foundation and Quality Gate ✅ COMPLETE

**Objective:** Establish the clean project skeleton and verification baseline.
**PRD ref:** §11 M1; NFR-1, NFR-6, NFR-8
**AC satisfied:** AC-14 (CI green)
**Research refs:** `research-fastapi-sqlalchemy.md` §1–2, `research-testing-setup.md` §4, `RESEARCH_INDEX.md` §Dependency Versions

### Files to Create

```
pyproject.toml
uv.lock                              (generated)
pyrightconfig.json
Dockerfile
docker-compose.yml
.env.example
.github/workflows/ci.yml
alembic.ini
alembic/
  env.py
  versions/                           (empty)
src/
  health_coach/
    __init__.py
    __main__.py                       # uvicorn entry: `uv run python -m health_coach`
    main.py                           # FastAPI app with lifespan
    settings.py                       # Pydantic Settings
    persistence/
      __init__.py
      db.py                           # Engine, session factory, LangGraph pool
    observability/
      __init__.py
      logging.py                      # structlog configuration
    api/
      __init__.py
      routes/
        __init__.py
        health.py                     # /health/live, /health/ready
tests/
  __init__.py
  conftest.py                         # Shared fixtures
  unit/
    __init__.py
    test_settings.py
    test_health.py
```

### Steps

#### Step 1.1: Project scaffolding and dependency management

Create `pyproject.toml` with all dependencies from `RESEARCH_INDEX.md` §Dependency Versions. Use `[dependency-groups]` (PEP 735) for dev dependencies. Configure `[tool.pytest.ini_options]` with `asyncio_mode = "auto"` and `asyncio_default_fixture_loop_scope = "session"`. Configure `[tool.ruff.lint]` with rule set: `select = ["E", "W", "F", "I", "UP", "B", "C4", "SIM", "RET", "RUF", "N", "ANN", "ASYNC", "S", "PTH", "TC"]`, with relaxed rules for `tests/`. Set `[tool.hatch.build.targets.wheel]` with `packages = ["src/health_coach"]`.

Create `pyrightconfig.json` with strict mode on `src/health_coach/` and basic mode on `tests/`. The separate file takes precedence over pyproject.toml settings.

Run `uv sync` to generate `uv.lock`.

**Files:** `pyproject.toml`, `pyrightconfig.json`
**Research ref:** `research-testing-setup.md` §4.1–4.3
**Verify:** `uv sync --locked && uv run ruff check . && uv run pyright .`

#### Step 1.2: Pydantic Settings

Create `settings.py` with `pydantic-settings` `BaseSettings`. Define all configuration values with sensible defaults for local dev. Use `SecretStr` for API keys. Use `field_validator` to normalize database URL scheme to `postgresql+psycopg://`. Include settings for: database URL, LangGraph pool size, log level, log format (json/console), environment (dev/staging/prod), LLM provider config, quiet hours defaults.

**Files:** `src/health_coach/settings.py`
**Research ref:** `research-fastapi-sqlalchemy.md` §3
**Verify:** `uv run pyright src/health_coach/settings.py`

#### Step 1.3: Database engine and session factory

Create `persistence/db.py` with:
- `create_async_engine()` with `expire_on_commit=False` via session factory, `pool_pre_ping=True`
- `async_sessionmaker` bound to engine
- psycopg3 `AsyncConnectionPool` for LangGraph (separate pool), created with `open=False` (opened in lifespan)
- `get_session()` async dependency for FastAPI injection

Two pools: Pool A (SQLAlchemy) for app queries; Pool B (psycopg3 with `autocommit=True`, `prepare_threshold=0`, `row_factory=dict_row`) for LangGraph checkpointer.

**Local dev persistence modes (Plan Invariant #4):**
- Unit tests (`test-unit` CI job): SQLite for app queries + `InMemorySaver` for LangGraph. No psycopg3 pool instantiated.
- Local dev (`docker-compose up`): PostgreSQL from docker-compose, both pools active.
- Integration tests (`test-integration` CI job): PostgreSQL service container, both pools active.

The psycopg3 pool is only instantiated when `DATABASE_URL` starts with `postgresql`. When SQLite is detected, `get_langgraph_pool()` returns `None` and graph tests use `InMemorySaver`.

**Files:** `src/health_coach/persistence/__init__.py`, `src/health_coach/persistence/db.py`
**Research ref:** `research-fastapi-sqlalchemy.md` §2.3, §5
**Verify:** `uv run pyright src/health_coach/persistence/`

#### Step 1.4: structlog configuration

Create `observability/logging.py` with:
- `configure_logging(log_format: str)` — sets up structlog processor chain
- `merge_contextvars` as first processor
- `JSONRenderer` for prod, `ConsoleRenderer` for dev
- OTEL trace injection processor (extracts `trace_id`, `span_id` from current span)
- Standard bound fields: `service`, `environment`
- PHI-safe: no message content logging

**Files:** `src/health_coach/observability/__init__.py`, `src/health_coach/observability/logging.py`
**Research ref:** `research-scheduling-observability.md` §3.1–3.2
**Verify:** `uv run pyright src/health_coach/observability/`

#### Step 1.5: FastAPI app with lifespan

Create `main.py` with:
- `@asynccontextmanager` lifespan: opens LangGraph pool, configures logging, conditionally starts background workers (scheduler + delivery) based on mode, yields, then signals shutdown and closes pools
- Mount routers for health and (later) chat/webhooks
- `app.state` for shared resources

Create `__main__.py` supporting `--mode api|worker|all` (AD-4):
- `api` — starts uvicorn HTTP server only (production API containers)
- `worker` — runs scheduler + delivery workers without HTTP (production worker containers)
- `all` — runs everything in one process (local dev default)
- `uv run python -m health_coach` defaults to `all`

**Files:** `src/health_coach/main.py`, `src/health_coach/__main__.py`, `src/health_coach/__init__.py`
**Research ref:** `research-fastapi-sqlalchemy.md` §1
**Verify:** `uv run pyright src/health_coach/ && uv run python -c "from health_coach.main import app; print(app.title)"`

#### Step 1.6: Health endpoints

Create `/health/live` (always 200, no DB check) and `/health/ready` (checks SQLAlchemy pool connectivity + LangGraph pool connectivity). Return 503 on any readiness failure.

**Files:** `src/health_coach/api/__init__.py`, `src/health_coach/api/routes/__init__.py`, `src/health_coach/api/routes/health.py`
**Research ref:** `research-fastapi-sqlalchemy.md` §7
**Verify:** `uv run pyright src/health_coach/api/`

#### Step 1.7: Alembic setup

Run `alembic init -t async alembic`. Configure `alembic/env.py` for async with `async_engine_from_config` + `connection.run_sync(do_run_migrations)`. Use `NullPool` for migrations. Import `Base.metadata` from models module (empty initially). Set naming convention on Base.metadata.

**Files:** `alembic.ini`, `alembic/env.py`
**Research ref:** `research-fastapi-sqlalchemy.md` §4
**Verify:** `uv run alembic check` (should report no pending migrations since no models yet)

#### Step 1.7a: LangGraph persistence bootstrap

Add a bootstrap step that calls `checkpointer.setup()` and `store.setup()` (if Store is used). These create the required checkpoint/store tables in the LangGraph pool's database. This must run after Alembic migrations, not at app startup (to avoid racing with migrations or running in every replica).

Options: (a) a management command `uv run python -m health_coach.bootstrap`, (b) an Alembic `post_migration` hook, or (c) a step in the deployment pipeline. For local dev, run in the lifespan if tables don't exist.

Add a readiness check that verifies the checkpoint tables exist (not just pool connectivity).

**Files:** `src/health_coach/persistence/db.py` (add bootstrap function), `src/health_coach/api/routes/health.py` (update readiness check)
**Research ref:** `research-fastapi-sqlalchemy.md` §5 (bootstrap requirement)
**Verify:** `uv run python -m health_coach.bootstrap && uv run pytest tests/unit/test_health.py -v`

#### Step 1.8: Docker and docker-compose

Create multi-stage `Dockerfile`: stage 1 builds deps with uv + BuildKit cache mount, stage 2 copies venv + source, runs as non-root `appuser`. Create `docker-compose.yml` with PostgreSQL service + health-coach service + `.env.example`.

**Files:** `Dockerfile`, `docker-compose.yml`, `.env.example`
**Research ref:** `research-testing-setup.md` §4.5
**Verify:** `docker build -t health-coach .`

#### Step 1.9: GitHub Actions CI

Create CI workflow with 4 parallel jobs: `lint` (ruff check + format check), `typecheck` (pyright), `test-unit` (pytest on SQLite), `test-integration` (pytest with PostgreSQL service container, `pg_isready` health check). Use `astral-sh/setup-uv@v7` with `enable-cache: true`. Add a `docker-build` job.

**Files:** `.github/workflows/ci.yml`
**Research ref:** `research-testing-setup.md` §4.4
**Verify:** `git push` → CI green

#### Step 1.10: First tests

Create `tests/conftest.py` with session-scoped async engine fixture, function-scoped async session fixture with `join_transaction_mode="create_savepoint"`, and app client fixture using `httpx.AsyncClient` + `ASGITransport`.

Write tests for settings loading and health endpoints.

**Files:** `tests/conftest.py`, `tests/unit/test_settings.py`, `tests/unit/test_health.py`
**Research ref:** `research-testing-setup.md` §3.1, §3.3, §3.4
**Verify:** `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run pyright .`

### M1 Exit Gate
- `uv run ruff check . && uv run ruff format --check .` → clean
- `uv run pyright .` → clean (strict on src/, basic on tests/)
- `uv run pytest` → all pass
- `docker build -t health-coach .` → succeeds
- App boots locally: `uv run python -m health_coach` responds on `/health/live`
- CI green on push

---

## Milestone 2: Deterministic Domain Core ✅ COMPLETE

**Objective:** Lock down rules that must never drift into prompts. All policy logic is application-owned, independently testable, and has zero LLM dependency.
**PRD ref:** §11 M2; FR-2, FR-3, FR-10; NFR-1, NFR-4, NFR-5
**AC satisfied:** AC-1 (consent gate), AC-5 (phase routing), AC-11 (idempotency)
**Research refs:** `research-domain-model.md` §3–10

### Files to Create

```
src/health_coach/
  domain/
    __init__.py
    phases.py                          # PatientPhase StrEnum
    phase_machine.py                   # transition() adjacency map
    consent.py                         # ConsentService, ConsentResult
    safety_types.py                    # SafetyDecision, CrisisLevel, ClassifierOutput
    errors.py                          # PhaseTransitionError, ConsentDeniedError
    scheduling.py                      # Quiet hours, timezone, jitter, cadence config
  persistence/
    models.py                          # All ORM models (Base, Patient, AuditEvent, etc.)
    repositories/
      __init__.py
      base.py                          # BaseRepository[ModelT]
      patient.py                       # PatientRepository
      audit.py                         # AuditRepository (append-only)
    schemas/
      __init__.py
      patient.py                       # PatientCreate, PatientRead
      audit.py                         # AuditEventRead
      goal.py                          # GoalCreate, GoalRead, ExtractedGoal
tests/
  unit/
    test_phases.py
    test_phase_machine.py
    test_consent.py
    test_safety_types.py
    test_scheduling.py
    test_repositories.py
```

### Files to Change

```
src/health_coach/persistence/db.py    # Import Base for metadata
alembic/env.py                         # Import models for autogenerate
```

### Steps

#### Step 2.1: Domain enums and errors

Create `domain/phases.py` with `PatientPhase` as `StrEnum`. Create `domain/errors.py` with `PhaseTransitionError`, `ConsentDeniedError`. Create `domain/safety_types.py` with `SafetyDecision` enum (SAFE, CLINICAL_BOUNDARY, CRISIS, JAILBREAK), `CrisisLevel` enum (NONE, POSSIBLE, EXPLICIT), and `ClassifierOutput` Pydantic model.

**Files:** `domain/phases.py`, `domain/errors.py`, `domain/safety_types.py`
**Research ref:** `research-domain-model.md` §3.1, `research-safety-llm.md` §2
**Verify:** `uv run pyright src/health_coach/domain/ && uv run pytest tests/unit/test_phases.py tests/unit/test_safety_types.py`

#### Step 2.2: Phase state machine

Create `domain/phase_machine.py` with `transition(current: PatientPhase, event: str) -> PatientPhase`. Implement as a `_TRANSITIONS` adjacency dict mapping `(PatientPhase, event_name)` to `PatientPhase`. Invalid transitions raise `PhaseTransitionError`. No I/O — pure function, fully testable.

Events: `onboarding_initiated`, `goal_confirmed`, `no_response_timeout`, `unanswered_outreach` (ACTIVE → RE_ENGAGING on first miss), `missed_third_message` (RE_ENGAGING → DORMANT on third miss), `patient_disengaged`, `patient_responded`, `patient_returned`.

Write exhaustive tests: all valid transitions, all invalid transitions, boundary cases.

**Files:** `domain/phase_machine.py`, `tests/unit/test_phase_machine.py`
**Research ref:** `research-domain-model.md` §3.2
**Verify:** `uv run pytest tests/unit/test_phase_machine.py -v`

#### Step 2.3: Consent service contract

Create `domain/consent.py` with:
- `ConsentResult` dataclass: `logged_in: bool`, `consented_to_outreach: bool`, `reason: str`, `checked_at: datetime`. Property `allowed -> bool` returns `logged_in and consented_to_outreach`. PRD §5.5 requires both conditions verified per interaction.
- `ConsentService` protocol/ABC with `async check(patient_id: str, tenant_id: str) -> ConsentResult`
- Fail-safe: any exception during check → return denied result
- `FakeConsentService` for testing (always consented or always denied, configurable)

**Files:** `domain/consent.py`, `tests/unit/test_consent.py`
**Research ref:** `research-domain-model.md` §4
**Verify:** `uv run pytest tests/unit/test_consent.py -v`

#### Step 2.4: Scheduling domain logic

Create `domain/scheduling.py` with:
- `calculate_send_time(base_time, patient_tz, quiet_start, quiet_end) -> datetime` — shifts to next valid window if in quiet hours
- `add_jitter(scheduled_at, max_jitter_minutes) -> datetime` — uniform random 0–30 min
- `CoachConfig` Pydantic model: follow-up cadence (day 2/5/7), backoff sequence, quiet hours, max messages per day, tone presets
- All times use `zoneinfo.ZoneInfo` and return UTC `datetime`

**Files:** `domain/scheduling.py`, `tests/unit/test_scheduling.py`
**Research ref:** `research-scheduling-observability.md` §2.6, `research-domain-model.md` §12
**Verify:** `uv run pytest tests/unit/test_scheduling.py -v`

#### Step 2.5: ORM models

Create `persistence/models.py` with `Base` (naming convention), and all 16 entities from `FINAL_CONSOLIDATED_RESEARCH.md` §18.1:

Core entities (implement now):
- `Patient` — UUID PK, `tenant_id`, `external_patient_id`, `phase` (String(20)), `timezone`, `unanswered_count`, `last_outreach_at` (TIMESTAMPTZ, nullable — set by `save_patient_context` when creating scheduler-initiated outreach outbox entry), `last_patient_response_at` (TIMESTAMPTZ, nullable — set when `invocation_source="patient"`)
- `PatientGoal` — FK to patient, `goal_text`, `raw_patient_text`, `structured_goal` (JSONB), `confirmed_at`, `idempotency_key` (unique) — key format: `{patient_id}:goal:{hashlib.sha256(goal_text.encode()).hexdigest()[:16]}`. Uses `hashlib` (not Python's `hash()`) because `hash()` is salted per process via `PYTHONHASHSEED` and produces different values across API and worker processes. No time component — the key must be replay-stable (identical across graph retries and across processes). Prevents duplicate goal creation on graph replay.
- `PatientConsentSnapshot` — immutable, FK to patient, `consented`, `reason`, `checked_at`
- `AuditEvent` — NO FK to patient (survives deletion), `patient_id` as UUID column, `event_type`, `outcome`, `metadata` (JSONB), `created_at`. Relationship from Patient uses `write_only=True`
- `ScheduledJob` — FK to patient, `job_type`, `idempotency_key` (unique), `status`, `scheduled_at` (TIMESTAMPTZ), `attempts`, `max_attempts`, `metadata` (JSONB)
- `OutboxEntry` — outbound message intent table (AD-6): `delivery_key` (unique), `message_type` (`"patient_message"` or `"clinician_alert"` — delivery worker uses this to determine consent re-check scope), `priority`, `channel`, `payload` (JSONB with `message_ref_id`, never raw text), `status` (pending/delivering/delivered/cancelled/dead)
- `DeliveryAttempt` — transport execution history: FK to outbox entry, `attempt_number`, `outcome`, `delivery_receipt` (JSONB), `error`, `latency_ms`
- `ClinicianAlert` — `patient_id`, `reason`, `priority`, `idempotency_key`, `acknowledged_at`
- `SafetyDecision` (DB record) — per-message safety classifier outcome
- `ConversationThread` — maps to LangGraph `thread_id`
- `Message` — queryable message record (separate from checkpoint)
- `ToolInvocation` — tool call audit record
- `ProcessedEvent` — inbound event deduplication
- `PromptVersion` — versioned prompt templates

Stub entities (create with minimal columns, flesh out in later milestones):
- `AdherenceSnapshot`, `ProgramSnapshot`, `ReminderPreference`

All models:
- `lazy="raise"` on all relationships
- `write_only=True` on append-only collections
- `tenant_id` on every table
- UUID primary keys via `mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)`
- `created_at` / `updated_at` with `func.now()` / `onupdate=func.now()`

**Files:** `persistence/models.py`
**Research ref:** `research-domain-model.md` §6–7, `research-fastapi-sqlalchemy.md` §2
**Verify:** `uv run pyright src/health_coach/persistence/models.py`

#### Step 2.6: Initial Alembic migration

Generate the first migration with `alembic revision --autogenerate -m "initial_schema"`. Manually review. Add `REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM health_coach_app;` and the `BEFORE UPDATE OR DELETE` trigger as raw SQL ops in the migration (PostgreSQL-only, guarded by dialect check). Add partial indices for `scheduled_jobs` and `delivery_attempts`.

**Files:** `alembic/versions/001_initial_schema.py`
**Research ref:** `research-scheduling-observability.md` §3.3, `research-domain-model.md` §5
**Verify:** `uv run alembic upgrade head && uv run alembic downgrade base && uv run alembic upgrade head` (roundtrip)

#### Step 2.7: Repositories

Create `persistence/repositories/base.py` with `BaseRepository[ModelT]` generic: `create()`, `get_by_id()`, `list_by()`, `update()`. Use `session.flush()` in create (not commit) — caller owns transaction. Create `PatientRepository` and `AuditRepository` (create-only, no update/delete).

**Files:** `persistence/repositories/base.py`, `persistence/repositories/patient.py`, `persistence/repositories/audit.py`
**Research ref:** `research-domain-model.md` §8
**Verify:** `uv run pyright src/health_coach/persistence/repositories/`

#### Step 2.8: Pydantic schemas

Create `persistence/schemas/patient.py` (`PatientCreate`, `PatientRead`), `persistence/schemas/goal.py` (`GoalCreate`, `GoalRead` — excludes `raw_patient_text`, `ExtractedGoal` — the structured output model for LLM extraction), `persistence/schemas/audit.py` (`AuditEventRead`). All Read schemas use `ConfigDict(from_attributes=True)`.

**Files:** `persistence/schemas/*.py`
**Research ref:** `research-domain-model.md` §9
**Verify:** `uv run pyright src/health_coach/persistence/schemas/`

#### Step 2.9: Domain tests

Write comprehensive tests for:
- Phase machine: all valid transitions, all invalid transitions (exhaustive enumeration tests; `RuleBasedStateMachine` expansion deferred to M7 Step 7.3)
- Consent: service contract, fail-safe behavior, snapshot creation
- Scheduling: quiet hours enforcement, timezone conversion, DST edge cases (use `time-machine`)
- Repositories: CRUD operations, audit append-only behavior, idempotency key conflicts

**Files:** `tests/unit/test_phase_machine.py`, `tests/unit/test_consent.py`, `tests/unit/test_scheduling.py`, `tests/unit/test_repositories.py`
**Research ref:** `research-testing-setup.md` §3.3, §3.6, §3.7
**Verify:** `uv run pytest tests/unit/ -v && uv run pyright . && uv run ruff check .`

### M2 Exit Gate
- All phase transitions testable without LLM
- Consent gate independently verifiable
- Audit events append-only, enforced at DB level
- Migration roundtrips cleanly
- Full CI green
- **PRD gate (§12):** MedBridge Go interface spec must be agreed before M3 exit. This means: consent API shape, webhook payload schema, auth mechanism, and patient event taxonomy. The implementation uses `FakeConsentService` / `FakeMedBridgeClient` through M5 — but the Protocol/ABC contracts defined in M2 must match the real API shape. Begin interface discussions during M2.

---

## Milestone 3: Graph Orchestration Shell

**Objective:** Prove the LangGraph workflow shape with deterministic routing, fake model/tool wiring, and checkpointed thread flow — before adding live integrations.
**PRD ref:** §11 M3; FR-2, FR-9; NFR-2
**AC satisfied:** AC-5 (phase routing)
**Research refs:** `research.md` §1–8

### Files to Create

```
src/health_coach/
  agent/
    __init__.py
    state.py                           # PatientState TypedDict
    context.py                         # CoachContext dataclass
    graph.py                           # StateGraph construction + compilation
    nodes/
      __init__.py
      consent.py                       # consent_gate node
      context.py                       # load_patient_context, save_patient_context
      router.py                        # phase_router (pure function)
      onboarding.py                    # onboarding_agent node (stub)
      active.py                        # active_agent node (stub)
      re_engaging.py                   # reengagement_agent node (stub)
      dormant.py                       # dormant_node
      pending.py                       # pending_node
      crisis_check.py                  # input-side crisis pre-check (stub — real impl in M4)
      history.py                       # manage_history node (conditional message trimming)
      safety.py                        # safety_gate node (stub)
      retry.py                         # retry_generation node (stub — real impl in M4)
      fallback.py                      # fallback_response node (deterministic safe message)
    tools/
      __init__.py
      goal.py                          # set_goal, get_program_summary
      reminder.py                      # set_reminder
      adherence.py                     # get_adherence_summary
      clinician.py                     # alert_clinician
    prompts/
      __init__.py
      system.py                        # System prompt templates per phase
  persistence/
    locking.py                         # patient_advisory_lock context manager (Plan Invariant #3)
  integrations/
    __init__.py
    model_gateway.py                   # ModelGateway: LLM factory + fallback
tests/
  integration/
    __init__.py
    test_graph_routing.py
    test_graph_thread.py
    test_locking.py                    # Advisory lock serialization tests (PostgreSQL only)
  unit/
    test_state.py
    test_tools.py
```

### Steps

#### Step 3.1: LangGraph state definition

Create `agent/state.py` with `PatientState(TypedDict)`:
- `patient_id: str`
- `tenant_id: str`
- `consent_verified: bool`
- `phase: str` (string, not enum — LangGraph serialization)
- `messages: Annotated[list[BaseMessage], add_messages]`
- `goal: str | None`
- `unanswered_count: int`
- `safety_decision: str | None`
- `crisis_detected: bool`
- `outbound_message: str | None`
- `delivery_status: str | None`
- `invocation_source: str | None` — `"patient"` or `"scheduler"` (AD-3). Set at invoke time, never by the LLM. Patient messages arriving via webhook use `"patient"` — webhook is a transport mechanism, not a semantic mode.
- `pending_effects: dict | None` — accumulated side effects from tools and nodes (AD-2). Structure: `{"goal": {...} | None, "alerts": [...], "phase_event": str | None, "scheduled_jobs": [...], "safety_decisions": [...], "outbox_entries": [...], "audit_events": [...], "cancel_pending_jobs": bool}`. Populated by tool executions (via `Command(update={...})`, not InjectedState mutation) and node logic, flushed atomically by `save_patient_context`.

Create `agent/context.py` with `CoachContext` dataclass for `context_schema`: `patient_id`, `tenant_id`, `db_session_factory`, settings references.

**Files:** `agent/state.py`, `agent/context.py`
**Research ref:** `research.md` §1 (State definition), §2 (Runtime and context_schema)
**Verify:** `uv run pyright src/health_coach/agent/`

#### Step 3.2: Tool definitions

Create tool functions using `@tool` decorator from `langchain_core.tools`. Two patterns:

**Read-only tools** (`get_program_summary`, `get_adherence_summary`): return plain `str`. `ToolNode` wraps the return in a `ToolMessage` automatically. May use `InjectedState` to read state.

**Side-effecting tools** (`set_goal`, `set_reminder`, `alert_clinician`): return `Command(update={"pending_effects": updated_dict, "messages": [ToolMessage(content=..., tool_call_id=tool_call_id)]})`. Use `InjectedState` to READ current `pending_effects`, build an updated dict via immutable merge (`{**current, "goal": ...}`), and return via `Command.update`. `InjectedToolCallId` injects the tool_call_id without exposing it to the LLM schema. **Critical:** `InjectedState` is read-only — mutations to the injected dict are silently discarded by `ToolNode`. State updates only propagate via `Command`.

All tools have:
- Flat parameter schema with explicit descriptions
- Stub implementations that return realistic mock data (M3); real implementations in M4+
- Idempotency key generation for side-effecting tools

Tools: `set_goal`, `set_reminder`, `get_program_summary`, `get_adherence_summary`, `alert_clinician`.

**Files:** `agent/tools/*.py`
**Research ref:** `research.md` §6 (ToolNode and tools_condition), `research-domain-model.md` §10.3, `research-injectedstate-tool-mutation.md`
**Verify:** `uv run pytest tests/unit/test_tools.py -v`

#### Step 3.2a: Patient advisory lock utility

Create `persistence/locking.py` with `patient_advisory_lock` async context manager (Plan Invariant #3):
- Uses `pg_advisory_lock(patient_id_hash)` (session-level, NOT transaction-level) on a dedicated connection from Pool A
- The connection uses `isolation_level="AUTOCOMMIT"` to prevent SQLAlchemy 2.x autobegin — without this, `engine.connect()` + `execute()` implicitly starts a transaction, making the connection idle-in-transaction during 5-30s LLM calls (operational risk: `idle_in_transaction_session_timeout` could kill the connection and silently release the lock)
- With AUTOCOMMIT, the connection holds only the advisory lock with no open transaction, so it is truly idle (not idle-in-transaction) during LLM calls
- Released via `pg_advisory_unlock(patient_id_hash)` in `finally` block
- SQLite: no-op `yield` (SQLite's global write lock provides equivalent serialization)
- Call sites (chat endpoint, webhook handler, scheduler job handler) wrap `graph.ainvoke()` in this context manager
- **Hash determinism:** Uses `hashlib` (not Python's `hash()`) because `hash()` is salted per process via `PYTHONHASHSEED` (randomized by default since Python 3.3). Two processes (API server + worker) would compute different lock keys for the same patient, defeating cross-process serialization entirely.

```python
import hashlib

@asynccontextmanager
async def patient_advisory_lock(engine: AsyncEngine, patient_id: str):
    if "sqlite" in str(engine.url):
        yield; return
    lock_key = int.from_bytes(
        hashlib.sha256(patient_id.encode()).digest()[:4], "big"
    ) & 0x7FFFFFFF  # positive 32-bit int, deterministic across processes
    async with engine.connect() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": lock_key})
        try:
            yield
        finally:
            await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
```

**Pool sizing note:** Each concurrent patient invocation holds one extra connection from Pool A for the lock duration (typically 5-30s during LLM calls). Pool A must be sized to accommodate this: `max_concurrent_patients + normal_query_connections`. Default `pool_size=20, max_overflow=10` should handle ~20 concurrent patients comfortably.

**Files:** `persistence/locking.py`
**Research ref:** `research-advisory-lock-lifecycle.md`
**Verify:** `uv run pyright src/health_coach/persistence/locking.py && uv run pytest tests/integration/test_locking.py -v`

#### Step 3.3: Graph nodes (stubs)

Create all graph nodes as async functions accepting `(state: PatientState, runtime: Runtime[CoachContext]) -> dict`:

- `consent_gate` — calls `ConsentService.check()`, sets `consent_verified`, emits audit event. If denied, writes consent audit event directly to the DB (Plan Invariant #1 exception b — consent-denied path exits before `save_patient_context` runs) and returns `Command(goto=END)`. This is the only graph node that writes to the DB without using the `save_patient_context` write path (the advisory lock IS held at the call site, but consent_gate does not participate in the intent accumulation pattern because the consent-denied path exits before `save_patient_context` runs).
- `load_patient_context` — reads patient from domain DB, populates state fields (`phase`, `unanswered_count`, `last_outreach_at`, `last_patient_response_at`). Initializes `pending_effects` to empty. **Note:** The advisory lock is NOT acquired here — it is acquired at the call site (chat endpoint, webhook handler, scheduler job handler) before `graph.ainvoke()` is called (Plan Invariant #3).
- `crisis_check` — stub in M3: always returns no crisis. Real implementation in M4. Skipped when `invocation_source != "patient"` (no patient message to check on proactive outreach). Runs AFTER `load_patient_context` so the advisory lock is held and patient context is available.
- `manage_history` — conditional node between `crisis_check` and `phase_router`. Checks message count in state. If below threshold (e.g., 20), passes through (no-op). If above, calls LLM for summary, stores summary message, and returns `RemoveMessage` updates to trim older messages (AD-1). This keeps LLM summarization out of `save_patient_context` (Plan Invariant #1). If the summarization LLM call fails, no domain state has been written yet — safe to retry.
- `phase_router` — pure function, reads `state["phase"]`, returns node name string
- `onboarding_agent` — stub: returns a placeholder message (will be replaced with real LLM in M4)
- `active_agent` — stub. In M5: on scheduler-initiated invocations, checks if patient responded since `last_outreach_at`. If not, increments `unanswered_count` via `pending_effects` and triggers `unanswered_outreach` phase event.
- `reengagement_agent` — stub
- `dormant_node` — logs interaction, returns no outbound message
- `pending_node` — initiates onboarding: sets `pending_effects.phase_event = "onboarding_initiated"`, creates the initial welcome message for delivery, creates an onboarding timeout job (72h) in `pending_effects.scheduled_jobs`, and records `SafetyDecision(SAFE, source="template")` in `pending_effects.safety_decisions` for the template welcome message (FR-4: every outbound message must have a recorded safety decision). This is the PENDING → ONBOARDING trigger point. **Template safety exemption policy:** Template messages (hardcoded strings written by developers) are exempt from the output-side safety classifier. They are pre-approved, static, not subject to LLM drift or prompt injection. A `SafetyDecision` is still recorded for audit completeness, but with `source="template"` to distinguish from classifier-evaluated decisions. This is why the PENDING path routes directly to `save_patient_context` without passing through `safety_gate`.
- `fallback_response` — returns deterministic safe message from `domain/safety.py`
- `safety_gate` — stub: always returns SAFE (will be replaced in M4)
- `save_patient_context` — flushes all accumulated `pending_effects` to the domain DB atomically (AD-2): persists goals (with idempotency keys), creates alert records, applies phase transitions, writes outbox entries, creates scheduled jobs, records safety decisions, and updates `last_outreach_at` when creating outreach outbox entries. This is the ONLY node (besides `crisis_check` and `consent_gate`) that writes to the domain DB. Contains zero LLM calls (Plan Invariant #1). **Phase transition replay safety:** if `transition()` raises `PhaseTransitionError`, check whether `current_phase == expected_target_phase`. If match → already applied on a previous attempt, skip. If mismatch → real invalid transition, propagate error. **Pending job cancellation:** when a `phase_event` is present, cancels all pending `scheduled_jobs` for the patient (SET status = 'cancelled') in the same transaction. The new phase's logic creates new jobs as appropriate. This prevents stale jobs from firing after phase transitions. **Unanswered count reset:** resets `unanswered_count` to 0 when `invocation_source="patient"` (patient responded). Unanswered count increment is handled by agent nodes (active_agent, reengagement_agent) based on no-response detection, NOT by save_patient_context on outreach send.

**Files:** `agent/nodes/*.py`
**Research ref:** `research.md` §2 (Runtime access), §3 (Command for routing)
**Verify:** `uv run pyright src/health_coach/agent/nodes/`

#### Step 3.4: Graph construction

Create `agent/graph.py` with `build_graph()` function.

**Full graph topology (including tool execution loops):**

```
START → consent_gate
  ├── [consent denied] → END (consent_gate writes audit event directly — Invariant #1 exception b)
  └── [consent granted] → load_patient_context → crisis_check
        │                                           (skipped when invocation_source="scheduler")
        ├── [EXPLICIT crisis] → crisis_response → save_patient_context → END
        │     (crisis_check writes ClinicianAlert + OutboxEntry immediately — Invariant #1 exception a.
        │      crisis_response sets 988 message in state. save_patient_context writes the
        │      patient-facing message outbox entry via normal atomic flush.)
        └── [no crisis / POSSIBLE] → manage_history → phase_router
              │
              ├── PENDING → pending_node → save_patient_context → END
              │     (pending_node initiates onboarding: triggers phase transition
              │      PENDING→ONBOARDING via pending_effects, creates onboarding
              │      timeout job (72h), records SafetyDecision(SAFE, source="template")
              │      for welcome message.)
              │
              ├── ONBOARDING → onboarding_agent → tools_condition
              │     ├── [tool_calls present] → tool_node → onboarding_agent (loop back)
              │     └── [no tool_calls] → safety_gate → safety_route
              │           ├── [SAFE] → save_patient_context → END
              │           ├── [CLINICAL_BOUNDARY] → retry_generation → safety_gate (max 1 retry)
              │           ├── [CRISIS/JAILBREAK] → fallback_response → save_patient_context → END
              │           └── [retry still blocked] → fallback_response → save_patient_context → END
              │
              ├── ACTIVE → active_agent → tools_condition
              │     ├── [tool_calls] → tool_node → active_agent (loop back)
              │     └── [no tool_calls] → safety_gate → (same safety_route as above)
              │
              ├── RE_ENGAGING → reengagement_agent → tools_condition
              │     ├── [tool_calls] → tool_node → reengagement_agent (loop back)
              │     └── [no tool_calls] → safety_gate → (same safety_route as above)
              │
              └── DORMANT → dormant_node → save_patient_context → END
```

**Note:** `deliver_message` has been removed as a separate node. The outbound message is set in state by the agent or fallback node. `save_patient_context` writes it to the outbox atomically with all other domain state (Plan Invariant #2). This eliminates the ambiguity of a "delivery" node writing to the DB independently.

**Key structural notes:**
- **Topology ordering:** `consent_gate` → `load_patient_context` → `crisis_check`. This ensures: (1) the advisory lock (held at call site) covers crisis_check and all downstream nodes; (2) `save_patient_context` can function on the crisis path because patient context was loaded; (3) the PRD requirement that crisis pre-check happens before "main generation" (phase-specific agents) is satisfied. The only path that exits without load_patient_context running is consent-denied, which does not touch patient domain state.
- Each phase-specific agent node (`onboarding_agent`, `active_agent`, `reengagement_agent`) has its own tool loop via `tools_condition` → `tool_node` → loop back. The `tool_node` is a shared `ToolNode(tools)` instance — the same node can serve all agents since tool selection depends on `llm.bind_tools()` per agent. Side-effecting tools return `Command(update={...})` to propagate state changes (AD-2).
- `crisis_check` runs only when `invocation_source == "patient"` (proactive outreach has no patient message to check). If `invocation_source == "scheduler"`, skip directly to `manage_history`.
- `pending_node` is NOT a no-op. It is the PENDING→ONBOARDING initiation point. When triggered, it sets `pending_effects.phase_event = "onboarding_initiated"` which `save_patient_context` will persist, creates a 72-hour onboarding timeout job, and records a template-source safety decision for the welcome message. The NEXT graph invocation (immediately chained or via scheduler) enters the ONBOARDING phase.
- `safety_gate` → `retry_generation` → `safety_gate` uses a state counter (`safety_retry_count`) to enforce exactly one retry. No back-edge to the agent — the retry node appends an augmented `HumanMessage` and re-invokes the same LLM call with tighter constraints.

Use `StateGraph(PatientState, context_schema=CoachContext)`. Use `add_conditional_edges` with `# type: ignore[arg-type]`. Export `compile_graph(checkpointer, store)` function.

**Files:** `agent/graph.py`
**Research ref:** `research.md` §1 (StateGraph), §4 (Compilation), §6 (ToolNode + tools_condition)
**Verify:** `uv run pyright src/health_coach/agent/graph.py`

#### Step 3.5: ModelGateway

Create `integrations/model_gateway.py` with:
- `ModelGateway` class: constructs `ChatAnthropic` primary, `ChatOpenAI` fallback via `.with_fallbacks()`
- `max_retries=0` on both (built-in retries prevent fallback triggering)
- **BAA gating:** Fallback to OpenAI MUST NOT receive PHI until the OpenAI BAA is signed (PRD §5.2). ModelGateway checks `fallback_phi_approved: bool` setting at construction time. If `False`, the fallback passed to `.with_fallbacks()` is a `FakeChatModel` returning a deterministic safe message — the "fallback" IS the safe message, not OpenAI. This preserves the `with_fallbacks()` pattern without runtime conditional routing (which `with_fallbacks()` does not support). Log the fallback trigger for operator visibility.
- `max_tokens` explicitly set on `ChatAnthropic`
- `get_chat_model(purpose: str)` — returns configured LLM for "coach", "classifier", "extractor"
- `FakeModelGateway` for testing — returns `GenericFakeChatModel` (note: cannot use `bind_tools()` — tests must construct `AIMessage(tool_calls=[...])` directly)

**Files:** `integrations/model_gateway.py`
**Research ref:** `research-safety-llm.md` §6–8, `research-testing-setup.md` §3.2
**Verify:** `uv run pyright src/health_coach/integrations/`

#### Step 3.6: Graph integration tests

Write tests using `InMemorySaver` + `InMemoryStore`:
- Test 1: Consent denied → graph exits before any LLM call, audit event emitted
- Test 2: Each phase routes to correct node (test all 5 phases)
- Test 3: Thread persistence — invoke graph, get checkpoint, resume with same `thread_id`
- Test 4: Tool call routing with `ToolNode` + `tools_condition` (use manually constructed `AIMessage`). Side-effecting tool returns `Command(update={...})` and state is correctly updated.
- Test 5: Advisory lock serialization (PostgreSQL only): two concurrent `graph.ainvoke()` calls for the same patient_id execute serially, not interleaved

**Files:** `tests/integration/test_graph_routing.py`, `tests/integration/test_graph_thread.py`, `tests/integration/test_locking.py`
**Research ref:** `research-testing-setup.md` §3.2
**Verify:** `uv run pytest tests/integration/ -v`

### M3 Exit Gate
- Graph compiles and executes with fake models
- All 5 phases route correctly, including PENDING → ONBOARDING initiation with template safety decision
- Tool execution loops work: agent → tools_condition → ToolNode → agent for each phase. Side-effecting tools return `Command(update={...})` and `pending_effects` is correctly updated in state.
- Consent gate blocks execution when denied, consent audit event written directly
- Thread state persists across invocations (same `thread_id` resumes)
- Proactive (`invocation_source="scheduler"`) and reactive (`invocation_source="patient"`) paths both work. No third "webhook" mode.
- Intent accumulation: tools return `Command(update={"pending_effects": ...})`, `save_patient_context` flushes to DB (including scheduled jobs, safety decisions, and outbox entries)
- Patient-level advisory lock acquired at call site (not in load_patient_context), verified in integration tests (PostgreSQL)
- Phase transition replay safety verified: re-applying an already-committed transition is a no-op
- `manage_history` node runs without error (no-op when below threshold)
- Onboarding timeout job created via `pending_effects.scheduled_jobs` during PENDING → ONBOARDING
- Pending job cancellation: when `pending_effects.phase_event` is present, `save_patient_context` cancels existing pending jobs for the patient
- Full CI green
- **Note:** Graph topology is now final — crisis_check is positioned after load_patient_context. M4 replaces stub implementations but does not change graph structure.
- **Migration note:** If M3 adds/modifies any ORM models (e.g., adding `ConversationThread` fields), generate incremental migration with `uv run alembic revision --autogenerate -m "m3_graph_schema"` and review.

---

## Milestone 4: Safe Onboarding ✅ COMPLETE

**Objective:** Deliver the first end-to-end patient value path: a patient can complete onboarding safely with auditable outcomes.
**PRD ref:** §11 M4; FR-1, FR-4, FR-5, FR-9, FR-10; NFR-3, NFR-4
**AC satisfied:** AC-1, AC-2, AC-3, AC-4, AC-6, AC-7
**Research refs:** `research-safety-llm.md` §2–5, `research.md` §6, `research-domain-model.md` §4

### Files to Create

```
src/health_coach/
  agent/
    prompts/
      onboarding.py                    # Onboarding system prompt template
      safety.py                        # Safety classifier prompt
    nodes/
      crisis_check.py                  # Input-side crisis pre-check
  domain/
    safety.py                          # Safety policy rules, fallback templates
tests/
  unit/
    test_safety_classifier.py
    test_goal_extraction.py
    test_crisis_check.py
  integration/
    test_onboarding_flow.py
  safety/
    __init__.py
    test_clinical_boundary.py
    test_crisis_detection.py
```

### Files to Change

```
src/health_coach/agent/graph.py           # Wire crisis check, real safety gate, retry logic
src/health_coach/agent/nodes/onboarding.py # Replace stub with real LLM onboarding agent
src/health_coach/agent/nodes/safety.py     # Replace stub with real safety classifier
src/health_coach/agent/tools/goal.py       # Real implementation using repository
```

### Steps

#### Step 4.1: System prompts

Create `agent/prompts/onboarding.py` with the onboarding system prompt. The prompt must:
- Identify the coach as a non-clinical accountability partner
- Reference the patient's assigned exercises (from context)
- Elicit an open-ended exercise goal
- Never provide clinical advice
- Include tone scaling variable (`warm_and_encouraging` for onboarding)

Create `agent/prompts/safety.py` with the safety classifier prompt. The prompt instructs the classifier to output a `ClassifierOutput` with `SafetyDecision`, `CrisisLevel`, `confidence`, and `reasoning`.

**Files:** `agent/prompts/onboarding.py`, `agent/prompts/safety.py`
**Research ref:** `research-safety-llm.md` §2 (classifier prompt structure)
**Verify:** `uv run pyright src/health_coach/agent/prompts/`

#### Step 4.2: Input-side crisis pre-check

Replace `agent/nodes/crisis_check.py` stub with real implementation:
- Runs on patient input BEFORE main generation (skipped when `invocation_source != "patient"`)
- Uses safety classifier model (Haiku 4.5) with a lightweight crisis-focused prompt
- Maps to `CrisisLevel.NONE`, `POSSIBLE`, or `EXPLICIT`
- `EXPLICIT` → writes durable `ClinicianAlert` AND its `OutboxEntry` (`message_type="clinician_alert"`, high priority) **immediately** in the same session (Plan Invariant #1 exception a — crisis alerts must survive crashes and be deliverable). Also writes audit event. Sets `crisis_detected=True` in state, routes to crisis response (safe 988 message, NO retry). The `OutboxEntry` ensures the alert enters the delivery worker's retryable delivery path (PRD §5.4: "preserve alert delivery through retries"). The delivery worker does NOT apply consent re-check to clinician alerts (AD-5).
- `POSSIBLE` → creates routine alert via `pending_effects.alerts`, continues normal flow

Graph topology for crisis_check is already wired in M3 (positioned after load_patient_context). No graph structure changes needed — only the node implementation changes.

**Files:** `agent/nodes/crisis_check.py`, `agent/graph.py` (modified)
**Research ref:** `research-safety-llm.md` §3 (input crisis pre-check)
**Verify:** `uv run pytest tests/unit/test_crisis_check.py -v`

#### Step 4.3: Real onboarding agent node

Replace `agent/nodes/onboarding.py` stub with real implementation:
- Constructs system prompt with patient context (first name, assigned exercises)
- Binds tools: `set_goal`, `get_program_summary` via `llm.bind_tools(tools, parallel_tool_calls=False)`
- Invokes LLM with `ChatAnthropic` via `ModelGateway`
- Handles multi-turn: welcome → elicit goal → extract structured goal → confirm
- Goal extraction uses `with_structured_output(ExtractedGoal, method="json_schema", strict=True)`
- On `set_goal` tool call: the tool validates the goal structure and returns success, accumulating the goal data in `pending_effects.goal` and setting `pending_effects.phase_event = "goal_confirmed"` (AD-2). The actual DB write happens in `save_patient_context`.
- Prompt template uses `invocation_source` (AD-3) to distinguish initial welcome (proactive) from responding to patient messages (reactive)

**Files:** `agent/nodes/onboarding.py` (replaced)
**Research ref:** `research.md` §6 (ToolNode), `research-domain-model.md` §4 (goal extraction)
**Verify:** `uv run pytest tests/integration/test_onboarding_flow.py -v`

#### Step 4.4: Output-side safety gate

Replace `agent/nodes/safety.py` stub:
- Takes outbound message from state
- Calls safety classifier (Haiku 4.5) with `ClassifierOutput` structured output
- Decision routing:
  - `SAFE` → proceed to delivery
  - `CLINICAL_BOUNDARY` → retry once with augmented `HumanMessage` appended, if still blocked → deterministic fallback
  - `CRISIS` → never retry, deliver safe 988 message
  - `JAILBREAK` → never retry, deliver safe generic message, log
- Accumulates `SafetyDecision` data in `pending_effects` (persisted by `save_patient_context`, per AD-2)
- Audit event data also accumulated in `pending_effects.audit_events`

The safety retry/fallback graph edges are already wired in M3's topology (stubs). M4 replaces the stub implementations. No graph structural changes needed — only the `safety_gate` → `safety_route` conditional edge logic needs real classification results.

**Files:** `agent/nodes/safety.py` (replaced), `agent/graph.py` (modified)
**Research ref:** `research-safety-llm.md` §4 (output gate and retry)
**Verify:** `uv run pytest tests/unit/test_safety_classifier.py -v`

#### Step 4.5: Safe fallback and crisis response

Create `domain/safety.py` with:
- `SAFE_FALLBACK_MESSAGE` — deterministic safe generic message
- `CRISIS_RESPONSE_MESSAGE` — 988 Lifeline guidance, no counseling
- `CLINICAL_REDIRECT_MESSAGE` — redirect to care team

These are hardcoded strings, not LLM-generated. They are the last line of defense.

**Files:** `domain/safety.py`
**Research ref:** `research-safety-llm.md` §4
**Verify:** `uv run pyright src/health_coach/domain/safety.py`

#### Step 4.6: Tool implementations (real)

Replace `set_goal` stub with intent-accumulating implementation (AD-2):
- Validates structured goal using `ExtractedGoal` Pydantic model
- Does NOT write to DB directly — uses `InjectedState` to READ current `pending_effects`, builds updated dict via immutable merge (`{**current_effects, "goal": goal_data, "phase_event": "goal_confirmed"}`), and returns `Command(update={"pending_effects": updated, "messages": [ToolMessage(content="Goal confirmed", tool_call_id=tool_call_id)]})`. **Critical:** `InjectedState` is read-only — mutations are silently discarded. State updates only propagate via `Command`.
- Requires `InjectedToolCallId` annotation to inject the tool_call_id for the ToolMessage
- Generates idempotency key: `{patient_id}:goal:{hashlib.sha256(goal_text.encode()).hexdigest()[:16]}` — uses `hashlib` (not `hash()`) for cross-process determinism. No time component, must be replay-stable (identical across graph retries and across processes). Prevents duplicate goal creation on graph replay.
- The actual persistence happens in `save_patient_context` (with `ON CONFLICT DO NOTHING` on idempotency key)

Keep other tools (`get_program_summary`, `get_adherence_summary`, `set_reminder`, `alert_clinician`) as stubs returning realistic synthetic data, but with real invocation contracts — Pydantic input/output schemas, idempotency key generation. Read-only tools (`get_program_summary`, `get_adherence_summary`) return plain `str` — `ToolNode` wraps automatically. They CAN read from the domain DB since reads don't break replay safety. Side-effecting tools (`set_reminder`, `alert_clinician`) must return `Command(update={"pending_effects": ...})` to accumulate intents.

**Files:** `agent/tools/goal.py` (replaced), `agent/tools/*.py` (updated stubs)
**Research ref:** `research-domain-model.md` §4 (goal extraction), §10.3 (idempotency)
**Verify:** `uv run pytest tests/unit/test_tools.py -v`

#### Step 4.7: Onboarding integration tests

Write end-to-end tests with `InMemorySaver` + fake LLM:
- Happy path: welcome → goal elicited → goal confirmed → stored → phase ACTIVE
- Clinical content during onboarding → safety gate blocks, redirect message sent
- Crisis language → urgent alert created, 988 response delivered, no retry
- Safety retry: first attempt blocked (CLINICAL_BOUNDARY) → augmented retry → fallback if still blocked
- Consent denied mid-flow → exits safely
- Goal extraction: realistic patient free text → structured `ExtractedGoal`

**Files:** `tests/integration/test_onboarding_flow.py`, `tests/safety/test_clinical_boundary.py`, `tests/safety/test_crisis_detection.py`
**Research ref:** `research-testing-setup.md` §3.2 (LangGraph testing)
**Verify:** `uv run pytest tests/ -v --tb=short`

### M4 Exit Gate
- Patient can complete onboarding through multi-turn conversation
- Tool loop works: LLM requests `set_goal` → ToolNode executes → result returns to agent → agent confirms
- Goal extracted, structured, accumulated in `pending_effects`, persisted by `save_patient_context`
- Phase transitions from ONBOARDING → ACTIVE on goal confirmation
- Safety classifier blocks clinical content, retries once (augmented HumanMessage), falls back
- Crisis detection triggers durable alert + 988 response, no retry
- All safety decisions audited
- Intent accumulation boundary verified: no direct DB writes from agent nodes or tools (except crisis_check's ClinicianAlert + OutboxEntry, and consent_gate's audit event — Plan Invariant #1 exceptions)
- Full CI green
- **Migration note:** If M4 adds safety decision or classifier fields to models, generate incremental migration.

---

## Milestone 5: Durable Follow-up and Lifecycle Management ✅ COMPLETE

**Objective:** Add multi-day persistence, scheduling, disengagement, and re-engagement.
**PRD ref:** §11 M5; FR-6, FR-7, FR-8; NFR-2, NFR-5
**AC satisfied:** AC-8, AC-9, AC-10, AC-11, AC-12
**Research refs:** `research-scheduling-observability.md` §2, `research-domain-model.md` §12

### Files to Create

```
src/health_coach/
  orchestration/
    __init__.py
    scheduler.py                       # Poll worker, claim/process/complete
    jobs.py                            # Job type handlers (day_2, day_5, day_7, backoff)
    reconciliation.py                  # Startup reconciliation + periodic sweep
  agent/
    prompts/
      active.py                        # Active phase prompts (celebration, nudge, check-in)
      re_engaging.py                   # Re-engagement prompt
  domain/
    backoff.py                         # Backoff sequence logic
tests/
  unit/
    test_scheduler.py
    test_jobs.py
    test_backoff.py
    test_reconciliation.py
  integration/
    test_followup_lifecycle.py
    test_backoff_dormant.py
    test_reengagement.py
```

### Files to Change

```
src/health_coach/agent/nodes/active.py       # Replace stub with real LLM active agent
src/health_coach/agent/nodes/re_engaging.py   # Replace stub with real re-engagement agent
src/health_coach/agent/nodes/onboarding.py   # Add scheduled job accumulation on goal_confirmed
src/health_coach/main.py                      # Start scheduler worker in lifespan
```

### Steps

#### Step 5.1: Scheduler worker

Create `orchestration/scheduler.py`:
- `SchedulerWorker` class with poll loop
- Uses `asyncio.Event` for graceful shutdown (set in lifespan shutdown)
- Claims jobs with `SELECT ... FOR UPDATE SKIP LOCKED` via `with_for_update(skip_locked=True)`
- Claim + status transition in same transaction
- Jitter ±20% on poll interval (default 30s)
- Per-job error handling: catch, log, increment attempts, mark failed/dead
- One `AsyncSession` per job execution (not shared)
- **Patient-level serialization:** When processing a batch, group claimed jobs by `patient_id`. Process each patient's jobs sequentially (not concurrently). Each job handler that invokes the graph wraps `graph.ainvoke()` in `patient_advisory_lock` (Plan Invariant #3). Jobs for DIFFERENT patients can still run concurrently via `asyncio.gather()`. The advisory lock also serializes against concurrent API/webhook invocations for the same patient.

**Files:** `orchestration/scheduler.py`
**Research ref:** `research-scheduling-observability.md` §2.1–2.3
**Verify:** `uv run pyright src/health_coach/orchestration/`

#### Step 5.2: Job type handlers

Create `orchestration/jobs.py`:
- `JobHandler` protocol: `async handle(job: ScheduledJob, session: AsyncSession) -> None`
- `FollowupJobHandler` — acquires `patient_advisory_lock`, then invokes graph with the patient's persistent thread (`thread_id = f"patient-{patient_id}"`, per AD-1). The checkpointer resumes from the last saved state, preserving full conversation history. Sets `invocation_source="scheduler"` (AD-3) so agent nodes know this is proactive outreach (no patient message to respond to). Job metadata includes `follow_up_day` so the active_agent knows which follow-up to schedule next (chain scheduling).
- Tone adaptation: `celebration` (after confirmed adherence), `nudge` (after no data), `check_in` (default)
- Each handler receives `CoachConfig` for cadence/backoff settings
- `OnboardingTimeoutHandler` — handles the 72h onboarding timeout job created by `pending_node`. This is a pure lifecycle transition that does NOT invoke the graph:
  1. Acquires `patient_advisory_lock` (same lock used by graph invocations — serialization guaranteed)
  2. Opens a session, loads the patient
  3. Checks phase is still `ONBOARDING` (idempotency guard — if patient already completed onboarding or went DORMANT, mark job completed and return)
  4. Calls `transition(ONBOARDING, "no_response_timeout")` → DORMANT
  5. Creates `ClinicianAlert` (routine priority — patient unresponsive, not crisis) with `OutboxEntry` for delivery
  6. Writes audit event (`onboarding_timeout`)
  7. Commits all in one transaction
  8. Marks job completed
  This bypasses the graph because there is no message to generate — it is a deterministic lifecycle transition. The advisory lock ensures no concurrent graph invocation for this patient.

**Files:** `orchestration/jobs.py`
**Research ref:** `research-scheduling-observability.md` §2.4, `research-scheduling-gaps.md`
**Verify:** `uv run pytest tests/unit/test_jobs.py -v`

#### Step 5.3: Startup reconciliation

Create `orchestration/reconciliation.py`:
- On startup: reset jobs stuck in `processing` → `pending` (crashed worker recovery)
- Periodic sweep (every 10 min): find patients in ACTIVE or ONBOARDING phase with no pending scheduled job → create missing follow-up jobs (ACTIVE) or missing timeout jobs (ONBOARDING, 72h). This ensures stuck ONBOARDING patients eventually transition to DORMANT via `no_response_timeout`.
- Idempotency: `INSERT ... ON CONFLICT DO NOTHING` with stable keys

**Files:** `orchestration/reconciliation.py`
**Research ref:** `research-scheduling-observability.md` §2.3
**Verify:** `uv run pytest tests/unit/test_reconciliation.py -v`

#### Step 5.4: Follow-up scheduling via intent accumulation (AD-2)

Follow-up job creation follows the AD-2 intent accumulation pattern with **chain scheduling** — no pre-seeding of all jobs at once. Agent nodes accumulate the NEXT scheduled job in `pending_effects.scheduled_jobs`, and `save_patient_context` writes it atomically with the domain state change.

**Chain scheduling model:** At most one pending follow-up job exists per patient at any time. Each job, when executed, schedules the next one. This prevents stale jobs from firing after phase transitions (since `save_patient_context` cancels pending jobs on any phase transition).

- **Onboarding agent** (Step 4.3): when `set_goal` succeeds and `pending_effects.phase_event = "goal_confirmed"`, populate `pending_effects.scheduled_jobs` with ONLY the Day 2 job. Apply `calculate_send_time()` with patient timezone + quiet hours. Apply `add_jitter()`. Use idempotency key: `{patient_id}:day_2_followup:{onboarding_date}`. Day 5 and Day 7 are NOT pre-seeded — they are created when Day 2 and Day 5 execute, respectively.
- **Active agent** (on Day 2 execution): after generating a follow-up response, accumulate the Day 5 job in `pending_effects.scheduled_jobs`. Job metadata includes `follow_up_day: 5` so the next execution knows what to schedule. Use idempotency key: `{patient_id}:day_5_followup:{onboarding_date}`.
- **Active agent** (on Day 5 execution): accumulate the Day 7 job. After Day 7, no further automatic follow-up is scheduled (the cadence is complete per FR-6).
- **Re-engaging agent**: on successful re-engagement, accumulate a new follow-up job to restart the cadence.
- `save_patient_context` writes all accumulated jobs atomically with `ON CONFLICT DO NOTHING` on idempotency keys. When a `phase_event` is present, `save_patient_context` first cancels all pending `scheduled_jobs` for the patient, THEN writes the new jobs. This ensures no stale jobs survive phase transitions.

This ensures a patient cannot transition to ACTIVE without a follow-up job (FR-6), because both writes happen in the same transaction. The reconciliation sweep (Step 5.3) is a safety net, not the primary mechanism.

**Files:** `agent/nodes/onboarding.py` (modified), `agent/nodes/active.py` (modified), `agent/nodes/re_engaging.py` (modified), `agent/nodes/context.py` (save_patient_context handles scheduled_jobs + cancellation)
**Research ref:** `research-scheduling-observability.md` §2.5, `research-domain-model.md` §12, `research-scheduling-gaps.md`
**Verify:** `uv run pytest tests/integration/test_followup_lifecycle.py -v`

#### Step 5.5: Backoff and dormant transition

Create `domain/backoff.py`:
- `next_backoff_delay(attempt: int, base_days: int = 2) -> timedelta` — exponential backoff
- `should_transition_to_dormant(unanswered_count: int, max_unanswered: int = 3) -> bool`

**Backoff ownership follows the PRD lifecycle (ACTIVE → RE_ENGAGING → DORMANT):**
- **ACTIVE node:** On scheduler-initiated invocations (`invocation_source="scheduler"`), the active agent checks if the patient responded since `last_outreach_at` by comparing `last_patient_response_at > last_outreach_at`. If not → the patient hasn't responded to the previous outreach. Increments `unanswered_count` via `pending_effects`, sets `pending_effects.phase_event = "unanswered_outreach"`, triggering transition to RE_ENGAGING. The FIRST unanswered outreach in ACTIVE triggers this transition. If the patient DID respond, reset is already handled (unanswered_count = 0 from the patient's invocation).
- **RE_ENGAGING node:** Owns the full backoff sequence. On each scheduler-initiated invocation, checks for no-response (same `last_outreach_at` vs `last_patient_response_at` comparison). Increments `unanswered_count` (now 2 → 3). Schedules next outreach with exponential delay via `pending_effects.scheduled_jobs`. On 3rd unanswered: creates `ClinicianAlert` (routine priority) via `pending_effects.alerts`, sets `pending_effects.phase_event = "missed_third_message"` for transition to DORMANT.
- **DORMANT node:** No further proactive outreach. If patient sends a message (`invocation_source="patient"`), triggers `patient_returned` → RE_ENGAGING.

**Unanswered count semantics:** `unanswered_count` is incremented by agent nodes (active_agent, reengagement_agent) when a scheduler-initiated invocation **detects** no patient response since `last_outreach_at` — NOT when outreach is sent. The detection is: `last_patient_response_at` is null or `last_patient_response_at < last_outreach_at`. `save_patient_context` records `last_outreach_at` when creating a scheduler-initiated outreach outbox entry, and resets `unanswered_count` to 0 when `invocation_source="patient"`. The count persists in the `Patient` domain model, not in graph state.

**Pending job cancellation on patient response:** When a patient responds (`invocation_source="patient"`) and `pending_effects.phase_event` triggers a phase transition (e.g., RE_ENGAGING back to ACTIVE via `patient_responded`), `save_patient_context` cancels pending backoff/follow-up jobs in the same transaction. With chain scheduling, at most one pending job needs cancellation.

This matches the PRD lifecycle: RE_ENGAGING IS the backoff phase. ACTIVE detects the first miss and transitions to RE_ENGAGING, which manages the escalating sequence.

**Files:** `domain/backoff.py`, `agent/nodes/active.py` (modified), `agent/nodes/re_engaging.py` (modified)
**Research ref:** `research-domain-model.md` §3.2 (phase transitions), PRD §4.1 (lifecycle), `research-scheduling-gaps.md`
**Verify:** `uv run pytest tests/integration/test_backoff_dormant.py -v`

#### Step 5.6: Re-engagement

Replace `agent/nodes/re_engaging.py` stub:
- When a dormant patient sends a message, route to re-engagement node
- Different from onboarding: acknowledges time away, references previous goal, doesn't re-elicit from scratch
- On successful re-engagement: transition RE_ENGAGING → ACTIVE (via `patient_responded` event). The DORMANT → RE_ENGAGING transition happened when the patient's message arrived (handled by phase_router detecting a DORMANT patient with `invocation_source="patient"`).
- Cancel any pending dormant-state jobs
- Schedule new follow-up cadence
- Uses the patient's persistent thread (AD-1) — the LLM has access to the full conversation history, including the original goal. No need to re-elicit context from scratch.

Create `agent/prompts/re_engaging.py` with warm, gentle tone.

**Files:** `agent/nodes/re_engaging.py` (replaced), `agent/prompts/re_engaging.py`
**Research ref:** PRD §4.1 (warm re-engagement)
**Verify:** `uv run pytest tests/integration/test_reengagement.py -v`

#### Step 5.7: Wire scheduler into app lifespan

Update `main.py` lifespan: start `SchedulerWorker` + `DeliveryWorker` as background `asyncio.Task`s (only when mode is `worker` or `all`, per AD-4). Signal shutdown via `asyncio.Event`. Run reconciliation on startup before starting the poll loop.

Verify that `--mode worker` runs scheduler and delivery workers without starting the HTTP server. Verify that `--mode api` does not start background workers. This ensures production deployability as separate containers.

**Files:** `main.py` (modified), `__main__.py` (modified)
**Research ref:** `research-scheduling-observability.md` §2.3
**Verify:** `uv run python -m health_coach --mode worker` — scheduler starts, logs poll activity; `uv run python -m health_coach --mode api` — HTTP only

#### Step 5.8: Lifecycle integration tests

Write tests (these MUST run against PostgreSQL for SKIP LOCKED):
- Day 2/5/7 jobs created after onboarding, honor timezone + quiet hours
- Scheduler picks up due job, creates graph invocation, delivers follow-up
- Unanswered backoff: 1 → 2 → 3 → dormant, clinician alert on 3rd
- Dormant patient returns → re-engagement path (not onboarding replay)
- Duplicate job pickup prevention (SKIP LOCKED)
- Service restart recovery: processing jobs reset to pending

**Files:** `tests/integration/test_followup_lifecycle.py`, `tests/integration/test_backoff_dormant.py`, `tests/integration/test_reengagement.py`
**Research ref:** `research-testing-setup.md` §3.6 (time-machine)
**Verify:** `uv run pytest tests/integration/ -v` (with PostgreSQL service)

### M5 Exit Gate
- Day 2 follow-up scheduled after onboarding (chain scheduling), respecting timezone/quiet hours. Day 5 scheduled when Day 2 executes. Day 7 scheduled when Day 5 executes.
- Scheduler worker picks up and processes due jobs using persistent patient threads (AD-1)
- Conversation continuity verified: follow-up responses reference prior context correctly
- Backoff sequence works: ACTIVE → RE_ENGAGING (first miss detected by no-response since `last_outreach_at`) → backoff 1 → 2 → 3 → DORMANT with clinician alert
- Unanswered count increments on no-response DETECTION (at next scheduler invocation), NOT on outreach send
- Pending jobs cancelled on phase transition (chain scheduling ensures at most one)
- Re-engagement path distinct from onboarding, uses full conversation history
- Onboarding timeout: 72h job fires, `OnboardingTimeoutHandler` transitions ONBOARDING → DORMANT with clinician alert
- Jobs survive service restart (reconciliation)
- No duplicate sends (SKIP LOCKED + idempotency keys)
- No concurrent invocations for same patient (`patient_advisory_lock` at call site + scheduler batch serialization)
- Follow-up jobs written atomically with phase transition (AD-2 verified: no separate `schedule_followup` node)
- Worker runs independently via `--mode worker`
- Full CI green (integration tests require PostgreSQL)
- **Migration note:** If M5 modifies any ORM models (e.g., `last_outreach_at`, `last_patient_response_at` on Patient), generate incremental migration with `uv run alembic revision --autogenerate -m "m5_scheduling_changes"` and review before proceeding.

---

## Milestone 6: External Integration and Delivery

**Objective:** Connect the workflow engine to real system boundaries and make the result demonstrable.
**PRD ref:** §11 M6; FR-9, FR-10; NFR-5, NFR-7
**AC satisfied:** AC-11 (idempotency), AC-13 (PHI-safe logs)
**Research refs:** `research-fastapi-sqlalchemy.md` §6, `research-scheduling-observability.md` §2.7–2.8

### Files to Create

```
src/health_coach/
  integrations/
    medbridge.py                       # MedBridge Go API client (consent, patient events)
    notification.py                    # NotificationChannel ABC + implementations
    alert_channel.py                   # AlertChannel ABC + implementations
  orchestration/
    delivery_worker.py                 # Outbox delivery worker
  api/
    routes/
      chat.py                         # SSE chat endpoint
      webhooks.py                     # MedBridge Go webhook receiver
      state.py                        # Read-only state query endpoints
    middleware/
      __init__.py
      logging.py                       # Request logging + structlog contextvars
  api/
    dependencies.py                    # FastAPI Depends() factories
demo-ui/
  package.json
  vite.config.ts
  src/
    App.tsx
    components/
      Chat.tsx
      ObservabilitySidebar.tsx
tests/
  unit/
    test_delivery_worker.py
    test_notification.py
  integration/
    test_chat_endpoint.py
    test_webhook_endpoint.py
  contract/
    __init__.py
    test_webhook_contracts.py
```

### Files to Change

```
src/health_coach/main.py                     # Add delivery worker, middleware, new routes
src/health_coach/agent/nodes/context.py      # save_patient_context handles outbox writes
src/health_coach/domain/consent.py           # Wire real MedBridge Go consent check
src/health_coach/agent/tools/clinician.py    # Wire real alert channel
```

### Steps

#### Step 6.1: MedBridge Go integration client

Create `integrations/medbridge.py`:
- `MedBridgeClient` with `httpx.AsyncClient`
- `check_consent(patient_id, tenant_id) -> ConsentResult` — calls MedBridge Go consent API
- HMAC signature verification for inbound webhooks
- `FakeMedBridgeClient` for testing (configurable responses)
- Retry with `stamina` on transient errors

Wire into `ConsentService` as the real implementation (replacing fake).

**Files:** `integrations/medbridge.py`, `domain/consent.py` (modified)
**Research ref:** PRD §12 (MedBridge Go integration)
**Verify:** `uv run pytest tests/unit/test_consent.py -v`

#### Step 6.2: Notification and alert channels

Create `integrations/notification.py`:
- `NotificationChannel` ABC: `async send(message, patient_id, metadata) -> DeliveryResult`
- `MockChannel` implementation for dev/test
- `MedBridgePushChannel` stub (to be implemented when API contract is defined)

Create `integrations/alert_channel.py`:
- `AlertChannel` ABC: `async send_alert(alert: ClinicianAlert) -> DeliveryResult`
- `WebhookAlertChannel` — POST to configured URL
- `MockAlertChannel` for testing

**Files:** `integrations/notification.py`, `integrations/alert_channel.py`
**Research ref:** `FINAL_CONSOLIDATED_RESEARCH.md` §12.1
**Verify:** `uv run pyright src/health_coach/integrations/`

#### Step 6.3: Outbox delivery worker

Create `orchestration/delivery_worker.py`:
- Same SKIP LOCKED pattern as scheduler
- Polls every 5–10 seconds
- Orders by `priority DESC, created_at ASC` (urgent alerts first)
- **Consent re-check before transport (AD-5) — scoped to patient-facing messages only:** Before each delivery attempt for `message_type="patient_message"`, call `ConsentService.check(patient_id, tenant_id)` — both arguments required, matching the protocol signature from Step 2.3. Verifies the patient is still logged in AND consented to outreach (PRD §5.5). If either condition fails: set outbox status to `cancelled`, emit `consent_check` + `delivery_cancelled` audit events, do NOT retry. This satisfies PRD §5.5 "before any outbound delivery attempt." **Clinician alerts** (`message_type="clinician_alert"`) **skip consent re-check** — a crisis escalation must be delivered regardless of patient consent status (PRD §5.4: "preserve alert delivery through retries and operator visibility if transport is unavailable").
- Routes to appropriate channel based on `message_type`: `NotificationChannel` for patient messages, `AlertChannel` for clinician alerts
- Creates `DeliveryAttempt` record per transport attempt (AD-6) — separate from the outbox entry
- Updates outbox entry status on final success/failure
- Dead-letter after max attempts

Wire into `main.py` lifespan (only in `worker` or `all` mode per AD-4).

**Files:** `orchestration/delivery_worker.py`, `main.py` (modified)
**Research ref:** `research-scheduling-observability.md` §2.7–2.8
**Verify:** `uv run pytest tests/unit/test_delivery_worker.py -v`

#### Step 6.4: Outbox intent accumulation in save_patient_context

`save_patient_context` already accumulates and flushes all domain writes (AD-2). This step ensures outbox entries are part of that atomic flush:

- When `save_patient_context` processes `pending_effects`, it writes `OutboxEntry` records (not `DeliveryAttempt` — AD-6) for each outbound message. This is an intent record. The delivery worker creates `DeliveryAttempt` records per transport attempt.
- `Message` record written to domain DB in the same transaction for queryable message history.
- `message_ref_id` (UUID) stored in outbox payload — never raw message text (PHI minimization).
- No separate `deliver_message` node exists in the graph. The outbound message is set in state by the agent or fallback node; `save_patient_context` writes it to the outbox atomically (Plan Invariant #2).

**Clarification on crisis path:** `crisis_check` writes BOTH a `ClinicianAlert` AND its `OutboxEntry` (`message_type="clinician_alert"`) immediately (AD-2 exception a for durability). This ensures the alert enters the delivery worker's retryable delivery path — the delivery worker does NOT apply consent re-check to clinician alerts (AD-5). The crisis *response message* (988 Lifeline guidance) is still delivered through the normal path: `crisis_response` node sets it in state → `save_patient_context` writes a separate `OutboxEntry` (`message_type="patient_message"`). This means: the clinician alert is durable even if save_patient_context fails, while the patient-facing message follows the standard atomic write path.

**Files:** `agent/nodes/context.py` (save_patient_context updated), `persistence/models.py` (verify OutboxEntry model)
**Research ref:** `research-scheduling-observability.md` §2.7
**Verify:** `uv run pytest tests/integration/ -v`

#### Step 6.5: SSE chat endpoint

Create `api/routes/chat.py`:
- `POST /v1/chat` — accepts patient message, invokes graph, streams response via SSE
- **Acquires `patient_advisory_lock`** before `graph.astream()` (Plan Invariant #3). Sets `invocation_source="patient"`.
- Uses `StreamingResponse` with async generator
- Calls `graph.astream(version="v2")` for typed streaming
- Headers: `Cache-Control: no-cache`, `Connection: keep-alive`, `X-Accel-Buffering: no`
- Validates patient_id and tenant_id from auth context

**Files:** `api/routes/chat.py`
**Research ref:** `research-fastapi-sqlalchemy.md` §6
**Verify:** `uv run pytest tests/integration/test_chat_endpoint.py -v`

#### Step 6.6: Webhook endpoint

Create `api/routes/webhooks.py`:
- `POST /webhooks/medbridge` — receives patient events (login, message, consent change)
- HMAC signature verification
- Inbound event deduplication via `ProcessedEvent` table
- Routes to appropriate handler (consent update, patient message, etc.)
- **Patient message events:** set `invocation_source="patient"` (NOT `"webhook"` — webhook is a transport mechanism, not a semantic mode per AD-3). Acquires `patient_advisory_lock` before `graph.ainvoke()` (Plan Invariant #3).
- **Non-graph events** (consent change, login): do NOT invoke the graph. Update domain DB directly (consent snapshot, etc.).

**Files:** `api/routes/webhooks.py`
**Research ref:** PRD §12 (MedBridge Go integration)
**Verify:** `uv run pytest tests/contract/test_webhook_contracts.py -v`

#### Step 6.7: State query endpoints

Create `api/routes/state.py`:
- `GET /v1/patients/{id}/phase` — current phase
- `GET /v1/patients/{id}/goals` — goals list
- `GET /v1/patients/{id}/safety-decisions` — safety decision history
- `GET /v1/patients/{id}/alerts` — clinician alerts
- All read-only, tenant-scoped

**Files:** `api/routes/state.py`
**Research ref:** PRD §11 M6
**Verify:** `uv run pytest tests/integration/ -v`

#### Step 6.8: Auth mechanism

Create `api/dependencies.py` with a minimal auth dependency:
- **Dev/demo:** Header-based identity — `X-Patient-ID` and `X-Tenant-ID` headers. The demo UI sets these directly. No token validation.
- **Production intent:** The MedBridge Go integration will provide auth context (likely JWT or API key passed from the MedBridge Go backend-to-backend call). The auth dependency is a `Depends()` function that extracts `patient_id` and `tenant_id` from the auth context. Swap the implementation when the MedBridge Go contract is finalized.
- All state query endpoints and the chat endpoint require the auth dependency. Webhook endpoints use HMAC signature verification instead.

**Files:** `api/dependencies.py`
**Research ref:** PRD §12 (open question: MedBridge Go auth mechanism)
**Verify:** `uv run pyright src/health_coach/api/dependencies.py`

#### Step 6.9: Request logging middleware

Create `api/middleware/logging.py`:
- Clears `structlog.contextvars` at request start (mandatory for async)
- Binds `request_id`, `patient_id` (from auth), `path`, `method`
- Logs request duration on completion
- Never logs request/response bodies (PHI safety)

**Files:** `api/middleware/logging.py`, `main.py` (modified)
**Research ref:** `research-scheduling-observability.md` §3.1
**Verify:** `uv run pyright src/health_coach/api/middleware/`

#### Step 6.10: Demo UI

Create `demo-ui/` as a React + Vite SPA (dev/staging only):
- Chat interface consuming `POST /v1/chat` (SSE)
- Observability sidebar showing: current phase, extracted goals, safety decisions, clinician alerts (polling state query endpoints)
- Synthetic patient selector
- NOT served in production

**Files:** `demo-ui/` directory
**Research ref:** PRD §11 M6, `FINAL_CONSOLIDATED_RESEARCH.md` §16
**Verify:** `cd demo-ui && npm install && npm run build`

### M6 Exit Gate
- Team can demo full coaching lifecycle through internal chat UI
- Real consent verification via MedBridge Go client (or configured fake)
- Delivery worker re-checks consent before transport for patient-facing messages only (AD-5 verified). Clinician alerts skip consent re-check.
- Outbox delivery worker processes all outbox entries (patient messages AND clinician alerts); DeliveryAttempt records created per attempt (AD-6)
- Clinician alerts delivered via AlertChannel through the outbox delivery path (retryable per PRD §5.4)
- Inbound webhooks deduplicated via ProcessedEvent table
- State query endpoints return correct data, scoped by auth context
- SSE streaming works end-to-end
- API and worker deployable separately (`--mode api` vs `--mode worker`)
- Full CI green

---

## Milestone 7: Release Hardening

**Objective:** Close operational and compliance gaps required for controlled launch.
**PRD ref:** §11 M7; NFR-6, NFR-8; AC-13, AC-14, AC-15
**Research refs:** `research-testing-setup.md` §3.8, PRD §5.2

### Files to Create

```
docs/
  phi-data-flow.md                    # PHI data flow documentation
  intended-use.md                     # Internal intended-use statement
  release-runbook.md                  # Deployment and rollback procedures
.github/workflows/
  eval.yml                            # LLM evaluation workflow (main branch only)
  deploy.yml                          # Deployment workflow
tests/
  evals/
    __init__.py
    test_safety_evals.py
    test_coaching_quality.py
    test_goal_extraction.py
    conftest.py                        # DeepEval fixtures, DEEPEVAL_TELEMETRY_OPT_OUT=1
```

### Steps

#### Step 7.1: PHI-safe logging verification

Audit all structlog calls across the codebase. Ensure no message content, patient names, or contact info appear in log output. Write a `scrub_phi_fields` processor as defense-in-depth. Create `docs/phi-data-flow.md` documenting where PHI enters, flows through, and exits the system.

**Files:** `observability/logging.py` (updated), `docs/phi-data-flow.md`
**Research ref:** `research-scheduling-observability.md` §3.4
**Verify:** `grep -r "message_content\|patient_name\|email\|phone" src/health_coach/ --include="*.py"` → no matches in log statements

#### Step 7.2: LLM evaluation suite

Create eval tests using DeepEval with `GEval` criteria:
- Safety classifier accuracy: clinical boundary detection, crisis detection, jailbreak detection
- Coaching response quality: tone appropriateness, non-clinical content
- Goal extraction accuracy: structured goal from free text
- Use `DEEPEVAL_TELEMETRY_OPT_OUT=1`

Create separate CI workflow `eval.yml` that runs only on `main` branch (evals make real LLM API calls).

**Files:** `tests/evals/*.py`, `.github/workflows/eval.yml`
**Research ref:** `research-testing-setup.md` §3.8
**Verify:** `DEEPEVAL_TELEMETRY_OPT_OUT=1 uv run deepeval test run tests/evals/`

#### Step 7.3: Expand property-based testing for state machine

M2 created exhaustive enumeration tests for the phase machine. Now expand with hypothesis `RuleBasedStateMachine`:
- Verify no invalid state is reachable via ANY sequence of events
- Verify all valid transitions work from any reachable state in any order
- Verify idempotency of transition attempts
- Verify that the backoff sequence (ACTIVE → RE_ENGAGING → DORMANT) cannot be bypassed

This is a hardening step — the M2 tests cover known paths, `RuleBasedStateMachine` discovers unknown edge cases.

**Files:** `tests/unit/test_phase_machine.py` (expanded, not replaced)
**Research ref:** `research-testing-setup.md` §3.7
**Verify:** `uv run pytest tests/unit/test_phase_machine.py -v --hypothesis-show-statistics`

#### Step 7.4: Deployment workflow

Create `.github/workflows/deploy.yml`:
- Triggered on tag push or manual dispatch
- Builds and pushes container image
- Runs Alembic migration check
- Deploys to target environment (parameterized for GCP/AWS)

Create `docs/release-runbook.md` with deployment and rollback procedures.

**Files:** `.github/workflows/deploy.yml`, `docs/release-runbook.md`
**Research ref:** PRD §8.1 (CI/CD)
**Verify:** Manual review

#### Step 7.5: Compliance artifacts

Create `docs/intended-use.md` — internal intended-use statement for the AI health coach.

Verify all acceptance criteria from PRD §10 are covered by existing tests.

Create a compliance checklist mapping each PRD §5.2 guardrail to its implementation evidence.

**Files:** `docs/intended-use.md`
**Research ref:** PRD §5.2, §5.6
**Verify:** Manual review + all tests pass

### M7 Milestones

- [x] Step 7.1 — PHI-safe logging: `scrub_phi_fields` processor + `docs/phi-data-flow.md` → verify: `ruff check . && pyright . && pytest tests/unit/test_phi_logging.py -v`
- [x] Step 7.2 — LLM evaluation suite: DeepEval `GEval` tests + `eval.yml` CI → verify: `pytest tests/evals/ --collect-only`
- [x] Step 7.3 — Property-based testing: Hypothesis `RuleBasedStateMachine` for phase machine → verify: `pytest tests/unit/test_phase_machine.py -v`
- [x] Step 7.4 — Deployment workflow: `deploy.yml` + `docs/release-runbook.md` → verify: manual review
- [x] Step 7.5 — Compliance artifacts: `docs/intended-use.md` + AC traceability → verify: manual review + all tests pass
Commit: "feat: M7 release hardening — PHI logging, evals, property tests, deploy, compliance"

### M7 Exit Gate
- PHI-safe logging verified across codebase
- LLM evaluation suite passing with acceptable scores
- Property-based tests cover state machine invariants
- Deployment workflow functional
- Compliance artifacts complete
- All 15 acceptance criteria from PRD §10 verified by tests
- Team can deploy safely once external approvals close

---

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| MedBridge Go API contract undefined | Blocks real consent verification; forces rework if interface assumptions are wrong | Use `FakeMedBridgeClient` through M5; define interface spec before M3 exit (PRD §12). Protocol/ABC contracts in M2 must match real API shape. |
| Consent stale at delivery time | Unauthorized outreach if consent revoked between generation and delivery | Delivery worker re-checks consent before transport (AD-5). Cancelled deliveries emit audit events. |
| OpenAI fallback receives PHI before BAA | HIPAA violation | ModelGateway checks `fallback_phi_approved` setting. If `False`, fallback returns safe message instead of routing to OpenAI. |
| Thread checkpoint unbounded growth | Memory/storage bloat on persistent patient threads | `RemoveMessage` + periodic summarization in `manage_history` node (AD-1). Monitor checkpoint size. |
| Concurrent graph invocations for same patient | State corruption on domain records (phase, unanswered_count) | `patient_advisory_lock` (`pg_advisory_lock`, session-level) acquired at call site (chat endpoint, webhook handler, scheduler) serializes ALL invocations per patient (Plan Invariant #3). Lock key derived from `hashlib.sha256` (not `hash()` — Python salts hashes per process). Lock connection uses `isolation_level="AUTOCOMMIT"` to prevent idle-in-transaction. Scheduler additionally processes same-patient jobs sequentially within a batch. |
| Advisory lock pool pressure | Each concurrent patient holds one extra Pool A connection for lock duration (5-30s during LLM calls) | Pool A sized at `pool_size=20, max_overflow=10`. At 20 concurrent patients: 20 lock connections + brief query sessions. Monitor pool wait time in production. |
| Onboarding timeout gap | Patient stuck in ONBOARDING if they never respond | `pending_node` creates 72h timeout job; `OnboardingTimeoutHandler` processes it (phase check guards idempotency); reconciliation sweep covers ONBOARDING patients with no pending jobs |
| Stale scheduled jobs after phase transition | Day 5/7 or backoff jobs fire into wrong phase | Chain scheduling (at most one pending job per patient) + `save_patient_context` cancels pending jobs on any phase transition |
| InjectedState mutation silent failure | Tools that mutate injected state produce zero state update | All side-effecting tools return `Command(update={...})` — the only mechanism for tools to write non-message state. Read-only tools return plain strings. Enforced by code review and integration tests. |
| Phase transition replay failure | `save_patient_context` replays after DB already advanced → PhaseTransitionError | Explicit idempotency guard: if transition fails, check `current == target` → skip if match |
| LangGraph `context_schema`/`Runtime` API instability | Could require node signature changes | Pin `langgraph>=1.1.0,<2.0`; isolate DI pattern in `agent/context.py` |
| `GenericFakeChatModel` limitation (no `bind_tools()`) | Makes tool-calling tests verbose | Accepted — construct `AIMessage(tool_calls=[...])` directly; document pattern in conftest.py |
| Haiku 3 retirement (April 20, 2026) | Safety classifier API errors if wrong model ID used | Hard-code `claude-haiku-4-5-20251001` in settings; verify at startup |
| Two-pool connection lifecycle | Complex startup/shutdown; potential resource leaks | Centralize in lifespan; test startup/shutdown in CI |
| Scheduler tests require PostgreSQL | Cannot run in SQLite-only CI job | Separate `test-integration` CI job with PostgreSQL service container |
| Safety classifier false positives | Legitimate coaching messages blocked | Track block rate in metrics; tune prompts before launch; augmented retry as first defense |
| Outbox delivery ordering | Urgent alerts delayed behind queued messages | `priority DESC` ordering in delivery worker query |
| Worker topology in production | Follow-ups and alerts lost during scale-to-zero or rolling deploys | Separate `--mode worker` process (AD-4); always-on worker containers in production |
| Schema drift across milestones | ORM models modified in M3-M5 without migration | Migration note on all milestones that modify models; `alembic revision --autogenerate` verification |

## Open Questions Requiring Human Input

1. **MedBridge Go API contract** — What endpoints exist for consent verification and patient events? What auth mechanism? **Blocks M3 exit** (PRD §12), not just M6. The Protocol/ABC contracts defined in M2 must match the real API shape. Begin interface discussions during M2.
2. **Clinician alert channel** — Email? Slack webhook? MedBridge Go dashboard? (Blocks M6)
3. **Cloud target** — GCP or AWS? (Blocks deploy workflow in M7)
4. **Patient timezone source** — Does MedBridge Go provide IANA timezone? Default assumption? (Blocks M5 quiet-hours logic)
5. **Retention policy** — How long to keep checkpoint blobs? Audit events have 6-year HIPAA minimum. What about conversation content? (Blocks M7 compliance artifacts)
6. **Launch tenancy scope** — Single tenant or multi-tenant from day one? Schema is tenant-ready either way, but RLS enforcement is deferred. (Inform before M6)
7. **Demo UI scope** — Is React + Vite acceptable, or does the team prefer a simpler approach? (M6 step 6.10)
8. **Thread strategy ADR** — AD-1 (one persistent thread per patient) contradicts FINAL_CONSOLIDATED_RESEARCH.md §6.5 (new thread per check-in). Requires explicit ADR-002 before M4 (not M5 — AD-1 affects graph construction in M3 and onboarding design in M4). The plan proceeds with persistent threads; reverse this decision only with explicit justification.
9. **PENDING → ONBOARDING trigger** — How does a patient enter the system? Current plan assumes a MedBridge Go webhook ("patient assigned HEP") triggers the PENDING → ONBOARDING transition. Confirm this is the intended entry path, or specify alternatives (e.g., manual enrollment API, batch import).
