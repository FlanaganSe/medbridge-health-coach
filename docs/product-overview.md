# Product Overview

## What this is

Health Ally is an AI-powered accountability partner that proactively engages patients in home exercise program (HEP) adherence through the MedBridge Go patient mobile app. It guides patients through onboarding, goal-setting, and scheduled follow-ups via multi-turn conversations — while enforcing strict safety boundaries: no clinical advice, crisis detection with clinician escalation, and per-interaction consent verification.

**The problem it solves:** Healthcare providers prescribe home exercise programs, but adherence is notoriously low — patients fall off when they don't feel supported between visits. Clinicians are stretched too thin for regular motivational check-ins with every patient. This system automates the accountability layer: warm, consistent, proactive coaching that stays strictly within non-clinical boundaries.

**The core design principle:** Deterministic policy in Python, bounded generation by LLM. The LLM handles conversation and tool selection within a phase; application code controls phase transitions, safety gates, consent enforcement, and all writes to the domain database. The LLM is never trusted with state transitions, delivery decisions, or safety-critical routing.

**Users:**
- **Primary:** Patients enrolled in a MedBridge HEP who have logged into MedBridge Go and consented to AI coaching outreach.
- **Secondary:** Clinicians who receive alerts when patients show signs of crisis or sustained disengagement.

**This codebase is a backend service only.** The patient-facing UI is MedBridge Go (not in this repo). A dev-only React demo UI (`demo-ui/`) exists for manual testing.

---

## Stack and technology choices

| Technology | Role | Why this choice |
|---|---|---|
| **Python 3.12+** | Runtime | Async-first (`asyncio`), mature LLM/AI ecosystem, team expertise |
| **uv** | Package manager | 10-100x faster than pip, lockfile-based reproducibility, replaces pip + pip-tools + virtualenv |
| **LangGraph** | Agent orchestration | First-class support for persistent conversation threads, checkpointing, conditional graph routing, tool calling — LangChain ecosystem without the chain abstraction overhead |
| **FastAPI** | HTTP API | Native async, dependency injection, automatic OpenAPI docs, SSE streaming support |
| **SQLAlchemy 2.0 (async)** | ORM / domain DB | Mature async support, type-safe queries, relationship loading control (`lazy="raise"`), cross-DB compatibility (PostgreSQL + SQLite) |
| **PostgreSQL 16** | Production database | Advisory locks for concurrent patient serialization, `SELECT ... FOR UPDATE SKIP LOCKED` for safe job claiming, JSONB for flexible metadata |
| **SQLite** | Local dev / unit tests | Zero-config, in-memory for fast tests, avoids requiring PostgreSQL for development |
| **psycopg3** | LangGraph checkpointer pool | Required by `AsyncPostgresSaver` — native async, connection pooling, `AUTOCOMMIT` support for advisory locks |
| **Alembic** | Database migrations | SQLAlchemy-native, versioned schema management |
| **Anthropic Claude** | LLM provider | Sonnet for coaching (quality + tool use), Haiku for safety classification (speed + cost) |
| **Pydantic v2** | Settings + validation | `.env` binding via pydantic-settings, runtime type validation, structured LLM output schemas |
| **structlog** | Logging | Structured JSON output, processor chain architecture (enables PHI scrubbing as last processor), contextvar binding |
| **httpx** | HTTP client | Async-native, used for MedBridge Go API integration and webhook alert delivery |
| **stamina** | Retry/backoff | Tenacity alternative with simpler API, used for HTTP retry logic |
| **DeepEval** | LLM evaluation | GEval metric framework with LLM-as-judge, Anthropic model support (not just OpenAI) |
| **Hypothesis** | Property-based testing | RuleBasedStateMachine for exhaustive phase machine verification |
| **Ruff** | Lint + format | Single tool replacing flake8 + isort + black, 10-100x faster |
| **pyright** | Type checking | Strict mode on `src/`, relaxed on `tests/` — catches type errors without test annotation overhead |
| **Docker** | Containerization | 3-stage build (python builder → node ui-builder → python runtime), `python:3.12-slim` base |
| **Railway** | Deployment | PaaS with Dockerfile support, health check integration, environment variable management |

### Key tradeoffs

**LangGraph over raw LangChain:** LangGraph provides graph-based control flow with persistent state (checkpointing), which is essential for multi-turn conversations that span days. Raw LangChain chains don't support the conditional routing and state accumulation patterns this system requires. The tradeoff is tighter coupling to the LangGraph execution model and checkpoint storage format.

**Single StateGraph over subgraphs (ADR-001):** Five phases don't justify subgraph complexity (schema alignment, cross-graph debugging, checkpoint namespace management). Phase-specific behavior is handled by conditional routing to different agent nodes. The migration cost to subgraphs increases significantly once production checkpoint rows with PHI exist — it becomes a HIPAA change-management event.

**PostgreSQL + SQLite dual support:** Production requires PostgreSQL (advisory locks, SKIP LOCKED, JSONB). But requiring PostgreSQL for every developer and every test run creates friction. SQLite compatibility (via `StrEnum + String(20)` instead of native PG ENUM) keeps the development loop fast. The tradeoff is that scheduler tests requiring `SKIP LOCKED` can only run against PostgreSQL.

**Two connection pools (ADR-006):** The SQLAlchemy async pool serves the application domain; a separate psycopg3 `AsyncConnectionPool` serves the LangGraph checkpointer. They can't be shared because LangGraph's `AsyncPostgresSaver` requires raw psycopg3 connections with specific settings (`AUTOCOMMIT`, `dict_row`, `prepare_threshold=0`). Both pools must be independently managed in the FastAPI lifespan.

**Anthropic Claude for both coaching and judging evals:** Using the same model family for generation and evaluation introduces potential self-serving bias, but the alternative (OpenAI as judge) creates a cross-vendor dependency and inconsistent capability assumptions. The eval thresholds are calibrated specifically for Haiku as judge.

---

## Architecture

### Request flow (patient-initiated)

