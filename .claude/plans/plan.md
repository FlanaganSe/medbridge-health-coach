# Plan: Code Cleanliness Initiative

**Date:** 2026-03-11
**Based on:** `.claude/plans/research.md` + full source file review
**Constraint:** Demo app — simplify over-engineering, preserve core invariants (ADR-001–007, immutable rules)

---

## Summary

Six milestones, ordered by dependency and risk. M1 fixes confirmed bugs (smallest blast radius). M2 eliminates code duplication and dead code — the biggest code quality win. M3 improves API type safety and middleware. M4 makes the app Railway-deployable. M5 transforms the demo UI from "needs manual curl" to "click and demo." M6 improves test quality. Each milestone is independently verifiable: tests pass, types check, ruff clean after every step.

---

## Milestones

### M1: Bug Fixes (5 bugs, low risk) ✅ COMPLETE

- [x] Step 1 — Fix pending_node outbox payload mismatch
- [x] Step 2 — Fix retry_generation synthetic HumanMessage
- [x] Step 3 — Fix delivery worker `updated_at` on bulk status change
- [x] Step 4 — Fix scheduler failed job retry
- [x] Step 5 — Fix Pool B missing `row_factory=dict_row`
Commit: "fix: resolve 5 confirmed bugs (outbox payload, retry pollution, updated_at, job retry, row_factory)"

**Step 1:** Fix pending_node outbox payload mismatch
- **File:** `src/health_coach/agent/nodes/pending.py:72`
- **Change:** Replace `{"message_ref_id": str(uuid.uuid4())}` with `{"message": WELCOME_MESSAGE}`
- **Why:** Delivery worker reads `payload.message` — the current payload has a random UUID ref that nothing can look up, so welcome messages silently fail
- **Verify:** `pytest tests/unit/test_tools.py tests/integration/test_graph_routing.py -x`

**Step 2:** Fix retry_generation synthetic HumanMessage
- **File:** `src/health_coach/agent/nodes/retry.py:59,74-76`
- **Change:** Replace `HumanMessage(content=RETRY_AUGMENTATION)` with injecting the augmentation into the system prompt. The return value should NOT include the retry augmentation in `messages` — only return `[AIMessage(content=content)]`. The augmentation is ephemeral, not persisted.
- **Why:** Currently appends a fake patient message to conversation history. On future turns, LLM sees "IMPORTANT: Your previous response was flagged..." as if the patient said it.
- **Detail:** In the LLM invocation (line 63), prepend `RETRY_AUGMENTATION` to the system prompt string instead of appending it as a HumanMessage. The returned messages should only contain the AIMessage response.
- **Verify:** `pytest tests/integration/test_onboarding_flow.py -x`

**Step 3:** Fix delivery worker `updated_at` on bulk status change
- **File:** `src/health_coach/orchestration/delivery_worker.py:135-139`
- **Change:** Add `updated_at=func.now()` to the `.values(...)` call in the bulk update
- **Why:** SQLAlchemy's `onupdate` only fires on ORM flush, not bulk `execute(update(...))`. Without this, `_recover_stuck_entries` (which filters by `updated_at <= cutoff`) can never find entries stuck in "delivering" state.
- **Verify:** `pytest tests/unit/test_delivery_worker.py -x`

**Step 4:** Fix scheduler failed job retry
- **File:** `src/health_coach/orchestration/scheduler.py:195-208`
- **Change:** In `_handle_job_failure`, change `"failed"` status to `"pending"` (reset for retry). Only transition to `"dead"` when `new_attempts >= max_attempts`. This makes failed jobs immediately eligible for the next poll.
- **Why:** Currently `status="failed"` is a terminal state — the scheduler only queries `status == "pending"`, so failed jobs are silently dropped.
- **Verify:** `pytest tests/unit/test_scheduler.py -x`

**Step 5:** Fix Pool B missing `row_factory=dict_row`
- **File:** `src/health_coach/persistence/db.py:59-62`
- **Change:** Add `"row_factory": dict_row` to the `kwargs` dict. Import `from psycopg.rows import dict_row` inside the `if not settings.is_postgres` guard.
- **Why:** `AsyncPostgresSaver` expects dict-based rows from the connection pool. Without this, switching from `MemorySaver` to `AsyncPostgresSaver` (M4) would fail.
- **Verify:** `pytest tests/unit/test_settings.py -x && pyright src/health_coach/persistence/db.py`

