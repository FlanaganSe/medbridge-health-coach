# Product Overview

## What this is

MedBridge Health Coach is an AI-powered accountability partner that proactively engages patients in home exercise program (HEP) adherence. It guides patients through onboarding, goal-setting, and scheduled follow-ups via multi-turn conversations — while enforcing strict safety boundaries (no clinical advice, crisis detection, consent verification). The system is a backend service consumed by MedBridge Go (patient mobile app); there is no patient-facing UI in this codebase.

The core design principle: **deterministic policy in Python, bounded generation by LLM.** The LLM handles conversation and tool selection within a phase; application code controls phase transitions, safety gates, and all writes to the domain database.

## Stack

- **Python 3.12+** with **uv** for package management
- **LangGraph** (LangChain ecosystem) for agent orchestration — single `StateGraph` with conditional edges, no subgraphs
- **FastAPI** for the HTTP API layer (async, SSE streaming for chat)
- **SQLAlchemy 2.0** (async) for the domain database, **Alembic** for migrations
- **PostgreSQL 16** in production/staging, **SQLite** for local dev and unit tests
- **psycopg3** connection pool for LangGraph's checkpointer (separate from the SQLAlchemy pool)
- **Anthropic Claude** — Sonnet 4.5 for coaching, Haiku 4.5 for safety classification
- **structlog** for structured logging with PHI scrubbing
- **Pydantic v2** for settings and data validation
- **Ruff** (lint + format), **pyright** (strict on `src/`, basic on `tests/`), **pytest**

## Architecture

A request flows through the system in this order:

1. **Inbound** — Patient message arrives via `/v1/chat` (direct) or `/webhooks/medbridge` (from MedBridge Go). Webhooks are HMAC-verified.
2. **Consent gate** — Every interaction verifies the patient is logged in and has consented to outreach. Fails closed.
3. **Crisis pre-check** — Patient messages (not scheduler-initiated) are classified for self-harm/crisis signals. Explicit crisis triggers an immediate durable clinician alert.
4. **History management** — Long conversation threads are summarized and trimmed to keep the LLM context window manageable.
5. **Phase routing** — Deterministic dispatch to the correct agent node based on patient phase (`PENDING`, `ONBOARDING`, `ACTIVE`, `RE_ENGAGING`, `DORMANT`).
6. **LLM generation** — Phase-specific agent generates a response, optionally calling tools (`set_goal`, `alert_clinician`, etc.). Tools accumulate side effects in state rather than writing directly.
7. **Safety gate** — The outbound message is classified before delivery. Unsafe content triggers a retry (once) or falls back to a hardcoded safe message.
8. **Save** — `save_patient_context` atomically flushes all accumulated effects (goals, alerts, jobs, outbox entries, audit events) to the database.
9. **Delivery** — The `DeliveryWorker` polls the outbox and transports messages, re-checking consent for patient messages before send.

Proactive outreach follows a parallel path: the `SchedulerWorker` polls `scheduled_jobs`, acquires a patient advisory lock, and invokes the graph with `invocation_source="scheduler"` (no patient message in context).

### Deployment modes

The app runs in three modes via `--mode` flag:
- **`api`** — HTTP server only
- **`worker`** — Scheduler + delivery worker only (no HTTP)
- **`all`** — Both (default, suitable for dev)

## Directory structure

```
src/health_coach/
  __main__.py          # CLI entry point (--mode, --host, --port)
  main.py              # FastAPI app factory, lifespan, worker spawning
  settings.py          # Pydantic BaseSettings (.env binding)
  agent/               # LangGraph graph, state, nodes, tools, prompts
    graph.py           # StateGraph definition (13 nodes + routing)
    state.py           # PatientState TypedDict, PendingEffects
    nodes/             # One module per graph node
    tools/             # LLM-callable tools (set_goal, alert_clinician, etc.)
    prompts/           # System/phase prompts as Python strings
  api/                 # FastAPI routes and middleware
    routes/            # chat, webhooks, state queries, health
    middleware/        # Request logging
    dependencies.py    # Auth context extraction
  domain/              # Pure business logic (no I/O)
    phase_machine.py   # Deterministic FSM (5 phases, 7 transitions)
    safety.py          # SafetyDecision enum, fallback messages
    scheduling.py      # Send-time calculation, quiet hours, jitter
    consent.py         # ConsentService interface + implementations
    backoff.py         # Disengagement escalation logic
  integrations/        # External service clients
    model_gateway.py   # LLM model selection (Anthropic primary, OpenAI fallback)
    medbridge.py       # MedBridge Go API client + webhook verification
    notification.py    # Patient message transport (stub)
    alert_channel.py   # Clinician alert transport (stub)
  orchestration/       # Background workers
    scheduler.py       # SchedulerWorker (polls scheduled_jobs)
    delivery_worker.py # DeliveryWorker (polls outbox)
    jobs.py            # Job handlers (followup, onboarding_timeout)
    reconciliation.py  # Sweep for missing scheduled jobs
  observability/       # Logging and monitoring
    logging.py         # structlog config, PHI scrubber processor
  persistence/         # Database layer
    db.py              # Engine, session factory, checkpointer pool
    models.py          # SQLAlchemy ORM models (12 tables)
    locking.py         # PostgreSQL advisory locks
    repositories/      # Data access (patient, audit)
    schemas/           # Pydantic validation schemas

tests/
  unit/                # Fast, SQLite-backed (172 tests)
  integration/         # Requires PostgreSQL
  safety/              # LLM-based safety boundary tests
  contract/            # Webhook payload contract tests
  evals/               # DeepEval LLM-as-judge (excluded from default run)

alembic/               # Database migrations
demo-ui/               # React + Vite chat UI (dev/staging only)
docs/                  # ADRs, PHI data flow, release runbook, intended use
```

