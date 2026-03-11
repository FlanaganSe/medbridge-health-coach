# Implementation Research Index

**Date:** 2026-03-10
**Status:** Complete — 6 targeted research investigations
**Purpose:** Consolidated index and critical findings for implementation planning. Each section links to the detailed research file.
**Input:** PRD v1.6, FINAL_CONSOLIDATED_RESEARCH.md, live documentation verification

---

## Research Files

| # | File | Lines | Scope |
|---|------|-------|-------|
| 1 | `research.md` | 851 | LangGraph 1.x API patterns — StateGraph, Runtime/context_schema, Command, checkpointer, Store, ToolNode, streaming, thread management |
| 2 | `research-fastapi-sqlalchemy.md` | 996 | FastAPI lifespan, SQLAlchemy 2.0 async, Pydantic v2, Alembic, SSE streaming, health endpoints, two-pool architecture |
| 3 | `research-safety-llm.md` | 737 | Multi-layer safety pipeline, LLM-as-classifier, crisis detection, Anthropic/OpenAI/Bedrock APIs, fallback patterns |
| 4 | `research-scheduling-observability.md` | 1012 | SKIP LOCKED job scheduling, outbox pattern, structlog+OTEL, audit events, timezone/quiet hours |
| 5 | `research-testing-setup.md` | 1083 | pytest-asyncio, LangGraph testing, async SQLAlchemy tests, respx, time-machine, hypothesis, DeepEval, uv, Ruff, pyright, CI, Docker |
| 6 | `research-domain-model.md` | 1163 | State machine, consent, goals, audit schema, multi-tenancy/RLS, repository pattern, LangGraph↔DB synchronization, idempotency |

---

## Critical Corrections to FINAL_CONSOLIDATED_RESEARCH.md

These findings SUPERSEDE the older consolidated research where they conflict:

| # | Topic | Old (Wrong) | New (Correct) | Source |
|---|-------|-------------|---------------|--------|
| 1 | `langchain-anthropic` version | `>=0.3` (line 594) | `>=1.3.4` — 0.3 series dead since Oct 2025 | research-safety-llm.md |
| 2 | Structured outputs | Beta header `anthropic-beta: structured-outputs-2025-11-13` | **GA** — beta header deprecated; `with_structured_output(method="json_schema")` works transparently | research-safety-llm.md |
| 3 | Safety classifier output | Multi-boolean `{clinical: bool, crisis: bool, jailbreak: bool}` | Single `SafetyDecision` enum (SAFE/CLINICAL_BOUNDARY/CRISIS/JAILBREAK) — eliminates ambiguous states | research-safety-llm.md |
| 4 | Shared checkpointer/Store pool | "One psycopg3 pool shared by both" (line 443-451) | **Two separate pools required** — Pool A (SQLAlchemy) and Pool B (psycopg3 for LangGraph). `autocommit=True` on LangGraph pool is incompatible with SQLAlchemy | research-fastapi-sqlalchemy.md |
| 5 | `config_schema` | Used in examples | **Deprecated** since 0.6 — use `context_schema` exclusively; removed in 2.0 | research.md |
| 6 | `create_react_agent` | Referenced as option | **Deprecated** in 1.x, removal in 2.0 — use explicit StateGraph construction | research.md |
| 7 | OpenAI `max_tokens` | Used as parameter name | OpenAI deprecated `max_tokens` Sep 2024 — use `max_completion_tokens` | research-safety-llm.md |
| 8 | Haiku model ID | `claude-3-haiku-20240307` mentioned | Retires **April 20, 2026** — must use `claude-haiku-4-5-20251001` | research-safety-llm.md |
| 9 | PatientPhase enum | `(str, Enum)` mixin | Use `enum.StrEnum` (Python 3.12+) — avoids subtle psycopg2/psycopg3 storage differences; store as `String(20)` not native PG ENUM | research-domain-model.md |
| 10 | LangGraph streaming | `astream_events(version="v2")` | Both work, but `astream(version="v2")` with `StreamPart` dicts is the preferred path in 1.1+ | research.md |
| 11 | `GenericFakeChatModel` | Implied usable for tool-calling tests | Does **NOT** support `bind_tools()` — construct `AIMessage(tool_calls=[ToolCall(...)])` directly | research-testing-setup.md |
| 12 | Procrastinate 3.7.2 | Listed as scheduler candidate | Custom `scheduled_jobs` table is the confirmed choice per PRD §8.1; Procrastinate only if custom worker becomes harder to maintain | research-scheduling-observability.md |

