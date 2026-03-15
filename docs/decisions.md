# Decisions

Append-only log. Never edit past entries.

## Format
```
### ADR-NNN: [Title]
**Date:** YYYY-MM-DD
**Status:** accepted | superseded by ADR-NNN
**Context:** [Why — 1-2 sentences]
**Decision:** [What — 1-2 sentences]
**Consequences:** [What follows]
```

---

<!-- Add new decisions below this line -->

### ADR-001: Single StateGraph with Conditional Edges Instead of Subgraphs
**Date:** 2026-03-10
**Status:** accepted
**Context:** `docs/requirements.md` §2 references "phase-specific subgraphs." With 5 phases, subgraphs add complexity (schema alignment, cross-graph debugging, checkpoint namespace management) without sufficient benefit. Different tools, prompts, and LLM bindings per phase are achievable via per-node configuration in a flat graph. Research §4.2 confirms single StateGraph is recommended at this scale.
**Decision:** Use a single `StateGraph` with `add_conditional_edges` for phase routing. No subgraphs. Annotate `PatientState` fields with phase-ownership comments to make future extraction mechanical.
**Consequences:** Simpler debugging and state management. Revisit subgraph extraction if: (1) HITL interrupts are required inside a phase, (2) more than 3 genuinely phase-private state fields accumulate in `PatientState` for a single phase, or (3) phases require independent development by separate teams. Phase count alone is not the trigger. **Migration cost:** subgraph extraction changes the LangGraph checkpoint namespace scheme (`""` → `"phase:UUID"`). In-flight patient threads must be drained or their checkpoint rows migrated — checkpoint blobs contain PHI, so post-production migration is a HIPAA change-management event. The practical migration window with minimal cost is before M4 (when persistent per-patient threads begin).

### ADR-002: One Persistent LangGraph Thread Per Patient
**Date:** 2026-03-11
**Status:** accepted
**Context:** Earlier research recommended new threads per check-in for isolation. However, conversational continuity across onboarding, follow-ups, and re-engagement requires the LLM to see prior conversation history. Loading context from the domain DB is lossy for conversational coherence.
**Decision:** Use `thread_id = f"patient-{patient_id}"` for all interactions. A `manage_history` node trims and summarizes when message count exceeds a threshold, keeping the context window manageable.
**Consequences:** Unbounded checkpoint growth mitigated by history management. Migration cost is high once production checkpoint rows with PHI exist — thread ID scheme changes require draining or migrating checkpoint blobs (HIPAA change-management event). Revisit if HITL interrupts are needed (LangGraph HITL model is thread-scoped).

### ADR-003: Pending Effects Accumulation — save_patient_context is the Only Domain Writer
**Date:** 2026-03-11
**Status:** accepted
**Context:** Graph nodes could write directly to the domain DB, but this creates dual-write divergence under retries. If a node writes and the graph later fails, the domain DB is left in an inconsistent state that cannot be replayed.
**Decision:** Nodes accumulate side effects as "pending effects" in graph state. `save_patient_context` flushes all intents to the DB atomically. Two narrow exceptions: (1) `crisis_check` writes `ClinicianAlert` + `OutboxEntry` eagerly for durability, (2) `consent_gate` writes an audit event on denial since it exits before `save_patient_context` runs.
**Consequences:** Replay safety — failed graphs leave the domain DB unchanged. New node authors must understand this boundary. Side-effecting tools return `Command(update={...})` since `InjectedState` is read-only for tools.

### ADR-004: Consent Verification Re-checked at Delivery
**Date:** 2026-03-11
**Status:** accepted
**Context:** A patient may revoke consent between message generation and delivery. Delivering a message after revocation is an unauthorized outreach — a compliance violation.
**Decision:** The delivery worker re-checks consent for `patient_message` entries before transport. Clinician alerts skip consent re-check — they are internal clinical communications not subject to patient outreach consent.
**Consequences:** The asymmetry (patient messages re-verified, clinician alerts not) is clinically load-bearing. Cancelled deliveries emit audit events. A developer must not "simplify" this into uniform consent checking — doing so would block crisis alerts when consent is revoked.

