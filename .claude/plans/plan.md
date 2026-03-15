# Health Ally Improvement Plan

## Milestone Outline

- [ ] M1: Fix Critical Backend Bugs
  - [ ] Step 1 — Add `ReminderJobHandler` to `orchestration/jobs.py` and register in `JobDispatcher` → verify: `pyright src/health_ally/orchestration/jobs.py`
  - [ ] Step 2 — Update call sites (`__main__.py`, `main.py`) to instantiate and pass `ReminderJobHandler` → verify: `pyright src/health_ally/__main__.py src/health_ally/main.py`
  - [ ] Step 3 — Guard `set_reminder` against malformed ISO input → verify: `pyright src/health_ally/agent/tools/reminder.py`
  - [ ] Step 4 — Add `Literal` type hint + runtime coercion to `alert_clinician` priority → verify: `pyright src/health_ally/agent/tools/clinician.py`
  - [ ] Step 5 — Add tests for all 3 bugs in `test_jobs.py` and `test_tools.py` → verify: `pytest tests/unit/test_jobs.py tests/unit/test_tools.py -v`
  Commit: "fix: register reminder handler, guard ISO parse, coerce alert priority"