---

## Mandatory Implementation Constraints

These are non-negotiable and must be enforced structurally:

### Database & ORM
- `expire_on_commit=False` on all async sessions
- `pool_pre_ping=True` on async engine
- `lazy="raise"` on all ORM relationships — catches implicit lazy loads
- `write_only=True` on append-only collections (audit events)
- `NullPool` in Alembic migrations — avoids lifecycle conflicts
- `MetaData(naming_convention=...)` on Base — required before any model declarations
- `postgresql+psycopg://` URL scheme must be explicit — `postgresql://` selects wrong dialect
- `String(20)` for enum columns, not native PG ENUM — SQLite compatibility

### LangGraph
- `autocommit=True`, `prepare_threshold=0`, `row_factory=dict_row` on checkpointer pool
- `# type: ignore[arg-type]` on `add_conditional_edges` (issue #6540, still open)
- `max_tokens` must be set explicitly on `ChatAnthropic` — no safe default in 1.x
- `parallel_tool_calls=False` on `llm.bind_tools()` when exactly one tool call is desired
- `recursion_limit` set via config at invoke time (not on `compile()`)
- `context_schema` (not `config_schema`) for dependency injection

### Safety
- Alert intent row written BEFORE patient-facing safe message delivery
- Outbox INSERT in same `session.begin()` block as domain state write
- Jailbreak and crisis decisions NEVER retry — only CLINICAL_BOUNDARY retries
- `DEEPEVAL_TELEMETRY_OPT_OUT=1` (numeric 1, not YES)

### Observability
- `clear_contextvars()` per-request in async middleware — prevents context bleed
- `REVOKE UPDATE, DELETE, TRUNCATE` on audit table (primary control) + `BEFORE UPDATE OR DELETE` trigger (defense-in-depth)
- Never log message content — only opaque UUIDs
- `tzdata` as explicit runtime dependency for containers

### Testing
- `asyncio_default_fixture_loop_scope = "session"` — `event_loop` fixture gone in pytest-asyncio 1.x
- Session-scoped engine, function-scoped session with `join_transaction_mode="create_savepoint"`
- Scheduler tests MUST run against PostgreSQL (SQLite lacks SKIP LOCKED)
- `max_retries=0` on both primary and fallback with `with_fallbacks()` — built-in retries prevent fallback triggering

---

## Key Implementation Patterns by Milestone

### M1: Foundation
- FastAPI `lifespan` context manager (not deprecated `on_event`)
- Two-pool architecture in lifespan: SQLAlchemy engine + psycopg3 `AsyncConnectionPool(open=False)` → `await pool.open()`
- structlog with JSON (prod) / ConsoleRenderer (dev) via settings flag
- `/health/live` (no DB) + `/health/ready` (checks both pools)
- Docker two-stage build with BuildKit cache mount for dependency layer
- CI: 4 parallel jobs (lint, typecheck, test-unit SQLite, test-integration PostgreSQL)
- `astral-sh/setup-uv@v7` + `enable-cache: true`

### M2: Domain Core
- `PatientPhase` as `StrEnum` stored in `String(20)`
- `transition(current, event) -> next` pure-Python adjacency map — raises `PhaseTransitionError`
- `ConsentService.check()` wraps MedBridge Go call in broad `except Exception` → denied on any failure
- Immutable `PatientConsentSnapshot` per check
- `AuditEvent` with NO FK to patients (must survive patient deletion for 6-year HIPAA retention)
- `BaseRepository[ModelT]` generic with `flush()` in create (not commit)
- Session commit owned by FastAPI dependency (HTTP) or `async with session.begin()` (workers)
- `ProcessedEvent` table for inbound deduplication

### M3: Graph Shell
- `StateGraph(PatientState, context_schema=CoachContext)` construction
- `runtime: Runtime[CoachContext]` parameter in node signatures
- `Command(update={...}, goto="node_name")` for in-node routing decisions
- `add_conditional_edges` for state-based routing (with `# type: ignore[arg-type]`)
- `ToolNode(tools)` + `tools_condition` for tool execution routing
- `InMemorySaver` + `InMemoryStore` for all graph tests
- Construct `AIMessage(tool_calls=[...])` directly for fake tool-calling responses

### M4: Safe Onboarding
- Input crisis pre-check as separate light call BEFORE main generation
- `ClassifierOutput` with single `SafetyDecision` enum + `CrisisLevel` + `confidence`
- Retry: append `HumanMessage` with augmented constraint — not system prompt replacement
- `with_structured_output(method="json_schema", strict=True)` for goal extraction
- `ExtractedGoal` Pydantic model with field-level descriptions (flow into JSON schema)
- Store `raw_patient_text` in ORM for audit; exclude from `GoalRead` (PHI minimization)

### M5: Follow-up & Lifecycle
- `scheduled_jobs` table with `SELECT ... FOR UPDATE SKIP LOCKED` via `.with_for_update(skip_locked=True)`
- Job claim + status transition in same transaction
- Startup reconciliation: reset `processing` → `pending` for crashed jobs
- Idempotency keys: `{patient_id}:{job_type}:{reference_date}` with `INSERT ... ON CONFLICT DO NOTHING`
- `zoneinfo.ZoneInfo` + `tzdata` for timezone handling
- Quiet hours: build local datetime, enforce 9 PM–8 AM, convert to UTC
- Jitter: 0–30 minutes uniform random for day-scale scheduling

### M6: Integration & Delivery
- Outbox table: stores `message_ref_id` (UUID), never raw text (PHI safety)
- Delivery worker: 5–10 second poll, `priority DESC, created_at ASC` ordering (urgent alerts first)
- `with_fallbacks()` with `max_retries=0` on both primary and fallback
- SSE: `StreamingResponse` + async generator, headers `Cache-Control: no-cache`, `X-Accel-Buffering: no`
- `astream(version="v2")` for typed streaming from LangGraph

### M7: Release Hardening
- DeepEval evals gated to `main` branch only (make real LLM API calls)
- `RuleBasedStateMachine` (hypothesis) for phase transition invariant testing
- Non-root `appuser` in Docker
- `min-instances >= 1` for container deployments

---

## Dependency Versions (Confirmed March 2026)

```
langgraph>=1.1.0
langgraph-checkpoint-postgres>=3.0.4
langgraph-checkpoint-sqlite>=3.0.3
langchain-anthropic>=1.3.4
langchain-openai>=1.1.11
fastapi>=0.115
uvicorn[standard]>=0.30
sqlalchemy[asyncio]>=2.0.48,<2.1
psycopg[binary,pool]>=3.2
alembic>=1.14
pydantic>=2.0
pydantic-settings>=2.0
structlog>=24.0
httpx>=0.27
stamina>=25.0
tzdata>=2024.1

# Dev
pytest>=8.0
pytest-asyncio>=1.3.0
pytest-cov>=5.0
pyright>=1.1
ruff>=0.8
deepeval>=1.0
respx>=0.21
time-machine>=2.0
hypothesis>=6.0
```

---

## Open Research Gaps

Items that remain unresolved and need answers before or during implementation:

1. **MedBridge Go API contract** — consent endpoint, webhook schema, auth mechanism
2. **Clinician alert channel** — email/Slack webhook vs dashboard integration
3. **Cloud platform** — GCP vs AWS (affects deployment workflow, managed services)
4. **Patient timezone source** — where does the IANA timezone come from?
5. **Retention/deletion policy** — checkpoint blob retention, audit retention beyond 6-year minimum
6. **LangGraph Store decision** — PRD says "keep optional"; current research confirms domain DB is sufficient for MVP. Defer Store unless cross-thread memory needs exceed relational model.
7. **`get_runtime()` in tools** — still broken (issue #6431). Use `InjectedStore`/`InjectedState` annotations. Monitor for fix.
8. **`get_stream_writer()` in async tools** — still broken (issue #6447). Use `StreamWriter` parameter injection as workaround.
