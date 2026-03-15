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
- [ ] M7: Dormant Welcome-Back Prompt
  - [ ] Step 1 — Add `DORMANT_PROMPT` to `system.py` and register in `PHASE_PROMPTS` → verify: `grep -q 'DORMANT_PROMPT' src/health_ally/agent/prompts/system.py`
  - [ ] Step 2 — Update `dormant.py` to use `get_system_prompt("dormant")` instead of `build_re_engaging_prompt("patient")` → verify: `grep -q 'get_system_prompt' src/health_ally/agent/nodes/dormant.py`
  - [ ] Step 3 — Run lint, typecheck, and tests → verify: `ruff check . && ruff format --check . && pyright . && pytest`
  Commit: "feat: add dedicated dormant welcome-back prompt"
