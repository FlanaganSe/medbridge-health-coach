# Research: InjectedState Semantics and Tool State Mutation in LangGraph 1.x

**Date:** 2026-03-10
**LangGraph version:** 1.1.0
**Scope:** AD-2 intent accumulation pattern — whether tools can mutate `pending_effects` via `InjectedState`, or must use an alternative mechanism
**Input:** `research.md` §5-6, `plan.md:531,796`, official LangGraph documentation, ToolNode source analysis

---

## 1. Current State

The implementation plan (`plan.md`) specifies:

> "Does NOT write to DB directly — instead, uses `InjectedState` to read current `pending_effects` and adds the goal data to it" — `plan.md:796`

> "`pending_effects: dict | None` — accumulated side effects from tools and nodes (AD-2). Populated by tool executions and node logic, flushed atomically by `save_patient_context`." — `plan.md:531`

The existing `research.md` shows `InjectedState` exclusively in a read context:

```python
@tool
def get_adherence_summary(
    state: Annotated[dict, InjectedState],
) -> str:
    """Get patient adherence summary."""
    patient_id = state["patient_id"]
    # InjectedState is hidden from the LLM's tool schema
    return fetch_adherence(patient_id)
```
— `research.md:463-476`

No existing research confirms or denies whether mutating the injected state dict propagates back into graph state.

---

## 2. Constraints

1. **`load_patient_context` / `save_patient_context` are the only nodes that touch the domain DB** — all agent nodes between them work on `PatientState`. (`research-domain-model.md:858`)
2. **Phase transitions are deterministic application code, never LLM-decided.** (`.claude/rules/immutable.md:3`)
3. **Replay safety:** if `save_patient_context` fails, the domain DB must be unchanged and the invocation must be safe to replay from the last checkpoint. (`research-domain-model.md:928`)
4. **`parallel_tool_calls=False` is set** on the LLM binding — tool calls are sequential, not concurrent, for the health coach. (`research.md:434-436`, `plan.md` §4)
5. **`InjectedState` params are hidden from the LLM schema** — the annotation tells ToolNode not to expose those params to the LLM. This is a schema-suppression feature, not a mutation mechanism. (`research.md:474`)

---

## 3. Key Finding: InjectedState is Effectively Read-Only

### How ToolNode injects state

ToolNode injects state via `_inject_tool_args`:

```python
# from ToolNode source (github.com/langchain-ai/langgraph, tool_node.py)
if injected.state:
    state = tool_runtime.state
    if isinstance(state, dict):
        for tool_arg, state_field in injected.state.items():
            injected_args[tool_arg] = (
                state[state_field] if state_field else state
            )
```

This is a **shallow reference assignment** — no explicit copy. For mutable fields (dicts, lists), the tool receives a reference to the same object that is stored in `tool_runtime.state`.

### What happens when you mutate the injected state

Mutating the injected `state` dict inside a tool function modifies the local `tool_runtime.state` object. This does **NOT** flow back into the LangGraph checkpoint. LangGraph state updates only flow into the graph via node return values that are merged by reducers. A tool that mutates the injected dict in-place is modifying a transient runtime object that is discarded after the ToolNode call completes.

**Concretely:** If a tool reads `state["pending_effects"]`, appends to it, and returns a plain `str`, the mutation is lost. The checkpointer saves only the node's return value (wrapped in a `ToolMessage`) — not the mutated runtime state.

### What ToolNode actually propagates

For regular tool returns, ToolNode returns:
```python
{"messages": [ToolMessage(content=str(result), tool_call_id=...)]}
```
Only the `messages` key is updated in graph state. All other state fields are unchanged.

For `Command` returns, ToolNode passes the `Command` through directly without wrapping:
```python
# ToolNode passes Command through; LangGraph runtime processes Command.update
return updated_command  # with full update dict intact
```

**Conclusion: `InjectedState` is logically read-only for state-update purposes.** Mutations to the injected object do not propagate. The only mechanisms that produce state updates from a tool are:

1. **`Command(update={...})`** returned from the tool — ToolNode passes it through; LangGraph runtime applies `Command.update` to graph state.
2. **`ToolMessage` in `messages`** — always required; the only thing plain-return tools write.

---

## 4. Options for Tools That Need to Produce State Updates

### Option A — Tools return `Command(update={...})` directly (CORRECT mechanism)

`Command` allows a tool to simultaneously return a `ToolMessage` to the LLM and update non-message state keys:

```python
from langgraph.types import Command
from langgraph.prebuilt import InjectedState
from langchain_core.messages import ToolMessage
from typing import Annotated

@tool
async def set_goal(
    goal_text: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Store the patient's structured goal."""
    patient_id = state["patient_id"]  # read-only — just for reading
    extracted = ExtractedGoal(text=goal_text, ...)
    idempotency_key = f"{patient_id}:goal:{hash(goal_text)}:{timestamp_bucket()}"

    # Merge new goal intent into pending_effects
    current = state.get("pending_effects") or {}
    updated_effects = {**current, "goal": extracted.model_dump(), "phase_event": "goal_confirmed"}

    return Command(
        update={
            "pending_effects": updated_effects,
            "messages": [ToolMessage(
                content=f"Goal recorded: {goal_text}",
                tool_call_id=tool_call_id,
            )],
        }
    )
```

**Critical constraint on `Command` from tools:** The `messages` key MUST be included in `Command.update` and MUST contain a `ToolMessage` with the correct `tool_call_id`. LangGraph enforces this — missing `ToolMessage` causes a runtime error.

`InjectedToolCallId` is the annotation that injects the tool call ID without exposing it to the LLM schema:
```python
from langgraph.prebuilt import InjectedToolCallId
```

**ToolNode support:** ToolNode in LangGraph 1.x **does** support tools returning `Command`. It passes the `Command` through rather than wrapping the output in a `ToolMessage`. The `goto` field in `Command` can optionally route to a different node (normally left unset for tools — routing stays with `tools_condition`).

**Trade-offs:**
- (+) Correct mechanism — LangGraph officially supports and recommends this
- (+) Prebuilt `ToolNode` handles it without a custom node
- (+) `pending_effects` is built up incrementally per tool call; each tool creates a new dict merging old + new (immutable update pattern, safe with `parallel_tool_calls=False`)
- (-) Requires `InjectedToolCallId` alongside `InjectedState` in every side-effecting tool
- (-) Tool return type changes from `str` to `Command` — less ergonomic for plain read-only tools; use `str` returns for reads

### Option B — Tools return plain strings; agent node accumulates effects (INCORRECT for this design)

Tools always return `str` (or `ToolMessage`). The agent node post-processes the `AIMessage.tool_calls` list, reads each tool's return `ToolMessage`, and writes `pending_effects` to state directly.

**Trade-offs:**
- (+) Simpler tool signatures
- (-) Agent node becomes a coordinator that parses tool results — breaks separation of concerns
- (-) Tool cannot directly validate its own intent before it's committed to `pending_effects`
- (-) Requires agent node to have domain knowledge of what each tool's return means — coupling
- (-) The existing plan design (AD-2) explicitly scopes effect accumulation to tool execution, not agent nodes

### Option C — Custom ToolNode that writes to state (OVERKILL for this design)

Implement a custom `ToolNode` replacement that applies state mutations from tool side-effects.

**Trade-offs:**
- (+) Full control over state update semantics
- (-) Abandons prebuilt `ToolNode` — must maintain compatibility with `tools_condition`, `handle_tool_errors`, error handling, async gather semantics
- (-) Unnecessary: `Command` from tools already achieves the same goal with the prebuilt node

---

## 5. `pending_effects` Reducer Requirement

Since tools can run sequentially and each `Command.update` replaces the `pending_effects` key, the state definition needs a reducer that merges rather than replaces, OR the tool must manually merge old + new before returning `Command`.

Two viable sub-approaches:

### Sub-approach A — Tool-side merge (no reducer needed)

Each tool reads current `pending_effects` from `InjectedState`, merges its intent in, and returns the full updated dict in `Command.update`:

```python
current = state.get("pending_effects") or {}
updated = {**current, "goal": extracted.model_dump()}
return Command(update={"pending_effects": updated, "messages": [...]})
```

Works correctly with `parallel_tool_calls=False` (sequential — no concurrent write conflict).

**Trade-off:** Slightly verbose; safe under sequential execution. Fails silently under concurrent tool calls (race on read-modify-write). Since `parallel_tool_calls=False` is already required for the health coach (`research.md:436`), this is acceptable.

### Sub-approach B — Custom reducer on `pending_effects`

Define `pending_effects` with a merge reducer:

```python
def merge_pending_effects(left: dict | None, right: dict | None) -> dict:
    merged = dict(left or {})
    right = right or {}
    # merge lists (alerts, scheduled_jobs, etc.) by extending, not replacing
    for key in ("alerts", "scheduled_jobs", "safety_decisions", "audit_events", "outbox_entries"):
        if key in right:
            merged[key] = list(merged.get(key, [])) + list(right.get(key, []))
    # merge scalar overrides (goal, phase_event)
    for key in ("goal", "phase_event"):
        if key in right:
            merged[key] = right[key]
    return merged

class PatientState(TypedDict):
    pending_effects: Annotated[dict | None, merge_pending_effects]
    ...
```

