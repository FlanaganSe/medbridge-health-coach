# Plan: Demo Experience Polish

## Summary

Four features to make the Health Ally demo reliable, fluid, and architecturally legible. F1 fixes the broken reset by clearing LangGraph checkpoints alongside domain DB. F2 adds token-level streaming using LangGraph's `custom` stream mode with `get_stream_writer()` inside agent nodes — chosen over `stream_mode="messages"` to avoid 3 open LangGraph bugs with tool execution ordering. F3 adds a conversation history endpoint reading from the checkpoint via `graph.aget_state()` and renders it in the ObservabilityPanel. F4 replaces the flat PipelineTrace pills with a hand-rolled JSX SVG DAG that lights up nodes in real-time during streaming. Sequence: F1 → F3 → F2 → F4 (F3 validates F1; F2 and F4 are independent but benefit from the shared test fixture).

## Files to change

| File | Feature | Change |
|---|---|---|
| `src/health_ally/api/routes/demo.py` | F1, F3 | Add checkpoint clearing after DB reset; add `GET /v1/demo/conversation/{patient_id}` endpoint |
| `tests/conftest.py` | F1 | Add `application.state.graph = compile_graph()` to `app` fixture (enables checkpoint access in all demo tests) |
| `tests/unit/test_demo_endpoints.py` | F1, F3 | Add test for checkpoint clearing on reset; add test for conversation history endpoint |
| `src/health_ally/api/routes/chat.py` | F2 | Change `stream_mode="updates"` to `["updates", "custom"]`; both event types share `yield _format_sse(event["data"])` |
| `src/health_ally/agent/nodes/active.py` | F2 | Replace `model_with_tools.ainvoke()` with `astream` loop + `get_stream_writer()` for token chunks |
| `src/health_ally/agent/nodes/onboarding.py` | F2 | Same `ainvoke` → `astream` + `get_stream_writer()` pattern |
| `src/health_ally/agent/nodes/re_engaging.py` | F2 | Same `ainvoke` → `astream` + `get_stream_writer()` pattern |
| `tests/integration/test_chat_endpoint.py` | F2 | Update mock_stream to yield custom token events; add test for token event shape |
| `demo-ui/src/hooks/useSSE.ts` | F2 | Add `{"type": "token"}` early-return handler; change `outboundMessage` from replace to append |
| `demo-ui/src/hooks/usePatientState.ts` | F3 | Add `fetchConversationHistory()` to `Promise.all` fetch chain |
| `demo-ui/src/api.ts` | F3 | Add `fetchConversationHistory(patientId)` function |
| `demo-ui/src/types.ts` | F3, F4 | Add `ConversationMessage` interface; add `conversationHistory` to `PatientState` |
| `demo-ui/src/components/ObservabilityPanel.tsx` | F3 | Add collapsible "Conversation History" section between Scheduled Jobs and Audit Trail |
| `demo-ui/src/components/ChatPanel.tsx` | F4 | Replace `<PipelineTrace>` with `<GraphView>` at line 152 |
| `demo-ui/src/index.css` | F4 | Add `@keyframes node-pulse` animation + `prefers-reduced-motion` entry |

## Files to create

| File | Feature | Purpose |
|---|---|---|
| `demo-ui/src/components/GraphView.tsx` | F4 | Hand-rolled JSX SVG rendering the 14-node graph DAG with real-time node highlighting from `PipelineNode[]` |
| `demo-ui/src/components/graphLayout.ts` | F4 | Static node coordinates, edge paths, cluster groupings, and color constants for the graph SVG |

## Milestone outline

- [x] M1: Checkpoint clearing on reset — `reset_patient` calls `adelete_thread` after DB cleanup; test fixture gains `app.state.graph`
  Verify: `ruff check . && pyright . && pytest tests/unit/test_demo_endpoints.py -v`
  Commit: "fix: clear LangGraph checkpoint on patient reset"

- [x] M2: Conversation history endpoint — `GET /v1/demo/conversation/{patient_id}` reads checkpoint messages via `graph.aget_state`; filters empty sentinels and tool-invoking AIMessages; normalizes list content to strings; includes ToolMessages with tool_name
  - [ ] Step 1 — Add response models + `_serialize_message` helper + endpoint to `demo.py` → verify: `ruff check src/health_ally/api/routes/demo.py`
  - [ ] Step 2 — Add tests (empty history, messages with filtering, sentinel exclusion) → verify: `pytest tests/unit/test_demo_endpoints.py -v`
  - [ ] Step 3 — Full verification → verify: `ruff check . && ruff format --check . && pyright . && pytest -v`
  Commit: "feat: add conversation history endpoint for demo UI"

- [x] M3: Conversation history UI — `ObservabilityPanel` gains collapsible section; user/assistant messages with role badges; tool messages compact with amber left border and `tool_name: result` format; `usePatientState` fetches and refreshes it
  - [ ] Step 1 — Add `ConversationMessage` interface + extend `PatientState` in `types.ts` → verify: `cd demo-ui && npx tsc --noEmit`
  - [ ] Step 2 — Add `fetchConversationHistory` to `api.ts` + wire into `usePatientState.ts` → verify: `cd demo-ui && npx tsc --noEmit`
  - [ ] Step 3 — Add Conversation section to `ObservabilityPanel.tsx` → verify: `cd demo-ui && npm run build`
  Commit: "feat: render conversation history in observability panel"

