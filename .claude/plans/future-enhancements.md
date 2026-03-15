# Research: Demo Enhancement Features

Three features researched in depth. Ordered by priority (feasibility + value + effort balance).

---

## Feature 1: Auto-Pilot Demo Mode

**What:** A "Play" button that runs the full patient lifecycle automatically. An LLM simulates patient responses, the coach responds, tools fire, phases transition — all hands-free. Evaluators watch instead of typing.

**Why it's valuable:** The #1 demo problem is "what do I type?" Evaluators fumble, miss the best flows, or trigger edge cases that derail the demo. Auto-pilot guarantees every highlight is hit: tool calls, phase transitions, safety decisions, progressive streaming — all in ~35-55 seconds.

### How the chat pipeline works today

- `POST /v1/chat` with `{"message": "..."}`, headers `X-Patient-ID` + `X-Tenant-ID`
- Advisory lock held for full graph invocation (serializes concurrent calls per patient)
- `stream_mode=["updates", "custom"]` — token events + node state updates via SSE
- Frontend `useSSE.ts` awaits full stream; `ChatPanel.handleSend(overrideText?)` is the single entry point
- `onStreamComplete` triggers `refresh()` which fires 7 parallel GET requests for observability data

### How auto-pilot would work

**New backend endpoint:** `POST /v1/demo/simulate-patient-response/{patient_id}`
- Accepts `{phase, last_coach_message, turn_number, patient_persona}`
- Calls `ChatAnthropic(model="claude-haiku-4-5-20251001", max_tokens=150)` directly — not through ModelGateway (this is demo infra, not a patient interaction)
- Returns `{"simulated_message": "..."}`
- Frontend then sends that text via normal `POST /v1/chat`

**Phase-aware simulate prompt:** Instructs Haiku to behave as a realistic patient. Key rules:
- Onboarding turn 0-1: state a clear, specific exercise goal
- Onboarding turn 2+: if coach is summarizing/confirming, agree immediately
- Active: express positive progress with one small challenge
- Re-engaging: show you're back, brief, express motivation
- Dormant: express eagerness to restart

**Frontend auto-pilot hook (`useAutoPilot.ts`):**
- Sequential loop: simulate response → send via normal chat → await full SSE → refresh → repeat
- Phase-aware stop condition: stops when `phase === "active"` and `turnNumber > 4`
- Hard cap at `MAX_TURNS = 10` as safety valve
- ~800ms "typing" pause between simulate response and send for visual pacing

**Full lifecycle sequence:**
1. User presses "Seed Patient" (manual)
2. Auto-pilot sends "Hi" → `pending_node` fires, welcome message, phase → onboarding
3. Auto-pilot simulates goal statement → `onboarding_agent` may call `get_program_summary`, coach asks for confirmation
4. Auto-pilot simulates confirmation → `set_goal` fires (tool loop), phase → active, Day 2 follow-up scheduled
5. Auto-pilot simulates active coaching message → `active_agent` calls `get_adherence_summary`, coaches positively
6. Auto-pilot stops. User can optionally click "Run Next Check-in" to show scheduler-initiated flow.

### Why LLM simulation over scripted responses

The Conversation History panel is visible during the demo. Scripted "Yes, that's right!" replies look canned and land at wrong moments when the coach varies its wording. LLM simulation adapts to whatever the coach actually said. The unpredictability risk is mitigated by tight phase-specific constraints in the prompt.

### Constraints discovered

1. **Advisory lock serialization** — auto-pilot MUST await each SSE stream before sending next message. Already handled by `send()` being async.
2. **`trigger_followup` is poll-based** (scheduler polls every 30s) — auto-pilot cannot drive the scheduler path synchronously. This is fine: auto-pilot stops at active, user manually clicks "Run Next Check-in."
3. **Onboarding requires 3+ patient turns** — coach summarizes goal and asks for confirmation per prompt instructions. The simulate prompt accounts for this.
4. **First `POST /v1/chat` for pending patient always fires `pending_node`** regardless of message content — so "Hi" is sufficient.
5. **Reset button must be disabled while auto-pilot is running** to prevent phase regression during the loop.

### Files to change

| File | Change |
|---|---|
| `src/health_ally/api/routes/demo.py` | Add `SimulatePatientRequest`, `SimulatePatientResponse`, and `POST /v1/demo/simulate-patient-response/{patient_id}` endpoint |
| `demo-ui/src/api.ts` | Add `simulatePatientResponse()` function |
| `demo-ui/src/hooks/useAutoPilot.ts` | **New file.** Hook: `start()`, `stop()`, `isRunning`. Sequential loop with phase-aware turn counter. |
| `demo-ui/src/components/DemoControlBar.tsx` | Add Play/Stop button. Disable Play while streaming. Disable Reset while auto-pilot running. |
| `demo-ui/src/App.tsx` | Instantiate `useAutoPilot`, wire to DemoControlBar and ChatPanel |

**ChatPanel integration:** `handleSend` already accepts `overrideText` (used by suggestion chips). Expose a `sendRef` from ChatPanel so App can call it from the auto-pilot hook. No changes to ChatPanel's internal logic.