**M1 Final Verify:** `pytest --tb=short -q && pyright . && ruff check . && ruff format --check .`

---

### M2: Code Quality — Deduplication, Dead Code, Type Safety (high impact) ✅ COMPLETE

- [x] Step 6 — Extract `create_context_factory` to context.py, fix types in main.py + __main__.py
- [x] Step 8 — Extract `accumulate_effects` helper, refactor 4 node modules
- [x] Step 9 — Unify prompt sources (onboarding.py composes from system.py)
- [x] Step 10 — Remove dead code (_select_tone, CELEBRATION/NUDGE augmentations, MESSAGE_THRESHOLD)
- [x] Step 11 — Add `transition_target` to phase_machine, replace `_expected_target`
- [x] Step 12 — Remove orphan DB tables (ConversationThread, Message, ToolInvocation)
Commit: "refactor: code quality — deduplicate, remove dead code, improve type safety"

**Step 6:** Extract `create_coach_context` factory
- **File to change:** `src/health_coach/agent/context.py`
- **Change:** Add a `create_coach_context()` function that takes `session_factory`, `engine`, `consent_service`, `settings`, `coach_config`, `model_gateway` and returns a `CoachContext`. This replaces the 3 identical `ctx_factory` closures.
- **Files to change:** `src/health_coach/main.py` — replace both inline `ctx_factory` closures with calls to `create_coach_context`. Pass real types instead of `object`.
- **File to change:** `src/health_coach/__main__.py` — same replacement.
- **Type fix:** Change `session_factory: object` and `engine: object` params in `_setup_graph_and_context` and `_run_background_workers` to `async_sessionmaker[AsyncSession]` and `AsyncEngine`. Remove all `# type: ignore[arg-type]` on these calls.
- **Verify:** `pyright src/health_coach/main.py src/health_coach/__main__.py src/health_coach/agent/context.py`

**Step 7:** Consolidate graph compilation with settings-aware checkpointer
- **File to create:** None — add to existing `src/health_coach/agent/graph.py`
- **Change:** Add a `create_checkpointer(settings: Settings) -> BaseCheckpointSaver` function. For `is_postgres`, return `AsyncPostgresSaver(pool)` (pool passed in). For SQLite, return `MemorySaver()`. Update `main.py` and `__main__.py` to use this instead of hardcoded `MemorySaver()`.
- **Note:** The actual PostgreSQL pool integration happens in M4. For now, the function signature is established but `MemorySaver` remains the default when no pool is provided.
- **Files to change:** `src/health_coach/agent/graph.py`, `src/health_coach/main.py`, `src/health_coach/__main__.py`
- **Verify:** `pytest tests/integration/ -x`

**Step 8:** Extract `accumulate_effect` helper
- **File to create:** `src/health_coach/agent/effects.py`
- **Content:** A pure function `accumulate_effect(state: PatientState, key: str, items: list[dict]) -> PendingEffects` that handles the get-or-default, spread, append pattern. Also a `merge_effects(state: PatientState, **updates) -> PendingEffects` variant for setting scalar keys like `phase_event`.
- **Files to change:** All 8 instances:
  - `agent/nodes/crisis_check.py:70-85,100-118`
  - `agent/nodes/active.py:118-134,162-200`
  - `agent/nodes/re_engaging.py:112-148,160-193,204-246`
  - `agent/nodes/safety.py:67-96`
- **Verify:** `pytest tests/ -x --tb=short`

**Step 9:** Unify prompt sources
- **File to change:** `src/health_coach/agent/prompts/onboarding.py`
- **Change:** Remove the duplicated `ONBOARDING_SYSTEM_PROMPT` string. Have `build_onboarding_prompt()` compose from `system.py`'s `ONBOARDING_PROMPT` + the context section. This makes `system.py` the single source of truth.
- **File to change:** `src/health_coach/agent/nodes/retry.py:55`
- **Change:** Import and call the appropriate phase-specific `build_*_prompt()` function based on `phase`, instead of `get_system_prompt(phase)`. This ensures retry uses the same (richer) prompt as the original generation.
- **Verify:** `pytest tests/integration/test_onboarding_flow.py tests/evals/ -x`

