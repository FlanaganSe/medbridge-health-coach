# Research Summary for Milestone Plan Review

**Date:** 2026-03-10
**Purpose:** Distilled critical constraints, recommended patterns, known issues, and ordering dependencies from four research files — for use when reviewing a milestone implementation plan.

---

## 1. Safety Pipeline and LLM APIs (`research-safety-llm.md`)

### Critical Constraints

- **Every outbound message must pass the output safety gate** — no exceptions, PRD §5.3.
- **Two classifier passes are required:** (1) input crisis pre-check on the patient message before generation starts; (2) output classifier on the coach reply before delivery. These are separate invocations with separate prompts and schemas.
- **Crisis path does not retry** — jailbreak and crisis results go directly to safe fallback. Only `CLINICAL_BOUNDARY` gets one retry with an augmented prompt.
- **Alert intent row must be written BEFORE the patient-facing message is delivered** — crash durability. The outbox worker handles actual transport. If the process crashes between these two steps, the alert still survives.
- **Idempotency key format for alerts:** `crisis:{patient_id}:{conversation_id}:{turn_number}` — prevents duplicate alerts on crash recovery.
- **Classifier failure mode is conservative block** — if the classifier times out, returns invalid JSON, or raises, treat the result as `CLINICAL_BOUNDARY` (blocked). Never fall back to a different vendor mid-request on the classifier path.
- **Do not use Anthropic Batch API, Code Execution, or Files API on PHI paths** — these are not ZDR-eligible and have multi-day retention.
- **`max_tokens` must be set explicitly on every `ChatAnthropic` instance** — no safe default in the 1.x series. Classifier: `max_tokens=512`. Main gen: `max_tokens=4096` or more.
- **`max_retries=0` on both primary and fallback when using `with_fallbacks()`** — built-in retry logic prevents the fallback from triggering.

### Recommended Patterns vs Rejected Alternatives

| Decision | Recommended | Rejected |
|---|---|---|
| Classifier output format | Single `SafetyDecision` enum with priority ordering | Multi-boolean flags (create ambiguous states) |
| Classifier vendor fallback | None — block conservatively if classifier fails | Cross-vendor fallback on classifier path (two BAA surfaces mid-request) |
| Main gen model | `claude-sonnet-4-6` (1.29% injection success rate) | Sonnet 4.5 (49.36% injection rate) |
| Classifier model | `claude-haiku-4-5-20251001` | `claude-3-haiku-20240307` — **retires April 20, 2026** |
| Structured outputs | `with_structured_output(method="json_schema")` — GA | Old beta header `anthropic-beta: structured-outputs-2025-11-13` — deprecated |
| OpenAI API for fallback | Chat Completions | Responses API (stores state by default, ZDR complications) |
| Bedrock | Registered factory option, deferred for MVP | Wired as primary or secondary (adds asyncpg + IAM complexity) |
| Input marking | `<patient_message>` XML tags as delimiter | No delimiter (context confusion attacks succeed more easily) |

### Known Issues and Gotchas

- Haiku 3 (`claude-3-haiku-20240307`) retires April 20, 2026. Any reference to it in a plan is a defect.
- `FINAL_CONSOLIDATED_RESEARCH.md:594` incorrectly shows `langchain-anthropic>=0.3`. Use `>=1.3.4`.
- OpenAI `max_tokens` is deprecated (Sep 2024); use `max_completion_tokens` in new code.
- Bedrock structured output (`ChatBedrockConverse.with_structured_output()`) uses tool-call forcing, not constrained decoding — schema compliance is not guaranteed. Add application-side validation if Bedrock is used for classification.
- Rate limit 429 from Anthropic: decide at design time whether to fall back to OpenAI on rate limits. It adds HIPAA surface even if the BAA exists.

### Ordering Dependencies

1. BAA with primary vendor (Anthropic) must be in place before any PHI flows.
2. Classifier must be wired and tested before the main generation node — the input pre-check gate is the first node a message hits.
3. Alert intent table (see scheduling section) must exist before the crisis handler can write to it.
4. Outbox/delivery worker (see scheduling section) must be operational before clinician alerts can actually reach staff.

---

## 2. Scheduling, Outbox, and Observability (`research-scheduling-observability.md`)

### Critical Constraints