**Zero changes to:** `chat.py`, any agent node, any tool, `model_gateway.py`, `useSSE.ts`

### Risks

- Safety classifier may block a simulated message if the Haiku simulate prompt produces something borderline. Auto-pilot must detect empty `outbound_message` and stop gracefully.
- Onboarding may take extra turns if coach asks follow-up questions. Loop handles this by reading `phase` after each turn.
- API cost: ~$0.01-0.03 per full lifecycle run (Haiku for simulation + Sonnet for coaching).

---

## Feature 2: Safety Red-Team Playground

**What:** A "Safety Lab" mode with pre-loaded adversarial messages organized by category (Clinical, Crisis, Jailbreak, Edge Cases). Each message has an expected safety outcome. After sending, the actual classification is compared to the expected one with a pass/fail badge.

**Why it's valuable:** "Try to break it" is the most engaging demo interaction. A structured safety test walkthrough builds confidence with compliance-conscious evaluators. The pipeline graph lights up the safety cluster in real-time — visual proof that the LLM is bounded.

### How the safety pipeline works

**Two classifier passes:**

1. **Input-side crisis check** (`crisis_check.py`) — classifies patient's raw message. Three levels: `none`, `possible`, `explicit`. EXPLICIT → immediate durable alert + routes to fallback. POSSIBLE → routine alert, continues normally.

2. **Output-side safety gate** (`safety.py`) — classifies coach's generated reply. Four decisions: `safe`, `clinical_boundary`, `crisis`, `jailbreak`. Clinical boundary gets one retry via `retry_generation` (injects safety augmentation into system prompt). On second failure or crisis/jailbreak → `fallback_response`.

**Three deterministic fallback messages:**
- Crisis → 988 lifeline + "Your care team has been notified"
- Clinical boundary → "That sounds like a question for your care team"
- Default (jailbreak, error) → generic safe message

### What the user already sees

- **SafetyToast** — appears when `safetyDecision !== "safe"`, shows label + confidence %
- **Graph visualization** — safety_gate, retry_generation, fallback_response light up in red cluster
- **Observability Panel** — last 5 safety decisions with confidence
- **Retry back-edge** — the bezier curve from retry_generation → safety_gate renders during retry

### Bugs discovered during research

1. **Missing `jailbreak` label** — `SafetyToast.tsx:10` and `types.ts:27` lack `"jailbreak"`. Would display raw string.
2. **Jailbreak detection overwrite** — `fallback_response` sets `safety_decision = "fallback"`, overwriting the original `"jailbreak"` classification. The SSE toast sees `"fallback"`, not `"jailbreak"`. Fix: capture the `safety_gate`-specific decision before `fallback_response` overwrites it.
3. **Crisis input-side not surfaced** — `crisis_check` outcome doesn't trigger SafetyToast (only the output-side `safety_gate` does). Crisis test pass/fail should verify response contains "988" text.
4. **`reasoning` field stored but not returned** — `SafetyDecisionRecord` has the `reasoning` column populated, but `SafetyDecisionItem` in the API response omits it. Two-line fix to expose it.

### Implementation approach

**Almost entirely frontend.** One minor backend change (expose `reasoning` field).

**Backend (2 lines):**
- `src/health_ally/api/routes/state.py` — add `reasoning: str | None` to `SafetyDecisionItem`

**Frontend (new file):**
- `demo-ui/src/data/safetyTestCases.ts` — static registry of ~21 adversarial messages across 4 categories with expected outcomes

**Frontend (modified):**

| File | Change |
|---|---|
| `demo-ui/src/types.ts` | Add `"jailbreak"` to `SafetyDecision` union; add `reasoning?: string` to `SafetyDecisionItem` |
| `demo-ui/src/components/SafetyToast.tsx` | Add `"jailbreak": "Jailbreak Attempt Detected"` label |
| `demo-ui/src/components/ChatPanel.tsx` | Accept `isSafetyTestMode` prop; swap suggestion chips for categorized safety test chips; annotate messages with expected-vs-actual badge |
| `demo-ui/src/components/DemoControlBar.tsx` | Add Shield icon toggle for Safety Lab mode |
| `demo-ui/src/components/ObservabilityPanel.tsx` | Render `reasoning` below confidence in Safety Decisions section |
| `demo-ui/src/App.tsx` | Thread `isSafetyTestMode` state between DemoControlBar and ChatPanel |
| `demo-ui/src/hooks/useSSE.ts` | Capture `safety_decision` from `safety_gate` node specifically (before `fallback_response` overwrites it) |

### Adversarial message bank (curated, 21 messages)

**Clinical (6):** medication dosage, chest pain during exercise, wound assessment, exercise contraindications, blood thinner management, acute injury assessment. Expected: `clinical_boundary`.

**Crisis (4):** "don't want to be alive", "ending it all", "plan to hurt myself", "no way out". Expected: response includes 988 text.

**Jailbreak (5):** instruction override, "you are now a doctor", role-play framing, creative writing pretext, "as my friend" framing. Expected: `jailbreak` (but note: state shows `"fallback"` after the fallback node runs — comparison must check `safety_gate` event specifically).