### ADR-005: Safety Classifier Failure Modes Are Asymmetric
**Date:** 2026-03-11
**Status:** accepted
**Context:** Two safety classifier invocations serve different purposes: the output safety gate prevents clinical advice from reaching patients; the crisis pre-check detects self-harm and triggers clinician alerts. Their failure modes must differ because the consequences of silent failure differ.
**Decision:** Output safety gate fails closed — classifier errors produce `CLINICAL_BOUNDARY`, blocking the message. Crisis pre-check fails by escalating — classifier errors trigger a clinician alert rather than silently missing a potential crisis. Safety state uses a single `SafetyDecision` StrEnum (not multi-boolean) to eliminate ambiguous states.
**Consequences:** False positives on output safety (blocked safe messages) are acceptable; false negatives on crisis (missed suicidal patient) are not. The asymmetry is intentional and must be preserved. Model choice (`claude-haiku-4-5-20251001`) balances latency and accuracy for both paths.

### ADR-006: Patient-Scoped Advisory Lock on AUTOCOMMIT Connection
**Date:** 2026-03-11
**Status:** accepted
**Context:** Concurrent graph invocations for the same patient (e.g., patient replies while a scheduled follow-up is in flight) can corrupt domain state (phase, unanswered_count). PostgreSQL advisory locks serialize access, but three independent traps exist: (1) `hash()` is salted per-process via `PYTHONHASHSEED`, (2) transaction-level locks release too early during LLM calls, (3) SQLAlchemy 2.x autobegin creates idle-in-transaction on the lock connection.
**Decision:** Use `pg_advisory_lock` (session-level) acquired at call sites (chat endpoint, webhook handler, scheduler) — not inside graph nodes. Lock key derived from `hashlib.sha256` for cross-process determinism. Lock connection uses `isolation_level="AUTOCOMMIT"` to prevent autobegin.
**Consequences:** Any call site that omits the lock can produce patient state corruption. The earlier incorrect design (transaction-level lock inside a node) is documented here so it is not repeated. SQLite dev environments skip locking (single-writer semantics sufficient).

### ADR-007: PHI Scrubbing as Last Processor in structlog Chain
**Date:** 2026-03-11
**Status:** accepted
**Context:** HIPAA requires that PHI not appear in application logs. Structured logging with structlog uses a processor chain where each processor can add or transform fields. If PHI scrubbing runs too early, later processors (e.g., `format_exc_info`) can re-introduce PHI from exception tracebacks.
**Decision:** `scrub_phi_fields` runs as the last processor before the renderer in the structlog chain. It uses a field-name blocklist (`message_content`, `patient_name`, `ssn`, etc.) plus regex patterns (SSN, email) with recursive dict traversal.
**Consequences:** The `_PHI_FIELD_NAMES` blocklist must grow with the domain model. Defense-in-depth — this is the last line, not the only line. The processor must always remain after `format_exc_info` in the chain.

### ADR-008: Code Cleanup — Effects Helpers, Context Factory, Channel Factories
**Date:** 2026-03-11
**Status:** accepted
**Context:** The M1–M7 implementation created significant duplication: 3 identical `ctx_factory` closures, 8 copy-pasted pending effects accumulation blocks across 4 node modules, hardcoded `MockNotificationChannel()`/`MockAlertChannel()` instantiation, and 4 identical mock session helpers in tests. The demo UI was unusable without manual curl commands.
**Decision:** (1) Extract `create_coach_context()` factory to `context.py`, typed with `ContextFactory` alias. (2) Extract `accumulate_effect()`/`merge_effects()` to `agent/effects.py` as pure functions. (3) Add settings-driven `create_notification_channel()`/`create_alert_channel()` factories in `integrations/channels.py`. (4) Replace `BaseHTTPMiddleware` with pure ASGI middleware to fix SSE buffering. (5) Gate demo endpoints behind `settings.environment == "dev"`. (6) Wire `AsyncPostgresSaver` for PostgreSQL with `MemorySaver` fallback for SQLite.
**Consequences:** Duplication eliminated. New nodes use `accumulate_effect()` instead of copy-pasting the get-or-default pattern. Channel behavior is configurable via `NOTIFICATION_CHANNEL`/`ALERT_CHANNEL` env vars. Demo endpoints are never exposed in production.