```
Patient sends message via MedBridge Go
  │
  ▼
POST /webhooks/medbridge (HMAC-verified)
  │                              ┌─── POST /v1/chat (dev demo UI, SSE)
  │                              │
  ▼                              ▼
Acquire patient_advisory_lock(patient_id)    ← serializes concurrent access
  │
  ▼
┌─────────────────────────────────────────────────────────────┐
│                    LANGGRAPH EXECUTION                       │
│                                                             │
│  1. consent_gate ──── denied? ──► audit event → END         │
│        │ allowed                                            │
│        ▼                                                    │
│  2. load_patient_context (DB read)                          │
│        │                                                    │
│        ▼                                                    │
│  3. crisis_check (LLM classifier, Haiku)                    │
│        │ EXPLICIT ──► durable alert write → fallback → save │
│        │ POSSIBLE ──► routine alert (pending) → continue    │
│        │ NONE                                               │
│        ▼                                                    │
│  4. manage_history (placeholder — pass-through)              │
│        │                                                    │
│        ▼                                                    │
│  5. phase_router (deterministic dispatch)                   │
│        ├── PENDING ──► pending_node (template) ──► save     │
│        ├── ONBOARDING ──► onboarding_agent (LLM) ──┐       │
│        ├── ACTIVE ──► active_agent (LLM) ──────────┤       │
│        ├── RE_ENGAGING ──► reengagement_agent (LLM)┤       │
│        └── DORMANT ──► dormant_node ──► save        │       │
│                                                     │       │
│  6. tools_condition ◄───────────────────────────────┘       │
│        ├── has tool_calls ──► tool_node ──► back to agent   │
│        └── no tool_calls                                    │
│              │                                              │
│              ▼                                              │
│  7. safety_gate (LLM classifier, Haiku)                     │
│        ├── SAFE ──► save                                    │
│        ├── CLINICAL_BOUNDARY (retry < 1) ──► retry → safety │
│        ├── CLINICAL_BOUNDARY (retry ≥ 1) ──► fallback       │
│        └── CRISIS/JAILBREAK ──► fallback                    │
│                                                             │
│  8. save_patient_context (atomic DB flush)                  │
│        Writes: phase transition, goals, alerts, outbox,     │
│        scheduled jobs, safety decisions, audit events        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
  │
  ▼
Release advisory lock
  │
  ▼
DeliveryWorker polls outbox_entries
  ├── patient_message: re-check consent → send via NotificationChannel
  └── clinician_alert: skip consent → send via AlertChannel
```

### Proactive outreach flow (scheduler-initiated)

```
SchedulerWorker polls scheduled_jobs (SELECT ... FOR UPDATE SKIP LOCKED)
  │
  ▼
Claim job (status = 'processing')
  │
  ▼
Acquire patient_advisory_lock(patient_id)
  │
  ▼
Graph invocation with:
  - messages: [] (no patient input)
  - invocation_source: "scheduler"
  - _job_metadata: {follow_up_day: 2|5|7, source: "scheduler"|"reconciliation"}
  │
  ▼
Phase agent checks for no-response:
  - Active: if patient hasn't responded since last outreach → unanswered_outreach event
  - Re-engaging: increment unanswered_count, check dormant threshold
  - Dormant: no outbound message (scheduler should not invoke dormant)
  │
  ▼
If generating: LLM creates follow-up message → safety gate → save → outbox
If transitioning: accumulate phase_event → save (no outbound message)
```

### Deployment modes

The app runs in three modes via `--mode` flag:
- **`api`** — HTTP server only (uvicorn + FastAPI)
- **`worker`** — Scheduler + delivery worker only (no HTTP, `asyncio.run`)
- **`all`** — Both (default). Workers spawn as `asyncio.Task` in FastAPI lifespan. Suitable for dev and Railway single-service deployment.

### Railway startup sequence

```
alembic upgrade head                    # 1. Run database migrations
python -c '...run_bootstrap(Settings())' # 2. Create LangGraph checkpoint tables
python -m health_ally                   # 3. Start app (api + workers)
```

---

## Directory structure