**Step 10:** Remove dead code
- **Files to change:**
  - `agent/nodes/active.py`: Remove `_select_tone()` function, replace call with `tone = "check_in"` directly. Remove `CELEBRATION_AUGMENTATION` and `NUDGE_AUGMENTATION` imports if any.
  - `agent/prompts/active.py`: Remove `CELEBRATION_AUGMENTATION`, `NUDGE_AUGMENTATION` constants if they exist.
  - `agent/nodes/history.py`: Remove `MESSAGE_THRESHOLD = 20` constant.
  - `api/routes/chat.py`: Remove unused `ChatRequest` class (lines 27-30). Replace `body = await request.json()` with a proper Pydantic model parameter on the endpoint (see Step 14).
  - `agent/nodes/active.py:156`, `agent/nodes/re_engaging.py:152-155`: Type `coach_config` as `CoachConfig` directly. Remove `isinstance` guards and deferred imports.
- **Verify:** `ruff check . && pyright . && pytest -x`

**Step 11:** Eliminate `_expected_target` duplication
- **File to change:** `src/health_coach/domain/phase_machine.py`
- **Change:** Add `def transition_target(event: str) -> str | None` that looks up the target phase from `_TRANSITIONS` by event name (iterating values).
- **File to change:** `src/health_coach/agent/nodes/context.py:284-295`
- **Change:** Replace the hardcoded `_expected_target` dict with a call to `phase_machine.transition_target(event)`.
- **Verify:** `pytest tests/unit/test_phase_machine.py tests/integration/ -x`

**Step 12:** Remove orphan DB tables
- **File to change:** `src/health_coach/persistence/models.py`
- **Change:** Remove ORM classes `ConversationThread`, `Message`, `ToolInvocation` and their relationships. These tables have zero writers in the codebase.
- **Note:** Leave the Alembic migration intact (the tables can remain in the DB). Only remove the Python classes.
- **Verify:** `pytest -x && pyright src/health_coach/persistence/models.py`

**M2 Final Verify:** `pytest --tb=short -q && pyright . && ruff check . && ruff format --check .`

---

### M3: API Quality & Middleware (type safety + deployment prep) ✅ COMPLETE

- [x] Step 13 — Pydantic response models on state endpoints
- [x] Step 14 — Pydantic request model for chat endpoint
- [x] Step 15 — CORS middleware + settings
- [x] Step 16 — Replace BaseHTTPMiddleware with pure ASGI middleware
- [x] Step 17 — Fix pool size defaults (5+5=10 << Railway 25 limit)
Commit: "feat: API quality, CORS, ASGI middleware, Railway pool sizes"

**Step 13:** Use Pydantic response models on state endpoints
- **File to change:** `src/health_coach/api/routes/state.py`
- **Change:** Define response models (`PhaseResponse`, `GoalsResponse`, `AlertsResponse`, `SafetyDecisionsResponse`) using the existing Pydantic schemas from `persistence/schemas/`. Use them as `response_model=` on each endpoint. Replace inline dict comprehensions with `model.model_validate(orm_obj)`.
- **File to change:** `src/health_coach/persistence/schemas/audit.py` — add `Field(alias="metadata")` to fix the `metadata_` serialization issue.
- **Why:** Enables OpenAPI docs, auto-validation, and removes `dict[str, Any]` return types.
- **Verify:** `pyright src/health_coach/api/routes/state.py && pytest -x`

**Step 14:** Use Pydantic model for chat endpoint request body
- **File to change:** `src/health_coach/api/routes/chat.py`
- **Change:** Replace `body = await request.json()` with a Pydantic model parameter: `async def chat(body: ChatRequest, ...)`. Define `ChatRequest` as `BaseModel` with `message: str = Field(min_length=1)`. Remove the manual validation.
- **Verify:** `pytest tests/integration/test_chat_endpoint.py -x`

