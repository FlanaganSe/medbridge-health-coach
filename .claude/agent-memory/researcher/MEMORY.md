# Researcher Memory

## Project Context
- Python backend service + React demo frontend (`demo-ui/`, 18 source files, dev/staging only)
- Patient UI lives in MedBridge Go (external app) — not this repo; `demo-ui/` is for internal demos
- Stack: Python 3.12+, LangGraph, FastAPI, PostgreSQL/SQLite, uv, pytest, ruff, pyright
- Frontend stack: React 19, TypeScript 5.5 strict, Vite 6, Tailwind v4 (CSS-first, no tailwind.config.js)
- Requirements: `docs/requirements.md` (original spec; subgraph note is superseded by ADR-001)
- Authoritative system docs: `docs/product-overview.md` (comprehensive, current)
- ADRs: `docs/decisions.md` (append-only, ADR-001 through ADR-012)
- NOTE: All `.claude/plans/` files (prd.md, research.md, plan.md, research-*.md) are ephemeral — cleaned up after each task cycle. Do not reference them as permanent sources.

## LangGraph 1.x API Patterns (verified 2026-03-10)
- LangGraph 1.1.0 released 2026-03-10; checkpoint-postgres 3.0.4; checkpoint-sqlite 3.0.3
- `context_schema` (not `config_schema`, deprecated) for DI — access via `runtime: Runtime[ContextSchema]` in nodes
- `add_conditional_edges` always needs `# type: ignore[arg-type]` for pyright-strict (issue #6540, open)
- `create_react_agent` deprecated in 1.x, removed in 2.0; use explicit `StateGraph` construction
- Checkpointer Pool B MUST have `autocommit=True`, `prepare_threshold=0`, `row_factory=dict_row`
- `get_stream_writer()` does NOT work in async tools (issue #6447); use `StreamWriter` param injection
- `version="v2"` on astream/ainvoke returns typed `StreamPart` dicts + `GraphOutput` — adopt from day 1
- `InjectedStore`/`InjectedState` annotations hide tool params from LLM schema
- `InjectedState` is READ-ONLY for state propagation — mutating the injected dict does NOT update graph state (ToolNode does shallow ref inject; mutation is discarded after node returns)
- Side-effecting tools MUST return `Command(update={"pending_effects": ..., "messages": [ToolMessage(..., tool_call_id=tool_call_id)]})` — this is the ONLY way tool output reaches non-message state
- `InjectedToolCallId` annotation provides the `tool_call_id` for the mandatory `ToolMessage` in `Command.update`
- ToolNode passes `Command` returns through directly (does NOT wrap in ToolMessage); LangGraph runtime applies `Command.update`
- `Command.update` from a tool MUST include `"messages": [ToolMessage(...)]` — missing ToolMessage causes runtime error
- Read-only tools (`get_program_summary`, `get_adherence_summary`) return plain `str` — ToolNode wraps in ToolMessage automatically
- NOTE: `set_goal` tool reads patient state via InjectedState (read-only), returns new goal data via `Command(update={"pending_effects": ..., "messages": [ToolMessage(...)]})` — NOT by mutating InjectedState
- `RetryPolicy` NamedTuple: `initial_interval`, `backoff_factor`, `max_interval`, `max_attempts`, `jitter`, `retry_on`
- `RemoveMessage` does NOT cross subgraph boundaries (issue #5112) — fine for single-graph arch
- Store namespace is a tuple of strings: `namespace = ("patient_profiles", patient_id)`
- All patterns above verified against `src/health_ally/agent/` source tree

## Domain Model Patterns (verified 2026-03-10)
- `PatientPhase` as `StrEnum` stored in `String(20)` — NOT native PostgreSQL ENUM (breaks SQLite; psycopg3 edge case #13052)
- `transition(current, event) -> PatientPhase` in `domain/phase_machine.py` is the complete truth table; LLM never calls it
- `AuditEvent` has NO FK to `patients` (audit must survive patient record deletion for HIPAA 6-year retention)
- `AuditEvent` relationship on Patient uses `write_only=True` — never iterate as collection
- `REVOKE UPDATE, DELETE ON audit_events FROM healthcoach_app` in the Alembic migration (not app code)
- `load_patient_context` / `save_patient_context` are the ONLY nodes touching the domain DB — all agent nodes between them work on LangGraph state
- `PatientState` uses `total=True` with `T | None` fields — avoid `total=False` (pyright partial-return issues)
- `tenant_id` on every table; `SET LOCAL app.current_tenant_id` per session for RLS
- `INSERT ... ON CONFLICT DO NOTHING` is the idempotency primitive for inbound events and scheduled jobs
- Goal extraction: `model.with_structured_output(ExtractedGoal, method="json_schema", strict=True)`
- `ConsentService.check()` fails safe: `except Exception` → `ConsentResult(logged_in=False, outreach_consented=False)`
- GoalRead API schema EXCLUDES `raw_patient_text` field (PHI minimization)

## FastAPI + SQLAlchemy Async Patterns (verified 2026-03-10)
- `@app.on_event` is deprecated — use `@asynccontextmanager` + `lifespan=` parameter
- `expire_on_commit=False`, `pool_pre_ping=True`, `lazy="raise"` are MANDATORY (confirmed)
- Two pools are SEPARATE: Pool A = SQLAlchemy `create_async_engine`, Pool B = psycopg3 `AsyncConnectionPool`
- Pool B must use `open=False` in constructor + `await pool.open()` in lifespan
- psycopg3 URL scheme must be explicit: `postgresql+psycopg://` (not `postgresql://`)
- Alembic: init with `-t async`; use `NullPool` in migrations; `run_sync()` pattern in env.py
- `Mapped[T]` + `mapped_column()` works natively with pyright strict — no plugin needed
- `ConfigDict(from_attributes=True)` + `model_validate()` replaces v1 `orm_mode`/`from_orm()`
- `astream_events(version="v2")` is the current API (v1 is legacy)
- SSE headers: `Cache-Control: no-cache`, `Connection: keep-alive`, `X-Accel-Buffering: no`
- Naming convention on `Base.metadata` is mandatory for deterministic Alembic constraint names

## Safety Pipeline and LLM API Patterns (verified 2026-03-10)
- langchain-anthropic: 1.3.4 (Feb 24, 2026) — FINAL_CONSOLIDATED_RESEARCH.md line 594 incorrectly shows `>=0.3`; use `>=1.3.4`
- langchain-openai: 1.1.11 (Mar 9, 2026); langchain-aws: 1.4.0 (Mar 9, 2026)
- Anthropic structured outputs are GA; old beta header `structured-outputs-2025-11-13` deprecated
- API param: `output_format` → `output_config.format`; LangChain SDK abstracts this
- Haiku 4.5 (`claude-haiku-4-5-20251001`) supports structured outputs; active until Oct 15, 2026
- Haiku 3 (`claude-3-haiku-20240307`) deprecated, retires April 20, 2026 — DO NOT USE
- Sonnet 4.6 prompt injection rate: 1.29% (vs 49.36% Sonnet 4.5); ASL-3 safeguards
- `max_tokens` MUST be set on all ChatAnthropic instances; OpenAI: use `max_completion_tokens`
- `with_fallbacks()`: set `max_retries=0` on primary AND fallback — retries mask errors, preventing fallback trigger
- Classifier failure mode: block conservatively (CLINICAL_BOUNDARY), NOT fall back to different vendor
- Bedrock `ChatBedrockConverse`: structured output = tool-call forcing (not constrained decoding); issue #883
- Anthropic ZDR: per-org via account team, no API param; covers `/v1/messages` only
- NOT ZDR-eligible: Batch API, Code Execution tool, Files API — never on PHI paths
- Crisis alert order: write `alert_intents` row FIRST before patient-facing delivery (crash durability)
- Two classifier passes: (1) input pre-check on patient message, (2) output gate on coach reply

## Testing Patterns and Project Setup (researched 2026-03-10)
- pytest-asyncio 1.3 stable: `asyncio_mode = "auto"` + `asyncio_default_fixture_loop_scope = "session"`
- `event_loop` fixture REMOVED in 1.x — never override it
- `GenericFakeChatModel` does NOT implement `bind_tools()` — NotImplementedError (GH discussion #29893). Use `AIMessage(tool_calls=[ToolCall(...)])` directly for tool call testing.
- SQLAlchemy test isolation: session-scoped engine + function-scoped `AsyncSession(join_transaction_mode="create_savepoint")` + connection rollback
- SSE + ASGITransport: known limitation (GH issue #2186); test SSE generator function directly
- `DEEPEVAL_TELEMETRY_OPT_OUT=1` (numeric `1`, NOT `YES`) — post-2025 patch only accepts numeric truthy
- `deepeval test run` (not bare pytest) for full eval reporting; evals run in branch-gated separate CI job
- time-machine over freezegun; use context manager for async tests
- Hypothesis `RuleBasedStateMachine` for phase transition invariant tests; rules CANNOT use pytest fixtures
- `astral-sh/setup-uv@v7` is current (March 2026); separate CI jobs: lint, typecheck, test-unit (SQLite), test-integration (PG service), docker-build
- Docker: two-stage, `--no-install-project` for dep cache layer, then copy source; `COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/`
- Ruff select: E, W, F, I, UP, B, C4, SIM, RET, RUF, N, ANN, ASYNC, S, PTH, TC; ignore ANN101/102/401; per-file relax ANN+S in tests/

## Advisory Lock Concurrency (researched 2026-03-10)
- IMPORTANT: Lock must be acquired at the CALL SITE (FastAPI handler / scheduler job) NOT inside a graph node
  - `pg_advisory_xact_lock` (transaction-level) releases too early — during LLM calls inside the graph
  - Only `pg_advisory_lock` (session-level) on a dedicated AUTOCOMMIT connection covers the full invocation
- Use `pg_advisory_lock` (session-level) on a DEDICATED connection from Pool A, held for the full invocation
- Release it explicitly in `try/finally` before the connection returns to Pool A — no lock leak on crash (PG releases on connection close)
- Do NOT use a single long-lived `AsyncSession` across nodes (pool exhaustion: pool_size=10 exhausts at 10 concurrent LLM-phase invocations)
- Do NOT use optimistic concurrency alone: `save_patient_context` writes 6+ tables; per-table versioning is incomplete
- SQLite: skip advisory lock entirely — `if "postgresql" not in str(engine.url): yield; return`
- lock key: `hash(patient_id) & 0x7FFFFFFF` (positive bigint; advisory lock keys are bigint)
- `CoachContext` keeps `db_session_factory` as factory (Callable) — nodes open short sessions; no session spans nodes

## CI/CD State (researched 2026-03-15, verified against .github/workflows/)
- Three workflows: `ci.yml` (push/PR to main), `eval.yml` (workflow_dispatch ONLY), `deploy.yml` (v* tags + dispatch)
- `eval.yml` does NOT auto-run on push to main — workflow_dispatch only (prior memory was wrong)
- Tool: `astral-sh/setup-uv@v7` with `enable-cache: true` and explicit `python-version: "3.12"`
- Install command: `uv sync --frozen` (always frozen lockfile)
- Lint: `uv run ruff check .` + `uv run ruff format --check .`
- Typecheck: `uv run pyright .`
- Unit tests: `pytest tests/unit/ tests/safety/ tests/contract/ -v --tb=short`
- Integration tests: `pytest tests/integration/ -v --tb=short` (NO `-m integration` flag — correct; all 8 tests run)
- Eval tests: `pytest tests/evals/ -v --tb=short` with `ANTHROPIC_API_KEY` + `DEEPEVAL_TELEMETRY_OPT_OUT=1`
- Required secrets: only `ANTHROPIC_API_KEY` (eval job); `GITHUB_TOKEN` automatic for ghcr.io push
- All integration tests use MemorySaver + mocks — no PostgreSQL service in CI
- `addopts = "--ignore=tests/evals"` in pyproject.toml keeps evals out of default `pytest` run
- Python: 3.12 only (no matrix), `python:3.12-slim` in Dockerfile

## SQLite vs PostgreSQL Demo Compatibility (researched 2026-03-11)
- `sqlalchemy.dialects.postgresql` is always importable (it's in SQLAlchemy core) — `ImportError` will never fire as a dialect guard. Use `settings.is_sqlite` or `settings.is_postgres` to branch instead.
- `webhooks.py` `_insert_on_conflict_ignore()` uses `try/except ImportError` to detect SQLite — this is broken; the fallback path is unreachable. PostgreSQL insert dialect runs on SQLite and crashes.
- SKIP LOCKED crashes SQLite — confirmed in production code at `scheduler.py:111` and `delivery_worker.py:126`.
- `MemorySaver` is the SQLite checkpointer — state is lost on process restart.
- `locking.py` correctly guards advisory lock behind `if "sqlite" in str(engine.url)`.

## Test Quality and Coverage (updated 2026-03-15)
- 34 total files: 22 unit, 8 integration, 2 safety, 1 contract, 3 evals (previously 20 unit — 2 added)
- New unit test files since prior research: `test_save_patient_context.py`, `test_retry_generation.py`, `test_tools.py`, `test_demo_endpoints.py`, `test_jobs.py`
- New integration test files: `test_chat_endpoint.py`, `test_followup_lifecycle.py`, `test_graph_routing.py`, `test_onboarding_flow.py`
- `@pytest.mark.integration` is declared but NEVER applied to any test — the marker is vestigial
- No PostgreSQL service in CI `test-integration` job; all integration tests use MemorySaver + mocks
- Root `session` fixture in `conftest.py` lacks savepoint isolation — per-test DBs in `test_repositories.py` work around this correctly
- `MedBridgePushChannel.send()` always returns `success=False` (stub with TODO) — production delivery path is unimplemented
- Phase machine (`test_phase_machine.py`) is the best-covered module: exhaustive transitions + Hypothesis RuleBasedStateMachine
- `scrub_phi_fields` (`test_phi_logging.py`) thoroughly tested including nested dicts

## Health Coaching UX & Demo Best Practices (researched 2026-03-15)
- FAST framework (Frontiers 2025): evaluates AI coaches on Fidelity, Accuracy, Safety, Tone
- OARS (MI framework): Open questions, Affirmations, Reflections, Summaries — not in current system prompts
- `get_adherence_summary` + `get_program_summary` return identical hardcoded data for ALL patients
- `alert_clinician` priority is unvalidated free-string — LLM passes "high"/"medium" which breaks `"routine" | "urgent"` union in `types.ts:22`
- `set_reminder` does NOT catch `ValueError` from malformed ISO datetime — will crash tool node
- Optimal AI coaching session: 3-10 minutes; brevity instruction missing from prompts

## Demo UI Frontend (updated 2026-03-15 — verified against source)
- Stack: React 19, TypeScript 5.5 strict, Vite 6, Tailwind v4 (CSS-first, no tailwind.config.js)
- 18 source files; dependencies: lucide-react, clsx, react-markdown@10.1.0 (NEW — markdown renders)
- Two custom hooks: `useSSE` (SSE + pipeline trace), `usePatientState` (cancellation-token + 10s poll)
- Two hardcoded patients: "Sarah M. — Knee Rehab" + "James T. — Shoulder Recovery" (`App.tsx:8-11`)
- Tenant hardcoded: `"demo-tenant"` (`App.tsx:13`)
- SSE: hand-rolled line-buffered parser (`useSSE.ts:24-45`) — POST + custom headers, EventSource not usable
- Bot messages use `<ReactMarkdown>` (`ChatMessage.tsx:19`) — markdown renders correctly (prior note about raw chars is STALE)
- Suggestion chips ARE implemented, phase-aware, 2 per phase (`ChatPanel.tsx:10-57`) — prior note "missing" is STALE
- Audit Trail IS connected in `ObservabilityPanel.tsx:248-275` — prior note "not connected" is STALE
- `ChatPanel.tsx:91-93` clears messages on patientId change (not just resetKey)
- `ConfirmDialog` has initial focus on Cancel + Escape handler but no full focus trap (`ConfirmDialog.tsx:22-33`)
- `ObservabilityPanel` fixed `w-[420px]` — desktop only, no responsive breakpoints (`ObservabilityPanel.tsx:110`)
- `SafetyToast` auto-dismisses after 5s (`SafetyToast.tsx:22`)

## Entry Points and Boot Sequence (researched 2026-03-15)
- `python -m health_ally` → `__main__.main()` → uvicorn with `factory=True` (api/all) or bare `asyncio.run` (worker)
- Three modes: `api` (HTTP only), `worker` (scheduler+delivery, no HTTP), `all` (default — both)
- `ENVIRONMENT` defaults to `"dev"` — must be explicitly set to prod/staging in Railway or demo routes are live
- `DATABASE_URL` env var: Railway injects `postgres://` scheme; `settings.py:61-69` normalizes to `postgresql+psycopg://`
- Two-phase PG boot: `pool.open()` first, then `AsyncPostgresSaver.setup()` (idempotent; also in Railway `preDeployCommand`)
- Railway `preDeployCommand`: `alembic upgrade head` then `run_bootstrap(Settings())` (checkpoint tables)
- `_run_background_workers` in `main.py` is a near-copy of `_run_worker` in `__main__.py` — worker code is duplicated
- `create_session_factory` does NOT set `lazy="raise"` — that must be enforced at model relationship level
- Static files path: tries `<package_parent>/static` (local dev), then `/app/static` (Docker) — non-configurable
- uv pinned to `0.10` in Dockerfile; docker-compose has no Alembic migration step (local dev gap)

## API Layer and Integrations (researched 2026-03-15)
- 12 total endpoints: GET /health/live, GET /health/ready, POST /v1/chat (SSE), GET /v1/patients/{id}/phase|goals|safety-decisions|alerts, POST /webhooks/medbridge, POST /v1/demo/seed-patient, POST /v1/demo/trigger-followup/{id}, POST /v1/demo/reset-patient/{id}, GET /v1/demo/scheduled-jobs/{id}, GET /v1/demo/audit-events/{id}, GET /v1/demo/conversation/{id}
- Auth: header-based trust (`X-Patient-ID` + `X-Tenant-ID`) — no JWT/token verification (dev-only security posture)
- Middleware order (outermost first): `RequestLoggingMiddleware` (pure ASGI) → `CORSMiddleware`
- SSE format: `data: <json>\n\n` — no typed `event:` field; sentinels are `{"type":"done"}` and `{"type":"error","message":...}`
- `stream_mode=["updates", "custom"]` — node state updates + token-level streaming via `get_stream_writer()`
- Advisory lock: `pg_advisory_lock` session-level on AUTOCOMMIT connection; key = `sha256(patient_id)[:4] & 0x7FFFFFFF`; SQLite is no-op
- `ModelGateway` ABC: `get_chat_model("classifier")` → Haiku; `get_chat_model("coach")` → Sonnet; fallback to OpenAI gpt-4o only when `fallback_phi_approved=True`
- Channel factory always returns `MockNotificationChannel` + `MockAlertChannel` — no production channel wired
- `MedBridgePushChannel.send()` always returns `success=False` — production push notifications unimplemented
- `WebhookAlertChannel` exists but is never instantiated from settings
- `ConsentService` selection: dev/SQLite → `FakeConsentService`; prod + `medbridge_api_url` → `FailSafeConsentService(MedBridgeClient)`
- `_insert_on_conflict_ignore()` bug in `webhooks.py:29` — `try/except ImportError` dialect guard is unreachable; always uses PG dialect → crashes on SQLite
- Demo reset (`POST /v1/demo/reset-patient`) clears LangGraph checkpoint via `adelete_thread` (fixed 2026-03-15)
- PHI scrubber processes 18 named fields + SSN/email regex, runs last in structlog chain after format_exc_info

## Scheduling / Outbox / Observability (researched 2026-03-10)
- SQLAlchemy 2.0 async SKIP LOCKED: `.with_for_update(skip_locked=True)` — identical in sync/async
- Startup reconciliation resets stale `processing` jobs (crashed worker recovery)
- Idempotency keys: `f"{patient_id}:{job_type}:{reference_date}"` — use `INSERT ... ON CONFLICT DO NOTHING`
- `zoneinfo.ZoneInfo` for DST-safe quiet-hours calc; add `tzdata` as runtime dep for container portability
- Outbox INSERT must be in same transaction as domain state write (atomicity guarantee)
- OTEL trace_id/span_id inject into structlog via custom processor calling `trace.get_current_span()`
- `structlog.contextvars.clear_contextvars()` at start of EVERY request (prevent bleed in async)
- Audit immutability: REVOKE UPDATE/DELETE/TRUNCATE + immutability trigger (defense-in-depth)
- Liveness probe: never check DB or LLM. Readiness probe: check DB + scheduler worker, return 503 on failure