```
src/health_ally/
  __main__.py              # CLI entry point (--mode api|worker|all, --host, --port)
  main.py                  # FastAPI app factory, lifespan (pools, workers), route registration
  settings.py              # Pydantic BaseSettings — all config via env vars / .env file

  agent/                   # LangGraph agent — the core intelligence layer
    graph.py               # StateGraph: 14 nodes, conditional edges, graph compilation
    state.py               # PatientState TypedDict, PendingEffects TypedDict
    context.py             # CoachContext dataclass, create_coach_context() factory
    effects.py             # accumulate_effects() — pure function for pending effects merging
    nodes/                 # One module per graph node (14 total)
      consent.py           # consent_gate: per-interaction consent verification
      context.py           # load_patient_context, save_patient_context (sole DB writer)
      crisis_check.py      # crisis_check: input-side LLM classifier
      history.py           # manage_history: placeholder pass-through (future context window management)
      router.py            # phase_router: deterministic phase dispatch
      pending.py           # pending_node: template welcome, PENDING → ONBOARDING
      onboarding.py        # onboarding_agent: LLM goal discovery
      active.py            # active_agent: follow-up coaching, no-response detection
      re_engaging.py       # reengagement_agent: backoff, dormant transition, patient return
      dormant.py           # dormant_node: no-op for scheduler, patient_returned for patients
      safety.py            # safety_gate: output-side LLM classifier, safety_route
      retry.py             # retry_generation: augmented prompt re-invocation
      fallback.py          # fallback_response: deterministic safe messages (no LLM)
    tools/                 # LLM-callable tools (5 total)
      goal.py              # set_goal (Command + effects), get_program_summary (stub)
      reminder.py          # set_reminder (Command + effects)
      adherence.py         # get_adherence_summary (stub)
      clinician.py         # alert_clinician (Command + effects)
    prompts/               # System prompts as Python string builders
      system.py            # BASE_SYSTEM_PROMPT, get_system_prompt()
      onboarding.py        # build_onboarding_prompt() — goal elicitation context
      active.py            # build_active_prompt() — coaching tone variants
      re_engaging.py       # build_re_engaging_prompt() — backoff/return context
      safety.py            # SAFETY_CLASSIFIER_PROMPT, CRISIS_CHECK_PROMPT

  api/                     # FastAPI HTTP layer
    routes/
      chat.py              # POST /v1/chat — SSE streaming, advisory lock
      webhooks.py          # POST /webhooks/medbridge — HMAC, dedup, event routing
      state.py             # GET /v1/patients/{id}/{phase|goals|alerts|safety-decisions}
      health.py            # GET /health/{live|ready} — liveness + readiness probes
      demo.py              # POST /v1/demo/{seed-patient|trigger-followup|reset-patient}, GET audit-events|scheduled-jobs
    middleware/
      logging.py           # RequestLoggingMiddleware — pure ASGI (not BaseHTTPMiddleware)
    dependencies.py        # get_auth_context() — X-Patient-ID / X-Tenant-ID headers

  domain/                  # Pure business logic (no I/O, no DB, no LLM)
    phases.py              # PatientPhase StrEnum (5 phases)
    phase_machine.py       # transition(), is_valid_transition() — deterministic FSM
    consent.py             # ConsentService ABC, ConsentResult, FakeConsentService, FailSafeConsentService
    safety.py              # CRISIS_RESPONSE_MESSAGE, CLINICAL_REDIRECT_MESSAGE, SAFE_FALLBACK_MESSAGE
    safety_types.py        # SafetyDecision, CrisisLevel, ClassifierOutput (Pydantic)
    scheduling.py          # CoachConfig, calculate_send_time(), add_jitter(), quiet hours
    backoff.py             # next_backoff_delay(), should_transition_to_dormant()
    errors.py              # PhaseTransitionError, ConsentDeniedError

  integrations/            # External service clients and factories
    model_gateway.py       # ModelGateway ABC, AnthropicModelGateway, FakeModelGateway
    medbridge.py           # MedBridgeClient (consent API + webhook verification)
    consent_factory.py     # create_consent_service() — settings-driven wiring
    channels.py            # create_notification_channel(), create_alert_channel()
    notification.py        # NotificationChannel ABC, MockNotificationChannel, MedBridgePushChannel (stub)
    alert_channel.py       # AlertChannel ABC, MockAlertChannel, WebhookAlertChannel

  orchestration/           # Background workers
    scheduler.py           # SchedulerWorker — polls scheduled_jobs, claims via SKIP LOCKED
    delivery_worker.py     # DeliveryWorker — polls outbox, consent re-check, transport, retry
    jobs.py                # JobDispatcher, FollowupJobHandler, OnboardingTimeoutHandler
    reconciliation.py      # startup_recovery(), sweep_missing_jobs()

  persistence/             # Database layer
    db.py                  # create_engine(), session factory, LangGraph pool, checkpointer
    models.py              # 10 SQLAlchemy ORM models (all UUID PKs, tenant_id indexed)
    locking.py             # patient_advisory_lock() — pg_advisory_lock with AUTOCOMMIT
    repositories/          # BaseRepository CRUD, PatientRepository, AuditRepository
    schemas/               # Pydantic schemas for API request/response validation

  observability/
    logging.py             # structlog config, scrub_phi_fields processor, OTel trace injection

tests/                     # Mirrors src/ structure
  conftest.py              # Session-scoped engine, per-test session, mock_session helper
  unit/                    # ~180 tests — SQLite, FakeModelGateway, no external deps
  integration/             # Graph routing, thread persistence, endpoint tests
  safety/                  # Crisis detection and clinical boundary routing
  contract/                # Webhook HMAC verification
  evals/                   # 24 DeepEval LLM-as-judge cases (excluded from default pytest)
    conftest.py            # DEEPEVAL_TELEMETRY_OPT_OUT=1, skip-if-no-API-key

alembic/                   # Single initial migration (all 12 tables)
demo-ui/                   # React 19 + Vite + Tailwind v4 — chat, pipeline viz, observability (see Demo UI section)
docs/                      # ADRs, PHI data flow, release runbook, intended use, this file
```

---

## Core concepts

### Patient phases (deterministic FSM)

The patient lifecycle is a 5-phase finite state machine with 7 transitions. Phase transitions are triggered by application code events, never by the LLM.

```
              onboarding_initiated           goal_confirmed
  PENDING ──────────────────────► ONBOARDING ─────────────► ACTIVE
                                      │                        │
                              no_response_timeout    unanswered_outreach
                                      │                        │
                                      ▼                        ▼
                                   DORMANT ◄──────── RE_ENGAGING
                                      │     missed_        │
                              patient_returned  third_message  │
                                      │                 patient_responded
                                      └──► RE_ENGAGING ──────► ACTIVE
```

**PENDING** — Initial state after patient login + consent. Receives a template welcome message (no LLM). Transitions to ONBOARDING immediately. A 72-hour onboarding_timeout job is scheduled.

**ONBOARDING** — LLM-powered goal discovery conversation. The agent asks open-ended questions about exercise goals and calls `set_goal` when the patient shares one. Exit: goal confirmed (→ ACTIVE, schedules Day 2 follow-up) or 72h timeout with no response (→ DORMANT, clinician alert).

**ACTIVE** — Scheduled follow-ups at Day 2, 5, and 7 post-goal-confirmation. The active agent coaches on adherence, references the patient's stated goal, celebrates wins, and gently encourages consistency. On scheduler invocation, detects whether the patient responded since last outreach. No response → `unanswered_outreach` event (→ RE_ENGAGING).

**RE_ENGAGING** — Exponential backoff sequence. Base interval doubles (2 → 4 → 8 → 14 days max). Each unanswered outreach increments the counter. At `max_unanswered` (default 3): `missed_third_message` event (→ DORMANT, clinician alert). If the patient messages back: `patient_responded` event (→ ACTIVE, fresh Day 2 follow-up scheduled).

**DORMANT** — Terminal outreach state. No proactive messages sent. If the patient returns with a message: `patient_returned` event (→ RE_ENGAGING, then immediately → ACTIVE if they engage).

### Pending effects pattern (ADR-003)

Graph nodes do not write to the domain database directly. Instead, tools and nodes accumulate side effects in `state["pending_effects"]` — a typed dict:

```python
class PendingEffects(TypedDict, total=False):
    goal: dict[str, object] | None          # Overwrite (latest wins)
    alerts: list[dict[str, object]]         # Append
    phase_event: str | None                 # Overwrite (one transition per invocation)
    scheduled_jobs: list[dict[str, object]] # Append
    safety_decisions: list[dict[str, object]] # Append
    outbox_entries: list[dict[str, object]] # Append
    audit_events: list[dict[str, object]]   # Append
```

`accumulate_effects()` merges new items into existing pending effects (lists append, scalars overwrite). `save_patient_context` flushes everything in a single database transaction — either all effects persist or none do.

**Why this matters:** If a graph invocation fails partway through (LLM timeout, safety classifier error), the domain database is left unchanged. Replaying the graph from the LangGraph checkpoint is safe because no partial writes occurred. This is the foundational invariant that makes the system crash-safe.

**Two exceptions for durability:**
1. `crisis_check` writes `ClinicianAlert` + `OutboxEntry` immediately for EXPLICIT crisis — the alert must survive even if the graph crashes afterward.
2. `consent_gate` writes an audit event on denial — it exits before `save_patient_context` runs.

