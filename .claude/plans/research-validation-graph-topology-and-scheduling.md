# Research Validation: Graph Topology, Scheduling, and Thread Management

**Date:** 2026-03-10
**Purpose:** Validate specific implementation plan claims against the three primary research documents.
**Files examined:**
- `.claude/plans/research.md` (LangGraph 1.x patterns)
- `.claude/plans/research-scheduling-observability.md` (scheduler / outbox / observability)
- `.claude/plans/research-domain-model.md` (domain model / LangGraph state integration)

---

## 1. Current State — What the Research Says Verbatim

### 1.1 ToolNode + tools_condition Routing Pattern

**Source:** `research.md:420–481` (Section 6: ToolNode and tools_condition)

The research describes the full agent-tools loop topology:

```python
# Nodes
builder.add_node("active_agent", active_agent_node)
builder.add_node("tools", tool_node)

# Conditional edge: agent → tools_condition → ToolNode or END
builder.add_conditional_edges(  # type: ignore[arg-type]
    "active_agent",
    tools_condition,  # returns "tools" if last message has tool_calls, else END
)
# Loop: tools → agent
builder.add_edge("tools", "active_agent")
```

Verbatim on `tools_condition` behavior (`research.md:459–461`):
> "`tools_condition` inspects the last message in `state["messages"]`. Returns the string `"tools"` if `tool_calls` is non-empty, otherwise returns `END`. The return string `"tools"` must match the node name you used in `add_node`. If you named the node differently, use a custom routing function."

`ToolNode` is constructed as (`research.md:437–443`):
```python
tool_node = ToolNode(
    tools,
    name="tools",
    messages_key="messages",
    handle_tool_errors=True,  # catch exceptions, return ToolMessage with error
)
```

`ToolNode` uses `asyncio.gather()` for parallel async tools. `parallel_tool_calls=False` must be set on `.bind_tools()` when order matters (`research.md:479–480`):
> "Set `parallel_tool_calls=False` on `.bind_tools()` when tool execution order matters or when exactly one tool call should be enforced. ToolNode executes multiple simultaneous tool calls with `asyncio.gather()` — safe for independent reads, risky for writes that depend on each other."

**Loop mechanism:** `add_edge("tools", "active_agent")` creates the loop. `tools_condition` is the exit gate. The loop is bounded by `recursion_limit` (default 25, set at invoke time — `research.md:550–566`).

---

### 1.2 Command Routing

**Source:** `research.md:208–267` (Section 3: Command for Routing)

The research distinguishes `Command` from `add_conditional_edges` (`research.md:213–219`):

| Use `add_conditional_edges` | Use `Command` |
|---|---|
| Routing based on existing state | Routing based on result of node work |
| Route from one source to multiple targets | Node decides its own next destination |
| Static, declarable at build time | Dynamic, only known at runtime |
| Safety classifier → deliver or retry | Active agent → tool or end |

`Command` API (`research.md:222–254`):
```python
from langgraph.types import Command

def safety_classifier_node(state: PatientState) -> Command[Literal["deliver_message", "retry_generation", "send_fallback"]]:
    result = classify_safety(state["messages"][-1])
    if result.is_safe:
        return Command(
            update={"safety_flags": {"checked": True, "passed": True}},
            goto="deliver_message",
        )
    ...
```

`Send` for fan-out is also covered (`research.md:256–267`).

---

### 1.3 Thread Management Strategy

**Source:** `research.md:703–768` (Section 10: Thread Management)

Verbatim on thread-safety (`research.md:706–708`):
> "Thread IDs are passed via `config["configurable"]["thread_id"]`. The compiled graph is thread-safe and shareable across concurrent executions — no state is stored on the graph instance."

Two options are given (`research.md:718–730`):

**Option A — One persistent thread per patient** (recommended):
- `thread_id = f"patient-{patient_id}"`
- All interactions accumulate in a single thread
- Full conversation history, time-travel debugging
- Message trimming via `RemoveMessage` prevents unbounded growth

**Option B — One thread per check-in session:**
- `thread_id = f"patient-{patient_id}-checkin-{date}"`
- Each scheduled check-in is an independent thread
- Requires loading cross-thread context from Store

Verbatim recommendation (`research.md:730`):
> "**Recommendation:** Option A for v1. Simpler. Use `RemoveMessage` + periodic summarization to manage message history length."

---

### 1.4 Proactive (Scheduled) Graph Invocations

**Source:** `research.md:744–768` (Section 10, subsection "Creating threads for scheduled check-ins")

The research explicitly addresses this (`research.md:746–748`):
> "For proactive outreach, the scheduler calls `graph.ainvoke()` with the patient's thread_id. The checkpointer resumes from the last saved state. If the thread does not exist yet, LangGraph creates it automatically on first invoke."

