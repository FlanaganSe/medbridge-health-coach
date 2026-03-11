## 1. LangGraph 1.x Implementation Patterns

**Date:** 2026-03-10
**LangGraph version researched:** 1.1.0 (released 2026-03-10)
**Companion packages:** `langgraph-checkpoint-postgres` 3.0.4, `langgraph-checkpoint-sqlite` 3.0.3

---

### Current State

The project has selected LangGraph 1.x as the AI orchestration framework (`.claude/plans/FINAL_CONSOLIDATED_RESEARCH.md:194`). The high-level graph topology and state schema are already designed there (`FINAL_CONSOLIDATED_RESEARCH.md:204-260`), but no implementation code exists yet. This document provides the concrete 1.x API patterns needed to actually write that code.

---

### Constraints

1. **Python 3.12+** — stack rule; relevant because `get_stream_writer()` context propagation requires Python ≥ 3.11. We are safe.
2. **Async-first** — all I/O nodes must be `async def`. `ToolNode` uses `asyncio.gather()` for parallel async tools.
3. **Two separate connection pools** — Pool A (SQLAlchemy, psycopg3) for app queries; Pool B (psycopg3 direct) for LangGraph checkpointer+Store. Do NOT share — incompatible lifecycle (`FINAL_CONSOLIDATED_RESEARCH.md` §17.8).
4. **`config_schema` is deprecated** in LangGraph 0.6+ — use `context_schema` exclusively. Support for `config_schema` will be removed in 2.0.
5. **`create_react_agent` is deprecated** in 1.x, scheduled for removal in 2.0. Replacement is explicit `StateGraph` construction or `langchain.agents.create_agent` for trivial cases. This project must use explicit construction.
6. **`total=False` TypedDict gotcha** — pyright and mypy have partial-return issues with LangGraph TypedDict state. Use `# type: ignore[arg-type]` on `add_conditional_edges`. (Known issue #6540.)
7. **`add_conditional_edges` type annotation** requires `# type: ignore[arg-type]` for pyright-strict clean builds (issue still open as of 1.1.0).
8. **Async tools + `get_stream_writer()`** — known issue #6447: async tools do not support `get_stream_writer()` for custom events. Use sync tools or the `StreamWriter` parameter injection approach for streaming from tools.

---

### 1. StateGraph Construction

**Package:** `from langgraph.graph import StateGraph, START, END`

#### Constructor signature

```python
builder = StateGraph(
    State,                          # Required: TypedDict or Pydantic BaseModel
    input_schema=InputState,        # Optional: restrict what callers pass in
    output_schema=OutputState,      # Optional: restrict what callers receive back
    context_schema=ContextSchema,   # Optional: replaces config_schema (deprecated)
)
```

#### State definition pattern

LangGraph recommends `TypedDict` with `Annotated` fields. Pydantic BaseModel works but adds validation overhead on every node transition.

```python
from typing import Annotated
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage
from datetime import datetime
from enum import Enum

class PatientPhase(str, Enum):
    PENDING = "PENDING"
    ONBOARDING = "ONBOARDING"
    ACTIVE = "ACTIVE"
    RE_ENGAGING = "RE_ENGAGING"
    DORMANT = "DORMANT"

class PatientState(TypedDict):
    patient_id: str
    tenant_id: str
    consent_verified: bool
    phase: PatientPhase
    messages: Annotated[list[BaseMessage], add_messages]  # reducer handles append/update
    goal: str | None
    unanswered_count: int
    last_contact_at: datetime | None
    safety_flags: dict[str, object]
```

**Note on `total=False`:** LangGraph TypedDict state does NOT require `total=False` in itself, but pyright will flag partial returns from node functions (not all keys returned). Use `# type: ignore[return-value]` on those nodes or return only the keys being updated (LangGraph merges, not replaces, returned dicts into state).

#### add_node

```python
from langgraph.types import RetryPolicy

builder.add_node("consent_gate", consent_gate_node)

# With retry policy:
builder.add_node(
    "active_agent",
    active_agent_node,
    retry_policy=RetryPolicy(
        initial_interval=1.0,   # seconds before first retry
        backoff_factor=2.0,     # exponential multiplier
        max_interval=10.0,      # cap on wait time
        max_attempts=3,         # total attempts including first
        jitter=True,            # add randomness to avoid thundering herd
        retry_on=Exception,     # exception class(es) or callable
        # retry_on=lambda e: isinstance(e, (httpx.HTTPStatusError,))
    ),
)

# With cache:
from langgraph.types import CachePolicy
builder.add_node("node", func, cache_policy=CachePolicy(ttl=60))
```

**`RetryPolicy.retry_on` default behavior:** Retries on any exception EXCEPT `ValueError`, `TypeError`, `ArithmeticError`, `ImportError`, `LookupError`, `NameError`, `SyntaxError`, `RuntimeError`, `ReferenceError`, `StopIteration`, `StopAsyncIteration`, `OSError`. For HTTP libraries (requests, httpx), only retries on 5xx. Known issue: Pydantic `ValidationError` is not retried by default (issue #6027).

#### add_edge and add_conditional_edges

```python
builder.add_edge(START, "consent_gate")
builder.add_edge("consent_gate", "load_patient_context")

# Conditional routing — pure Python function, reads state:
def phase_router(state: PatientState) -> str:
    phase = state["phase"]
    if phase == PatientPhase.ONBOARDING:
        return "onboarding_agent"
    elif phase == PatientPhase.ACTIVE:
        return "active_agent"
    elif phase == PatientPhase.RE_ENGAGING:
        return "reengagement_agent"
    elif phase == PatientPhase.DORMANT:
        return "dormant_node"
    return "pending_node"

builder.add_conditional_edges(  # type: ignore[arg-type]
    "load_patient_context",
    phase_router,
    # optional mapping dict if return values aren't node names:
    # {"onboarding": "onboarding_agent", "active": "active_agent"}
)
```

The `# type: ignore[arg-type]` is required for pyright-strict. It is a known open issue (#6540).

---

### 2. Runtime and `context_schema`

`context_schema` replaces `config["configurable"]` for dependency injection. Runtime context is **immutable per run** (set at invoke time, not changeable mid-graph).

#### Define context

```python
from dataclasses import dataclass
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Callable

@dataclass
class CoachContext:
    patient_id: str
    tenant_id: str
    # Pass factories, not live sessions (sessions are not safe to share across async boundaries)
    db_session_factory: Callable[[], AsyncSession]
    consent_api_url: str
```

#### Reference in graph construction

```python
builder = StateGraph(PatientState, context_schema=CoachContext)
```

#### Access in node functions

```python
from langgraph.runtime import Runtime

async def load_patient_context(
    state: PatientState,
    runtime: Runtime[CoachContext],
) -> dict:
    async with runtime.context.db_session_factory() as session:
        # query DB...
        pass
    return {"goal": patient.goal}
```

#### Access store via Runtime

```python
async def load_patient_context(
    state: PatientState,
    runtime: Runtime[CoachContext],
) -> dict:
    namespace = (state["patient_id"], "profile")
    item = await runtime.store.aget(namespace, "current")
    return {"goal": item.value["goal"] if item else None}
```

#### Pass context at invocation

```python
result = await graph.ainvoke(
    {"patient_id": "p-123", "phase": PatientPhase.ONBOARDING, ...},
    config={"configurable": {"thread_id": "thread-abc"}},
    context=CoachContext(
        patient_id="p-123",
        tenant_id="tenant-xyz",
        db_session_factory=get_async_session,
        consent_api_url="https://...",
    ),
)
```

**Critical note on `get_runtime()` in tool bodies:** The MEMORY.md notes that `get_runtime()` inside tool bodies works but `ToolRuntime` parameter injection is broken (issue #6431, still open as of 1.1.0). Use `InjectedState` and `InjectedStore` annotations instead for tools, or pass context via the `ToolRuntime` bundle which does not require the broken injection path.

---

### 3. Command for Routing

`Command` combines state update + routing in a single return. Use it when routing logic depends on work done inside the node itself (not on pre-existing state).

#### When to use Command vs add_conditional_edges

| Use `add_conditional_edges` | Use `Command` |
|---|---|
| Routing based on existing state | Routing based on result of node work |
| Route from one source to multiple targets | Node decides its own next destination |
| Static, declarable at build time | Dynamic, only known at runtime |
| Safety classifier → deliver or retry | Active agent → tool or end |

#### Command API

```python
from typing import Literal
from langgraph.types import Command

def safety_classifier_node(state: PatientState) -> Command[Literal["deliver_message", "retry_generation", "send_fallback"]]:
    result = classify_safety(state["messages"][-1])
    if result.is_safe:
        return Command(
            update={"safety_flags": {"checked": True, "passed": True}},
            goto="deliver_message",
        )
    elif result.is_retryable:
        return Command(
            update={"safety_flags": {"checked": True, "passed": False, "reason": result.reason}},
            goto="retry_generation",
        )
    else:
        return Command(
            update={"safety_flags": {"checked": True, "passed": False, "reason": result.reason}},
            goto="send_fallback",
        )
```

#### Command to parent graph (from subgraph)

```python
return Command(
    update={"foo": "bar"},
    goto="parent_node",
    graph=Command.PARENT,
)
```

#### Send for map-reduce (parallel fan-out)

```python
from langgraph.types import Send

def fan_out_node(state: PatientState) -> list[Send]:
    # Spawn parallel executions with per-instance state
    return [
        Send("process_goal", {"goal_text": g})
        for g in state["raw_goals"]
    ]
```

---

### 4. Checkpointer Setup

#### AsyncPostgresSaver (production)

**Package:** `langgraph-checkpoint-postgres` 3.0.4
**Import:** `from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver`

```python
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

# Pool B — dedicated to LangGraph only. Do NOT share with SQLAlchemy Pool A.
CHECKPOINTER_POOL_KWARGS = {
    "autocommit": True,       # REQUIRED — setup() needs autocommit to persist tables
    "prepare_threshold": 0,   # Disable prepared statements (pipeline mode incompatibility)
    "row_factory": dict_row,  # REQUIRED — checkpointer accesses rows as dicts
}

async def create_checkpointer(db_uri: str) -> AsyncPostgresSaver:
    pool = AsyncConnectionPool(
        conninfo=db_uri,
        kwargs=CHECKPOINTER_POOL_KWARGS,
        min_size=2,
        max_size=10,
    )
    await pool.open()
    checkpointer = AsyncPostgresSaver(pool)
    await checkpointer.setup()  # Creates checkpoint tables — idempotent
    return checkpointer
```

**Why `autocommit=True`:** The `.setup()` DDL must commit immediately. Without autocommit, table creation won't persist.
**Why `prepare_threshold=0`:** psycopg3 prepared statements conflict with LangGraph's use of pipeline mode. Set to 0 to disable.
**Why `row_factory=dict_row`:** The checkpointer implementation accesses rows as `row["column_name"]`.

**Issue to know:** `AsyncConnectionPool + AsyncPostgresSaver` in pipeline mode raises "cannot send pipeline when not in pipeline mode" — addressed by `prepare_threshold=0` (issue #3193).

#### AsyncSqliteSaver (local dev)

**Package:** `langgraph-checkpoint-sqlite` 3.0.3
**Import:** `from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver`

```python
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

# Context manager handles setup automatically
async with AsyncSqliteSaver.from_conn_string("checkpoints.db") as checkpointer:
    graph = builder.compile(checkpointer=checkpointer)
    # use graph here

# For testing:
async with AsyncSqliteSaver.from_conn_string(":memory:") as checkpointer:
    ...
```

**Note:** No explicit `.setup()` call needed with `AsyncSqliteSaver` — the context manager initializes tables. Not production-suitable (SQLite write performance, no SKIP LOCKED support).

---

### 5. Store API

Store provides cross-thread, long-term memory. Separate from checkpointer (which is per-thread).

#### Setup

```python
# Development:
from langgraph.store.memory import InMemoryStore
store = InMemoryStore()

# Production (dedicated pool — not shared with checkpointer or SQLAlchemy):
from langgraph.store.postgres.aio import AsyncPostgresStore

async with AsyncPostgresStore.from_conn_string(db_uri) as store:
    await store.setup()  # Creates store tables — run once
```

#### Compile with both checkpointer and store

```python
graph = builder.compile(
    checkpointer=checkpointer,
    store=store,
)
```

#### Core store methods

```python
# Namespace is a tuple of strings — hierarchical scope
namespace = ("patient_profiles", patient_id)
# or: ("goals", patient_id, "structured")

# Write
await store.aput(namespace, "key", {"goal": "walk 30 min", "set_at": "2026-03-10"})

# Read
item = await store.aget(namespace, "key")
if item:
    value = item.value   # dict you stored
    key = item.key
    ns = item.namespace

# Semantic search (requires embedding model config at store init)
results = await store.asearch(namespace, query="exercise goals", limit=5)
# results: list[Item]

# Delete
await store.adelete(namespace, "key")
```

#### Access store in nodes via Runtime

```python
async def load_patient_context(
    state: PatientState,
    runtime: Runtime[CoachContext],
) -> dict:
    namespace = ("patient_profiles", state["patient_id"])
    profile_item = await runtime.store.aget(namespace, "profile")
    return {
        "goal": profile_item.value["goal"] if profile_item else None,
    }
```

#### Access store in tools via InjectedStore

```python
from typing import Annotated
from langgraph.prebuilt import InjectedStore
from langgraph.store.base import BaseStore
from langchain.tools import tool

@tool
async def set_goal(
    goal_text: str,
    store: Annotated[BaseStore, InjectedStore()],
    state: Annotated[dict, InjectedState],  # for patient_id
) -> str:
    """Store the patient's structured goal."""
    patient_id = state["patient_id"]
    namespace = ("patient_profiles", patient_id)
    await store.aput(namespace, "goal", {"text": goal_text, "set_at": "..."})
    return f"Goal recorded: {goal_text}"
```

---

### 6. ToolNode and tools_condition

#### Standard tool calling pattern

```python
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_anthropic import ChatAnthropic

tools = [set_goal, set_reminder, get_program_summary, get_adherence_summary, alert_clinician]

llm = ChatAnthropic(
    model="claude-sonnet-4-6",
    max_tokens=2048,      # MUST be set explicitly — default changed in langchain-anthropic 1.x
).bind_tools(
    tools,
    parallel_tool_calls=False,  # Enforce sequential tool calls where order matters
)

tool_node = ToolNode(
    tools,
    name="tools",            # node name (default: "tools")
    messages_key="messages", # which state key holds messages (default: "messages")
    handle_tool_errors=True, # catch exceptions, return ToolMessage with error
)
```

#### Wire into graph

```python
builder.add_node("active_agent", active_agent_node)
builder.add_node("tools", tool_node)

builder.add_conditional_edges(  # type: ignore[arg-type]
    "active_agent",
    tools_condition,  # returns "tools" if last message has tool_calls, else END
)
builder.add_edge("tools", "active_agent")  # loop back after tool execution
```

#### tools_condition behavior

`tools_condition` inspects the last message in `state["messages"]`. Returns the string `"tools"` if `tool_calls` is non-empty, otherwise returns `END`. The return string `"tools"` must match the node name you used in `add_node`. If you named the node differently, use a custom routing function.

#### InjectedState in tools

```python
from langgraph.prebuilt import InjectedState

@tool
def get_adherence_summary(
    state: Annotated[dict, InjectedState],
) -> str:
    """Get patient adherence summary."""
    patient_id = state["patient_id"]
    # InjectedState is hidden from the LLM's tool schema
    return fetch_adherence(patient_id)
```

#### parallel_tool_calls

Set `parallel_tool_calls=False` on `.bind_tools()` when tool execution order matters or when exactly one tool call should be enforced. ToolNode executes multiple simultaneous tool calls with `asyncio.gather()` — safe for independent reads, risky for writes that depend on each other.

---

### 7. Message Handling

#### add_messages reducer

`Annotated[list[BaseMessage], add_messages]` on a state field gives it CRDT-like merge semantics:
- Messages with **new IDs** are appended.
- Messages with **existing IDs** replace the old message (update-in-place).
- Messages are never automatically deleted.

```python
# Returning messages from a node — reducer handles the merge:
async def active_agent_node(state: PatientState) -> dict:
    response = await llm.ainvoke(state["messages"])
    return {"messages": [response]}  # appended via add_messages reducer
```

#### RemoveMessage — delete by ID

```python
from langchain_core.messages import RemoveMessage

# Delete a specific message by ID:
async def trim_old_messages(state: PatientState) -> dict:
    # Keep only last 20 messages — delete everything older
    messages_to_remove = state["messages"][:-20]
    return {
        "messages": [RemoveMessage(id=m.id) for m in messages_to_remove]
    }
```

**Known issue #5112:** `RemoveMessage` does not propagate across subgraph boundaries. For single-graph architectures (our case), this works correctly.

#### trim_messages (utility function)

```python
from langchain_core.messages import trim_messages

# Trim to fit within token budget before LLM call:
async def active_agent_node(state: PatientState) -> dict:
    trimmed = trim_messages(
        state["messages"],
        max_tokens=4000,
        token_counter=llm,          # uses model's tokenizer
        strategy="last",            # keep most recent
        start_on="human",           # ensure first message is from human
        include_system=True,        # always keep system message
    )
    response = await llm.ainvoke(trimmed)
    return {"messages": [response]}
```

**Note:** `trim_messages` does NOT modify state — it returns a new list for the LLM call only. To actually remove messages from persisted state, use `RemoveMessage`.

---

### 8. Graph Compilation

```python
graph = builder.compile(
    checkpointer=checkpointer,          # enables thread persistence
    store=store,                        # enables cross-thread long-term memory
    interrupt_before=["deliver_message"],  # pause before node for HITL
    interrupt_after=[],                 # pause after node (less common)
)
```

**recursion_limit:** Default is 25. Set at invoke time (not compile time):

```python
result = await graph.ainvoke(
    state,
    config={
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 50,
    },
)
```

Or set a default at compile time via `with_config`:

```python
graph = builder.compile(...).with_config({"recursion_limit": 50})
```

**Reading current step in a node:**

```python
from langchain_core.runnables import RunnableConfig

async def some_node(state: PatientState, config: RunnableConfig) -> dict:
    step = config["metadata"]["langgraph_step"]
    return {}
```

#### RetryPolicy full signature

```python
from langgraph.types import RetryPolicy

RetryPolicy(
    initial_interval: float = 0.5,    # seconds
    backoff_factor: float = 2.0,
    max_interval: float = 128.0,      # seconds
    max_attempts: int = 3,
    jitter: bool = True,
    retry_on: type[Exception] | tuple[type[Exception], ...] | Callable[[Exception], bool] = default_retry_on,
)
```

---

### 9. Streaming

#### Stream modes

| Mode | What it yields | Best for |
|------|---------------|---------|
| `"values"` | Full state dict after each superstep | Debugging, complete state tracking |
| `"updates"` | `{node_name: {changed_keys}}` deltas | Lightweight monitoring |
| `"messages"` | `(BaseMessage_token, metadata)` tuples | Token streaming to client |
| `"custom"` | Any JSON-serializable data emitted via `get_stream_writer()` | Progress events, intermediate results |
| `"checkpoints"` | Checkpoint save events | Persistence monitoring |
| `"tasks"` | Task start/end with results/errors | Execution tracing |
| `"debug"` | Combined checkpoint + task + metadata | Full execution trace |

#### v2 streaming format (LangGraph 1.1+)

LangGraph 1.1 introduced `version="v2"` — opt-in, backward-compatible. Strongly recommended for new code.

```python
# v2: all chunks are StreamPart TypedDicts — discriminated union on chunk["type"]
async for chunk in graph.astream(
    state,
    config={"configurable": {"thread_id": thread_id}},
    stream_mode=["messages", "updates", "custom"],
    version="v2",
):
    match chunk["type"]:
        case "messages":
            token, metadata = chunk["data"]
            # metadata["langgraph_node"] tells you which node emitted it
            print(token.content, end="", flush=True)
        case "updates":
            print(f"Node update: {chunk['data']}")
        case "custom":
            print(f"Custom event: {chunk['data']}")
```

`invoke()` with `version="v2"` returns `GraphOutput`:

```python
from langgraph.types import GraphOutput

result: GraphOutput = await graph.ainvoke(state, version="v2")
final_state = result.value        # dict (or Pydantic model if output_schema is Pydantic)
interrupts = result.interrupts    # tuple[Interrupt, ...]
```

v1 (default) returns a plain dict with interrupts under `"__interrupt__"`.

#### FastAPI SSE integration

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import json

app = FastAPI()

@app.post("/coach/stream")
async def stream_coach_interaction(request: InteractionRequest):
    async def event_generator():
        config = {"configurable": {"thread_id": request.thread_id}}
        async for chunk in graph.astream(
            {"patient_id": request.patient_id, ...},
            config=config,
            stream_mode=["messages", "custom"],
            version="v2",
        ):
            if chunk["type"] == "messages":
                token, meta = chunk["data"]
                yield f"data: {json.dumps({'type': 'token', 'content': token.content})}\n\n"
            elif chunk["type"] == "custom":
                yield f"data: {json.dumps({'type': 'progress', 'data': chunk['data']})}\n\n"
        yield "data: {\"type\": \"done\"}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
```

#### Custom events from within nodes (get_stream_writer)

```python
from langgraph.config import get_stream_writer

async def safety_classifier_node(state: PatientState) -> dict:
    writer = get_stream_writer()  # requires Python 3.11+ for proper context propagation
    writer({"status": "running_safety_check"})
    result = await classify_safety(state["messages"][-1])
    writer({"status": "safety_check_complete", "passed": result.is_safe})
    return {"safety_flags": {"passed": result.is_safe}}
```

**Known issue #6447:** Async tools do NOT support `get_stream_writer()`. Use sync tools OR the `StreamWriter` parameter approach for tools that need to stream:

```python
from langgraph.types import StreamWriter

async def some_node(state: PatientState, writer: StreamWriter) -> dict:
    writer({"progress": "starting"})
    # ... work ...
    return {}
```

---

### 10. Thread Management

#### Thread ID pattern

Thread IDs are passed via `config["configurable"]["thread_id"]`. The compiled graph is thread-safe and shareable across concurrent executions — no state is stored on the graph instance.

```python
config = {"configurable": {"thread_id": "patient-p123-session-2026-03-10"}}
await graph.ainvoke(state, config=config)
```

#### Recommended threading strategy for health coach

Two valid approaches:

**Option A — One persistent thread per patient** (recommended for this project):
- `thread_id = f"patient-{patient_id}"`
- All interactions accumulate in a single thread
- Full conversation history, time-travel debugging
- Message trimming (via `RemoveMessage`) prevents unbounded growth

**Option B — One thread per check-in session:**
- `thread_id = f"patient-{patient_id}-checkin-{date}"`
- Each scheduled check-in is an independent thread
- Requires loading cross-thread context from Store (goals, phase, history summary)
- Clean isolation per interaction

**Recommendation:** Option A for v1. Simpler. Use `RemoveMessage` + periodic summarization to manage message history length.

#### Reading thread state after execution

```python
# Get current state for a thread:
state_snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
current_values = state_snapshot.values
next_nodes = state_snapshot.next  # which nodes would execute next (empty if done)

# Full history of checkpoints:
async for checkpoint in graph.aget_state_history({"configurable": {"thread_id": thread_id}}):
    print(checkpoint.config, checkpoint.values)
```

#### Creating threads for scheduled check-ins

For proactive outreach, the scheduler calls `graph.ainvoke()` with the patient's thread_id. The checkpointer resumes from the last saved state. If the thread does not exist yet, LangGraph creates it automatically on first invoke.

```python
# In the scheduler job:
async def run_scheduled_checkin(patient_id: str) -> None:
    thread_id = f"patient-{patient_id}"
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 25,
    }
    context = CoachContext(
        patient_id=patient_id,
        tenant_id=await get_tenant(patient_id),
        db_session_factory=get_async_session,
        consent_api_url=settings.consent_api_url,
    )
    await graph.ainvoke(
        {"patient_id": patient_id},  # only new/changed fields needed; checkpointer restores rest
        config=config,
        context=context,
    )
```

---

### Options

#### Option A — Minimal viable graph (no `context_schema`, config.configurable only)

Use `config["configurable"]` for dependency injection — the old 0.x pattern, still functional but deprecated path.

**Trade-offs:**
- (+) Faster to wire up
- (-) `config_schema` is deprecated; `config["configurable"]` is untyped
- (-) Pyright can't check the context access
- (-) Will need migration before 2.0

#### Option B — Full `context_schema` + `Runtime` injection (recommended)

Use `context_schema=CoachContext` dataclass, access via `runtime: Runtime[CoachContext]` in nodes.

**Trade-offs:**
- (+) Fully typed, pyright-clean
- (+) Future-proof (the 1.x stable API)
- (+) Clean separation between per-run context and per-thread state
- (-) Slightly more upfront wiring

#### Option C — Pydantic state model instead of TypedDict

Use `PatientState(BaseModel)` instead of `TypedDict`.

**Trade-offs:**
- (+) Automatic validation on state updates
- (+) Better IDE support for field access
- (-) Validation overhead on every node transition (avoidable overhead in a tight loop)
- (-) `model_validate` adds latency that adds up across 10+ nodes
- (-) LangGraph team explicitly recommends TypedDict for graph state

---

### Recommendation

**Use Option B throughout.** The `context_schema` + `Runtime` pattern is the stable 1.x API, fully pyright-typed, and it directly solves the dependency injection need for DB sessions, consent API URLs, and other per-run immutable context.

**Critical implementation checklist:**

1. **Two separate connection pools.** SQLAlchemy Pool A for app queries. psycopg3 Pool B (with `autocommit=True`, `prepare_threshold=0`, `row_factory=dict_row`) for `AsyncPostgresSaver`. Third pool (or same Pool B if Store and checkpointer share a pool carefully) for `AsyncPostgresStore`. Confirm pool lifecycle compatibility before sharing.

2. **`context_schema` not `config_schema`.** Any older example using `config_schema` or `config["configurable"]` for DI should be rewritten.

3. **`# type: ignore[arg-type]` on every `add_conditional_edges` call.** pyright-strict will flag these. Known open issue #6540.

4. **`parallel_tool_calls=False`** on `llm.bind_tools()` for the health coach. Tools like `set_goal` and `alert_clinician` have ordering implications; sequential execution is safer.

5. **`version="v2"` on all `astream()` / `ainvoke()` calls.** Adopt the new streaming format from the start. Do not mix v1 and v2 — pick one and be consistent.

6. **`await checkpointer.setup()` and `await store.setup()`** must be called once on first startup (idempotent).

7. **`max_tokens` must be set explicitly** on `ChatAnthropic` — the default changed in `langchain-anthropic` 1.x and will silently produce truncated outputs if omitted.

8. **`get_stream_writer()` only works in sync tools** due to issue #6447. Use `StreamWriter` parameter injection for async nodes that need to stream custom events.

---

### Sources

- [LangGraph Graph API Overview](https://docs.langchain.com/oss/python/langgraph/graph-api)
- [LangGraph Context / Runtime Docs](https://docs.langchain.com/oss/python/concepts/context)
- [LangGraph Streaming Docs](https://docs.langchain.com/oss/python/langgraph/streaming)
- [LangGraph Memory / Store Docs](https://docs.langchain.com/oss/python/langgraph/add-memory)
- [langgraph-checkpoint-postgres PyPI (v3.0.4)](https://pypi.org/project/langgraph-checkpoint-postgres/)
- [langgraph-checkpoint-sqlite PyPI (v3.0.3)](https://pypi.org/project/langgraph-checkpoint-sqlite/)
- [langgraph PyPI (v1.1.0)](https://pypi.org/project/langgraph/)
- [LangGraph 1.0 GA Announcement](https://changelog.langchain.com/announcements/langgraph-1-0-is-now-generally-available)
- [LangGraph GitHub Releases](https://github.com/langchain-ai/langgraph/releases)
- [ToolNode DeepWiki](https://deepwiki.com/langchain-ai/langgraph/8.2-toolnode-and-tool-execution)
- [RetryPolicy Reference](https://reference.langchain.com/python/langgraph/types/RetryPolicy)
- [Issue #6027: RetryPolicy + Pydantic ValidationError](https://github.com/langchain-ai/langgraph/issues/6027)
- [Issue #6447: Async tools + get_stream_writer broken](https://github.com/langchain-ai/langgraph/issues/6447)
- [Issue #5112: RemoveMessage across subgraphs](https://github.com/langchain-ai/langgraph/issues/5112)
- [Issue #3193: AsyncConnectionPool pipeline mode](https://github.com/langchain-ai/langgraph/issues/3193)
- [Issue #5023: config.configurable → context API](https://github.com/langchain-ai/langgraph/issues/5023)
- [Issue #6404: create_react_agent deprecation message](https://github.com/langchain-ai/langgraph/issues/6404)
- [Forum: InjectedStore vs InjectedState vs ToolRuntime vs Runtime](https://forum.langchain.com/t/difference-between-injectedstore-injectedstate-toolruntime-runtime-context/1995)
- [PostgresSaver autocommit=False fails (Issue #5327)](https://github.com/langchain-ai/langgraph/issues/5327)

---

## 2. Advisory Lock Concurrency Strategy for LangGraph Graph Invocations

**Date:** 2026-03-10
**Question:** Can `pg_advisory_xact_lock` span an entire LangGraph graph invocation (including LLM calls), or does the plan require a fundamentally different concurrency approach?

---

### Current State

The plan (`plan.md:559`, `plan.md:85`) specifies:

- `load_patient_context` acquires `pg_advisory_xact_lock(patient_id_hash)` to serialize concurrent graph invocations per patient.
- The lock is released when `save_patient_context` commits the transaction.
- Between those two nodes: `manage_history` (may call LLM for summarization), `phase_router`, phase-specific agent nodes (tool loops + LLM calls that take 1–30+ seconds), `safety_gate` (LLM call).
- `CoachContext` carries `db_session_factory: Callable[[], AsyncSession]` — a factory, not a live session (`research.md:152`).
- `load_patient_context` and `save_patient_context` each open their own session via `async with runtime.context.db_session_factory() as session:` (`research-domain-model.md:870`).

The contradiction: `pg_advisory_xact_lock` is a **transaction-level** lock. It releases automatically at transaction end, not at session end. If each node opens and closes its own session (and therefore its own transaction), the lock acquired in `load_patient_context` is gone by the time that node's session commits — the next node's session has no lock.

---

### Constraints

1. **Two separate pools must remain separate.** Pool A (SQLAlchemy `AsyncAdaptedQueuePool`) for app queries. Pool B (psycopg3 `AsyncConnectionPool`) for LangGraph checkpointer. They cannot be merged (`research-fastapi-sqlalchemy.md:514–570`, `prd.md:239`).

2. **`pg_advisory_xact_lock` releases at transaction end.** Per PostgreSQL documentation, transaction-level advisory locks have no explicit unlock; they release automatically on COMMIT or ROLLBACK. A session opened in `load_patient_context` that commits before `save_patient_context` runs does NOT hold the lock into the next session (`postgresql.org/docs/current/explicit-locking.html`).

3. **A live `AsyncSession` holds a database connection for the duration of any open transaction.** SQLAlchemy's `autobegin` issues `BEGIN` as soon as the first SQL statement executes on a session. That connection is not returned to the pool until `commit()` or `rollback()`. Holding a session open across external LLM calls (which can take 1–30 seconds) leaves the connection in `idle in transaction` state — blocking pool slots and degrading concurrency (`gorgias.com/blog/prevent-idle-in-transaction-engineering`; SQLAlchemy session_transaction docs).

4. **`pg_advisory_lock` (session-level) with connection pools is dangerous.** Session-level locks survive transaction rollback. If a connection holding a session-level lock is returned to the pool, the lock persists for the next client that borrows that connection — causing lock leaks. This is incompatible with `SQLAlchemy`'s `QueuePool` which issues `ROLLBACK` on return-to-pool (`SQLAlchemy pooling docs`, `postgresql.org explicit-locking`).

5. **PgBouncer transaction mode blocks session-level advisory locks entirely.** While not currently in the stack, this rules out session-level locks as a portable choice.

6. **`AsyncSession` must not be shared across concurrent tasks** (`prd.md:237`). Sharing a single long-lived session from `load_patient_context` to `save_patient_context` across multiple graph nodes (which run in separate asyncio coroutines) would violate this rule.

7. **Pool A's `pool_size=10, max_overflow=5` for 10 workers.** Holding one connection per in-flight invocation for 5–30 seconds across LLM calls would exhaust the pool at modest concurrency (15 simultaneous patients = 15 held connections).

8. **SQLite (local dev) provides equivalent serialization via global write lock.** Plan Invariant #3 (`plan.md:85`) notes SQLite tests are unaffected. Any solution must degrade gracefully to SQLite for dev/test.

---

### Options

#### Option A: Single long-lived session spanning load→save (the naive reading of the plan)

Hold one `AsyncSession` (and therefore one DB connection) open from `load_patient_context` through `save_patient_context`. The `pg_advisory_xact_lock` is acquired at the start of the transaction and released on final commit.

**How:** Pass the live session through `CoachContext` instead of a factory, or open it in the FastAPI handler before invoking the graph and close it after.

**Trade-offs:**

| | |
|---|---|
| Lock semantics | Correct: transaction-level lock survives for the full invocation |
| Connection usage | One pool connection held idle-in-transaction during all LLM calls (5–30s). At 15 concurrent patients: 15 connections held. Pool A (size 10 + 5 overflow) exhausts at 15 concurrent invocations. |
| Blast radius of pool exhaustion | New requests block waiting for a connection. Timeout = pool `timeout` (default 30s). Risk of cascading failure. |
| Session sharing across nodes | Each LangGraph node runs as a separate asyncio coroutine. Sharing one session across them technically violates `prd.md:237` ("one AsyncSession per concurrent task"). |
| SQLite compat | Works (SQLite has its own write serialization) |
| Verdict | Viable at very low concurrency but pool exhaustion risk is real and the session-sharing violation is genuine. |

#### Option B: Session-level advisory lock on a dedicated connection from Pool A

Use `pg_advisory_lock` (session-level, not transaction-level) on a dedicated connection checked out explicitly from Pool A at the start of the invocation. Hold that connection open (outside of any SQLAlchemy session, using raw psycopg3 or SQLAlchemy `engine.connect()`) for the entire invocation. Release the lock explicitly before returning the connection to the pool.

```python
# Pseudocode — dedicated lock connection
async with engine.connect() as lock_conn:
    await lock_conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": patient_id_hash})
    try:
        await graph.ainvoke(...)  # individual nodes use separate short sessions
    finally:
        await lock_conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": patient_id_hash})
        # lock_conn returns to pool with lock released
```

Individual nodes (`load_patient_context`, `save_patient_context`) each open their own short-lived session for their DB work only.

**Trade-offs:**

| | |
|---|---|
| Lock semantics | Correct: session-level lock held for full invocation duration, explicitly released |
| Connection usage | Two connections per in-flight invocation: one dedicated lock connection (idle but not in a transaction — MUCH cheaper than idle-in-transaction), one short-lived session per node |
| Pool pressure | Lock connection is idle (not idle-in-transaction). PostgreSQL can serve thousands of idle connections efficiently. Pool A still consumes one slot per concurrent invocation for the lock connection, but without the idle-in-transaction cost. |
| Session sharing | No session is shared. Each node gets its own fresh session. Compliant with `prd.md:237`. |
| Crash safety | If the process crashes before `pg_advisory_unlock`, PostgreSQL releases the session-level lock automatically when the connection is closed/dropped. No dangling lock. |
| Pool return safety | Must release the lock BEFORE returning the connection to Pool A. A `try/finally` block guarantees this. The advisory lock check in `pg_advisory_unlock` raises an error if called without a matching lock — detectable. |
| SQLite compat | SQLite does not have advisory locks. The lock acquisition must be conditional on `settings.database_url` being PostgreSQL. For SQLite dev/test, the global write lock provides equivalent serialization. |
| Complexity | Slightly more wiring: need to handle the lock connection lifecycle outside the graph invocation call site. |
| Verdict | Best fit for the problem. Correct lock semantics + no idle-in-transaction cost + no session sharing violation. |

#### Option C: Optimistic concurrency with version column (no advisory lock)

Add a `version: Mapped[int]` column to `Patient`. `load_patient_context` reads the version into state. `save_patient_context` uses `WHERE version = :loaded_version` in the UPDATE. If another invocation committed in between, `save_patient_context` gets 0 rows updated, raises `StaleDataError`, and the invocation retries.

SQLAlchemy supports this natively via `__mapper_args__ = {"version_id_col": version}` which raises `StaleDataError` automatically on stale writes.

**Trade-offs:**

| | |
|---|---|
| Lock semantics | Optimistic: no lock held. Concurrent invocations proceed in parallel; the loser retries at `save_patient_context`. |
| Connection usage | Each node uses a short-lived session only. Zero idle-in-transaction. |
| Concurrency | High throughput under low-conflict workloads. |
| Retry cost | A conflict causes a full graph re-invocation (re-running LLM calls, tool loops). At low concurrency (1 patient ↔ 1 active invocation most of the time), conflicts are rare. But a scheduler job + incoming patient message arriving simultaneously will always conflict. The loser pays the full LLM cost again. |
| Multi-table atomicity | `save_patient_context` writes to `patients`, `goals`, `outbox`, `scheduled_jobs`, `safety_decisions`, `audit_events`. Optimistic locking on `patients` alone does not protect these other tables from concurrent writes from a parallel invocation that also passes consent and reaches `save_patient_context`. Would need version columns on every mutable table, or a more careful design. |
| Crisis path | `crisis_check` writes a `ClinicianAlert` immediately (AD-2 exception). That write happens before `save_patient_context`. If the invocation later retries due to stale data, the `ClinicianAlert` was already written — but it's idempotent (`ON CONFLICT DO NOTHING` on the idempotency key). Safe but noteworthy. |
| SQLite compat | SQLAlchemy's version_id_col works with SQLite. |
| Regulatory posture | No lock means a concurrent scheduler invocation + patient message can both reach `save_patient_context` "successfully" on first attempt if they wrote to different tables. The version guard on `patients` catches phase conflicts; it does not catch goal or outbox conflicts from interleaved writes. This is the key weakness. |
| Verdict | Acceptable if the goal were high concurrency with rare conflicts. Not ideal here because `save_patient_context` writes are wide (6+ tables) and the concurrency patterns are structured (scheduler fires at scheduled times, patient messages are low-frequency). An advisory lock gives a cleaner correctness guarantee at low added cost. |

---

### Recommendation

**Use Option B: Session-level advisory lock (`pg_advisory_lock`) on a dedicated connection checked out from Pool A.**

The lock connection sits outside any SQLAlchemy session and outside any transaction. It is genuinely idle (not idle-in-transaction) for the duration of the LLM calls. It is explicitly released in a `try/finally` block before the connection returns to Pool A. No session is shared across nodes.

**Implementation contract:**

1. In `CoachContext`, keep `db_session_factory` as a `Callable[[], AsyncContextManager[AsyncSession]]` (a factory, not a live session). Nodes open their own short sessions.

2. Add `lock_key: int` to `CoachContext`. Computed at invocation time as `hash(patient_id) & 0x7FFFFFFF` (PostgreSQL advisory lock keys are `bigint`; use the low 31 bits of a UUID hash to stay positive and avoid sign issues).

3. The FastAPI handler (or scheduler job) wraps the `graph.ainvoke()` call in a dedicated lock connection context:

```python
# src/health_coach/agent/locking.py
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

@asynccontextmanager
async def patient_advisory_lock(
    engine: AsyncEngine,
    patient_id_hash: int,
) -> AsyncGenerator[None, None]:
    """
    Acquire a session-level PostgreSQL advisory lock for the duration of the
    context. Releases the lock explicitly before returning the connection to
    the pool. No-op when not running against PostgreSQL (SQLite dev/test).
    """
    is_postgres = "postgresql" in str(engine.url)
    if not is_postgres:
        yield  # SQLite: global write lock provides equivalent serialization
        return

    async with engine.connect() as conn:
        await conn.execute(
            text("SELECT pg_advisory_lock(:key)"),
            {"key": patient_id_hash},
        )
        try:
            yield
        finally:
            await conn.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": patient_id_hash},
            )
            # conn returns to pool with lock released
```

4. Call site in the FastAPI route handler:

```python
async with patient_advisory_lock(engine, lock_key):
    result = await graph.ainvoke(state, config=config, context=context)
```

5. `load_patient_context` and `save_patient_context` do NOT acquire or release any lock. They open their own short-lived sessions for their DB work only. The plan text at `plan.md:559` that says `load_patient_context` acquires `pg_advisory_xact_lock` should be revised: the lock is acquired at the call site, not inside a graph node.

6. For SQLite (dev/test): the `is_postgres` check in `patient_advisory_lock` skips the lock entirely. SQLite's single-writer model already serializes writes globally.

**Why not Option A (single long session):** Pool exhaustion risk at `pool_size=10` is real. Holding one connection per concurrent patient invocation for the duration of LLM calls (potentially 10–30s) means the pool is exhausted at 10–15 concurrent invocations. This is not theoretical — a modest patient load with proactive scheduler jobs running simultaneously could reach this.

**Why not Option C (optimistic):** The `save_patient_context` write fan-out (6+ tables) makes per-table version columns complex and incomplete. The concurrency pattern here is not "many readers, occasional writer" but "at most one invocation should run at a time per patient" — an inherently serializing requirement better served by a lock than by retry-on-conflict.

**Key correction to the plan:** The plan's `plan.md:559` describes `pg_advisory_xact_lock` acquired *inside* `load_patient_context`. This does not work as described: a transaction-level lock acquired in `load_patient_context`'s session is released when that session commits, before `save_patient_context` runs. The lock must be acquired at the call site (FastAPI handler or scheduler), not inside a graph node, and it must be session-level (`pg_advisory_lock`) not transaction-level (`pg_advisory_xact_lock`).

---

### Sources

- [PostgreSQL Explicit Locking — Advisory Locks](https://www.postgresql.org/docs/current/explicit-locking.html)
- [Advisory Locks in Postgres — The Fresh Writes](https://medium.com/thefreshwrites/advisory-locks-in-postgres-1f993647d061)
- [PostgreSQL Advisory Locks explained — Flavio Del Grosso](https://flaviodelgrosso.com/blog/postgresql-advisory-locks)
- [Avoiding SQLAlchemy idle-in-transaction — Gorgias Engineering](https://www.gorgias.com/blog/prevent-idle-in-transaction-engineering)
- [SQLAlchemy Transactions and Connection Management](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html)
- [SQLAlchemy Connection Pooling](https://docs.sqlalchemy.org/en/20/core/pooling.html)
- [Postgres Advisory Locks for Python Developers — leontrolski](https://leontrolski.github.io/postgres-advisory-locks.html)
- [How do PostgreSQL advisory locks work — Vlad Mihalcea](https://vladmihalcea.com/how-do-postgresql-advisory-locks-work/)
- [Distributed Locking with Postgres Advisory Locks — Richard Clayton](https://rclayton.silvrback.com/distributed-locking-with-postgres-advisory-locks)
- [How to Implement Optimistic Locking in SQLAlchemy — OneUptime](https://oneuptime.com/blog/post/2026-01-25-optimistic-locking-sqlalchemy/view)
- [Optimistic vs Pessimistic Locking in ORMs — Heval Hazal Kurt](https://hevalhazalkurt.com/blog/optimistic-vs-pessimistic-locking-in-orms/)
- [psycopg-toolbox advisory lock context manager](https://github.com/jtbeach/psycopg-toolbox)