**Step 15:** Add CORS middleware
- **File to change:** `src/health_coach/main.py`
- **Change:** Add `CORSMiddleware` with `allow_origins` from settings. Add `cors_origins: list[str] = ["http://localhost:5173"]` to `Settings`.
- **File to change:** `src/health_coach/settings.py`
- **Verify:** `pytest tests/unit/test_settings.py -x`

**Step 16:** Replace `BaseHTTPMiddleware` with pure ASGI middleware
- **File to change:** `src/health_coach/api/middleware/logging.py`
- **Change:** Rewrite `RequestLoggingMiddleware` as a raw ASGI middleware class (implementing `__init__` + `__call__`). This eliminates the SSE buffering issue caused by Starlette's `BaseHTTPMiddleware`.
- **Pattern:**
  ```python
  class RequestLoggingMiddleware:
      def __init__(self, app: ASGIApp) -> None: ...
      async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
          if scope["type"] != "http":
              await self.app(scope, receive, send)
              return
          # bind contextvars, time the request, log on response start
  ```
- **Verify:** `pytest tests/integration/test_chat_endpoint.py -x` (SSE streaming should work without buffering)

**M3 Final Verify:** `pytest --tb=short -q && pyright . && ruff check . && ruff format --check .`

---

### M4: Railway Deployment Readiness ✅ COMPLETE

- [x] Step 17 — Fix pool size defaults (done in M3)
- [x] Step 18 — Wire AsyncPostgresSaver via create_checkpointer()
- [x] Step 19 — Create railway.toml
- [x] Step 20 — Settings-driven notification/alert channels
Commit: "feat: Railway deployment readiness"

**Step 17:** Fix pool size defaults
- **File to change:** `src/health_coach/settings.py`
- **Change:** `db_pool_size: int = 5`, `db_max_overflow: int = 5`, `langgraph_pool_size: int = 3`
- **Why:** Railway PostgreSQL starter allows 25 connections. Old defaults (20+10=30) exceed that.
- **Verify:** `pytest tests/unit/test_settings.py -x`

**Step 18:** Wire `AsyncPostgresSaver` into graph compilation
- **Files to change:** `src/health_coach/main.py`, `src/health_coach/__main__.py`, `src/health_coach/agent/graph.py`
- **Change:** In `lifespan()`, when `langgraph_pool is not None`, create `AsyncPostgresSaver(pool)` and call `await checkpointer.setup()`. Pass this checkpointer to `compile_graph()`. For SQLite, continue using `MemorySaver`.
- **Change:** Remove the duplicated graph compilation from `_run_background_workers()` — share the same graph instance from lifespan. For `__main__.py` worker mode, similarly create the appropriate checkpointer.
- **Verify:** `pytest -x` (tests use SQLite → MemorySaver path)

**Step 19:** Create `railway.toml`
- **File to create:** `railway.toml`
- **Content:**
  ```toml
  [build]
  builder = "dockerfile"

  [deploy]
  healthcheckPath = "/health/live"
  healthcheckTimeout = 30
  startCommand = "alembic upgrade head && python -m health_coach"
  ```
- **Verify:** File exists and is valid TOML

**Step 20:** Settings-driven notification channels
- **File to change:** `src/health_coach/settings.py` — add `notification_channel: Literal["mock", "log"] = "mock"` and `alert_channel: Literal["mock", "log"] = "mock"`
- **File to create:** `src/health_coach/integrations/channels.py` — factory function `create_notification_channel(settings)` and `create_alert_channel(settings)` that returns the appropriate channel based on settings. The `"log"` channel is a new simple channel that logs the message content (useful for Railway staging where you want to see messages in logs).
- **Files to change:** `src/health_coach/main.py`, `src/health_coach/__main__.py` — replace hardcoded `MockNotificationChannel()` and `MockAlertChannel()` with `create_notification_channel(settings)` and `create_alert_channel(settings)`.
- **Verify:** `pytest -x && pyright .`

**M4 Final Verify:** `pytest --tb=short -q && pyright . && ruff check . && ruff format --check .`

---

### M5: Demo UI Improvements ✅ COMPLETE