The scheduler dispatch pattern shown verbatim (`research.md:749–768`):
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

This is the **only invocation pattern documented for scheduled check-ins**. The research does not distinguish between reactive (patient-triggered HTTP) and proactive (scheduler-triggered) invocations at the API level — both use `graph.ainvoke` with the same thread_id. The difference is the caller (FastAPI handler vs. `dispatch_job`).

---

### 1.5 Scheduler Worker Pattern

**Source:** `research-scheduling-observability.md:24–408` (Section 2: PostgreSQL-Backed Job Scheduling)

SKIP LOCKED pattern (`research-scheduling-observability.md:32–54`):
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

The worker calls `await dispatch_job(job)` (`research-scheduling-observability.md:180`). The `dispatch_job` function is referenced but **not defined** in the scheduling research — it is labeled "Domain-specific dispatch." The implementation of `dispatch_job` is expected to call `graph.ainvoke()` per the pattern in `research.md:749–768`, but the scheduling research does not show this wiring explicitly.

Job processing uses `asyncio.gather()` for concurrent job execution within a batch (`research-scheduling-observability.md:158–159`):
```python
tasks = [asyncio.create_task(_execute_job(session_factory, job)) for job in jobs]
results = await asyncio.gather(*tasks, return_exceptions=True)
```

This means **multiple patients' graph invocations can run concurrently within one scheduler batch**. The research does not address whether the same patient can have two jobs in the same batch.

---

### 1.6 Outbox Delivery Pattern

**Source:** `research-scheduling-observability.md:410–545` (Section 3: Outbox Pattern)

Transaction boundary rule (verbatim, `research-scheduling-observability.md:459`):
> "The outbox INSERT must be in the same SQLAlchemy transaction as any domain state change that logically causes the message."

Verbatim on graph node responsibility (`research-scheduling-observability.md:490`):
> "**Do NOT deliver directly from graph nodes.** The node's role ends at writing to the outbox. Actual delivery happens in the delivery worker."

Crisis alert durability is covered (`research-scheduling-observability.md:523–533`): the outbox entry is written first (before delivery), `priority = 'urgent'` ensures it is processed first, and if all delivery attempts fail it becomes `dead` status which is visibly queryable.

---

### 1.7 Domain DB vs. LangGraph State — Synchronization

**Source:** `research-domain-model.md:839–929` (Section 11: LangGraph State vs. Domain DB)

The research draws a clear boundary (`research-domain-model.md:856–858`):
> "These two nodes are the synchronization boundary. They are the only nodes that touch the domain DB; all agent nodes between them work only on `PatientState`."

Data placement table (`research-domain-model.md:843–854`):

| Data | Where |
|------|-------|
| Patient phase | Domain DB |
| Consent verification result | LangGraph state (current-invocation only) |
| Active goal | Domain DB |
| Conversation messages | LangGraph checkpointer blob |
| Unanswered count | Domain DB |
| Safety decisions | Domain DB |
| Scheduled jobs | Domain DB |

Crash-safety note verbatim (`research-domain-model.md:927–928`):
> "If a crash occurs between `save_patient_context` and the checkpointer commit, the invocation replays from the last checkpoint. `save_patient_context` is idempotent by design (upsert / `ON CONFLICT DO NOTHING` where applicable)."

---

## 2. Constraints — What the Research Does Not Cover

The following topics were **not explicitly addressed** in any of the three files. These are gaps, not contradictions.

### Gap 1: Concurrent Graph Invocations for the Same Patient

**Not covered.** The scheduling research (`research-scheduling-observability.md:158–159`) shows jobs from a batch being processed concurrently via `asyncio.gather()`. If two jobs for the same patient happen to be due in the same batch (e.g., a Day 2 followup and a backoff retry both pending), `graph.ainvoke()` would be called twice on the same `thread_id` concurrently.

The research states the compiled graph is "thread-safe and shareable across concurrent executions" (`research.md:706–708`) — meaning multiple coroutines can call the same compiled graph object. However, what happens when two coroutines invoke the same thread_id concurrently depends on the checkpointer's locking semantics, which is not addressed.

**Risk:** LangGraph's PostgreSQL checkpointer uses row-level locking per thread checkpoint. Two concurrent invocations on the same thread_id would serialize at the checkpointer level (the second blocks until the first commits its checkpoint), or may produce a conflict error depending on the checkpointer version. This needs verification.

**Mitigation not documented in research:** The idempotency_key constraint on `scheduled_jobs` prevents duplicate jobs of the same type for the same patient on the same date (`research-scheduling-observability.md:86`, `research-scheduling-observability.md:276–303`), which reduces the risk of concurrent same-patient invocations in practice. But it does not eliminate it entirely.