### ADR-009: Same-Origin Demo UI Serving via StaticFiles Mount
**Date:** 2026-03-11
**Status:** accepted
**Context:** The demo UI (Vite SPA in `demo-ui/`) only works through Vite's dev proxy. On Railway there is no Vite dev server. Serving the UI from a separate origin requires CORS configuration and complicates deployment. The UI has no client-side routing (no react-router).
**Decision:** Bundle the Vite build output into the Docker image (`node:22-slim` build stage → `/app/static`). Mount via `StaticFiles(directory=..., html=True)` at `"/"` as the last route in `main.py`, gated behind `settings.environment == "dev"`. FastAPI routes registered before the mount take priority. Starlette uses `anyio` for async file I/O — `aiofiles` is not required since Starlette 0.21.0.
**Consequences:** Same-origin serving eliminates CORS entirely. All relative API URLs in the UI work without proxy configuration. The mount only fires for paths not matched by any API route. If the UI ever adds client-side routing (react-router), the `StaticFiles` mount must be replaced with a `SpaStaticFiles` subclass that overrides `lookup_path` to fall back to `index.html`.

### ADR-010: Dormant Node Phase Transition Gated on LLM Success
**Date:** 2026-03-11
**Status:** accepted
**Context:** When a DORMANT patient sends a message, the node must generate a welcome-back response and transition DORMANT → RE_ENGAGING. If the LLM call fails and the phase transition still fires, the patient is silently moved to RE_ENGAGING with no reply — an unrecoverable state corruption.
**Decision:** Accumulate `phase_event="patient_returned"` only after a successful `coach_model.ainvoke()`. On LLM failure, return `{"outbound_message": None}` with no effects — leaving the patient in DORMANT so the next attempt can succeed. Route through `safety_gate` via conditional edge `_dormant_route` when a message is generated, or directly to `save_patient_context` when not.
**Consequences:** LLM failures in the dormant path are gracefully degraded rather than silently corrupting phase state. The patient remains DORMANT and can retry. This matches the fail-safe pattern in `reengagement_agent`, which also returns no effects on LLM failure.

### ADR-011: Demo UI Overhaul — Tailwind v4, SSE Streaming, Event-Driven State
**Date:** 2026-03-11
**Status:** accepted
**Context:** The original demo UI (~750 LOC, inline styles) discarded all SSE node data except `outbound_message`, polled 4 endpoints at 2s intervals (8 req/s), and had multiple display bugs (safety badge checking `"allow"` instead of `"safe"`, reset not refreshing state). The UI was not suitable for stakeholder demos.
**Decision:** Full rewrite with Tailwind CSS v4 design tokens, a typed API client, an SSE parser that extracts pipeline progression/tool calls/safety decisions from every node update, and event-driven state refresh (SSE `done` triggers fetch, 10s fallback). Tool call-result pairing uses `tool_call_id` for correctness with multi-tool nodes. Pipeline trace shows real-time node state transitions (running → complete).
**Consequences:** 2,400+ LOC addition, 18 source files. The SSE parser depends on the shape of `stream_mode="updates"` events — changes to the graph's streamed fields require updating `useSSE.ts`. Checkpoint clearing on patient reset was deferred here and resolved in ADR-012. Audit events panel was connected in ADR-012.

