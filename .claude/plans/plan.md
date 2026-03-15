# Health Ally Improvement Plan

## Milestone Outline

- [x] M1: Fix Critical Backend Bugs
  - [x] Step 1 — Add `ReminderJobHandler` to `orchestration/jobs.py` and register in `JobDispatcher`
  - [x] Step 2 — Update call sites (`__main__.py`, `main.py`) to instantiate and pass `ReminderJobHandler`
  - [x] Step 3 — Guard `set_reminder` against malformed ISO input
  - [x] Step 4 — Add `Literal` type hint + runtime coercion to `alert_clinician` priority
  - [x] Step 5 — Add tests for all 3 bugs in `test_jobs.py` and `test_tools.py`
  Commit: f20d6eb "fix: register reminder handler, guard ISO parse, coerce alert priority"
- [x] M2: Markdown Rendering in Bot Messages
  - [x] Step 1 — Install `react-markdown` in demo-ui → verify: `cd demo-ui && npm ls react-markdown`
  - [x] Step 2 — Update `BotMessage` in `ChatMessage.tsx` to use `<ReactMarkdown>` → verify: `grep -q 'ReactMarkdown' demo-ui/src/components/ChatMessage.tsx`
  - [x] Step 3 — Add `.prose` styles to `index.css` → verify: `grep -q '.prose' demo-ui/src/index.css`
  - [x] Step 4 — Build succeeds → verify: `cd demo-ui && npm run build`
  Commit: "feat: render markdown in bot chat messages"