- [x] M4: Token streaming backend — agent nodes switch from `ainvoke` to `astream` + `get_stream_writer()`; `chat.py` emits both `updates` and `custom` events
  - [x] Step 1 — Add `astream` delegation to `_FakeCoachModel` in `model_gateway.py` → verify: `pytest tests/integration/test_graph_routing.py -v`
  - [x] Step 2 — Update `chat.py`: `stream_mode=["updates", "custom"]` + tuple unpacking in event loop → verify: `ruff check src/health_ally/api/routes/chat.py`
  - [x] Step 3 — Update `active.py`, `onboarding.py`, `re_engaging.py`: `ainvoke` → `astream` + `get_stream_writer()` token emission → verify: `ruff check src/health_ally/agent/nodes/`
  - [x] Step 4 — Update `test_chat_endpoint.py`: mock yields tuples, add token event test → verify: `pytest tests/integration/test_chat_endpoint.py -v`
  - [x] Step 5 — Full verification → verify: `ruff check . && ruff format --check . && pyright . && pytest -v`
  Commit: "feat: add token-level streaming via custom stream mode"

- [x] M5: Token streaming frontend — `useSSE.ts` handles `{"type": "token"}` events with text accumulation; bot messages render progressively
  - [x] Step 1 — Add token event handler before node-iteration loop in `useSSE.ts` → verify: `cd demo-ui && npx tsc --noEmit`
  - [x] Step 2 — Full build verification → verify: `cd demo-ui && npm run build`
  Commit: "feat: progressive token rendering in chat panel"

- [x] M6: Graph layout and data — `graphLayout.ts` with all 14 node positions, ~30 edge paths (including 4 back-edge beziers), cluster colors from `architecture.dot`
  - [x] Step 1 — Create `graphLayout.ts` with types, 14 nodes, 29 edges, 5 clusters, color maps → verify: `cd demo-ui && npx tsc --noEmit`
  Commit: "feat: add graph DAG layout data for architecture visualization"

- [x] M7: GraphView component — JSX SVG rendering nodes, edges, labels, arrowheads; driven by `PipelineNode[]` prop; replaces `PipelineTrace` in `ChatPanel` with same collapse/expand pattern (auto-expand during streaming, collapse to summary bar after)
  - [x] Step 1 — Create `GraphView.tsx`: SVG with clusters, edges (straight + bezier back-edges), terminals, nodes with status coloring, collapse/expand logic → verify: `cd demo-ui && npx tsc --noEmit`
  - [x] Step 2 — Add `node-pulse` animation to `index.css` + `prefers-reduced-motion` entry → verify: `cd demo-ui && npx tsc --noEmit`
  - [x] Step 3 — Swap PipelineTrace for GraphView in `ChatPanel.tsx` → verify: `cd demo-ui && npm run build`
  Commit: "feat: live architecture diagram with real-time node highlighting"

- [x] M8: Final verification + polish — full test suite, lint, typecheck, frontend build; visual polish pass on GraphView edges/labels
  Verify: `ruff check . && ruff format --check . && pyright . && pytest -v && cd demo-ui && npm run build`
  Commit: "chore: final verification and polish for demo experience"

## Manual setup tasks

None. All changes are code-only. No new environment variables, no external services, no database migrations.

## Risks

1. **`get_stream_writer()` behavior with `FakeModelGateway` in tests.** The `_FakeCoachModel` wraps `FakeListChatModel` which may not support `astream` correctly. Unit tests for agent nodes may need the mock to yield `AIMessageChunk` objects instead of returning a full `AIMessage`. Mitigation: verify `FakeListChatModel.astream()` behavior in M4; if broken, adjust the mock.

2. **`stream_mode=["updates", "custom"]` event shape under LangGraph 1.1.x.** Research confirms `event["data"]` contains the payload for both modes, but the exact top-level structure (`{"type": "updates", "data": ...}` vs raw dict) should be verified with a smoke test in M4 before committing to the frontend contract. Mitigation: M4 includes a verification step before frontend changes in M5.

3. **SVG back-edge bezier curves (F4).** Four edges route backward in the DAG (tool returns, safety retry). Getting clean curves without overlapping node labels requires iterative visual tuning. Mitigation: M6 defines coordinates; M7 renders and adjusts; M8 polishes.

4. **`aget_state` on MemorySaver with no checkpoint.** Research says `snapshot.values` is `{}` — but `aget_state` may return `None` on some versions. Mitigation: M2 adds a guard for both cases.

## Design decisions (resolved)

1. **GraphView replaces PipelineTrace** using the same collapse/expand pattern. Collapsed = compact summary bar ("N nodes completed ✓"). Expanded = full DAG with live node highlighting. Auto-expands during streaming, auto-collapses after. One component, one slot, same `PipelineNode[]` data. The 400px SVG is only visible when most useful (during streaming) and gets out of the way for reading the conversation.

2. **Conversation history shows tool messages with distinct compact styling.** Tool results (`ToolMessage`) render as indented, smaller rows with amber left border and `tool_name: result_text` format. AIMessages with `tool_calls` but no meaningful text content are filtered out server-side (they just indicate the LLM decided to invoke a tool — noise). AIMessage(content="") sentinels filtered out. No explicit grouping mechanism needed — consecutive tool messages are naturally adjacent in checkpoint order, and the visual styling makes them clearly subordinate to the primary user/assistant flow.

## Open questions

None — all design decisions resolved.
