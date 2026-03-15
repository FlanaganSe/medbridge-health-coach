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
- [ ] M3: Demo UX Quick Wins
  - [ ] Step 1 — Clear chat on patient reset (resetKey in App.tsx + onReset callback) → verify: `cd demo-ui && npx tsc --noEmit`
  - [ ] Step 2 — Distinct pipeline node labels in useSSE.ts → verify: `grep -q 'Onboarding' demo-ui/src/hooks/useSSE.ts`
  - [ ] Step 3 — Color-differentiate success/error status messages in DemoControlBar.tsx → verify: `cd demo-ui && npx tsc --noEmit`
  - [ ] Step 4 — Button loading width stability in Button.tsx → verify: `cd demo-ui && npx tsc --noEmit`
  - [ ] Step 5 — Fix muted text contrast (#B0B0B0 → #767676) in index.css → verify: `grep -q '767676' demo-ui/src/index.css`
  Commit: "fix: demo UX quick wins — reset clears chat, distinct labels, status colors, button stability, contrast"