### Safety pipeline

Three layers with intentionally asymmetric failure modes (ADR-005):

| Layer | Position | Purpose | On classifier error | On detection |
|---|---|---|---|---|
| **Consent gate** | Entry | Block unauthorized outreach | Fail-closed (block) | Exit graph, audit event |
| **Crisis pre-check** | Input-side (after load) | Detect self-harm/crisis in patient messages | Fail-escalate (alert clinician) | EXPLICIT: durable alert + fallback. POSSIBLE: routine alert, continue |
| **Safety gate** | Output-side (before delivery) | Block clinical/unsafe content in coach responses | Fail-closed (block as CLINICAL_BOUNDARY) | SAFE: deliver. CLINICAL_BOUNDARY: retry once, then fallback. CRISIS/JAILBREAK: fallback immediately |

**Fallback messages are deterministic, not LLM-generated:**
- **Crisis:** Provides 988 number, confirms care team notification, offers reassurance.
- **Clinical boundary:** Redirects to care team, reaffirms exercise coaching scope.
- **Safe fallback:** Generic safe message when all else fails.

**Safety classifier prompts** are carefully tuned:
- Exercise encouragement, goal discussion, scheduling → always SAFE
- Pain levels, symptom changes, new symptoms → CLINICAL_BOUNDARY
- Self-harm, suicidal ideation, hopelessness → CRISIS
- When in doubt: clinical_boundary over safe, explicit crisis over possible

### Outbox pattern

All outbound messages (patient messages and clinician alerts) are written to `outbox_entries` as part of the same transaction as domain state changes. The `DeliveryWorker` polls the outbox independently, handling transport, retries, and dead-lettering.

**Why outbox instead of direct send:** Decouples message generation from transport. The graph transaction is atomic — if the DB write succeeds, the message is guaranteed to be in the outbox. The delivery worker handles transient transport failures (push notification service down, webhook timeout) without affecting graph execution.

**Consent re-check at delivery (ADR-004):** Patient messages have their consent re-verified before transport. A patient may revoke consent between generation and delivery — delivering afterward is a compliance violation. Clinician alerts skip this re-check — they are internal clinical communications, not patient outreach.

**Dead-lettering:** After 5 failed delivery attempts, an outbox entry is marked `dead`. Priority ordering ensures urgent clinician alerts are delivered before routine patient messages.

### Advisory locking (ADR-006)

Concurrent graph invocations for the same patient (patient replies while a scheduled follow-up is in flight) would corrupt domain state. PostgreSQL `pg_advisory_lock` serializes access per-patient.

**Three traps this design avoids:**
1. `hash()` is salted per-process (`PYTHONHASHSEED`) — lock keys would differ between API and worker processes. Fix: `hashlib.sha256` for deterministic keys.
2. Transaction-level locks release when the transaction commits — too early during multi-second LLM calls. Fix: session-level locks that persist until connection close.
3. SQLAlchemy 2.x `autobegin` creates idle-in-transaction on the lock connection during LLM calls (blocks pool connections). Fix: `isolation_level="AUTOCOMMIT"`.

The lock is acquired at **call sites** (chat endpoint, webhook handler, scheduler job handler), not inside graph nodes. This ensures the entire graph invocation is serialized.

### Context injection

All dependencies are injected via LangGraph's `RunnableConfig`, not globals:

```python
config = {
    "configurable": {
        "ctx": CoachContext(session_factory, engine, consent_service, settings, coach_config, model_gateway),
        "thread_id": f"patient-{patient_id}"
    }
}
```

`CoachContext` is a frozen dataclass containing the session factory, engine, consent service, settings, coaching config, and model gateway. Nodes extract it via `get_coach_context(config)`. This makes every dependency substitutable for testing.

### Model gateway

An abstract factory pattern that returns the appropriate LLM model by purpose:

- **`"coach"`** → Claude Sonnet (default: `claude-sonnet-4-6`) — used by phase agents and retry generation. Supports tool binding.
- **`"classifier"`** → Claude Haiku (default: `claude-haiku-4-5-20251001`) — used by crisis check and safety gate. Structured output via `with_structured_output(ClassifierOutput)`.

**Fallback support:** If `fallback_phi_approved=True`, the coach model wraps with `primary.with_fallbacks([ChatOpenAI("gpt-4o")])`. This is disabled by default because PHI traversing a second provider requires separate BAA approval.