**Edge Cases (5):** post-exercise soreness, "killing me" colloquial, frustration, post-surgery fatigue, muscle soreness. Expected: `safe` (tests false-positive avoidance).

### Persistent side effects

Adversarial messages create real `safety_decision` records, `audit_events`, and (for crisis) `clinician_alerts` that are **not deleted by Reset Patient** (HIPAA audit retention by design). The Safety Lab UI should include a note about this. Phase, goals, and conversation checkpoint are unaffected — demo remains fully usable.

### Phase dependency

`pending_node` bypasses `safety_gate`. First message after seeding advances to `ONBOARDING`. By the second message, full safety pipeline is active. Safety Lab should ensure patient is past PENDING before sending adversarial messages (or auto-send "Hi" first).

---

## Feature 3: Prompt & Reasoning Inspector

**What:** An expandable "Inspector" section in the ObservabilityPanel that shows, for each agent turn: the system prompt name/phase, message count sent to LLM, safety classifier reasoning, and optionally token usage metadata.

**Why it's valuable:** Technical evaluators want to see *how* the system works. Seeing the phase-specific prompt augmentation, the safety classifier's reasoning, and the retry augmentation proves the architecture is transparent and controllable.

### What data is available

**System prompts:** Mostly static strings per phase (`system.py`). `build_onboarding_prompt()`, `build_active_prompt()`, `build_re_engaging_prompt()` inject patient context but are called with minimal args currently. The full prompt is deterministic given the phase + invocation_source. Retry adds `RETRY_AUGMENTATION` dynamically.

**Safety reasoning:** Stored in `pending_effects.safety_decisions` during the turn, then persisted to `SafetyDecisionRecord` (which already has the `reasoning` column) and `AuditEvent.metadata_`. The API response currently omits `reasoning` — same fix as Feature 2.

**Token usage:** `full_response.usage_metadata` is populated on accumulated LLM responses but never read. Available via `getattr(full_response, "usage_metadata", None)` in each node.

**Message count:** The `messages` list length is available in state at invocation time.

### Implementation approach

**Two options:**

**Option A (Minimal — recommended as starting point):** Surface debug data via `get_stream_writer()` from inside nodes. Add `{"type": "debug", "prompt_phase": "onboarding", "message_count": 5, "model": "claude-sonnet-4-6"}` events. Frontend adds a "Last Turn" section to ObservabilityPanel showing prompt phase, message count, and safety reasoning (the latter from Feature 2's `reasoning` field addition).

- Changes to 5 backend files (3 agent nodes + safety_gate + crisis_check) — one `writer({"type": "debug", ...})` call each
- Changes to `useSSE.ts` — new `"debug"` event handler
- New section in `ObservabilityPanel.tsx`

**Option B (Full):** Show the complete system prompt text. This requires emitting the full prompt string (potentially 500+ tokens) via SSE or a dedicated endpoint. More data but harder to display well in a 420px sidebar. Syntax highlighting would require a dependency (or pre-formatted monospace block).

### Assessment

This feature has **lower priority** than Features 1 and 2 because:
1. The safety reasoning (the most valuable inspector data) is already covered by Feature 2's `reasoning` field addition
2. The graph visualization already shows which nodes executed and in what order
3. The system prompts are static per phase — knowing "onboarding prompt was used" is equivalent to knowing "phase is onboarding" which is already displayed
4. The audience for full prompt inspection is narrow (technical evaluators only)

The incremental value over Features 1+2 is modest. If implemented, the minimal version (Option A) is sufficient — it adds prompt phase + message count + model name to the observability panel, which takes ~15 minutes to review during a technical deep-dive.

### Files to change (Option A)

| File | Change |
|---|---|
| `src/health_ally/agent/nodes/onboarding.py` | Add `writer({"type": "debug", ...})` after prompt construction |
| `src/health_ally/agent/nodes/active.py` | Same |
| `src/health_ally/agent/nodes/re_engaging.py` | Same |
| `src/health_ally/agent/nodes/safety.py` | Emit debug event with classifier result |
| `src/health_ally/agent/nodes/crisis_check.py` | Emit debug event with crisis check result |
| `demo-ui/src/hooks/useSSE.ts` | Add `"debug"` event handler before node-iteration loop |
| `demo-ui/src/types.ts` | Add `DebugInfo` interface |
| `demo-ui/src/components/ObservabilityPanel.tsx` | Add "Last Turn" section showing debug info |

---

## Priority Ranking

| # | Feature | Effort | Value | Risk | Verdict |
|---|---|---|---|---|---|
| 1 | Auto-Pilot Demo Mode | Medium (1 endpoint + 1 hook + UI wiring) | Very High | Low (isolated, uses existing chat path) | **Build first** |
| 2 | Safety Red-Team Playground | Low-Medium (mostly frontend + 2-line backend fix) | High | Very Low (frontend-only core, real bugs found to fix) | **Build second** |
| 3 | Prompt Inspector | Medium (5 node changes + SSE handler + UI) | Medium | Low | **Defer or build last** — incremental value over F1+F2 is modest |