### Gap 2: Distinguishing Proactive vs. Reactive Invocations at the State Level

**Not explicitly covered.** The research does not describe how the graph knows whether it was invoked by the scheduler (proactive) versus by a patient HTTP request (reactive). This matters because a proactive invocation should initiate the outreach message, while a reactive invocation responds to a patient reply.

The `PatientState` TypedDict in `research.md:62–72` does not include an `invocation_source` field. The existing fields (`phase`, `unanswered_count`, `messages`, etc.) would allow the graph to infer context from conversation history, but there is no explicit trigger field.

### Gap 3: `dispatch_job` Implementation

**Not defined.** The scheduling research calls `await dispatch_job(job)` at `research-scheduling-observability.md:180` but leaves the implementation as "Domain-specific dispatch." The bridge between the scheduler worker and `graph.ainvoke()` must be written by the implementation step without a research-defined pattern.

---

## 3. Options

### Option A: Treat Both Invocation Types Identically (No Source Field)

The graph always starts from `load_patient_context`, checks consent, routes by phase. For proactive invocations, if there is no inbound patient message in state, agent nodes generate an outreach message based on phase logic and unanswered_count.

**Trade-offs:**
- (+) No new state field needed; graph topology is unchanged
- (+) Phase-based routing already captures the appropriate behavior (onboarding sends intro, active sends check-in, re_engaging sends nudge)
- (-) Agent nodes must infer "no patient message = proactive" from absence of new HumanMessage, which is implicit
- (-) Harder to audit/distinguish proactive vs. reactive paths in logs

### Option B: Add `invocation_source` to PatientState

Add `invocation_source: Literal["patient", "scheduler"] | None` to `PatientState`. Set it at invocation time. Nodes can branch explicitly.

**Trade-offs:**
- (+) Explicit; easier to audit and test
- (+) Allows different prompt templates per source without heuristics
- (-) Adds a field not in the research-defined state schema; requires deliberate plan decision
- (-) Must be initialized correctly at both call sites (FastAPI handler and `dispatch_job`)

### Option C: Separate Thread IDs for Proactive vs. Reactive

Use `thread_id = f"patient-{patient_id}-proactive"` for scheduler invocations and `thread_id = f"patient-{patient_id}-reactive"` for HTTP invocations (or keep a single thread and use Option A/B).

**Trade-offs:**
- (+) Complete isolation between invocation types; no concurrent-thread-id risk
- (-) Loses conversation continuity across sessions (patient reply to a proactive message comes in on a different thread from the one that sent it)
- (-) Contradicts the research recommendation of a single persistent thread per patient (`research.md:730`)
- (-) Not recommended

---

## 4. Recommendation

**Use Option B** (add `invocation_source` to `PatientState`). The implementation plan must explicitly define:

1. `invocation_source: Literal["patient", "scheduler"] | None` on `PatientState` — set at the `graph.ainvoke()` call site.

2. `dispatch_job(job)` must call `graph.ainvoke({"patient_id": ..., "invocation_source": "scheduler"}, ...)`.

3. The FastAPI handler must set `invocation_source = "patient"` when invoking the graph on a patient message.

4. To prevent concurrent same-patient graph invocations from the scheduler, the `dispatch_job` implementation should either:
   - Serialize jobs by patient_id within the worker (simplest; acceptable at MVP scale)
   - Or rely on the idempotency_key constraint (which already prevents most duplicate-job scenarios per `research-scheduling-observability.md:272–303`) and accept that checkpointer-level serialization handles any remaining edge cases

5. The existing thread management recommendation (Option A, single persistent thread per patient, `research.md:718–730`) is correct and should be followed. Do not split threads by invocation source.

**The three research files fully cover** ToolNode topology, tools_condition loop wiring, Command routing, SKIP LOCKED scheduler worker, outbox delivery, and the domain DB vs. LangGraph state boundary. The only unresolved gap — concurrent same-patient invocations — is manageable at MVP scale through the existing idempotency_key constraint and is not a blocker for the implementation plan.

---

## Sources

- `research.md:420–481` — ToolNode, tools_condition, loop wiring
- `research.md:208–267` — Command routing
- `research.md:703–768` — Thread management, proactive invocation pattern
- `research-scheduling-observability.md:24–408` — Scheduler worker, SKIP LOCKED, dispatch_job
- `research-scheduling-observability.md:410–545` — Outbox pattern, delivery worker
- `research-domain-model.md:839–929` — LangGraph state vs. domain DB synchronization
- `research-domain-model.md:932–1012` — Idempotency patterns, tool call idempotency