- **`SELECT FOR UPDATE SKIP LOCKED` is the only safe multi-worker claim mechanism** — the claim and the status transition to `processing` must happen in the same transaction.
- **Scheduler tests MUST use PostgreSQL** — SQLite does not support `SKIP LOCKED`. This is a hard split between unit (SQLite) and integration (PostgreSQL) test environments.
- **Startup reconciliation is required** — crashed workers leave jobs stuck in `processing`. The reconciliation loop resets stale `processing` rows to `pending` before the poll loop starts. Without it, crashed jobs are lost permanently.
- **Outbox INSERT must be in the same transaction as the domain state write** — this is the atomicity guarantee. If they are in separate transactions, crash scenarios produce either lost messages or orphan audit records.
- **Audit events are permanent** — HIPAA 6-year retention. `REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM health_coach_app` must be in the migration SQL. A defense-in-depth trigger should also be added. No migration may drop or truncate the table.
- **Audit event and domain operation must commit atomically** — if the domain write rolls back, the audit event must too. `emit_audit_event()` must be called inside the caller's `session.begin()` block, not start its own.
- **`AuditEvent` has NO FK to `patients`** — audit records must survive patient record deletion for HIPAA retention. This is architectural, not optional.
- **OTEL span attributes must not contain PHI** — if using a hosted OTEL backend, BAA coverage must be confirmed. Use opaque UUIDs (`patient_id`, `job_id`) only.
- **`structlog.contextvars.clear_contextvars()` must be called at the start of every request** — prevents context bleeding between async requests.
- **`tzdata` must be a runtime dependency** — containers often strip system tzdata; `zoneinfo.ZoneInfo` silently fails without it.

### Recommended Patterns vs Rejected Alternatives

| Decision | Recommended | Rejected |
|---|---|---|
| Job scheduler | Custom `scheduled_jobs` polling worker | APScheduler, Procrastinate, cloud scheduler (for MVP) |
| Outbox delivery trigger | Simple polling every 5–10s with SKIP LOCKED | PostgreSQL LISTEN/NOTIFY (evaluate in M6 if alert latency matters) |
| Observability backend | Emit OTEL to stdout/cloud log drain | Langfuse v3 (requires ClickHouse + Redis + S3), Arize Phoenix (Phase 2) |
| Timezone math | `zoneinfo.ZoneInfo` + TIMESTAMPTZ storage in UTC | Naive datetimes, pytz |
| Idempotency | `INSERT ON CONFLICT DO NOTHING` with deterministic key | Pre-query check (race condition), random UUID (not idempotent) |
| Dead letters | `status = 'dead'` on same table | Separate dead-letter table (unnecessary at MVP scale) |

### Known Issues and Gotchas

- The poll worker must commit the status change to `processing` BEFORE processing the job — this releases the SKIP LOCKED row lock. If processing happens inside the lock transaction, the lock is held for the full job duration, blocking other workers.
- Exponential backoff for failed jobs: `backoff_seconds = 60 * (2 ** job.attempts)`. This means attempts 0,1,2 map to 60s, 120s, 240s. Make sure `max_attempts` is set to a value that results in reasonable total retry windows.
- Outbox poll interval must be shorter than job scheduler poll interval — patient message delivery needs ~5–10s; job scheduling can be 30s.
- Idempotency key for backoff retries must include the attempt number: `{patient_id}:backoff_check:{date}:attempt_2` — otherwise the same logical slot can't be re-inserted after cancellation.
- When a patient responds, pending backoff jobs should be cancelled (status set to `completed`) — document this as a step in the "patient responds" flow.

### Ordering Dependencies

1. `audit_events` table must exist in the first migration batch — every subsequent domain operation depends on being able to audit itself.
2. REVOKE statement must be in the same migration as the table creation — not a deferred step.
3. Structlog and OTEL must be configured at app startup (lifespan) before the first request — SQLAlchemy and httpx auto-instrumentation must run after engine creation.
4. `checkpointer.setup()` and `store.setup()` are one-time migration scripts — must run after Alembic migrations but before the app serves traffic.
5. Outbox table and delivery worker must exist before the safety pipeline can durably deliver crisis alerts.
6. `scheduled_jobs` table must exist before any follow-up scheduling code runs.

---

## 3. Testing Patterns and Project Setup (`research-testing-setup.md`)

### Critical Constraints

- **`asyncio_mode = "auto"` and `asyncio_default_fixture_loop_scope = "session"` are both required** — missing `asyncio_default_fixture_loop_scope` causes `ScopeMismatch` in pytest-asyncio 1.x when session-scoped async fixtures exist.
- **The `event_loop` fixture is removed in pytest-asyncio 1.x** — any code that overrides it will fail. Loop management is now via `loop_scope`.
- **`GenericFakeChatModel` does NOT implement `bind_tools()`** — raises `NotImplementedError`. Use explicit `AIMessage(tool_calls=[ToolCall(...)])` construction for tool call testing.
- **Scheduler tests must use PostgreSQL** — do not attempt to test SKIP LOCKED against SQLite; it silently returns incorrect behavior.
- **`DEEPEVAL_TELEMETRY_OPT_OUT=1` (numeric `1`, not `YES`)** — the env var's truthy check was changed to numeric in a 2025 patch; `YES` may silently fail.
- **Never share `AsyncSession` across tests** — each test must use a function-scoped session. Session sharing causes subtle state pollution.
- **Never call real LLM APIs in unit or integration tests** — use `GenericFakeChatModel` or `respx` mocks. Real calls go in `tests/evals/` only.
- **No PHI in any test data** — synthetic data only in all non-production environments.
- **Two pools must remain separate in tests too** — Pool A (SQLAlchemy) for test queries, Pool B (psycopg3 `InMemorySaver`) for LangGraph. Do not share.
- **`pyright strict` on `src/` only** — `tests/` uses `--level basic` to avoid annotation noise.