Each tool then returns only the delta:
```python
return Command(update={
    "pending_effects": {"goal": extracted.model_dump(), "phase_event": "goal_confirmed"},
    "messages": [...],
})
```

**Trade-off:** Cleaner per-tool returns; more correct under concurrent calls; requires the reducer to be maintained alongside the `pending_effects` schema.

---

## 6. Recommendation

**Use Option A: tools return `Command(update={...})`**, with sub-approach A (tool-side merge) for `pending_effects` accumulation.

Rationale:

1. **Correctness:** Mutating the `InjectedState` dict in-place does NOT propagate into graph state. The plan's current description ("uses `InjectedState` to read current `pending_effects` and adds the goal data to it") describes the read step correctly but implies mutation propagates — it does NOT. The fix is trivial: read via `InjectedState`, build updated dict, return via `Command.update`.

2. **Prebuilt ToolNode support:** LangGraph 1.x `ToolNode` handles `Command` returns natively — no custom node needed. The official docs state "we recommend using prebuilt `ToolNode` which automatically handles tools returning `Command` objects." (`docs.langchain.com/oss/python/langgraph/use-graph-api`)

3. **Sequential safety:** `parallel_tool_calls=False` is already required (`research.md:436`), so the tool-side read-modify-write merge is safe — no concurrent writes to `pending_effects`.

4. **`ToolMessage` requirement:** Every `Command` from a tool MUST include `"messages": [ToolMessage(..., tool_call_id=tool_call_id)]`. This requires `InjectedToolCallId` on every side-effecting tool signature. Read-only tools (`get_program_summary`, `get_adherence_summary`) continue returning plain `str`.

5. **Plan correction required:** `plan.md:796` should be updated to say "reads current `pending_effects` via `InjectedState`, builds an updated dict, and returns `Command(update={"pending_effects": ..., "messages": [ToolMessage(...)]})`. The actual persistence happens in `save_patient_context`." The AD-2 intent accumulation design is correct — only the mechanism for propagating tool intent into state needs clarification.

### Concrete corrected `set_goal` signature

```python
from typing import Annotated
from langgraph.types import Command
from langgraph.prebuilt import InjectedState, InjectedToolCallId
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool

@tool
async def set_goal(
    goal_text: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
) -> Command:
    """Record the patient's exercise goal. Call this once the patient has confirmed their goal."""
    patient_id: str = state["patient_id"]
    current_effects: dict = state.get("pending_effects") or {}

    extracted = ExtractedGoal(
        text=goal_text,
        idempotency_key=f"{patient_id}:goal:{_hash(goal_text)}:{_timestamp_bucket()}",
    )

    updated_effects = {
        **current_effects,
        "goal": extracted.model_dump(),
        "phase_event": "goal_confirmed",
    }

    return Command(
        update={
            "pending_effects": updated_effects,
            "messages": [
                ToolMessage(
                    content=f"Goal confirmed: {goal_text}",
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )
```

Read-only tools remain simple:

```python
@tool
async def get_program_summary(
    state: Annotated[dict, InjectedState],
) -> str:
    """Get a summary of the patient's home exercise program."""
    return await fetch_program_summary(state["patient_id"])
    # Returns str → ToolNode wraps in ToolMessage automatically
```

---

## Sources

- `plan.md:531` — `pending_effects` field definition (AD-2)
- `plan.md:796` — existing (incorrect) description of InjectedState mutation for `set_goal`
- `research.md:463-476` — InjectedState in read-only tool example
- `research.md:209-255` — Command API for nodes
- `research-domain-model.md:856-928` — load/save boundary; dual-write avoidance
- [LangGraph use-graph-api (official)](https://docs.langchain.com/oss/python/langgraph/use-graph-api) — confirms `Command` from tools, ToolMessage requirement, ToolNode support
- [LangChain Changelog: Modify graph state from tools](https://changelog.langchain.com/announcements/modify-graph-state-from-tools-in-langgraph) — official feature announcement
- [DeepWiki: ToolNode and Tool Execution](https://deepwiki.com/langchain-ai/langgraph/8.2-toolnode-and-tool-execution) — ToolNode Command pass-through behavior; InjectedState injection mechanism
- [LangGraph Forum: Return Tool Output and Update State Simultaneously](https://forum.langchain.com/t/how-to-return-tool-output-and-update-state-simultaneously-using-command-in-langchain/2424) — ToolMessage requirement confirmed; "all data must be sent via state updates"
- [GitHub: langgraph/prebuilt/tool_node.py](https://github.com/langchain-ai/langgraph/blob/main/libs/prebuilt/langgraph/prebuilt/tool_node.py) — source analysis: shallow reference injection, Command pass-through
- [LangGraph Discussion #2806: update-state-from-tools](https://github.com/langchain-ai/langgraph/discussions/2806)