### ADR-012: Demo Experience Polish — Streaming, History, Architecture Diagram
**Date:** 2026-03-15
**Status:** accepted
**Context:** The demo UI from ADR-011 had four gaps: (1) patient reset didn't clear LangGraph checkpoints, causing stale conversation history, (2) no token-level streaming — responses appeared all-at-once after LLM completion, (3) no way to view conversation history in the observability panel, (4) the pipeline visualization was a flat pill list with no architectural context. These issues made stakeholder demos feel sluggish and opaque.
**Decision:** Four features: F1 — checkpoint clearing via `adelete_thread` after DB reset. F2 — token-level streaming via LangGraph `custom` stream mode with `get_stream_writer()` inside agent nodes (chosen over `stream_mode="messages"` to avoid 3 open LangGraph bugs with tool execution ordering). F3 — conversation history endpoint reading from checkpoint via `graph.aget_state()` rendered in ObservabilityPanel with role badges and tool message styling. F4 — hand-rolled JSX SVG DAG (`GraphView.tsx` + `graphLayout.ts`) replacing `PipelineTrace` with a 14-node architecture diagram that lights up nodes in real-time during streaming.
**Consequences:** The SSE contract now emits both `updates` and `custom` (token) event types — `useSSE.ts` must handle both. `GraphView` layout data is separated into `graphLayout.ts` (coordinates, edges) and `GraphView.tsx` (rendering) — layout changes go in the data file only. The `graph` object is now on `app.state` (required for checkpoint clearing and conversation history), which is a coupling point. All deferred items from ADR-011 are resolved. Note: the SVG DAG was subsequently replaced by a compact vertical stepper — see ADR-013.

### ADR-013: Pipeline Stepper Replaces Architecture DAG
**Date:** 2026-03-15
**Status:** accepted
**Context:** The hand-rolled SVG DAG from ADR-012 F4 rendered 14 nodes, 29 edges, and 5 cluster backgrounds in a 680x520 viewport. It consumed ~700px of vertical space — roughly 87% of the visible chat area — pushing messages below the fold. The diagram was visually dense with overlapping edge labels, fragile (hardcoded pixel offsets for edge routing), and defined shape metadata that was never rendered. For a demo, the complexity obscured the useful signal (which node is currently executing).
**Decision:** Replace the SVG DAG (`GraphView.tsx` + `graphLayout.ts`, ~530 lines) with a compact vertical pipeline stepper (`PipelineStepper.tsx`, ~96 lines) that dynamically shows nodes as they fire. Uses `CircleCheck` (complete/green), `Loader2` with `animate-spin` (running/blue), and `Circle` (pending/gray) from lucide-react. Same `PipelineNode[]` data contract from `useSSE.ts` — no backend or SSE changes required.
**Consequences:** The pipeline visualization drops from ~700px to ~250px height, freeing space for the conversation. The stepper only shows nodes that actually execute (no predicted/conditional paths), which avoids the complexity of representing the full graph topology. The collapsed summary bar behavior is preserved. The `NODE_LABELS` map in `useSSE.ts` continues to serve as the single source of truth for node display names.

### ADR-014: Content Block Extraction for AIMessage Normalization
**Date:** 2026-03-15
**Status:** accepted
**Context:** `langchain-anthropic` returns `AIMessage.content` as `list[dict]` (not `str`) when tools are bound during streaming (`coerce_content_to_string=False`). All three tool-using agent nodes called `str(response.content)`, producing the Python `repr()` of the list as the user-facing message. The same list format caused `isinstance(chunk.content, str)` to silently drop all streaming tokens.
**Decision:** Add `extract_text_content()` in `agent/content.py` — a pure function that normalizes `str | list[str | dict]` to plain `str` by extracting `type=text` blocks. Applied at all content extraction sites across 5 node files (3 broken + 2 defensive). Mirrors the pattern already proven in `demo.py:_serialize_message()`.
**Consequences:** All agent nodes produce clean text regardless of whether `langchain-anthropic` returns string or list content. Token-by-token streaming is restored for tool-using nodes. The helper is the single source of truth for content extraction — future nodes should use it instead of `str(response.content)`.
