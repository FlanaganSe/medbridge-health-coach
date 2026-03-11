# Full Research Summary — All Files
**Date:** 2026-03-10
**Purpose:** Comprehensive summary of all eleven research files for plan evaluation. Cite sources as `filename:section` or `filename:line`.

---

## File 1: `RESEARCH_INDEX.md`

### Purpose
Consolidated index and corrections table. The authoritative cross-reference between research files and the superseded `FINAL_CONSOLIDATED_RESEARCH.md`.

### Twelve Critical Corrections to FINAL_CONSOLIDATED_RESEARCH.md

| # | What was wrong | What is correct |
|---|---|---|
| 1 | `langchain-anthropic>=0.3` (line 594) | `>=1.3.4` — 0.3 series dead since Oct 2025 |
| 2 | Beta header `anthropic-beta: structured-outputs-2025-11-13` | Structured outputs are GA; beta header deprecated |
| 3 | Multi-boolean classifier output `{clinical, crisis, jailbreak}` | Single `SafetyDecision` enum eliminates ambiguous states |
| 4 | "One psycopg3 pool shared by both" (lines 443-451) | Two separate pools required; `autocommit=True` on LG pool is incompatible with SQLAlchemy |
| 5 | `config_schema` in examples | Deprecated since 0.6, removed in 2.0 — use `context_schema` |
| 6 | `create_react_agent` listed as option | Deprecated in 1.x, removed in 2.0 — use explicit StateGraph |
| 7 | OpenAI `max_tokens` parameter | Deprecated Sep 2024 — use `max_completion_tokens` |
| 8 | `claude-3-haiku-20240307` | Retires April 20, 2026 — use `claude-haiku-4-5-20251001` |
| 9 | `(str, Enum)` mixin for PatientPhase | Use `enum.StrEnum` + `String(20)` — avoids psycopg3 edge case #13052 |
| 10 | `astream_events(version="v2")` as the preferred path | Both work; `astream(version="v2")` with `StreamPart` dicts is preferred in 1.1+ |
| 11 | `GenericFakeChatModel` implied usable for tool-calling | Does NOT support `bind_tools()` — raises `NotImplementedError` |
| 12 | Procrastinate 3.7.2 listed as scheduler candidate | Custom `scheduled_jobs` table is the confirmed choice per PRD §8.1 |

### Confirmed Dependency Versions (March 2026)
```
langgraph>=1.1.0
langgraph-checkpoint-postgres>=3.0.4
langgraph-checkpoint-sqlite>=3.0.3
langchain-anthropic>=1.3.4
langchain-openai>=1.1.11
langchain-aws>=1.4.0
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

### Open Research Gaps (No Research File Answers These)
1. MedBridge Go API contract — consent endpoint shape, webhook schema, auth mechanism
2. Clinician alert channel — email/Slack/dashboard
3. Cloud platform — GCP vs AWS
4. Patient timezone source — where does the IANA timezone string come from
5. Retention/deletion policy — checkpoint blob retention beyond 6-year audit minimum
6. LangGraph Store decision — deferred; domain DB sufficient for MVP
7. `get_runtime()` in tools — still broken (issue #6431); use `InjectedStore`/`InjectedState`
8. `get_stream_writer()` in async tools — still broken (issue #6447); use `StreamWriter` param injection

---

## File 2: `research.md` — LangGraph 1.x Patterns

### 1. StateGraph Construction

**Key patterns:**
- `StateGraph(State, context_schema=CoachContext)` — `context_schema` is the DI mechanism (replaces deprecated `config_schema`)
- `PatientState` as `TypedDict` with `total=True`; optional fields typed as `T | None`
- `messages: Annotated[list[BaseMessage], add_messages]` — reducer handles append-not-replace
- `add_conditional_edges` ALWAYS requires `# type: ignore[arg-type]` in pyright-strict (issue #6540, open)
- `RetryPolicy` on `add_node` for transient failure handling; `retry_on` defaults exclude `ValidationError`

**Plan correction documented:** `plan.md:796` says "adds goal data to [InjectedState]" — should be "reads via InjectedState, returns via Command.update"

### 2. Runtime and `context_schema`

```python
@dataclass
class CoachContext:
    patient_id: str
    tenant_id: str
    db_session_factory: Callable[[], AsyncSession]  # factory, NOT a live session
    consent_api_url: str
```

- Nodes access DI via `runtime: Runtime[CoachContext]` parameter
- Context is immutable per run; set at `graph.ainvoke(context=...)` call site
- `runtime.store` is how nodes access the LangGraph Store

### 3. Command Routing

`Command` is for when a node's next step depends on its own computation result. `add_conditional_edges` is for static topology. Key distinction: `Command` is returned instead of a plain dict when a node needs to both update state AND control routing in one operation.

Side-effecting tools MUST return `Command(update={"key": val, "messages": [ToolMessage(..., tool_call_id=...)]})`. Missing the `ToolMessage` in `Command.update` causes a runtime error. Read-only tools return plain `str` — ToolNode wraps in ToolMessage automatically.