## Core concepts

### Patient phases

The patient lifecycle is a deterministic FSM with 5 phases and 7 transitions:

- **PENDING** — Initial state. Template welcome message, no LLM.
- **ONBOARDING** — LLM elicits and confirms an exercise goal. Exit: goal confirmed (-> ACTIVE) or timeout (-> DORMANT).
- **ACTIVE** — Scheduled follow-ups on Day 2, 5, 7. LLM coaches on adherence. Exit: unanswered outreach (-> RE_ENGAGING).
- **RE_ENGAGING** — Escalated tone, multiple retries. Exit: patient responds (-> ACTIVE) or 3 unanswered (-> DORMANT with clinician alert).
- **DORMANT** — No outbound messages. Exit: patient returns (-> RE_ENGAGING).

Phase transitions are triggered by application code events, never by the LLM.

### Pending effects

Graph nodes do not write to the domain database. Instead, tools and nodes accumulate side effects in `state["pending_effects"]` — a typed dict of goals, alerts, jobs, outbox entries, safety decisions, and audit events. The `save_patient_context` node flushes everything atomically at the end.

Two exceptions exist for durability reasons:
- `crisis_check` writes `ClinicianAlert` + `OutboxEntry` immediately (must survive crashes)
- `consent_gate` writes an audit event on denial (exits before save runs)

### Safety pipeline

Three layers, each with distinct failure semantics:

| Layer | Position | On error |
|---|---|---|
| Consent gate | Before graph | Block (fail-closed) |
| Crisis pre-check | Input-side | Escalate to clinician (false positive preferred) |
| Safety gate | Output-side | Block message, use hardcoded fallback |

The safety gate allows one retry with an augmented prompt before falling back. Hardcoded fallback messages are not LLM-generated — they are static, pre-approved strings.

### Outbox pattern

All outbound messages (patient messages and clinician alerts) are written to the `outbox_entries` table as part of the same transaction as domain state changes. The `DeliveryWorker` polls the outbox and handles transport, retries, and dead-lettering. This guarantees at-least-once delivery without distributed transactions.

## Key patterns and conventions