- [x] M3: Demo UX Quick Wins
  - [x] Step 1 — Clear chat on patient reset (resetKey in App.tsx + onReset callback) → verify: `cd demo-ui && npx tsc --noEmit`
  - [x] Step 2 — Distinct pipeline node labels in useSSE.ts → verify: `grep -q 'Onboarding' demo-ui/src/hooks/useSSE.ts`
  - [x] Step 3 — Color-differentiate success/error status messages in DemoControlBar.tsx → verify: `cd demo-ui && npx tsc --noEmit`
  - [x] Step 4 — Button loading width stability in Button.tsx → verify: `cd demo-ui && npx tsc --noEmit`
  - [x] Step 5 — Fix muted text contrast (#B0B0B0 → #767676) in index.css → verify: `grep -q '767676' demo-ui/src/index.css`
  Commit: 99309f8 "fix: demo UX quick wins — reset clears chat, distinct labels, status colors, button stability, contrast"
- [x] M4: Suggested Message Chips
  - [x] Step 1 — Add `SuggestionChips` component and phase-aware suggestions to ChatPanel.tsx, refactor `handleSend` to accept optional text → verify: `cd demo-ui && npm run build`
  Commit: 5f74d9a "feat: add phase-aware suggestion chips to chat empty state"
- [x] M5: Audit Events Panel
  - [x] Step 1 — Add `fetchAuditEvents` to `api.ts` (mirrors `fetchScheduledJobs` pattern) → verify: `grep -q 'fetchAuditEvents' demo-ui/src/api.ts`
  - [x] Step 2 — Add `auditEvents: AuditEventItem[]` to `PatientState` in `types.ts` → verify: `grep -q 'auditEvents' demo-ui/src/types.ts`
  - [x] Step 3 — Wire `fetchAuditEvents` into `usePatientState.ts` `Promise.all` → verify: `grep -q 'fetchAuditEvents' demo-ui/src/hooks/usePatientState.ts`
  - [x] Step 4 — Add Audit Trail section to `ObservabilityPanel.tsx` → verify: `cd demo-ui && npm run build`
  Commit: 84442a1 "feat: wire audit events into observability panel"
- [x] M6: MI Fidelity — System Prompt Improvements
  - [x] Step 1 — Add "Communication Techniques" OARS section to `BASE_SYSTEM_PROMPT` in `system.py` → verify: `grep -q 'Communication Techniques' src/health_ally/agent/prompts/system.py`
  - [x] Step 2 — Replace directive language in `BASE_SYSTEM_PROMPT` and `ACTIVE_PROMPT` with autonomy-supportive framing → verify: `! grep -q 'encourage consistency' src/health_ally/agent/prompts/system.py`
  - [x] Step 3 — Add summary-before-confirmation instruction to `_GOAL_INSTRUCTIONS` in `onboarding.py` → verify: `grep -q 'summarize' src/health_ally/agent/prompts/onboarding.py`
  - [x] Step 4 — Run lint, typecheck, and tests → verify: `ruff check . && ruff format --check . && pyright . && pytest`
  Commit: f15b8dd "feat: improve system prompts with MI-aligned OARS techniques"
- [x] M7: Dormant Welcome-Back Prompt
  - [x] Step 1 — Add `DORMANT_PROMPT` to `system.py` and register in `PHASE_PROMPTS` → verify: `grep -q 'DORMANT_PROMPT' src/health_ally/agent/prompts/system.py`
  - [x] Step 2 — Update `dormant.py` to use `get_system_prompt("dormant")` instead of `build_re_engaging_prompt("patient")` → verify: `grep -q 'get_system_prompt' src/health_ally/agent/nodes/dormant.py`
  - [x] Step 3 — Run lint, typecheck, and tests → verify: `ruff check . && ruff format --check . && pyright . && pytest`
  Commit: 4e1ebbe "feat: add dedicated dormant welcome-back prompt"
- [x] M8: Per-Patient Stub Data
  - [x] Step 1 — Add 3 adherence profiles to `adherence.py`, select by `hash(patient_id)` → verify: `ruff check src/health_ally/agent/tools/adherence.py`
  - [x] Step 2 — Add 3 program profiles to `goal.py`, select by `hash(patient_id)` → verify: `ruff check src/health_ally/agent/tools/goal.py`
  - [x] Step 3 — Run lint, typecheck, and tests → verify: `ruff check . && ruff format --check . && pyright . && pytest`
  Commit: 2eb88f0 "feat: per-patient stub data for demo differentiation"
- [x] M9: Demo Personas
  - [x] Step 1 — Replace `name` values in `DEMO_PATIENTS` array in `App.tsx` with named personas → verify: `cd demo-ui && npm run build`
  Commit: 7f5552a "feat: replace generic demo patient names with named personas"
- [x] M10: Phase Transition Animation
  - [x] Step 1 — Add `phase-pulse` keyframe and `animate-phase-pulse` class with reduced-motion guard to `index.css` → verify: `grep -q 'phase-pulse' demo-ui/src/index.css`
  - [x] Step 2 — Add phase-change detection (`useRef`/`useEffect`/`useState`) and conditional `animate-phase-pulse` class to `PhaseBadge` in `Badge.tsx` → verify: `cd demo-ui && npm run build`
  Commit: 5da187c "feat: add phase transition pulse animation to PhaseBadge"
- [x] M11: Timezone Fix — propagate patient timezone through graph state
  - [x] Step 1 — Add `patient_timezone: str` to `PatientState` in `state.py` → verify: `grep -q 'patient_timezone' src/health_ally/agent/state.py`
  - [x] Step 2 — Populate `patient_timezone` from `patient.timezone` in `load_patient_context` → verify: `grep -q 'patient_timezone' src/health_ally/agent/nodes/context.py`
  - [x] Step 3 — Replace hardcoded timezone in `active.py`, `re_engaging.py`, and `goal.py` with `state.get("patient_timezone", "America/New_York")` → verify: `! grep -q '"America/New_York"' src/health_ally/agent/nodes/active.py`
  - [x] Step 4 — Run lint, typecheck, and tests → verify: `ruff check . && ruff format --check . && pyright . && pytest`
  Commit: 84e5f62 "fix: propagate patient timezone instead of hardcoded America/New_York"
- [ ] M12: Test Coverage + Accessibility Polish
  - [ ] Step 1 — Write `tests/unit/test_save_patient_context.py` (6 tests covering goal, phase transition, alerts, scheduled jobs, patient message reset, empty effects) → verify: `pytest tests/unit/test_save_patient_context.py -v`
  - [ ] Step 2 — Write `tests/unit/test_retry_generation.py` (3 tests covering retry count increment, outbound message, LLM error) → verify: `pytest tests/unit/test_retry_generation.py -v`
  - [ ] Step 3 — Write `tests/unit/test_demo_endpoints.py` (4 tests covering seed, idempotency, reset, trigger with no jobs) → verify: `pytest tests/unit/test_demo_endpoints.py -v`
  - [ ] Step 4 — Add SSE event shape test to `tests/integration/test_chat_endpoint.py` → verify: `pytest tests/integration/test_chat_endpoint.py -v`
  - [ ] Step 5 — Frontend polish: loading skeletons, confidence format, reduced-motion guards → verify: `cd demo-ui && npm run build`
  Commit: "test: add coverage for critical paths; polish frontend accessibility"