- [x] Step 21 — Demo API endpoints (seed, trigger, reset, jobs)
- [x] Step 22 — Chat SSE fix with line-buffered parser
- [x] Step 23 — DemoControls panel
- [x] Step 24 — Sidebar improvements (polling 2s, phase colors, load state, timestamps)
Commit: "feat: demo UI — controls panel, SSE fix, observability improvements"

**Step 21:** Add demo API endpoints for patient seeding and follow-up triggering
- **File to create:** `src/health_coach/api/routes/demo.py`
- **Endpoints:**
  - `POST /v1/demo/seed-patient` — creates a patient record, fires the equivalent of a webhook `patient_login` + `consent_granted` event. Returns the patient ID and initial phase. Only available when `settings.environment == "dev"`.
  - `POST /v1/demo/trigger-followup/{patient_id}` — finds the next pending `ScheduledJob` for this patient and marks it as immediately due (`scheduled_at = now`). The scheduler will pick it up on the next poll.
  - `POST /v1/demo/reset-patient/{patient_id}` — resets a patient to PENDING state (deletes goals, jobs, outbox entries, resets unanswered_count). For re-running demos.
  - `GET /v1/demo/scheduled-jobs/{patient_id}` — returns pending/completed/failed jobs for visibility.
- **File to change:** `src/health_coach/main.py` — conditionally include demo router when `settings.environment == "dev"`.
- **Verify:** `pytest -x`

**Step 22:** Improve Chat component with proper SSE parsing
- **File to change:** `demo-ui/src/components/Chat.tsx`
- **Change:** Replace manual `reader.read()` + `split("\n")` parsing with a proper SSE parser. Use the `eventsource-parser` npm package (lightweight, no deps) or implement a proper line-buffered parser that handles chunks spanning reads.
- **Add:** `npm install eventsource-parser` to demo-ui
- **Also:** Replace array index `key={i}` with a unique message ID (use `crypto.randomUUID()`).
- **Verify:** `cd demo-ui && npm run build`

**Step 23:** Add DemoControls panel
- **File to create:** `demo-ui/src/components/DemoControls.tsx`
- **Content:** A collapsible panel with:
  - "Setup Patient" button → calls `POST /v1/demo/seed-patient`
  - "Trigger Follow-up" button → calls `POST /v1/demo/trigger-followup/{patientId}`
  - "Reset Patient" button → calls `POST /v1/demo/reset-patient/{patientId}`
  - Scheduled jobs list → polls `GET /v1/demo/scheduled-jobs/{patientId}`
- **File to change:** `demo-ui/src/App.tsx` — add `<DemoControls />` above the chat area.
- **Verify:** `cd demo-ui && npm run build`

**Step 24:** Fix sidebar error handling and improve UX
- **File to change:** `demo-ui/src/components/ObservabilitySidebar.tsx`
- **Changes:**
  - Add error state per section (show "Error loading" instead of silent catch)
  - Add loading spinner on initial fetch
  - Increase poll frequency to 2s (demo responsiveness)
  - Show `updated_at` timestamp so operator knows data is fresh
- **Verify:** `cd demo-ui && npm run build`

**Step 25:** Add basic CSS and polish
- **File to create:** `demo-ui/src/styles.css`
- **Change:** Extract inline styles to CSS classes. Use CSS variables for colors. Add a simple responsive layout. This is cosmetic — no functionality changes.
- **File to change:** `demo-ui/src/main.tsx` — import `styles.css`
- **Verify:** `cd demo-ui && npm run build`

**M5 Final Verify:** `cd demo-ui && npm run build && cd .. && pytest --tb=short -q && pyright . && ruff check .`

---

### M6: Test Quality ✅ COMPLETE

- [x] Step 26 — State endpoint tests (7 tests)
- [x] Step 27 — Consolidate mock session helper to conftest
- [x] Step 28 — Fix misleading delivery worker test
- [x] Step 29 — Fix conftest engine fixture with create_all
- [x] Bonus — Fix datetime import (TC003 vs Pydantic), pyright extraPaths for tests, Chat identity split (🔴), fetchJobs dep array
Commit: "test: state endpoint tests, consolidate mock session, fix delivery test"

