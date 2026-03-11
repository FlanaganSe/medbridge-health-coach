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

## Patterns Confirmed in M4

- Crisis check → `_crisis_route` → `fallback_response` bypasses `safety_gate` entirely. The graph never sends a crisis message through the safety classifier.
- `fallback_response` sets `"safety_decision": "fallback"` (raw string literal, not a `SafetyDecision` enum value). This is not reachable by `safety_route` but is a consistency trap.
- `safety_gate` fail-safe correctly blocks (returns `CLINICAL_BOUNDARY`). Crisis check fail-safe incorrectly allows through (returns `crisis_detected=False` with no alert). These two nodes have opposite fail-safe policies.
- `retry_generation` only returns the `HumanMessage` augmentation prompt in `messages`, not the LLM's response. Found in M4 review, not yet fixed.

## Patterns Confirmed in M5

- `_job_metadata` is NOT a field on `PatientState` — intended to carry `follow_up_day` from the scheduler job into the graph, but never wired. `state.get("_job_metadata")` always returns `None`. Bug found in M5 review, not yet fixed.
- `sweep_missing_jobs` exists in `reconciliation.py` but is never called anywhere — dead code at runtime as of M5.
- `MemorySaver` is used in the production worker — conversation history lost on worker restart. Should use `AsyncPostgresSaver` for postgres deployments, same conditional logic as `persistence/db.py`.
- `session.add()` with a unique-constrained model does NOT do ON CONFLICT DO NOTHING — raises `IntegrityError` on duplicate. Use `insert(...).on_conflict_do_nothing()` explicitly when idempotency is required against concurrent writers.
- `asyncio.get_event_loop().run_until_complete()` is deprecated in Python 3.12+. Tests should be `async def` with `asyncio_mode = "auto"`.

## Known Issues Found in M2

- `FakeConsentService.reason` field in `check()` uses `self.allowed` (a property on the fake) — this is fine, not a bug.
- `_in_quiet_hours` uses exclusive `< end` boundary: hour=8 with end=8 returns False (not in quiet hours). The test `test_calculate_send_time_boundary_end` asserts 8 AM passes through — this is CORRECT behavior (quiet hours are 21-8, exclusive end means 8 AM is allowed).
- `CoachConfig.quiet_hours_end` field constraint is `ge=0, le=23` — the value 8 is valid. But note that the value 0 (midnight) combined with `start < end` path would mean `_in_quiet_hours(0, 0, 23) = True` (midnight is quiet). This edge case is not tested.
