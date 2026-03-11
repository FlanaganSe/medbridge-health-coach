# Researcher Memory

## Project Context
- Python-only backend service, no frontend code exists
- Patient UI lives in MedBridge Go (external app) — not this repo
- Stack: Python 3.12+, LangGraph, FastAPI, PostgreSQL/SQLite, uv, pytest, ruff, pyright
- Authoritative PRD: `.claude/plans/prd.md` (v1.6)
- Requirements: `docs/requirements.md`
- ADRs: `docs/decisions.md` (append-only)
- Deep reference: `.claude/plans/FINAL_CONSOLIDATED_RESEARCH.md` (76k, 2026-03-10)

## Key Constraints (from immutable.md)
1. Never generate clinical advice — redirect to care team
2. Verify consent on every interaction (not just thread creation)
3. Phase transitions are deterministic application code, never LLM-decided

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
- Full research: `.claude/plans/research-injectedstate-tool-mutation.md`
- `plan.md:796` contains an error: "adds goal data to [InjectedState]" — should be "reads via InjectedState, returns via Command.update"
- `RetryPolicy` NamedTuple: `initial_interval`, `backoff_factor`, `max_interval`, `max_attempts`, `jitter`, `retry_on`
- `RemoveMessage` does NOT cross subgraph boundaries (issue #5112) — fine for single-graph arch
- Store namespace is a tuple of strings: `namespace = ("patient_profiles", patient_id)`
- Full patterns in `.claude/plans/research.md` section "1. LangGraph 1.x Implementation Patterns"

## Domain Model Patterns (verified 2026-03-10)
- Full research in `.claude/plans/research-domain-model.md`
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
- Full research in `.claude/plans/research-fastapi-sqlalchemy.md`
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
- Safety research output: `.claude/plans/research-safety-llm.md`
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
- Detailed findings in `.claude/plans/research-testing-setup.md`
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
- Full research: `.claude/plans/research.md` section "2. Advisory Lock Concurrency Strategy"
- PLAN CORRECTION: `plan.md:559` says `load_patient_context` acquires `pg_advisory_xact_lock` — this is WRONG
  - `pg_advisory_xact_lock` is transaction-level; it releases when the node's session commits, before `save_patient_context` runs
  - The lock must be acquired at the CALL SITE (FastAPI handler / scheduler job) NOT inside a graph node
- Use `pg_advisory_lock` (session-level) on a DEDICATED connection from Pool A, held for the full invocation
- Release it explicitly in `try/finally` before the connection returns to Pool A — no lock leak on crash (PG releases on connection close)
- Do NOT use a single long-lived `AsyncSession` across nodes (pool exhaustion: pool_size=10 exhausts at 10 concurrent LLM-phase invocations)
- Do NOT use optimistic concurrency alone: `save_patient_context` writes 6+ tables; per-table versioning is incomplete
- SQLite: skip advisory lock entirely — `if "postgresql" not in str(engine.url): yield; return`
- lock key: `hash(patient_id) & 0x7FFFFFFF` (positive bigint; advisory lock keys are bigint)
- `CoachContext` keeps `db_session_factory` as factory (Callable) — nodes open short sessions; no session spans nodes

## CI/CD State (researched 2026-03-11)
- Full research: `.claude/plans/research-cicd.md`
- Three workflows: `ci.yml` (push/PR), `eval.yml` (push main + dispatch), `deploy.yml` (tags + dispatch)
- Tool: `astral-sh/setup-uv@v7` with `enable-cache: true` — reads `requires-python` for Python version automatically
- Install command: `uv sync --frozen` (always frozen lockfile)
- Lint: `uv run ruff check .` + `uv run ruff format --check .`
- Typecheck: `uv run pyright .`
- Unit tests: `uv run pytest tests/unit/ tests/safety/ tests/contract/ -v --tb=short` — no DB needed, SQLite only
- Integration tests: `uv run pytest tests/integration/ -v --tb=short -m integration` — BUT `-m integration` is a BUG: no test file applies `@pytest.mark.integration`, so this runs 0 tests
- Eval tests: `uv run pytest tests/evals/ -v --tb=short` with `ANTHROPIC_API_KEY` + `DEEPEVAL_TELEMETRY_OPT_OUT=1`
- Required secrets: only `ANTHROPIC_API_KEY` (eval job); `GITHUB_TOKEN` is automatic for ghcr.io push
- All `tests/integration/` tests use MemorySaver + mocks — they work on SQLite; no test actually needs PostgreSQL today
- `addopts = "--ignore=tests/evals"` in pyproject.toml keeps evals out of default `pytest` run
- Python: 3.12 only (no matrix), `python:3.12-slim` in Dockerfile

## SQLite vs PostgreSQL Demo Compatibility (researched 2026-03-11)
- `sqlalchemy.dialects.postgresql` is always importable (it's in SQLAlchemy core) — `ImportError` will never fire as a dialect guard. Use `settings.is_sqlite` or `settings.is_postgres` to branch instead.
- `webhooks.py` `_insert_on_conflict_ignore()` uses `try/except ImportError` to detect SQLite — this is broken; the fallback path is unreachable. PostgreSQL insert dialect runs on SQLite and crashes.
- SKIP LOCKED crashes SQLite — confirmed in production code at `scheduler.py:111` and `delivery_worker.py:126`.
- `MemorySaver` is the SQLite checkpointer — state is lost on process restart.
- `locking.py` correctly guards advisory lock behind `if "sqlite" in str(engine.url)`.

## Scheduling / Outbox / Observability (researched 2026-03-10)
- Detailed findings in `.claude/plans/research-scheduling-observability.md`
- SQLAlchemy 2.0 async SKIP LOCKED: `.with_for_update(skip_locked=True)` — identical in sync/async
- Startup reconciliation resets stale `processing` jobs (crashed worker recovery)
- Idempotency keys: `f"{patient_id}:{job_type}:{reference_date}"` — use `INSERT ... ON CONFLICT DO NOTHING`
- `zoneinfo.ZoneInfo` for DST-safe quiet-hours calc; add `tzdata` as runtime dep for container portability
- Outbox INSERT must be in same transaction as domain state write (atomicity guarantee)
- OTEL trace_id/span_id inject into structlog via custom processor calling `trace.get_current_span()`
- `structlog.contextvars.clear_contextvars()` at start of EVERY request (prevent bleed in async)
- Audit immutability: REVOKE UPDATE/DELETE/TRUNCATE + immutability trigger (defense-in-depth)
- Liveness probe: never check DB or LLM. Readiness probe: check DB + scheduler worker, return 503 on failure