**Step 26:** Add tests for state endpoints
- **File to create:** `tests/unit/test_state_endpoints.py`
- **Tests:**
  - `test_get_phase_returns_phase` — seed patient, GET phase, assert correct phase
  - `test_get_phase_unknown_patient_404` — GET with unknown UUID, assert 404
  - `test_get_phase_invalid_uuid_400` — GET with `"not-a-uuid"`, assert 400
  - `test_get_goals_empty` — seed patient with no goals, assert `{"goals": []}`
  - `test_get_goals_returns_goals` — seed patient + goal, assert goal in response
  - `test_get_alerts_returns_alerts` — seed patient + alert, assert in response
  - `test_get_safety_decisions_returns_decisions`
- **Fixture:** Use the FastAPI test client from conftest. Create a minimal test-specific engine with `create_all`.
- **Verify:** `pytest tests/unit/test_state_endpoints.py -v`

**Step 27:** Consolidate mock session fixture
- **File to change:** `tests/conftest.py`
- **Change:** Add a shared `mock_session` fixture that provides the mock `AsyncSession` with pre-configured `begin()`, `get()`, `execute()` mocks. Remove the 4+ identical `_make_mock_session()` helpers from:
  - `tests/integration/test_graph_thread.py`
  - `tests/integration/test_graph_routing.py`
  - `tests/integration/test_onboarding_flow.py`
  - `tests/integration/test_followup_lifecycle.py`
- **Verify:** `pytest tests/integration/ -x`

**Step 28:** Fix misleading tests
- **File to change:** `tests/unit/test_delivery_worker.py`
- **Change:** Rename `test_consent_denied_skips_delivery` → `test_consent_service_returns_denied` (what it actually tests). Or better: rewrite it to actually call `_deliver_single()` with a mock entry where `message_type="patient_message"` and verify the entry is cancelled.
- **Verify:** `pytest tests/unit/test_delivery_worker.py -v`

**Step 29:** Fix conftest session fixture
- **File to change:** `tests/conftest.py`
- **Change:** Add `Base.metadata.create_all(bind=engine)` to the `engine` fixture so the root conftest `session` fixture is actually usable for ORM tests. Currently it's dead weight because it never creates tables.
- **Verify:** `pytest -x`

**M6 Final Verify:** `pytest --tb=short -q && pyright . && ruff check . && ruff format --check .`

---

## Files to Change (Summary)

| File | Milestones | Nature of Change |
|------|-----------|-----------------|
| `src/health_coach/agent/nodes/pending.py` | M1 | Fix outbox payload |
| `src/health_coach/agent/nodes/retry.py` | M1, M2 | Fix synthetic HumanMessage; unify prompts |
| `src/health_coach/orchestration/delivery_worker.py` | M1 | Fix bulk update `updated_at` |
| `src/health_coach/orchestration/scheduler.py` | M1 | Fix failed job retry |
| `src/health_coach/persistence/db.py` | M1 | Add `row_factory=dict_row` |
| `src/health_coach/agent/context.py` | M2 | Add `create_coach_context` factory |
| `src/health_coach/main.py` | M2, M3, M4, M5 | Remove duplication, add CORS, wire checkpointer, include demo routes |
| `src/health_coach/__main__.py` | M2, M4 | Remove duplication, wire checkpointer |
| `src/health_coach/agent/graph.py` | M2, M4 | Checkpointer factory |
| `src/health_coach/agent/nodes/active.py` | M2 | Remove dead code, type fix, use effects helper |
| `src/health_coach/agent/nodes/re_engaging.py` | M2 | Type fix, use effects helper |
| `src/health_coach/agent/nodes/crisis_check.py` | M2 | Use effects helper |
| `src/health_coach/agent/nodes/safety.py` | M2 | Use effects helper |
| `src/health_coach/agent/nodes/context.py` | M2 | Delegate to `phase_machine.transition_target` |
| `src/health_coach/agent/nodes/history.py` | M2 | Remove dead constant |
| `src/health_coach/agent/prompts/onboarding.py` | M2 | Remove duplicate, compose from system.py |
| `src/health_coach/agent/prompts/active.py` | M2 | Remove dead constants |
| `src/health_coach/domain/phase_machine.py` | M2 | Add `transition_target()` |
| `src/health_coach/persistence/models.py` | M2 | Remove orphan table classes |
| `src/health_coach/settings.py` | M3, M4 | Add CORS, pool sizes, channel settings |
| `src/health_coach/api/routes/state.py` | M3 | Add response models |
| `src/health_coach/api/routes/chat.py` | M3 | Pydantic request model |
| `src/health_coach/api/middleware/logging.py` | M3 | Replace BaseHTTPMiddleware |
| `src/health_coach/persistence/schemas/audit.py` | M3 | Fix metadata alias |
| `demo-ui/src/components/Chat.tsx` | M5 | Fix SSE parsing, message keys |
| `demo-ui/src/components/ObservabilitySidebar.tsx` | M5 | Error handling, loading state |
| `demo-ui/src/App.tsx` | M5 | Add DemoControls |
| `tests/conftest.py` | M6 | Fix session fixture, add mock_session |
| `tests/unit/test_delivery_worker.py` | M6 | Fix misleading test |
| `tests/integration/test_graph_*.py` | M6 | Remove duplicate mock setup |