- **Async-first** — All I/O uses `async`/`await`. SQLAlchemy async sessions with `expire_on_commit=False` and `lazy="raise"`.
- **Immutable accumulation** — Tools return `Command(update={...})` with `InjectedToolCallId` to propagate effects. `InjectedState` is read-only for tools — direct mutations are silently discarded.
- **Idempotency keys** — All domain writes use unique `idempotency_key` columns. Keys are content-hashed with `hashlib.sha256` (never Python's `hash()`, which is salted per process).
- **`StrEnum` + `String(20)`** for phase/status columns (not native PG ENUM) — preserves SQLite compatibility for tests.
- **`__all__` exports** in `__init__.py` files for public API surfaces.
- **Type hints** on all public functions. pyright strict on `src/`, relaxed on `tests/`.

## Data layer

12 SQLAlchemy ORM tables, all with UUID primary keys:

| Table | Purpose |
|---|---|
| `patients` | Core entity: phase, timezone, unanswered_count |
| `patient_goals` | Goal text + structured JSON, idempotency-keyed |
| `patient_consent_snapshots` | Append-only consent audit trail |
| `scheduled_jobs` | Scheduler work queue (SKIP LOCKED claiming) |
| `outbox_entries` | Durable delivery queue (priority-ordered) |
| `delivery_attempts` | Per-transport audit trail |
| `clinician_alerts` | Alert records with acknowledgment tracking |
| `safety_decision_records` | Every safety classification (6-year HIPAA retention) |
| `audit_events` | Append-only event log (no FK to patients) |
| `conversation_threads` | Thread metadata |
| `messages` | Message content (PHI) |
| `processed_events` | Webhook deduplication |

Migrations are managed by Alembic. The initial migration creates all tables in a single revision.

**Two connection pools**: The SQLAlchemy async pool serves the application; a separate psycopg3 `AsyncConnectionPool` serves the LangGraph checkpointer. Their lifecycles are managed independently in the FastAPI lifespan.

## API surface

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/chat` | SSE-streaming chat (patient message -> coach response) |
| `POST` | `/webhooks/medbridge` | Inbound events from MedBridge Go (HMAC-verified) |
| `GET` | `/v1/patients/{id}/phase` | Current patient phase |
| `GET` | `/v1/patients/{id}/goals` | Patient goals |
| `GET` | `/v1/patients/{id}/alerts` | Clinician alerts |
| `GET` | `/v1/patients/{id}/safety-decisions` | Safety decision history |
| `GET` | `/health` | Liveness check |

The webhook endpoint handles three event types: `patient_message` (invokes graph), `consent_change` (updates snapshot), and `patient_login` (logged only).

## Environment and config

Required:

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `ANTHROPIC_API_KEY` | Anthropic API key for LLM calls |
| `ENVIRONMENT` | `dev`, `staging`, or `production` |

Optional:

| Variable | Default | Description |
|---|---|---|
| `APP_MODE` | `all` | `api`, `worker`, or `all` |
| `MEDBRIDGE_API_URL` | — | MedBridge Go API base URL (required for real consent) |
| `MEDBRIDGE_WEBHOOK_SECRET` | — | HMAC secret for webhook verification |
| `LOG_FORMAT` | `console` | `json` or `console` |
| `LOG_LEVEL` | `INFO` | Standard Python log levels |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Server bind address |

When `MEDBRIDGE_API_URL` is not set, the system uses `FakeConsentService` (always allows). This must not happen in production.

## Testing

```bash
pytest                              # Unit tests (172 tests, ~3s)
pytest tests/integration/ -m integration  # Integration tests (PostgreSQL required)
DEEPEVAL_TELEMETRY_OPT_OUT=1 pytest tests/evals/  # LLM evals (24 tests, ~2min, API key required)
```

- **Unit tests** use SQLite and `FakeModelGateway` — no external dependencies.
- **Integration tests** require PostgreSQL (for advisory locks, SKIP LOCKED).
- **Safety tests** use `FakeModelGateway` with controlled responses to verify safety pipeline routing.
- **Contract tests** verify webhook payload schemas.
- **Evals** use DeepEval `GEval` with Anthropic Claude Haiku as judge (not OpenAI). Excluded from default `pytest` run via `addopts = "--ignore=tests/evals"`.
- **Property-based tests** use Hypothesis `RuleBasedStateMachine` for the phase machine FSM.

Key fixture: `asyncio_default_fixture_loop_scope = "session"` (pytest-asyncio 1.x compatibility).

## Important decisions and tradeoffs

See `docs/decisions.md` for the full ADR log (ADR-001 through ADR-007). Key highlights:

**Single StateGraph, no subgraphs (ADR-001).** Five phases don't justify subgraph complexity. Phase-specific behavior is handled by conditional routing to different agent nodes within one graph. Revisit if HITL interrupts are needed per-phase.

**One persistent thread per patient (ADR-002).** All conversations accumulate in a single LangGraph thread for conversational continuity. Trade-off: unbounded checkpoint growth, mitigated by history summarization. Changing the thread ID scheme post-production is a HIPAA change-management event (checkpoint blobs contain PHI).

**Intent accumulation, not direct writes (ADR-003).** Nodes accumulate effects in state; `save_patient_context` flushes atomically. This makes graph replay safe — a failed invocation leaves the domain DB unchanged. The two exceptions (crisis alert, consent denial) are narrowly scoped and documented.

**Asymmetric safety failure modes (ADR-005).** Output safety fails closed (blocks message). Crisis pre-check fails by escalating (sends clinician alert). The asymmetry is intentional: blocking a safe message is acceptable; missing a suicidal patient is not.

**Advisory lock at call site, not in graph (ADR-006).** The lock must span the entire graph invocation to prevent concurrent state corruption. Placing it inside a node would release too early. Uses `AUTOCOMMIT` isolation to avoid idle-in-transaction during LLM latency.

## Gotchas

- **`InjectedState` is read-only for tools.** Mutations to the injected dict are silently discarded by `ToolNode`. Side-effecting tools must return `Command(update={...})`.
- **Never use Python's `hash()` for cross-process coordination.** `PYTHONHASHSEED` randomizes hashes per process. Use `hashlib.sha256` for lock keys and idempotency keys.
- **`GenericFakeChatModel` doesn't support `bind_tools()`.** In tests, construct `AIMessage(tool_calls=[...])` directly.
- **Two connection pools with independent lifecycles.** The SQLAlchemy pool and the psycopg3 LangGraph pool are not interchangeable. Both must be properly closed on shutdown.
- **`FakeConsentService` in production is a compliance violation.** The system silently falls back to it when `MEDBRIDGE_API_URL` is unset. The release runbook explicitly checks for this.
- **Scheduler tests require PostgreSQL.** SQLite lacks `SELECT ... FOR UPDATE SKIP LOCKED`.
- **DeepEval defaults to OpenAI as judge.** The eval tests explicitly use `AnthropicModel` — removing this would cause failures or unexpected OpenAI API calls.
- **`max_tokens` must be set explicitly on `ChatAnthropic`.** There is no safe default in the langchain-anthropic 1.x series.
- **Safety classifier model (`claude-haiku-4-5-20251001`)** — Haiku 3 retires April 20, 2026. The model identifier must be updated before then.
- **`DEEPEVAL_TELEMETRY_OPT_OUT` must be `1`** (numeric), not `YES` or `true`.