`InjectedState` is READ-ONLY — mutating the injected dict does NOT update graph state. Side-effecting tools must return via `Command.update`. (`research-injectedstate-tool-mutation.md`)

### 4. Checkpointer and Store

**Pool B requirements** (pool for LangGraph, separate from Pool A / SQLAlchemy):
```python
lg_pool = AsyncConnectionPool(
    conninfo=settings.langgraph_db_url,
    max_size=20,
    open=False,          # MUST be False — open manually in lifespan
    kwargs={
        "autocommit": True,         # required by checkpointer
        "row_factory": dict_row,    # required by checkpointer
        "prepare_threshold": 0,     # required by checkpointer
    },
)
```
`checkpointer.setup()` and `store.setup()` are one-time migration scripts — NOT called at app startup.

### 5. ToolNode and tools_condition

```python
tool_node = ToolNode(
    tools,
    name="tools",
    messages_key="messages",
    handle_tool_errors=True,
)
builder.add_conditional_edges("active_agent", tools_condition)  # type: ignore[arg-type]
builder.add_edge("tools", "active_agent")  # loop back
```

- `tools_condition` returns `"tools"` if last message has tool_calls, else `END`
- Node name "tools" MUST match the string `tools_condition` returns
- `parallel_tool_calls=False` on `bind_tools()` when order matters or exactly one call desired
- ToolNode passes `Command` returns through directly — does NOT wrap them in ToolMessage

### 6. Streaming