## Files to Create

| File | Milestone | Purpose |
|------|-----------|---------|
| `src/health_coach/agent/effects.py` | M2 | `accumulate_effect()` and `merge_effects()` helpers |
| `src/health_coach/integrations/channels.py` | M4 | Channel factory functions |
| `src/health_coach/api/routes/demo.py` | M5 | Demo-only endpoints (seed, trigger, reset) |
| `demo-ui/src/components/DemoControls.tsx` | M5 | Demo control panel |
| `demo-ui/src/styles.css` | M5 | Extracted CSS styles |
| `railway.toml` | M4 | Railway deployment config |
| `tests/unit/test_state_endpoints.py` | M6 | State endpoint tests |

---

## Risks

1. **M2 Step 8 (effects helper):** Touches 8 files across 4 node modules. Highest regression risk in the plan. Mitigated by: each node has integration test coverage, run full suite after each file change.

2. **M3 Step 16 (ASGI middleware):** Raw ASGI middleware is more complex than `BaseHTTPMiddleware`. If the SSE buffering isn't actually causing issues in practice, this is lower priority. Can be deferred.

3. **M4 Step 18 (AsyncPostgresSaver):** Cannot be fully verified without a running PostgreSQL instance. The SQLite → MemorySaver path must remain functional. Integration tests will continue using SQLite. Manual testing with `docker-compose up` required.

4. **M5 Step 21 (demo endpoints):** The `reset-patient` endpoint modifies DB state — needs to be gated behind `environment == "dev"` with a clear guard. Could accidentally be exposed in production.

5. **M2 Step 12 (remove orphan tables):** If any future code or external tool references these table names, removal would break. Grep for `ConversationThread`, `Message`, `ToolInvocation` class references before removal. Leave Alembic migration intact.

---

## Open Questions

1. **Reconciliation simplification (research §2.5):** `sweep_missing_jobs` naively re-schedules `day_2_followup` for all ACTIVE patients regardless of their actual cadence position. Should we:
   - (a) Remove it entirely (demo doesn't need it)?
   - (b) Keep it but document the limitation?
   - (c) Fix it to read actual cadence position (more complex)?

2. **Eval fixes (research §8.4):** The evals score hand-written ideal responses, not actual system output. Fixing this is high-value but requires running the real graph with real LLM calls during eval tests. Should we:
   - (a) Defer to a separate milestone?
   - (b) Add a small number of "live" evals that invoke the graph?
   - (c) Leave as-is for now?

3. **Demo UI styling:** Research suggests Tailwind for polish. Is that the right choice for a demo-only UI with 4 components, or is a plain CSS file sufficient? Tailwind adds build complexity.

4. **`manage_history` node:** Currently a no-op. Should we:
   - (a) Implement basic history trimming (e.g., keep last N messages)?
   - (b) Remove the node from the graph entirely?
   - (c) Leave as-is since demo conversations are short?

5. **Dormant patient acknowledgment (research §2.6):** When a dormant patient sends a message, they get no response until the next graph invocation. Should `dormant_node` generate a brief "Welcome back!" message?
