# Reviewer Agent Memory — Health Ally

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

## Patterns Confirmed in M6

- `FakeConsentService(logged_in=True, consented=True)` is hardcoded in ALL of `main.py` (_setup_graph_and_context, _run_background_workers) and `__main__.py` (_run_worker). The `MedBridgeClient` exists but is never wired in. Consent is always granted in every deployed mode.
- `sqlite_insert` from `sqlalchemy.dialects.sqlite` is used in `webhooks.py` for `ProcessedEvent`. This dialect-specific dialect insert will fail at runtime on PostgreSQL production with a `NotImplementedError` or import of a wrong class. The correct cross-dialect approach is `sqlalchemy.dialects.postgresql.insert` for Postgres or `insert(...).on_conflict_do_nothing()` with the generic dialect.
- `_deliver_alert` in `delivery_worker.py` looks up `ClinicianAlert` by `(patient_id, tenant_id)` newest-first, not by the `idempotency_key` / `delivery_key` that was stored in `OutboxEntry.delivery_key` at write time. A patient with two undelivered crisis alerts would always deliver the same (most recent) one twice.
- Webhook HMAC guard is bypassed when `medbridge_webhook_secret` is empty string — `if webhook_secret and not verify_webhook_signature(...)` short-circuits the check. Empty string is the default value in `Settings`, so HMAC is unenforced by default.
- `_handle_patient_message` in `webhooks.py` does NOT check consent before invoking the graph. Consent check happens inside `consent_gate` node — which uses `FakeConsentService(consented=True)` in all current wiring. The immutable rule "verify consent on every interaction" is structurally honored in graph code but the real service is never wired.
- `_poll_and_deliver` claims entries (`status="delivering"`) inside a transaction, then processes them outside. If the worker crashes after the transaction commits, entries are stuck in `"delivering"` forever — no recovery path. Need a `stuck_delivering_timeout` sweep.
- `_record_attempt` and `_handle_delivery_failure` each open separate sessions and count `DeliveryAttempt` rows. Between the two calls the count can diverge if another worker races (not guarded by advisory lock or FOR UPDATE).

## Patterns Confirmed in M5 Demo UI (f4dd6ee)

- `Chat` in `App.tsx` is always passed `patientId` (the hardcoded external ID from `DEMO_PATIENTS`), never `effectivePatientId`. `load_patient_context` does `session.get(Patient, pid)` against the external ID used as a UUID PK — if no row exists it auto-provisions. The seeded patient from `/seed-patient` is a **different row** with a different internal UUID. Result: Chat conversations land on the auto-provisioned record; `ObservabilitySidebar` and `DemoControls` (which use `effectivePatientId`) land on the seeded record. The two are never the same row unless seeding is skipped.
- `triggerFollowup` in `DemoControls.tsx` calls `/v1/demo/trigger-followup/${patientId}` where `patientId` is `effectivePatientId` (correct). But `fetchJobs` in its `useCallback` dep array does NOT include `fetchJobs` — React will warn about stale closure; however `fetchJobs` is stable (depends only on `patientId` which is a prop) so this is not an actual bug in practice.
- `showStatus` in `DemoControls` sets a `setTimeout` to clear the message after 3s. If the component unmounts before the timeout fires, `setStatusMessage("")` is called on an unmounted component — React 18 no longer throws here, but it's still a potential stale state update. No `clearTimeout` in a cleanup. Demo-only code; low severity.
- `seed_patient` in `demo.py` returns `phase="pending"` hardcoded at line 138-142, ignoring the actual `patient.phase` from the DB (which might differ if the patient already existed and was returned early at line 101-105 with the real phase). The `phase` field in the response is misleading on re-seed of an existing patient, but the early-return path at line 100-105 correctly reads `patient.phase`.
- `mountedRef` pattern in `ObservabilitySidebar`: `mountedRef.current = true` is set in the `useEffect` body (not initialization). Because `useRef(true)` initializes to `true`, the first render is fine. On patientId change, cleanup sets it `false`, then the new effect sets it `true` again before `fetchState` runs — correct.
- No SQL injection risk in `demo.py` — all queries use SQLAlchemy parameterized ORM expressions. UUID parsing is done via `uuid.UUID()` before use, which is a correct guard.
- The `environment == "dev"` gate in `main.py` is correct. The import is deferred inside the `if` block, so the module is not even loaded in non-dev environments.

## Patterns Confirmed in M3 Demo UI (617395b)

- `msg.id` — `_serialize_message` in `demo.py` generates `uuid.uuid4()` fallback for messages without IDs (fixed 2026-03-15 in M8). React key uniqueness is guaranteed.
- `ToolMessage.name` — `_serialize_message` recovers tool name via `tool_call_names` dict built from preceding `AIMessage.tool_calls` (fixed 2026-03-15 in M2).
- Ordering: `snapshot.values["messages"]` is oldest-first (LangGraph `add_messages` reducer always appends). Backend returns `items[:100]` (oldest 100). UI renders in array order (oldest at top). This is correct for a conversation transcript display.
- Count badge on the Conversation section header shows the full list length (up to 100 from the endpoint), and the overflow text shows `length - 20 more`. Both are accurate.

## Known Issues Found in M2

- `FakeConsentService.reason` field in `check()` uses `self.allowed` (a property on the fake) — this is fine, not a bug.
- `_in_quiet_hours` uses exclusive `< end` boundary: hour=8 with end=8 returns False (not in quiet hours). The test `test_calculate_send_time_boundary_end` asserts 8 AM passes through — this is CORRECT behavior (quiet hours are 21-8, exclusive end means 8 AM is allowed).
- `CoachConfig.quiet_hours_end` field constraint is `ge=0, le=23` — the value 8 is valid. But note that the value 0 (midnight) combined with `start < end` path would mean `_in_quiet_hours(0, 0, 23) = True` (midnight is quiet). This edge case is not tested.
