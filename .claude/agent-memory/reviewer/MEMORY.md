# Reviewer Agent Memory — MedBridge Health Coach

## Project Patterns Confirmed

- `WriteOnlyMapped` + `lazy="raise"` is the intentional pattern for collections on ORM models (not a bug — prevents N+1 lazy loads in async context).
- `expire_on_commit=False` is MANDATORY on all session factories (memory note from research).
- `asyncio_mode = "auto"` + `asyncio_default_fixture_loop_scope = "session"` is the correct pytest-asyncio 1.x setup for this project.
- Phase string values use `re_engaging` (underscore), not `re-engaging` (hyphen) — DB column is `String(20)`.
- `AuditEvent.patient_id` intentionally has NO FK to `patients` (documented: "survives deletion").
- `OutboxEntry.patient_id`, `ClinicianAlert.patient_id`, `SafetyDecisionRecord.patient_id`, `Message.patient_id`, `ToolInvocation.patient_id` — all intentionally NO FK (audit/log tables that must survive patient deletion or be written before patient row exists).
- The `ScheduledJob` partial index `ix_scheduled_jobs_pending` uses `postgresql_where` — this index does NOT exist in SQLite (partial indexes not supported). Tests that exercise this index path must be integration tests against PostgreSQL.
- `conftest.py` engine fixture is `scope="session"` but does NOT call `Base.metadata.create_all` — the session-scoped engine fixture is shared but tables are NOT pre-created. Individual test modules that need tables must create them themselves (as `test_repositories.py` does with its own `db_engine` fixture).

## Known Issues Found in M2

- `FakeConsentService.reason` field in `check()` uses `self.allowed` (a property on the fake) — this is fine, not a bug.
- `_in_quiet_hours` uses exclusive `< end` boundary: hour=8 with end=8 returns False (not in quiet hours). The test `test_calculate_send_time_boundary_end` asserts 8 AM passes through — this is CORRECT behavior (quiet hours are 21-8, exclusive end means 8 AM is allowed).
- `CoachConfig.quiet_hours_end` field constraint is `ge=0, le=23` — the value 8 is valid. But note that the value 0 (midnight) combined with `start < end` path would mean `_in_quiet_hours(0, 0, 23) = True` (midnight is quiet). This edge case is not tested.