- `astream(version="v2")` returns typed `StreamPart` dicts + `GraphOutput` — preferred in 1.1+
- `astream_events(version="v2")` also works; event type = `on_chat_model_stream` for tokens
- `get_stream_writer()` does NOT work in async tools (issue #6447) — use `StreamWriter` param injection

### 7. Thread Management

**Recommendation (from research):** One persistent thread per patient — `thread_id = f"patient-{patient_id}"`. All interactions accumulate. `RemoveMessage` for history trimming.

- Thread IDs passed via `config["configurable"]["thread_id"]`
- Compiled graph is thread-safe and shareable — no state on graph instance
- Proactive invocations: scheduler calls `graph.ainvoke()` with same thread_id; checkpointer resumes from last state

### 8. InjectedState / InjectedStore (Tool Annotations)

- `InjectedStore` and `InjectedState` hide tool params from LLM schema
- `InjectedToolCallId` provides `tool_call_id` for the mandatory `ToolMessage` in `Command.update`
- `RemoveMessage` does NOT cross subgraph boundaries (issue #5112) — acceptable for single-graph arch
- Store namespace is a tuple: `namespace = ("patient_profiles", patient_id)`

### 9. Advisory Lock for Concurrent Invocations

**Plan correction (`plan.md:559`):** `pg_advisory_xact_lock` was specified inside `load_patient_context` — this is WRONG. `pg_advisory_xact_lock` is transaction-level; it releases when the node's session commits, before `save_patient_context` runs. The lock must be acquired at the call site (FastAPI handler / scheduler job) on a DEDICATED connection from Pool A, held for the full invocation, released explicitly in `try/finally`.

- Use `pg_advisory_lock` (session-level), not `pg_advisory_xact_lock`
- Lock key: `hash(patient_id) & 0x7FFFFFFF` (positive bigint)
- SQLite: skip advisory lock entirely — `if "postgresql" not in str(engine.url): yield; return`

---

## File 3: `research-domain-model.md`

### 1. PatientPhase

```python
from enum import StrEnum

class PatientPhase(StrEnum):
    PENDING = "PENDING"
    ONBOARDING = "ONBOARDING"
    ACTIVE = "ACTIVE"
    RE_ENGAGING = "RE_ENGAGING"
    DORMANT = "DORMANT"
```

Stored as `String(20)` — NOT native PostgreSQL ENUM. Reason: native ENUM requires ALTER TABLE to add values, behaves inconsistently across Alembic dialects, and `(str, Enum)` mixin has a psycopg3 storage edge case (issue #13052).

### 2. Phase State Machine

Pure Python adjacency map in `domain/phase_machine.py`. The complete truth table:

| Current | Event | Next |
|---|---|---|
| PENDING | onboarding_initiated | ONBOARDING |
| ONBOARDING | goal_confirmed | ACTIVE |
| ONBOARDING | no_response_timeout | DORMANT |
| ACTIVE | missed_third_message | RE_ENGAGING |
| ACTIVE | patient_disengaged | DORMANT |
| RE_ENGAGING | patient_responded | ACTIVE |
| RE_ENGAGING | missed_third_message | DORMANT |
| DORMANT | patient_returned | RE_ENGAGING |

- `transition(current, event) -> PatientPhase` — raises `PhaseTransitionError` for any unmapped pair
- LLM never calls this function
- `allowed_events(phase)` supports Hypothesis property-based testing

### 3. Consent

- `ConsentService.check()` wraps MedBridge Go call in broad `except Exception` → denied on any failure (fail-safe)
- Returns immutable `ConsentResult(logged_in, outreach_consented, patient_id, checked_at)` — NOT a boolean
- Result stored in `PatientState` for current invocation only; never persisted as "consent is OK" in DB
- `is_valid` property: `logged_in AND outreach_consented`

### 4. AuditEvent Model

- NO foreign key to `patients` — audit must survive patient record deletion for HIPAA 6-year retention
- `write_only=True` on the Patient→AuditEvent relationship — never iterate as collection
- `REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM healthcoach_app` in the Alembic migration (not app code)
- Defense-in-depth: `BEFORE UPDATE OR DELETE` trigger also added
- `emit_audit_event()` must be called inside the caller's `session.begin()` block — not a separate commit

### 5. Repository Pattern

- `BaseRepository[ModelT]` generic with `flush()` in `create()` — not `commit()`
- Session commit owned by FastAPI dependency (HTTP) or `async with session.begin()` (workers)
- `get_by_id_for_update()` uses `with_for_update()` for optimistic-lock phase transitions
- `selectinload` for one-to-many, `joinedload` for many-to-one; never rely on `lazy="raise"` in queries

### 6. Structured Goal Extraction

```python
ExtractedGoal = model.with_structured_output(ExtractedGoal, method="json_schema", strict=True)
```
- `raw_patient_text` stored in ORM for audit; excluded from `GoalRead` API schema (PHI minimization)

### 7. Multi-Tenancy

- `tenant_id` on every table (UUID)
- `SET LOCAL app.current_tenant_id = :tenant_id` per session for Row-Level Security
- RLS policies on all tables reference `current_setting('app.current_tenant_id')`

### 8. Idempotency

- `ProcessedEvent` table for inbound webhook deduplication
- `INSERT ... ON CONFLICT DO NOTHING` is the idempotency primitive
- Tool calls in graph: `InjectedToolCallId` + idempotency_key in the tool's side-effect write

### 9. LangGraph↔DB Synchronization Boundary

`load_patient_context` and `save_patient_context` are the ONLY nodes touching the domain DB. All agent nodes between them operate only on `PatientState`.

Data placement:
- Domain DB: patient phase, active goal, unanswered count, safety decisions, scheduled jobs, audit events
- LangGraph checkpointer blob: conversation message history (for replay)
- PatientState (in-flight): consent result, current invocation's view of domain data

`save_patient_context` is idempotent by design — upserts and `ON CONFLICT DO NOTHING` allow replay safety if crash occurs between save and checkpointer commit.

---

## File 4: `research-fastapi-sqlalchemy.md`

### 1. App Lifecycle

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await lg_pool.open()    # Pool B first
    yield
    await lg_pool.close()   # Pool B closes first
    await engine.dispose()  # Pool A disposes after
```

`@app.on_event` is deprecated. `lifespan=` is the correct parameter.

### 2. Mandatory SQLAlchemy Settings

```python
engine = create_async_engine(
    settings.database_url,       # must be postgresql+psycopg://
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,          # MANDATORY — managed DB idle timeout
    pool_recycle=1800,           # 30 min — prevents stale connections
)
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,      # MANDATORY — post-commit access in async
    autoflush=False,
)
```

### 3. Pool Architecture

| | Pool A | Pool B |
|---|---|---|
| Type | SQLAlchemy `AsyncAdaptedQueuePool` | psycopg3 `AsyncConnectionPool` |
| Used by | App queries, repos, audit, scheduled_jobs | LangGraph checkpointer + Store |
| Config | Standard | `autocommit=True`, `row_factory=dict_row`, `prepare_threshold=0` |
| Incompatibility | Cannot share with Pool B | Cannot share with Pool A |
| Open | Managed by engine | `open=False` in constructor; `await pool.open()` in lifespan |

### 4. Alembic

- Initialize with `-t async` flag
- Use `NullPool` in migrations — avoids lifecycle issues with `asyncio.run()`
- `run_sync()` wrapper pattern in `env.py`
- `alembic revision --autogenerate` cannot detect column/table renames — always review before applying
- Alembic called programmatically from within an async context fails with "event loop already running" — use CLI in CI

### 5. Pydantic v2 ORM Integration

- `ConfigDict(from_attributes=True)` + `model_validate(orm_obj)` — replaces v1 `orm_mode` + `from_orm()`
- `GoalRead` excludes `raw_patient_text` (PHI minimization)

### 6. SSE Streaming

Required headers:
```
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no    # Without this, nginx buffers the entire stream
```

- `ASGITransport` with streaming has known limitation (GH issue #2186) — test the generator function directly
- `astream_events(version="v2")` — filter by `event.get("event")` kind

### 7. Health Endpoints

- `GET /health/live` — never checks DB or LLM; always 200 if process running
- `GET /health/ready` — checks Pool A and Pool B; returns 503 if either fails
- Pool B readiness check uses `async with await lg_pool.getconn() as conn:` (note `await` before `getconn()`)

### 8. Known Open Issues

- pyright strict + SQLAlchemy ORM constructors (issue #12268): no type validation on constructor args — use typed repository methods
- `add_conditional_edges` requires `# type: ignore[arg-type]` (issue #6540)
- `AsyncGenerator[str, None]` return annotation on SSE generators may need `# type: ignore[return-value]` on `StreamingResponse` line (issue #5411)

---

## File 5: `research-safety-llm.md`

### 1. Two Classifier Passes (Both Required)

**Pass 1 — Input crisis pre-check** (on patient message, before any main generation):
```python
class InputCrisisCheck(BaseModel):
    contains_crisis: bool
    crisis_level: CrisisLevel   # NONE | POSSIBLE | EXPLICIT
    reasoning: str              # one sentence, no patient text
```
If crisis detected at any level → write durable alert, deliver safe 988 message, END (no main generation).

**Pass 2 — Output safety gate** (on coach reply, before delivery):
```python
class ClassifierOutput(BaseModel):
    decision: SafetyDecision    # SAFE | CLINICAL_BOUNDARY | CRISIS | JAILBREAK
    crisis_level: CrisisLevel
    reasoning: str              # one sentence, no patient text
    confidence: float           # 0.0-1.0
```

### 2. Classifier Decision Routing

```
SAFE → deliver normally
CRISIS → [crisis protocol — alert FIRST, then safe 988 message, no retry]
JAILBREAK → safe fallback, no retry (retry feeds the attack), no internal disclosure
CLINICAL_BOUNDARY → retry once with augmented HumanMessage prefix
  retry result SAFE → deliver
  retry result ANYTHING ELSE → safe generic fallback
confidence < 0.7 → treat as blocked (same as flagged category)
classifier times out or invalid JSON → treat as CLINICAL_BOUNDARY (conservative block)
```

**CRITICAL ORDER:** Alert intent row written FIRST, then patient-facing message. Crash durability. Outbox worker handles transport.

### 3. Classifier Failure Mode

Do NOT fall back to a different LLM vendor on classifier failure. Block conservatively:
```python
except Exception:
    return ClassifierOutput(
        decision=SafetyDecision.CLINICAL_BOUNDARY,
        crisis_level=CrisisLevel.NONE,
        reasoning="classifier unavailable, blocking conservatively",
        confidence=1.0,
    )
```

### 4. LLM Provider Versions

| Package | Version | Notes |
|---|---|---|
| `langchain-anthropic` | 1.3.4 (Feb 24, 2026) | 0.3 series dead |
| `langchain-openai` | 1.1.11 (Mar 9, 2026) | |
| `langchain-aws` | 1.4.0 (Mar 9, 2026) | |

Active models:
- `claude-sonnet-4-6` — main generation; 1.29% prompt injection rate (vs 49.36% for Sonnet 4.5)
- `claude-haiku-4-5-20251001` — safety classifier; active until Oct 15, 2026
- `claude-3-haiku-20240307` — **RETIRES APRIL 20, 2026 — DO NOT USE**

### 5. Anthropic API

- `max_tokens` MUST be set explicitly on all `ChatAnthropic` instances (no safe default in 1.x)
  - Classifier: `max_tokens=512`
  - Main gen: `max_tokens=4096` or more
- Structured outputs GA — old beta header `anthropic-beta: structured-outputs-2025-11-13` deprecated
- `with_structured_output(method="json_schema")` works transparently
- ZDR: per-org via account team; covers `/v1/messages` only
- NOT ZDR-eligible: Batch API, Code Execution, Files API — never on PHI paths
- `temperature=0.0` on classifier for determinism

### 6. OpenAI API

- Use Chat Completions, NOT Responses API (Responses API stores state by default, ZDR complications)
- `max_completion_tokens` not `max_tokens` (deprecated Sep 2024)
- `max_retries=0` on both primary and fallback when using `with_fallbacks()` — built-in retries prevent fallback trigger

### 7. Fallback Pattern

```python
primary = ChatAnthropic(model="claude-sonnet-4-6", max_tokens=4096, max_retries=0)
fallback = ChatOpenAI(model="gpt-4o", max_completion_tokens=4096, max_retries=0)
llm = primary.with_fallbacks([fallback], exceptions_to_handle=(APIStatusError, APIConnectionError, APITimeoutError))
```

### 8. AWS Bedrock

- HIPAA-eligible under AWS BAA
- `ChatBedrockConverse.with_structured_output()` uses tool-call forcing, NOT constrained decoding — schema compliance not guaranteed; add application-side validation
- Recommended: register as factory option, defer for MVP

### 9. Prompt Injection Defense

| Layer | Implementation |
|---|---|
| Model selection | Sonnet 4.6 (1.29% injection rate — primary defense) |
| Structural isolation | System prompt server-side only; never echoed |
| Input delimiter | `<patient_message>{text}</patient_message>` in prompts |
| Classifier detection | `JAILBREAK` category in output classifier |
| No system disclosure | Safe fallback without exposing internals |

### 10. Safe Fallback Messages (Deterministic Strings)

Three hardcoded fallback strings in application code — never LLM-generated:
- `SAFE_FALLBACK_GENERIC` — for CLINICAL_BOUNDARY exhausted
- `SAFE_FALLBACK_JAILBREAK` — for JAILBREAK
- `SAFE_FALLBACK_CRISIS` — includes 988 number and care team notification

### 11. Crisis Protocol

What the coach must NOT do in a crisis response: no extended empathy, no coping strategies, no follow-up questions about the crisis, no counseling. Only provide 988 and care team notification.

Alert idempotency key format: `crisis:{patient_id}:{conversation_id}:{turn_number}`

---

## File 6: `research-scheduling-observability.md`

### 1. SKIP LOCKED Scheduler

```python
stmt = (
    select(ScheduledJob)
    .where(ScheduledJob.status == "pending")
    .where(ScheduledJob.scheduled_at <= func.now())
    .order_by(ScheduledJob.scheduled_at)
    .limit(batch_size)
    .with_for_update(skip_locked=True)
)
```

- Claim and status transition to `processing` MUST be in the same transaction
- Commit the `processing` status BEFORE executing the job — this releases the SKIP LOCKED row lock so other workers can see the row is claimed
- `asyncio.gather()` for concurrent job execution within a batch

### 2. Job Status Values

`pending | processing | completed | failed | dead`

Dead-letter: `status = 'dead'` on same table (not a separate table). Queryable by operator.

### 3. Startup Reconciliation (Required)

On worker startup, before the poll loop:
```python
# Reset stale 'processing' rows to 'pending'
stmt = update(ScheduledJob).where(
    ScheduledJob.status == "processing",
    ScheduledJob.started_at < func.now() - timedelta(minutes=STALE_TIMEOUT)
).values(status="pending")
```
Without this, jobs stuck in `processing` from a crash are lost permanently.

### 4. Idempotency Keys

Format: `{patient_id}:{job_type}:{reference_date}` with attempt suffix for backoff retries.
Backoff keys must include attempt number: `{patient_id}:backoff_check:{date}:attempt_2` — otherwise the same slot can't be re-inserted after cancellation.

`UNIQUE (idempotency_key)` constraint + `INSERT ... ON CONFLICT DO NOTHING` prevents duplicates.

### 5. Outbox Pattern

```
graph node writes outbox row (in same session.begin() as domain state write)
→ delivery worker polls (SKIP LOCKED, 5-10s interval)
→ delivery worker claims row
→ delivery worker sends message via MedBridge channel
→ marks row completed
→ on failure: exponential backoff, max_attempts, then 'dead'
```

- Do NOT deliver directly from graph nodes
- Outbox stores `message_ref_id` (UUID), never raw text (PHI safety)
- Priority: `priority DESC, created_at ASC` — urgent alerts (crisis) before routine messages
- Poll interval for delivery: 5-10s (shorter than scheduler 30s)
- PostgreSQL LISTEN/NOTIFY could reduce latency for crisis alerts — evaluate in M6 if needed

### 6. Timezone Handling

```python
from zoneinfo import ZoneInfo
tz = ZoneInfo("America/New_York")
local_now = datetime.now(tz)
```

- `tzdata` must be a runtime dependency — containers often strip system tzdata; `zoneinfo` silently fails without it
- Quiet hours: 9 PM–8 AM local time; enforce before inserting `scheduled_at`
- Jitter: 0–30 minutes uniform random for day-scale scheduling
- Always store `scheduled_at` in UTC (TIMESTAMPTZ)

### 7. Observability

**structlog:**
- `clear_contextvars()` at start of EVERY request — prevents context bleed between async requests
- JSON renderer in prod, ConsoleRenderer in dev
- Never log PHI — only opaque UUIDs (`patient_id`, `job_id`)

**OTEL:**
- Inject trace_id/span_id into structlog via custom processor calling `trace.get_current_span()`
- OTEL span attributes must not contain PHI
- OTEL backend BAA coverage must be confirmed if using hosted backend
- `SQLAlchemyInstrumentor().instrument(engine=engine)` called after engine creation (inside lifespan)
- `configure_otel()` before `configure_fastapi_otel()`, both before first request

### 8. Audit Event Immutability

- `REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM healthcoach_app` in the migration SQL
- `BEFORE UPDATE OR DELETE` trigger as defense-in-depth
- No migration may DROP or TRUNCATE the audit table
- `AuditEvent` has NO FK to `patients` (must survive patient record deletion)
- `emit_audit_event()` must be in the caller's transaction — not a separate commit

---

## File 7: `research-testing-setup.md`

### 1. pytest-asyncio Configuration

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "session"
```

- `event_loop` fixture REMOVED in 1.x — never override it
- `asyncio_default_fixture_loop_scope` must match the widest scope used (typically "session" for DB engine)
- `asyncio_mode = "auto"` takes ownership of all async fixtures

### 2. SQLAlchemy Test Isolation

```python
@pytest.fixture(scope="session")
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool, ...)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine

@pytest.fixture
async def db_session(engine):
    async with engine.connect() as conn:
        await conn.begin()
        async with AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ) as session:
            yield session
        await conn.rollback()
```

`join_transaction_mode="create_savepoint"` allows app code to call `session.commit()` without actually committing to the DB. Critical for test isolation.

SQLite (`StaticPool`, `check_same_thread=False`) for unit tests; PostgreSQL for integration tests that use SKIP LOCKED.

### 3. LangGraph Testing

- Fresh graph with new `InMemorySaver()` per test module (or per test for full isolation)
- `InMemorySaver` + `InMemoryStore` for all graph tests — never `AsyncPostgresSaver` in unit tests
- Node isolation: `graph.nodes["node_name"].ainvoke(state)` bypasses routing
- Test conditional routing by calling the router function directly — it is pure Python
- `graph.aupdate_state(config, state, as_node="node_name")` for partial execution

### 4. GenericFakeChatModel Limitation

`GenericFakeChatModel` does NOT implement `bind_tools()` — raises `NotImplementedError` (GH discussion #29893, still open).

For tool call testing:
```python
tool_call_message = AIMessage(
    content="",
    tool_calls=[ToolCall(name="tool_name", args={...}, id="fake-tool-call-id")]
)
fake = GenericFakeChatModel(messages=iter([tool_call_message]))
```

`GenericFakeChatModel` IS usable for non-tool conversational nodes and streaming tests.

### 5. HTTP Mocking (respx)

```python
async def test_consent(respx_mock):
    respx_mock.get("https://api.medbridge.example/consent/p-001").respond(
        200, json={"consented": True, "logged_in": True}
    )
    result = await consent_service.check("p-001")
```

For error simulation: `respx_mock.get(...).mock(side_effect=httpx.ConnectError)`

### 6. Time Mocking (time-machine)

Use context manager for async tests:
```python
with time_machine.travel("2026-01-15 08:00:00+00:00"):
    result = calculate_send_time(...)
```
Prefer `time_machine` over `freezegun` — C extension, more reliable with pytest assertion rewriting.

### 7. Hypothesis for Phase Transitions

```python
class PatientLifecycleMachine(RuleBasedStateMachine):
    @initialize()
    def setup(self):
        self.patient = PatientService.create_pending()

    @rule()
    @precondition(lambda self: self.patient.phase == PatientPhase.PENDING)
    def begin_onboarding(self):
        PhaseService.transition(self.patient, PatientPhase.ONBOARDING)
        assert self.patient.phase == PatientPhase.ONBOARDING
```

`RuleBasedStateMachine` rules CANNOT use pytest fixtures — use `initialize()` or strategies.

### 8. DeepEval

- `DEEPEVAL_TELEMETRY_OPT_OUT=1` (numeric `1`, NOT `YES`) — post-2025 patch changed truthy check
- Run with `deepeval test run tests/evals/` not bare pytest for full reporting
- Evals run in separate CI job, gated to `main` branch — they make real LLM API calls
- Thresholds: clinical safety 0.9, crisis detection 0.95, goal extraction 0.85

### 9. Project Setup

**Ruff config highlights:**
- Select: E, W, F, I, UP, B, C4, SIM, RET, RUF, N, ANN, ASYNC, S, PTH, TC
- Ignore: ANN101, ANN102, ANN401, S101, S105
- Per-file relax ANN+S in tests/; ANN+UP in alembic/
- DO NOT enable `preview = true` in CI

**pyright config:**
- `pyrightconfig.json` takes precedence over `pyproject.toml`
- `strict` on `src/health_coach/`; `--level basic` on `tests/`
- `reportMissingTypeStubs = false`

**CI jobs (parallel):**
1. lint (ruff check + format check)
2. typecheck (pyright src/ + pyright --level basic tests/)
3. test-unit (SQLite, fast)
4. test-integration (PostgreSQL service container)
5. docker-build (BuildKit cache)
6. evals (separate workflow, main branch only)

**`astral-sh/setup-uv@v7`** is the current action (March 2026).

**Docker:**
```dockerfile
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,...
    uv sync --locked --no-dev --no-install-project
```
`--no-install-project` maximizes layer caching — dependency layer only invalidated when `pyproject.toml` or `uv.lock` changes.

### 10. Critical Anti-Patterns

1. Never share `AsyncSession` across tests
2. Never call real LLM APIs in unit/integration tests
3. Never run scheduler tests against SQLite
4. Never set `DEEPEVAL_TELEMETRY_OPT_OUT=YES` — use `1`
5. Never override `event_loop` fixture in pytest-asyncio 1.x
6. Never share Pool A with Pool B

---

## File 8: `research-injectedstate-tool-mutation.md`

### Key Finding

`InjectedState` is READ-ONLY for state propagation. Mutating the injected dict does NOT update graph state. ToolNode does a shallow ref inject; mutation is discarded after node returns.

Side-effecting tools MUST use:
```python
return Command(
    update={
        "pending_effects": ...,
        "messages": [ToolMessage(..., tool_call_id=tool_call_id)]
    }
)
```

`InjectedToolCallId` annotation provides `tool_call_id` for the mandatory `ToolMessage` in `Command.update`. Missing `ToolMessage` causes a runtime error.

Read-only tools (e.g., `get_program_summary`, `get_adherence_summary`) return plain `str` — ToolNode wraps automatically.

`plan.md:796` contains an error: "adds goal data to [InjectedState]" — should be "reads via InjectedState, returns via Command.update".

---

## File 9: `research-scheduling-gaps.md`

### Four Specific Scheduling Questions Answered

**Q1: When does `unanswered_count` increment?**
At no-response detection (when the follow-up scheduler job fires and finds no patient reply since `last_contact_at`), NOT at outreach send time. Plan.md:945 specifies this correctly.

The graph detects "no reply" by inspecting `state["messages"]` for a `HumanMessage` with timestamp after `patient.last_contact_at`. Implementation detail: `last_contact_at` must be updated atomically when each outreach is sent.

**Q2: Day 5/7 job seeding strategy?**
Recommendation: **chain scheduling (B3)** — create only Day 2 at onboarding completion. Day 2 handler creates Day 5 (if patient responded) or starts backoff (if not). Day 5 handler creates Day 7 conditionally. This eliminates orphaned pre-seeded jobs.

**Q3: 72-hour onboarding timeout handler?**
Implement dedicated `handle_onboarding_timeout` handler:
1. Guard: if `patient.phase != ONBOARDING`, mark completed with skip metadata
2. Call `apply_phase_transition(patient_id, "no_response_timeout", actor="scheduler")`
3. Phase guard provides replay safety

**Q4: Cancel pending backoff jobs on patient response?**
In the RE_ENGAGING agent node when `invocation_source="patient"`: add `pending_effects["cancelled_jobs"]` with job IDs. `save_patient_context` executes the UPDATE in the same transaction as the `unanswered_count` reset.

### Open Gaps After This Research

1. How exactly does the graph detect "no reply since last outreach" — by timestamp or message list position?
2. Are Day 5/7 job types distinct (`day_5_followup`) or do they share a type with a day parameter?
3. Reconciliation logic under chain scheduling needs to check completion history, not just pending count
4. Backoff cancellation query scope — narrow (by job_type IN backoff types) is safer

---

## File 10: `research-summary-for-milestone-review.md`

### Cross-Cutting "Frequently Wrong in Plans" List

1. Using `claude-3-haiku-20240307` anywhere — retires April 20, 2026
2. Sharing Pool A and Pool B — incompatible connection requirements
3. Delivering crisis alert directly from graph node instead of writing to outbox first
4. `expire_on_commit=True` (default) on session factory — crashes in async context
5. Missing `clear_contextvars()` at request start — context bleed in async workers
6. Using `@app.on_event` — deprecated
7. Running scheduler tests against SQLite — SKIP LOCKED is silently unsupported
8. Setting `DEEPEVAL_TELEMETRY_OPT_OUT=YES` — use `1`
9. Putting `checkpointer.setup()` in lifespan startup — must be a one-time migration script
10. Opening Pool B in constructor — opens before event loop is ready

### Invariants That Must Hold Across All Milestones

- All outbound patient messages go through BOTH classifier passes (input pre-check + output gate)
- Phase transitions are never LLM-decided — always deterministic application code
- Consent is checked per-interaction, not cached from session creation
- Alert intent row precedes patient-facing message delivery in every crisis path
- Audit events and domain state writes commit atomically
- No PHI appears in logs, OTEL spans, or error responses
- Two connection pools remain permanently separate

### Ordering Dependencies by Milestone

**M1 must do before anything else:**
- `Base.metadata` naming convention before any model
- `configure_logging()` before any logger
- Pool B `open=False` + `await pool.open()` in lifespan (not constructor)

**Must be in migration, not app startup:**
- `REVOKE UPDATE, DELETE, TRUNCATE` on `audit_events`
- `checkpointer.setup()` and `store.setup()`

**Must exist before safety pipeline works:**
- `alert_intents` table (for durable crisis alerts)
- `outbox` table and delivery worker (for transport)

---

## File 11: `research-validation-graph-topology-and-scheduling.md`

### Validated Research Claims

**ToolNode + tools_condition loop:** Fully documented. `add_edge("tools", "active_agent")` creates the loop; `tools_condition` is the exit gate; node name "tools" must match the string `tools_condition` returns.

**Command vs add_conditional_edges:** Documented distinction. `Command` when routing depends on node's own computation; `add_conditional_edges` for static topology.

**Thread management:** Single persistent thread per patient confirmed. Scheduler calls `graph.ainvoke()` with same `thread_id`; checkpointer resumes from last state.

**Scheduler → graph.ainvoke wiring:** Not explicitly defined in scheduling research. The `dispatch_job` function is called at `research-scheduling-observability.md:180` but the bridge to `graph.ainvoke()` is "Domain-specific dispatch." Implementation must define this.

**Domain DB vs LangGraph state boundary:** Fully documented in domain model research. `load_patient_context` / `save_patient_context` are the only DB-touching nodes.

### Three Gaps Identified

**Gap 1: Concurrent same-patient graph invocations**
If two scheduler jobs for the same patient are due in the same batch, `graph.ainvoke()` could be called twice on the same `thread_id` concurrently. Research does not address checkpointer locking semantics for this case. Mitigation: idempotency_key constraint already prevents most duplicate-job scenarios; serialization within `dispatch_job` for same-patient jobs is the safest MVP approach.

**Gap 2: `invocation_source` field**
Research does not define how the graph distinguishes scheduler vs patient HTTP invocations. `PatientState` schema has no `invocation_source` field. Recommendation: add `invocation_source: Literal["patient", "scheduler"] | None` to `PatientState` and set at the call site.

**Gap 3: `dispatch_job` implementation**
Scheduling research calls it but doesn't define it. It must call `graph.ainvoke({"patient_id": ..., "invocation_source": "scheduler"}, config=..., context=...)`.

### Recommendation from This File

Use `invocation_source` field (Option B). Single persistent thread per patient (Option A from thread management research). Concurrent same-patient serialization at the dispatcher level for MVP.

---

## Summary: What an Implementation Plan Must Account For

### Non-Negotiable Technical Constraints

**LangGraph:**
- `context_schema` (not `config_schema`) for DI
- `# type: ignore[arg-type]` on every `add_conditional_edges` call
- `autocommit=True`, `prepare_threshold=0`, `row_factory=dict_row` on Pool B
- `max_tokens` explicit on every `ChatAnthropic`
- `max_retries=0` on both primary and fallback when using `with_fallbacks()`
- `InjectedState` is read-only; side effects must return via `Command.update` with `ToolMessage`
- `invocation_source` field on `PatientState` to distinguish scheduler vs patient invocations
- Advisory lock at call site (FastAPI handler / `dispatch_job`), not inside `load_patient_context`

**Database:**
- `expire_on_commit=False`, `pool_pre_ping=True`, `lazy="raise"` on all SQLAlchemy sessions
- `postgresql+psycopg://` URL scheme — normalized via `field_validator` in Settings
- `String(20)` for phase columns, NOT native PG ENUM
- `AuditEvent` has NO FK to `patients`
- `REVOKE UPDATE, DELETE, TRUNCATE` in the migration SQL
- `join_transaction_mode="create_savepoint"` for test sessions

**Safety:**
- Two classifier passes required — input pre-check AND output gate
- Alert intent written BEFORE patient-facing message
- `claude-3-haiku-20240307` must NOT appear anywhere — retires April 20, 2026
- Classifier failure = conservative block (CLINICAL_BOUNDARY), never vendor fallback

**Scheduling:**
- Startup reconciliation resets stale `processing` rows
- Commit `processing` status BEFORE executing the job
- `tzdata` as runtime dependency
- Scheduler tests against PostgreSQL only (SQLite lacks SKIP LOCKED)
- Chain scheduling for Day 2/5/7 jobs (not pre-seeded)

**Testing:**
- `asyncio_mode = "auto"` AND `asyncio_default_fixture_loop_scope = "session"` both required
- `event_loop` fixture removed in pytest-asyncio 1.x
- `DEEPEVAL_TELEMETRY_OPT_OUT=1` (numeric, not YES)
- `GenericFakeChatModel` cannot `bind_tools()` — construct `AIMessage(tool_calls=[...])` directly

**Models:**
- `langchain-anthropic>=1.3.4` (not `>=0.3`)
- `claude-sonnet-4-6` for main gen (1.29% injection rate)
- `claude-haiku-4-5-20251001` for classifier
- Structured outputs via `with_structured_output(method="json_schema")` — GA, no beta header

### Still-Open Questions (Need Decisions Before M5+)

1. MedBridge Go API contract shape
2. Clinician alert channel (email / Slack / dashboard)
3. Patient timezone source
4. Cloud platform (affects deployment)
5. How the graph detects "no reply since last outreach" — timestamp vs message position
6. Exact job_type naming convention for Day 2/5/7 (distinct types vs shared with parameter)
7. Reconciliation logic under chain scheduling (needs job completion history)