### Recommended Patterns vs Rejected Alternatives

| Decision | Recommended | Rejected |
|---|---|---|
| DB isolation | Session-scoped engine + function-scoped `AsyncSession(join_transaction_mode="create_savepoint")` + rollback | Truncating tables between tests, creating new DB per test |
| LangGraph testing | Fresh graph with `InMemorySaver` per test module | Shared checkpointer across tests |
| Node isolation | `graph.nodes["node_name"].ainvoke(state)` | Invoking the full graph for single-node tests |
| Time mocking | `time_machine` (C extension, context manager) | `freezegun` (less reliable with pytest assertion rewriting) |
| HTTP mocking | `respx` (async-native, designed for httpx) | `responses`, `requests_mock` (sync-only) |
| Phase invariant testing | `hypothesis.stateful.RuleBasedStateMachine` | Hand-written parametrized sequences |
| SSE testing | Unit test the generator function directly | `ASGITransport` with `async for` (known limitation, GH issue #2186) |
| Eval CI gating | Separate workflow, branch-gated, `deepeval test run` | Running evals in every PR |
| Docker build | Two-stage, `--no-install-project` for dep cache layer | Single-stage build |

### Known Issues and Gotchas

- `join_transaction_mode="create_savepoint"` is required if app code calls `session.commit()` inside a test context — without it, a commit inside a test would actually write to the DB.
- `StaticPool` with `check_same_thread=False` is required for SQLite in-memory tests.
- `Hypothesis` `RuleBasedStateMachine` rules cannot use pytest fixtures — use `initialize()` or strategies for shared data.
- `astral-sh/setup-uv@v7` is the current GHA action (March 2026) — earlier versions are outdated.
- `COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/` is the correct pattern for Docker builds.
- DeepEval evals make real LLM API calls — cost money. CI job must be branch-gated.
- `pyright strict` does not validate ORM constructor argument types (SQLAlchemy issue #12268) — use typed repository methods.
- `# type: ignore[arg-type]` is required on every `add_conditional_edges` call (LangGraph issue #6540, open upstream).
- `ruff preview = true` must NOT be in CI config — preview rules change without notice.

### Ordering Dependencies

1. `pyproject.toml` with correct `asyncio_mode` and `asyncio_default_fixture_loop_scope` must exist before any async tests run.
2. `pyrightconfig.json` must exist before typecheck CI runs — it takes precedence over `pyproject.toml` for pyright settings.
3. `conftest.py` hierarchy must be established before test fixtures can be shared.
4. CI jobs must be split: lint → typecheck → test-unit (SQLite) → test-integration (PostgreSQL service) → docker-build. Evals in a separate workflow.
5. `uv.lock` must be committed and `uv sync --locked` used in CI — never allow floating resolution in CI.

---

## 4. FastAPI and SQLAlchemy (`research-fastapi-sqlalchemy.md`)

### Critical Constraints

- **`expire_on_commit=False` is mandatory** on `async_sessionmaker` — without it, accessing any attribute on a committed ORM object raises `MissingGreenlet` in async context.
- **`pool_pre_ping=True` is mandatory** — managed databases (Cloud SQL, RDS) have aggressive idle connection timeouts. Without pre-ping, the pool silently hands out dead connections.
- **`lazy="raise"` on all ORM relationships** — prevents accidental implicit I/O in async. Explicit eager loading is required via `selectinload` or `joinedload` in queries.
- **Database URL must use `postgresql+psycopg://`** — not `postgresql://` or `postgres://`. A `field_validator` in Settings must normalize this because many hosted tools generate the wrong scheme.
- **Two connection pools are architecturally required and incompatible** — Pool A (SQLAlchemy `AsyncAdaptedQueuePool`) for app queries; Pool B (psycopg3 `AsyncConnectionPool`) for LangGraph. Pool B requires `autocommit=True` and `row_factory=dict_row`. These settings make Pool B incompatible with SQLAlchemy's session management.
- **Pool B must use `open=False` in constructor** and `await lg_pool.open()` in the lifespan — prevents the pool opening before the event loop is established.
- **`@app.on_event` is deprecated** — use `@asynccontextmanager` + `lifespan=` parameter.
- **`checkpointer.setup()` and `store.setup()` must NOT run at app startup** — they are one-time migration scripts called from a separate CLI script.
- **Alembic must use `NullPool` in migrations** — avoids pool lifecycle issues when running via `asyncio.run()`.
- **Naming convention on `Base.metadata` is mandatory** — without it, Alembic generates anonymous constraint names that break deterministic schema diffing.
- **`audit_events` relationship on `Patient` must use `write_only=True`** — audit events are append-only and must never be loaded into memory as a collection.
- **Never expose stack traces or internal error details in API responses** — use domain error hierarchy; map to HTTP codes in route handlers, not domain code.
- **Never log PHI** — patient name, phone, email, DOB, raw message content, and goal text are all PHI. Log only opaque UUIDs and operational metadata.
- **`SecretStr` for all API keys** — prevents secrets appearing in repr or logs.

### Recommended Patterns vs Rejected Alternatives

| Decision | Recommended | Rejected |
|---|---|---|
| App lifecycle | `@asynccontextmanager` + `lifespan=` | `@app.on_event` (deprecated) |
| ORM syntax | `Mapped[T]` + `mapped_column()` | Legacy `Column()` + `relationship()` without type annotations |
| Schema migration | `Alembic -t async` + `run_sync()` pattern | Synchronous Alembic env without the async wrapper |
| Pydantic ORM mode | `ConfigDict(from_attributes=True)` + `model_validate()` | v1 `orm_mode = True` + `.from_orm()` (removed in v2) |
- `Alembic revision --autogenerate` requires human review before applying — it cannot detect column renames (generates DROP+ADD) or table renames.
- `astream_events(version="v2")` is the stable API — `v1` is legacy.
- SSE headers must include `X-Accel-Buffering: no` for nginx deployments — without it, nginx buffers the stream and the client sees nothing until the connection closes.

### Known Issues and Gotchas

- Alembic called programmatically from within an async context will fail with "event loop already running" — call migrations via CLI in CI, not from inside the app's lifespan.
- `async with await lg_pool.getconn() as conn` is the psycopg3 Pool B connection pattern — note the `await` before `lg_pool.getconn()`.
- `astream_events` returns typed `StreamPart` dicts in v2 — filter by `event.get("event")` kind, not by index.
- `pool_recycle=1800` (30 min) is recommended for Pool A — prevents long-lived idle connections from being killed by the database server.
- Pyright strict does not validate ORM constructor argument types (SQLAlchemy issue #12268) — typed repository methods are the mitigation.
- `AsyncGenerator[str, None]` return type annotation on SSE generator functions is required for pyright strict — edge case issue #5411 may require `# type: ignore[return-value]` on the `StreamingResponse` line.

### Ordering Dependencies

1. `Base.metadata` naming convention must be set before any Table or model is defined — it cannot be added retroactively without regenerating all constraint names.
2. `configure_logging()` must be called before any logger is created.
3. `configure_otel()` must be called before `configure_fastapi_otel()`, and both before the first request.
4. `SQLAlchemyInstrumentor().instrument(engine=engine)` must be called after engine creation (inside lifespan, not at module import time).
5. Pool B (`lg_pool.open()`) must complete before any LangGraph graph invocation — graphs cannot run before their checkpointer's pool is ready.
6. Domain error hierarchy (`HealthCoachError` and subclasses) must exist before route handlers can catch and map errors.
7. Settings validation (including URL normalization) must happen at import time — validate-on-first-use via `@lru_cache` on `get_settings()`.

---

## Cross-Cutting Concerns

### Things That Are Frequently Wrong in Plans

1. Using `claude-3-haiku-20240307` anywhere — it retires April 20, 2026.
2. Sharing Pool A and Pool B — they have incompatible connection requirements.
3. Delivering a crisis alert directly from the graph node instead of writing to the outbox first.
4. Putting `expire_on_commit=True` (the default) on the session factory — will cause crashes in async context.
5. Missing `clear_contextvars()` at request start — causes subtle log context pollution in async workers.
6. Using `@app.on_event` — deprecated.
7. Running scheduler tests against SQLite — SKIP LOCKED is silently unsupported.
8. Setting `DEEPEVAL_TELEMETRY_OPT_OUT=YES` — use `1`.
9. Putting `checkpointer.setup()` in lifespan startup — it must be a one-time migration script.
10. Opening Pool B (`lg_pool`) in the constructor rather than in lifespan — it opens before the event loop is ready.

### Invariants That Must Hold Across All Milestones

- All outbound patient messages go through both classifier passes (input pre-check + output gate).
- Phase transitions are never LLM-decided — always application-code state machines.
- Consent is checked per-interaction, not cached from session creation.
- Alert intent row precedes patient-facing message delivery in every crisis path.
- Audit events and domain state writes commit atomically.
- No PHI appears in logs, OTEL spans, or error responses.
- Two connection pools remain permanently separate.