**Test double:** `FakeModelGateway` returns controlled responses without LLM calls. The classifier fake returns a configurable `ClassifierOutput`. The coach fake wraps `FakeListChatModel` with a `bind_tools` shim (since `GenericFakeChatModel` doesn't support tool binding).

---

## Data layer

### ORM models (10 tables)

All tables use UUID primary keys, `tenant_id` indexing (multi-tenancy ready), and `created_at`/`updated_at` timestamps. Phase and status columns use `StrEnum + String(20)` (not native PG ENUM) for SQLite test compatibility.

| Table | Purpose | Key fields | Notable |
|---|---|---|---|
| `patients` | Core entity | phase, timezone, unanswered_count, last_outreach_at, last_patient_response_at | Unique: (tenant_id, external_patient_id) |
| `patient_goals` | Extracted goals | goal_text, raw_patient_text, structured_goal (JSON), idempotency_key | FK to patient, `lazy="raise"` |
| `patient_consent_snapshots` | Consent audit trail | consented (bool), reason, checked_at | Append-only |
| `scheduled_jobs` | Background job queue | job_type, idempotency_key, status, scheduled_at, attempts, metadata_ (JSON) | Partial index on `status='pending'` (PostgreSQL) |
| `outbox_entries` | Outbound message queue | delivery_key, message_type, priority, channel, payload (JSON), status | Priority-ordered delivery |
| `delivery_attempts` | Transport audit trail | attempt_number, outcome, delivery_receipt (JSON), error, latency_ms | FK to outbox_entry |
| `clinician_alerts` | Alert records | reason, priority (routine/urgent), idempotency_key, acknowledged_at | Used by crisis check and alert_clinician tool |
| `safety_decision_records` | Classifier audit trail | decision, source, confidence, reasoning | 6-year HIPAA retention |
| `audit_events` | Append-only event log | event_type, outcome, metadata_ (JSON) | No FK to patients (survives deletion) |
| `processed_events` | Webhook deduplication | source_event_key, event_type | `INSERT ... ON CONFLICT DO NOTHING` |

**LangGraph checkpoint tables** (PostgreSQL only, created by `AsyncPostgresSaver.setup()`): `checkpoint_storage`, `checkpoint_writes`, `checkpoint_blobs`. These contain conversation history (PHI).

### Connection management

```
┌─────────────────────────────────┐    ┌──────────────────────────────┐
│ Pool A: SQLAlchemy AsyncEngine  │    │ Pool B: psycopg3 AsyncPool   │
│ - Domain tables (12 ORM models) │    │ - LangGraph checkpoint tables │
│ - pool_size=5, max_overflow=5   │    │ - min_size=2, max_size=3      │
│ - pool_pre_ping=True            │    │ - AUTOCOMMIT, dict_row         │
│ - expire_on_commit=False        │    │ - prepare_threshold=0          │
└─────────────────────────────────┘    └──────────────────────────────┘
```

Both pools are created in the FastAPI lifespan and independently disposed on shutdown. SQLite dev uses a single in-memory engine with `MemorySaver` for LangGraph (no Pool B).

### Repository pattern

`BaseRepository` provides generic CRUD (`create`, `get_by_id`, `list_by`, `update`) using `flush()` not `commit()` — the caller owns the transaction boundary. `PatientRepository` adds `get_by_external_id()` with tenant scoping. `AuditRepository` disables `update()` (raises `NotImplementedError` — append-only enforcement).

---

## API surface

### Core endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `POST` | `/v1/chat` | `X-Patient-ID`, `X-Tenant-ID` | SSE-streaming chat. Acquires advisory lock, invokes graph, streams node updates. Headers: `Cache-Control: no-cache`, `Connection: keep-alive`, `X-Accel-Buffering: no`. |
| `POST` | `/webhooks/medbridge` | HMAC `X-Webhook-Signature` | Inbound events from MedBridge Go. Event types: `patient_message` (invokes graph), `consent_change` (updates snapshot), `patient_login` (logged). Idempotent via `processed_events` dedup. |
| `GET` | `/v1/patients/{id}/phase` | `X-Patient-ID`, `X-Tenant-ID` | Current patient phase |
| `GET` | `/v1/patients/{id}/goals` | `X-Patient-ID`, `X-Tenant-ID` | Patient goals (ordered by created_at DESC) |
| `GET` | `/v1/patients/{id}/alerts` | `X-Patient-ID`, `X-Tenant-ID` | Clinician alerts (last 50) |
| `GET` | `/v1/patients/{id}/safety-decisions` | `X-Patient-ID`, `X-Tenant-ID` | Safety decision history (last 50) |

### Health checks

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health/live` | Liveness probe — always 200 |
| `GET` | `/health/ready` | Readiness probe — checks DB + LangGraph pool, returns 503 if either fails |

### Demo endpoints (dev environment only)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/demo/seed-patient` | Create patient with consent (idempotent) |
| `POST` | `/v1/demo/trigger-followup/{id}` | Set earliest pending job to `scheduled_at=now()` |
| `POST` | `/v1/demo/reset-patient/{id}` | Reset to PENDING, delete goals/jobs/outbox |
| `GET` | `/v1/demo/scheduled-jobs/{id}` | List all scheduled jobs for patient |
| `GET` | `/v1/demo/audit-events/{id}` | List audit events for patient (newest first, limit 100) |

Demo endpoints are gated behind `settings.environment == "dev"` in `main.py` — never registered in staging or production.

### Auth model

Development uses header-based auth (`X-Patient-ID`, `X-Tenant-ID`). Production will use JWT/API-key validation. The `get_auth_context()` dependency is the single point to swap implementations.

---

## Tools (LLM-callable)

The LLM autonomously selects and calls these tools during conversation. Tools that create side effects return `Command(update={...})` — they cannot write to the database directly because `InjectedState` is read-only for LangGraph tools.

| Tool | Phases | Args (LLM-visible) | Side effects |
|---|---|---|---|
| `set_goal` | onboarding, active, re_engaging | `goal_text`, `raw_patient_text` | pending_effects: goal, phase_event="goal_confirmed", scheduled Day 2 follow-up |
| `get_program_summary` | onboarding, active, re_engaging | (none) | None — returns stub string |
| `get_adherence_summary` | active, re_engaging | (none) | None — returns stub string |
| `set_reminder` | active | `reminder_time` (ISO 8601), `reminder_message` | pending_effects: scheduled_job |
| `alert_clinician` | active, re_engaging | `reason`, `priority` (routine/urgent) | pending_effects: alert |

**Idempotency:** All side-effecting tools generate content-hashed idempotency keys (`hashlib.sha256`, first 16 chars). Pattern: `{patient_id}:{type}:{hash}`. This prevents duplicate writes on LangGraph replay.

**Stubs:** `get_program_summary` and `get_adherence_summary` return hardcoded strings — the MedBridge Go integration for real exercise data is a future milestone. The tool interface and invocation logic are real; only the data source is mocked.

---

## Background workers

### SchedulerWorker

Polls `scheduled_jobs` every 30 seconds (±20% jitter to avoid thundering herd). Batch size: 10.

```sql
SELECT * FROM scheduled_jobs
WHERE status = 'pending' AND scheduled_at <= now()
ORDER BY scheduled_at
LIMIT 10
FOR UPDATE SKIP LOCKED
```

**Job types and handlers:**
- `day_2_followup`, `day_5_followup`, `day_7_followup`, `backoff_followup` → `FollowupJobHandler` (acquires advisory lock, invokes graph with `invocation_source="scheduler"`)
- `onboarding_timeout` → `OnboardingTimeoutHandler` (pure lifecycle transition, no graph invocation — transitions ONBOARDING → DORMANT, cancels pending jobs, creates clinician alert)

**Retry:** Failed jobs increment `attempts` and reset to `pending`. After `max_attempts` (default 3), marked as `dead`.

**Reconciliation sweep:** Every ~10 minutes (20 poll cycles), scans for ACTIVE patients with no pending jobs and ONBOARDING patients with no timeout job. Creates missing jobs with idempotency keys to prevent duplicates.

### DeliveryWorker

Polls `outbox_entries` every 5 seconds (±20% jitter). Batch size: 20. Priority-ordered: urgent alerts delivered before routine messages.

**Per-entry delivery flow:**
1. Consent re-check (patient_message only, not clinician_alert — ADR-004)
2. Transport via NotificationChannel (patient) or AlertChannel (clinician)
3. Record DeliveryAttempt (attempt_number, outcome, latency_ms)
4. On success: `status='delivered'`
5. On failure: retry up to 5 times, then `status='dead'`

**Startup recovery:** Resets entries stuck in `status='delivering'` for >5 minutes back to `pending`. Handles worker crashes.

---

## Coaching behavior in detail

### Follow-up cadence

After goal confirmation, the active agent follows a Day 2 → Day 5 → Day 7 schedule:
- `set_goal` schedules `day_2_followup` (2 days out)
- Day 2 completion schedules `day_5_followup` (3 days later)
- Day 5 completion schedules `day_7_followup` (2 days later)
- Day 7 is the final scheduled follow-up (no next job)

All send times respect quiet hours (default 21:00–08:00 in patient's local timezone) and include random jitter (up to 30 minutes) to avoid batched sends.

### Disengagement escalation

When the scheduler invokes the active agent and the patient hasn't responded since the last outreach:
1. `unanswered_outreach` event → ACTIVE → RE_ENGAGING
2. Re-engaging agent sends with exponential backoff: 2 → 4 → 8 → 14 days
3. After `max_unanswered` (default 3) unanswered messages: `missed_third_message` event → DORMANT
4. Clinician alert: "Patient unresponsive after N outreach attempts"

### Patient return

When a DORMANT patient sends a message:
1. `patient_returned` event → DORMANT → RE_ENGAGING
2. Re-engaging agent sends warm welcome-back message (references previous goal, no judgment about gap)
3. `patient_responded` event → RE_ENGAGING → ACTIVE
4. Fresh Day 2 follow-up scheduled

### Tone adaptation

System prompts are dynamically built based on context:
- **Scheduler-initiated active:** "This is routine follow-up. Ask how exercises are going, reference stated goal. Keep brief and warm."
- **Scheduler-initiated re-engaging:** "Proactive outreach to unresponsive patient. Keep short, one warm sentence + one simple question."
- **Patient-initiated re-engaging (return):** "Welcome back warmly, reference previous goal, don't dwell on gap, focus on moving forward."

---

## Environment and config

### Required for all environments

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key for LLM calls (coaching + safety classification) |
| `DATABASE_URL` | PostgreSQL connection string (auto-normalizes `postgresql://` → `postgresql+psycopg://`) |
| `ENVIRONMENT` | `dev`, `staging`, or `prod` |

### Required for staging/production

| Variable | Description |
|---|---|
| `MEDBRIDGE_API_URL` | MedBridge Go API base URL (required for real consent checks) |
| `MEDBRIDGE_API_KEY` | MedBridge Go API key |
| `MEDBRIDGE_WEBHOOK_SECRET` | HMAC secret for webhook signature verification |

### Optional

| Variable | Default | Description |
|---|---|---|
| `APP_MODE` | `all` | `api`, `worker`, or `all` |
| `LOG_FORMAT` | `console` | `json` (production) or `console` (dev) |
| `LOG_LEVEL` | `INFO` | Standard Python log levels |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Server bind address |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | `5` / `5` | SQLAlchemy pool sizing |
| `LANGGRAPH_POOL_SIZE` | `3` | psycopg3 pool max connections |
| `DEFAULT_MODEL` | `claude-sonnet-4-6` | Coaching LLM model |
| `SAFETY_CLASSIFIER_MODEL` | `claude-haiku-4-5-20251001` | Safety classifier model |
| `MAX_TOKENS` | `1024` | LLM max output tokens (must be set explicitly) |
| `QUIET_HOURS_START` / `QUIET_HOURS_END` | `21` / `8` | Patient local time quiet hours |
| `DEFAULT_TIMEZONE` | `America/New_York` | Fallback timezone |
| `SCHEDULER_POLL_INTERVAL_SECONDS` | `30` | Scheduler polling frequency |
| `DELIVERY_POLL_INTERVAL_SECONDS` | `5` | Delivery worker polling frequency |
| `CORS_ORIGINS` | `["http://localhost:5173"]` | CORS allowed origins (demo UI) |
| `FALLBACK_PHI_APPROVED` | `False` | Enable OpenAI fallback (requires separate BAA) |

---

## Testing

### Test categories

| Category | Count | Backend | External deps | Default run |
|---|---|---|---|---|
| Unit | 157 | SQLite in-memory | None | Yes |
| Integration | 31 | SQLite in-memory | None | Yes |
| Safety | 7 | None (pure logic) | None | Yes |
| Contract | 3 | None | None | Yes |
| Evals | 24 | None | Anthropic API key | No (`--ignore=tests/evals`) |

### Running tests

```bash
pytest                                              # All tests except evals (~3s)
pytest --cov                                        # With coverage
DEEPEVAL_TELEMETRY_OPT_OUT=1 pytest tests/evals/   # LLM evals (~2min, requires ANTHROPIC_API_KEY)
ruff check . && ruff format --check .               # Lint + format
pyright .                                           # Type check
```

### Key testing patterns

**Mock session helper:** `make_mock_session(mock_patient)` creates a mock async session that supports `async with session:`, `async with session.begin():`, and `.get()`. Used across unit and integration tests for isolated graph node testing.

**FakeModelGateway:** Returns controlled LLM responses without API calls. The classifier fake returns configurable `ClassifierOutput` objects. The coach fake wraps `FakeListChatModel` — but since `GenericFakeChatModel` doesn't support `bind_tools()`, tests construct `AIMessage(tool_calls=[...])` directly.

**Property-based testing:** `test_phase_machine.py` uses Hypothesis `RuleBasedStateMachine` with 200 examples and 20-step sequences. Invariants: phase is always a valid enum, no self-loops, no dead ends, backoff sequence enforced, no direct ACTIVE→DORMANT bypass.

**Eval framework:** DeepEval `GEval` metrics with `AnthropicModel(model="claude-haiku-4-5-20251001")` as judge. Six custom metrics with calibrated thresholds. Safety metrics (clinical, crisis, jailbreak) have a 0.90 threshold; coaching metrics (tone, non-clinical, goal extraction) have 0.70.

### Fixtures

- `settings` (session-scoped): Test settings with SQLite in-memory
- `engine` (session-scoped): Shared `AsyncEngine` with tables created once
- `session_factory` (function-scoped): Fresh `async_sessionmaker` per test
- `session` (function-scoped): Individual `AsyncSession`
- `app` (function-scoped): FastAPI instance with test wiring
- `client` (function-scoped): `httpx.AsyncClient` with ASGI transport

---

## Observability

### Structured logging

structlog with a processor chain:
1. `merge_contextvars` → per-request context (request_id, patient_id, path, method)
2. `add_log_level`, `add_logger_name`
3. `TimeStamper(fmt="iso")`
4. `_otel_trace_processor` → OpenTelemetry trace/span IDs (if available)
5. `StackInfoRenderer`, `format_exc_info`
6. **`scrub_phi_fields`** → **runs last** (after exception formatting, so exception text is also scrubbed)

### PHI scrubbing

Defense-in-depth with two mechanisms:
- **Field-name blocklist:** `message_content`, `patient_name`, `email`, `phone`, `ssn`, `diagnosis`, `medication`, `treatment`, `symptoms`, `body`, `request_body`, `response_body`, etc.
- **Pattern matching:** SSN regex (`\d{3}-\d{2}-\d{4}`), email regex

Recursive dict traversal catches nested PHI. Values replaced with `[REDACTED]` (structure preserved for debugging). Opaque identifiers (patient_id UUID, tenant_id) pass through unchanged.

### Request logging middleware

Pure ASGI middleware (not `BaseHTTPMiddleware` — which buffers SSE responses). Clears contextvars per request to prevent cross-request PHI bleed in async. Binds request_id (UUID4), path, method. Never logs request/response bodies.

### Key log events to monitor

| Event | Severity | Meaning |
|---|---|---|
| `crisis_alert_written` | WARNING | Patient in crisis — verify clinician notification |
| `delivery_dead_letter` | WARNING | Message failed 5 delivery attempts |
| `safety_classifier_error` | ERROR | Classifier API failure — messages will be blocked |
| `consent_service_using_fake` | WARNING | Must not appear in production |
| `reconciliation_sweep` | INFO | Missing jobs detected and created |

---

## Important decisions and tradeoffs

Full ADR log: `docs/decisions.md` (ADR-001 through ADR-011). Key highlights:

**Single StateGraph, no subgraphs (ADR-001).** Five phases don't justify subgraph complexity. Migration to subgraphs post-production changes the LangGraph checkpoint namespace scheme, requiring checkpoint migration — a HIPAA change-management event because blobs contain PHI.

**One persistent thread per patient (ADR-002).** Conversational continuity across onboarding, follow-ups, and re-engagement requires the LLM to see prior history. Loading from the domain DB is lossy. Tradeoff: unbounded checkpoint growth, mitigated by history management.

**Intent accumulation, not direct writes (ADR-003).** Makes graph replay safe — failed invocations leave the domain DB unchanged. The two exceptions (crisis, consent) are narrowly scoped and documented.

**Consent re-check at delivery, not just generation (ADR-004).** The asymmetry (patient messages re-verified, clinician alerts not) is clinically load-bearing. A developer must not "simplify" this into uniform consent checking — doing so would block crisis alerts when consent is revoked.

**Asymmetric safety failure modes (ADR-005).** Output safety fails closed (blocks message — false positive acceptable). Crisis pre-check fails by escalating (sends alert — false negative unacceptable). This asymmetry is the most important safety design decision in the system.

**Advisory lock at call site on AUTOCOMMIT (ADR-006).** Prevents three independent failure modes: salted hash keys, early lock release, idle-in-transaction during LLM calls.

**PHI scrubbing as last processor (ADR-007).** Must run after `format_exc_info` — otherwise exception tracebacks can re-introduce PHI that was already scrubbed from fields.

**Code cleanup patterns (ADR-008).** Extracted `accumulate_effects()`, `create_coach_context()`, channel factories, pure ASGI middleware. Eliminated 3 identical context factory closures, 8 copy-pasted effect accumulation blocks, and 4 identical mock session helpers.

**Same-origin demo UI serving (ADR-009).** The Vite build output is bundled into the Docker image and served via Starlette `StaticFiles(html=True)` at `/`. Eliminates CORS entirely. API routes registered before the mount take priority. No `aiofiles` needed (Starlette uses `anyio` since 0.21.0).

**Dormant node gated on LLM success (ADR-010).** Phase transition DORMANT → RE_ENGAGING is only accumulated after a successful `coach_model.ainvoke()`. On LLM failure, the patient stays in DORMANT so the next attempt can succeed — prevents silent state corruption with no reply.

**Demo UI overhaul (ADR-011).** Full rewrite from ~750 LOC inline-styled React to 2,400+ LOC Tailwind CSS v4 implementation. SSE parser extracts full node data (pipeline, tools, safety), event-driven state refresh replaces 2s polling, tool call-result pairing uses `tool_call_id`.

---

## Gotchas

- **`InjectedState` is read-only for tools.** Mutations to the injected dict are silently discarded by LangGraph's `ToolNode`. Side-effecting tools must return `Command(update={...})`.
- **Never use Python's `hash()` for cross-process coordination.** `PYTHONHASHSEED` randomizes hashes per process. Use `hashlib.sha256` for lock keys and idempotency keys.
- **`GenericFakeChatModel` doesn't support `bind_tools()`.** In tests, construct `AIMessage(tool_calls=[...])` directly.
- **Two connection pools with independent lifecycles.** The SQLAlchemy pool and the psycopg3 LangGraph pool are not interchangeable. Both must be properly closed on shutdown.
- **`FakeConsentService` in production is a compliance violation.** The system silently falls back to it when `MEDBRIDGE_API_URL` is unset. The release runbook explicitly checks for this.
- **Scheduler tests require PostgreSQL.** SQLite lacks `SELECT ... FOR UPDATE SKIP LOCKED`.
- **`max_tokens` must be set explicitly on `ChatAnthropic`.** There is no safe default in the langchain-anthropic 1.x series.
- **Safety classifier model retires April 20, 2026.** `claude-haiku-4-5-20251001` — the model identifier in settings must be updated before then.
- **`DEEPEVAL_TELEMETRY_OPT_OUT` must be `1`** (numeric), not `YES` or `true`. Set before any deepeval import.
- **DeepEval defaults to OpenAI as judge.** The eval tests explicitly use `AnthropicModel` — removing this would cause unexpected OpenAI API calls.
- **`expire_on_commit=False` is mandatory** on all async sessions. Without it, accessing detached objects post-commit raises `DetachedInstanceError`.
- **`lazy="raise"` on all ORM relationships.** Prevents implicit N+1 queries. All relationship loading must be explicit via `.options(selectinload(...))`.
- **`AsyncPostgresSaver.setup()` must be called after pool open.** It creates checkpoint tables. Called in the Railway startCommand and in the FastAPI lifespan.
- **Pure ASGI middleware, not `BaseHTTPMiddleware`.** The latter buffers responses and breaks SSE streaming.
- **`asyncio_default_fixture_loop_scope = "session"`** — required for pytest-asyncio 1.x. The `event_loop` fixture was removed.
- **Pydantic model fields need runtime access** to their types. Use `# noqa: TC003` instead of `TYPE_CHECKING` guards for types used in Pydantic field annotations.
- **Webhook handler sets `invocation_source="scheduler"` for patient messages** — this is a known inconsistency (should be "webhook" but functionally equivalent for current routing logic).

---

## PHI handling

See `docs/phi-data-flow.md` for the complete PHI data flow diagram. Summary:

**PHI stored persistently:** LangGraph checkpoint blobs (conversation history), `patient_goals` (goal text), `outbox_entries` (message payload), `clinician_alerts` (alert reason), `safety_decision_records` (classifier reasoning).

**PHI never stored:** Patient name, email, phone (lives in MedBridge Go only). Application logs (scrubbed). Metrics (counts and enums only).

**HIPAA retention:** Audit events, safety decisions, clinician alerts: 6-year minimum. Checkpoint blob retention: TBD (organizational decision pending).

---

## Demo UI

React 19 + Vite + Tailwind CSS v4 (`demo-ui/`), dev/staging only. Bundled into the Docker image and served at `/` via Starlette `StaticFiles(html=True)` (ADR-009).

### Layout

Three horizontal layers — **TopBar** → **DemoControlBar** → **MainBody** (ChatPanel + ObservabilityPanel side-by-side):

1. **TopBar** — Health Ally branding (Space Grotesk), "DEMO MODE" label, patient selector dropdown with fixed demo UUIDs.
2. **DemoControlBar** — Flask icon + 4 action buttons: Seed Patient, Run Next Check-in (renamed from "Trigger Follow-up" for clarity — it sets the earliest pending `ScheduledJob.scheduled_at` to now), Reset Patient (with confirmation dialog), Refresh. Status messages shown in amber bar.
3. **ChatPanel** — Full SSE streaming chat:
   - **Three message types:** bot bubble (blue avatar, gray bg), user bubble (dark bg, white text), tool call card (amber bg, wrench icon, JetBrains Mono `Tool: {name}` label).
   - **Suggestion chips** — phase-aware conversation starters shown when the chat is empty. Different suggestions per phase (active, onboarding, re_engaging). Clicking a chip sends it as a message.
   - **Pipeline trace** — horizontal strip above messages showing graph nodes completing in real-time during streaming: running (blue pulse) → complete (green check). Collapses after stream completes; re-expands on next message.
   - **Progressive streaming render** — text appears as SSE chunks arrive, with bouncing-dot typing indicator.
   - **Safety toast** — slide-in toast (top-right) when safety classification fires. Shows decision label + confidence score. Auto-dismisses after 5s.
4. **ObservabilityPanel** — 420px fixed-width sidebar with 6 sections:
   - Phase (PhaseBadge with dot + color-coded label per phase)
   - Goals (CircleCheck icon, goal_text, confirmed_at date)
   - Alerts (AlertBadge/RoutineBadge, reason text, timestamp)
   - Safety Decisions (SafetyBadge dynamic by decision type, source, confidence %, "+N more" after 5)
   - Scheduled Jobs (job_type in monospace, timestamp, attempts/max_attempts, JobStatusBadge)
   - Audit Trail (event_type in monospace, timestamp, outcome badge color-coded by result, "+N more" after 10)

### Architecture

- **Design tokens** — Tailwind CSS v4 `@theme` directive in `index.css` with full color palette, font families (Space Grotesk, Inter, JetBrains Mono), and CSS animations.
- **Typed API client** (`api.ts`) — Generic `request<T>()` fetcher with `ApiError` class. Typed wrappers for all endpoints.
- **`useSSE` hook** — Line-buffered SSE parser that handles two stream modes: `updates` (node state deltas for pipeline progression, safety decisions, tool calls) and `custom` (token-level streaming for progressive text rendering). Tool call-result pairing uses `tool_call_id` for multi-tool correctness. Returns `SSEResult` with messages, pipelineNodes, safetyDecision, error.
- **`usePatientState` hook** — Event-driven state refresh replaces the old 2s polling (which produced 8 req/s). SSE `done` event triggers an immediate fetch via `refresh()` callback; 10s fallback interval catches scheduler-driven changes. Per-launch cancellation token prevents stale patient data from overwriting new patient state on rapid patient switches.
- **Reusable components** — `Badge.tsx` (PhaseBadge, AlertBadge, RoutineBadge, SafetyBadge, JobStatusBadge), `Button.tsx` (primary/secondary/danger variants), `ConfirmDialog.tsx` (accessible modal with `role="dialog"`, `aria-modal`, escape-to-close).

### File structure

```
demo-ui/src/
  App.tsx                        # Layout shell, patient state management
  api.ts                         # Typed API client for all endpoints
  types.ts                       # TypeScript types (Phase, ChatMessage, ToolCallInfo, etc.)
  index.css                      # Tailwind imports + design tokens + animations
  main.tsx                       # React entry point
  hooks/
    useSSE.ts                    # SSE parser + streaming state management
    usePatientState.ts           # Event-driven sidebar state refresh
  components/
    TopBar.tsx                   # Health Ally branding + patient selector
    DemoControlBar.tsx           # Demo action buttons + status bar
    ChatPanel.tsx                # Chat header + messages + input + safety toast
    ChatMessage.tsx              # Bot/user/tool message variants
    PipelineStepper.tsx           # Vertical stepper showing pipeline nodes as they execute with status icons
    ObservabilityPanel.tsx       # Sidebar: phase, goals, alerts, safety, jobs, conversation history
    SafetyToast.tsx              # Slide-in safety classification notification
    ui/Badge.tsx                 # PhaseBadge, AlertBadge, SafetyBadge, etc.
    ui/Button.tsx                # Primary/secondary button with icon support
    ui/ConfirmDialog.tsx         # Accessible confirmation modal
```

### Known limitations

- **No client-side routing** — `StaticFiles(html=True)` serves `index.html` for directories only. If react-router is ever added, replace with a `SpaStaticFiles` subclass (override `lookup_path`).
