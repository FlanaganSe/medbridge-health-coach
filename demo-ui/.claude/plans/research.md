# Research: Worker and Webhook Bug Investigation

## Worker and Webhook Issues

### 1. HMAC verification uses `hmac.new` — does not exist (AttributeError crash)

**Severity: Demo-blocker (P0)**

`integrations/medbridge.py:93` calls `hmac.new(...)`. The Python `hmac` module has no `new` function — the correct call is `hmac.new` does not exist; it is `hmac.new` is not a thing. The correct API is `hmac.HMAC(key, msg, digestmod)` or the module-level shorthand `hmac.new` which is only valid as `hmac.new` — actually the correct name is just `hmac.new` is an alias for `hmac.HMAC` in CPython, but let me verify.

Re-reading `integrations/medbridge.py:93`:
```python
expected = hmac.new(
    secret.encode(),
    payload,
    hashlib.sha256,
).hexdigest()
```

The Python `hmac` module exposes `hmac.new()` as a module-level function (it is the public API, equivalent to `hmac.HMAC()`). This is actually valid — `hmac.new` is documented. **Not a bug.**

---

### 2. Webhook: `_handle_patient_message` double-reads the request body

**Severity: Negligible in practice**

`webhooks.py:55` reads `body = await request.body()` for HMAC verification, then `webhooks.py:70` calls `await request.json()`. FastAPI/Starlette caches the body after the first read, so the second read works correctly. **Not a bug.**

---

### 3. Webhook: Deduplication check and write are in separate sessions — TOCTOU window

**File:** `src/health_ally/api/routes/webhooks.py:81-106`

**Severity: Low (demo acceptable, but real race)**

The deduplication logic is:
1. Session 1 (line 81): SELECT from `processed_events` — if found, return "duplicate".
2. Handler runs (could take seconds for graph invocation).
3. Session 2 (line 99): INSERT with `ON CONFLICT DO NOTHING`.

Between steps 1 and 3, a concurrent duplicate webhook request can pass the SELECT check, execute the handler, then both writes land (or neither, due to the unique constraint). The `ON CONFLICT DO NOTHING` at line 99 prevents a DB error, but both handler invocations will have already completed (two graph invocations for the same event). For a demo, this is unlikely to trigger, but architecturally it is a race condition.

---

### 4. Scheduler: Jobs marked "processing" before async dispatch — stuck on crash

**File:** `src/health_ally/orchestration/scheduler.py:119-125`

**Severity: Recoverable (startup_recovery handles it)**

Jobs are marked `status="processing"` inside a committed transaction at lines 119-125, then the session is closed. Then `asyncio.gather` dispatches them outside that transaction. If the process crashes between mark and dispatch, jobs are stranded in `"processing"`. `startup_recovery` (`reconciliation.py:32-53`) resets all `"processing"` → `"pending"` on next startup. This is the intended design. **Not a bug, but a known gap that only closes at next startup.**

---

### 5. Scheduler: Unknown job type returns silently, then marked "completed"

**File:** `src/health_ally/orchestration/jobs.py:72-76` + `scheduler.py:172`

**Severity: Demo-relevant (silent data loss)**

`JobDispatcher.dispatch` logs a warning and returns `None` for unknown job types. The caller `_process_single_job` (`scheduler.py:167-172`) then calls `await self._mark_job(job.id, "completed")` unconditionally. An unregistered job type is silently consumed and marked completed without doing any work.

The registered job types are: `day_2_followup`, `day_5_followup`, `day_7_followup`, `backoff_followup`, `onboarding_timeout`. The reconciliation sweep (`reconciliation.py:92`) creates jobs with type `"day_2_followup"` — this is registered, so reconciliation jobs route correctly. **No demo impact today,** but if a job type is misspelled (e.g., in a migration or seed script), it silently vanishes.

---

### 6. Delivery worker: `_handle_delivery_failure` compares attempt_number against wrong constant

**File:** `src/health_ally/orchestration/delivery_worker.py:257-269`

**Severity: Medium — infinite retry potential**

`_MAX_DELIVERY_ATTEMPTS = 5` (line 46) is the module constant. `_handle_delivery_failure` compares `attempt_number >= _MAX_DELIVERY_ATTEMPTS` to decide dead-letter. However, `_record_attempt` returns the current attempt count, which is `COUNT(existing_attempts) + 1`. On the first failure the return is `1`. Only when `attempt_number >= 5` is the entry dead-lettered.

The `OutboxEntry` model has no `max_attempts` column — the limit is hard-coded in the worker. This is consistent, so retry logic works correctly. **Not a bug.**

However: if `_record_attempt` itself raises (DB error during the `INSERT DeliveryAttempt`), the outer `except Exception` at line 179 catches it, calls `_record_attempt` again (line 186), which may raise again, leaving the entry in `"delivering"` status forever — not reset to `"pending"`. The `_recover_stuck_entries` method (line 99-116) resets `"delivering"` entries older than 5 minutes back to `"pending"`, so this is eventually recovered, but only on the next startup (it's called once at startup, line 77).

**Confirmed bug:** `_recover_stuck_entries` is called only once at startup (`delivery_worker.py:77`). If the delivery worker runs continuously for more than 5 minutes after a crash-during-record-attempt, entries stuck in `"delivering"` will not be reclaimed until next restart. For a demo running for hours, this is a real gap.

---

### 7. Delivery worker: `_deliver_single` exception path calls `_record_attempt` which can itself fail

**File:** `src/health_ally/orchestration/delivery_worker.py:179-192`

**Severity: Medium**

In the outer `except Exception` block (line 179), `_record_attempt` is called at line 186. If that fails (e.g., DB down), the exception propagates up, the entry is left in `"delivering"`, and the loop continues to the next entry. The stuck-entry recovery at startup (line 77) is the only backstop.

**Confirmed: `_recover_stuck_entries` is only called once at startup, not periodically.** If a delivery attempt's DB write fails mid-run, the entry sits in `"delivering"` until restart. For a demo this is low risk (DB is local and stable), but it is a correctness gap.

---

### 8. `save_patient_context`: Outbox entry uses sha256 of `outbound_message` as delivery_key — no uniqueness guarantee across invocations with the same message text

**File:** `src/health_ally/agent/nodes/context.py:261-274`

**Severity: Low (demo acceptable)**

`delivery_key = f"{patient_id}:msg:{msg_hash}"` where `msg_hash` is `sha256(outbound)[:16]`. If the coach sends the exact same message text twice (unlikely but possible for short acknowledgements), the second `OutboxEntry` INSERT will fail on the `unique` constraint (`outbox_entries.delivery_key`). This raises an IntegrityError inside `save_patient_context`'s session, rolling back the entire effects flush for that invocation.

**Impact:** A repeated identical message causes the entire `save_patient_context` transaction to roll back — phase transitions, goal writes, scheduled jobs, everything is lost for that invocation. This is silent from the caller's perspective.

---

### 9. Demo "trigger-followup" flow: Races with scheduler's next poll

**File:** `src/health_ally/api/routes/demo.py:145-192`

**Severity: Low (demo acceptable)**

`trigger_followup` sets `scheduled_at = now()` on a pending job. The scheduler polls every 30s (±20% jitter). There is no mechanism to immediately wake the scheduler. After calling trigger-followup, the demo operator must wait up to ~36 seconds for the job to fire. The UI/demo script should account for this delay. **Not a bug, but a UX concern.**

---

### 10. `__main__.py` worker mode: CancelledError does not await shutdown of workers before pool close

**File:** `src/health_ally/__main__.py:150-163`

**Severity: Low**

In `_run_worker`, when `asyncio.CancelledError` is caught (line 156), `shutdown_event.set()` is called on both workers, but `asyncio.gather` has already been cancelled — the workers are not given time to drain their current poll. The `finally` block immediately closes the pool and engine. In contrast, `main.py:lifespan` cancels the whole `worker_task` and relies on the task's own cancellation propagation. Neither approach awaits graceful drain. For a demo this is acceptable — there is no in-flight message that would be irreparably lost (jobs reset on next startup), but it means outbox entries in `"delivering"` at shutdown won't be reset until restart.

---

### 11. `medbridge_webhook`: `tenant_id` defaults to empty string when missing from payload

**File:** `src/health_ally/api/routes/webhooks.py:73`

**Severity: Low**

`tenant_id = str(payload.get("tenant_id", ""))`. If the webhook payload omits `tenant_id`, all DB writes (ProcessedEvent, PatientConsentSnapshot) use `tenant_id=""`. This produces orphaned records with no tenant association but does not crash. **Not a demo-blocker.**

---

### 12. `consent_factory`: Non-dev environments without `medbridge_api_url` silently fall back to FakeConsentService

**File:** `src/health_ally/integrations/consent_factory.py:30-38`

**Severity: Info**

In staging/prod without `medbridge_api_url` configured, a warning is logged but `FakeConsentService(logged_in=True, consented=True)` is returned. This means consent is never actually checked. For the demo (dev environment), this is the intended behavior — consent always passes via `FakeConsentService`. **Not a bug in dev.**

---

## Summary Table

| # | File | Issue | Severity | Demo Impact |
|---|------|-------|----------|-------------|
| 3 | `webhooks.py:81-106` | TOCTOU between dedup check and write | Low | Unlikely to trigger |
| 5 | `jobs.py:72-76` + `scheduler.py:172` | Unknown job type silently marked completed | Low | Silent loss if job type misspelled |
| 6/7 | `delivery_worker.py:77,99-116` | `_recover_stuck_entries` called only at startup, not periodically | Medium | Entries stuck in "delivering" after DB error mid-run until restart |
| 8 | `nodes/context.py:261-274` | Repeated identical message text causes IntegrityError, rolls back entire effects flush | Low | Unlikely for coach messages, but silent data loss if it occurs |
| 10 | `__main__.py:150-163` | CancelledError doesn't await graceful drain | Low | "delivering" entries not reset until restart |

## Current State

- **Scheduler flow** (`scheduler.py` → `jobs.py` → `graph.ainvoke` → `nodes/context.py` → `outbox_entries`): Correct end-to-end. Jobs are claimed with `FOR UPDATE SKIP LOCKED`, dispatched per-patient sequentially, advisory lock is held during graph invocation, outbox entries written atomically in `save_patient_context`.
- **Delivery worker flow** (`delivery_worker.py` → `notification.py`/`alert_channel.py`): Correct. Consent re-check for patient messages. Alert delivery looks up `ClinicianAlert` by `delivery_key == idempotency_key`.
- **Demo trigger-followup flow**: Correct. Sets `scheduled_at=now()`, scheduler picks up on next poll (up to ~36s delay).
- **Webhook handler**: Correct for happy path. TOCTOU dedup race is real but demo-safe.
- **Recovery**: `startup_recovery` resets `"processing"` jobs. `_recover_stuck_entries` resets `"delivering"` outbox but only at startup — periodic recovery is missing.

## Constraints

- `startup_recovery` and `_recover_stuck_entries` are called once at startup; adding periodic calls would require structural changes to the worker loop.
- The advisory lock (`persistence/locking.py`) is session-level PG advisory lock on an AUTOCOMMIT connection — correct per ADR-006. SQLite mode is a no-op.
- Phase transitions are deterministic application code only — immutable rule per `immutable.md:3`.

## Options for `_recover_stuck_entries` (Issue #6/7)

**Option A: No change** — Accept that stuck "delivering" entries recover on restart. For a demo with a stable local DB, DB errors mid-delivery are extremely unlikely. Risk: one missed alert/message per worker crash.

**Option B: Call `_recover_stuck_entries` periodically in the poll loop** — Add a poll-count modulo check (like the scheduler's sweep), e.g., every 60 polls (~5 min). Adds ~5 lines to `delivery_worker.py:run()`.

**Option C: Use a separate background task** — Heavier change, not warranted for current scope.

## Recommendation

For the live demo, **Option A** is acceptable — the local SQLite DB will not have mid-delivery DB errors. The single real fix worth making before demo is documenting the ~36 second scheduler latency after `trigger-followup` so the demo script accounts for it. No source files need editing to unblock the demo.

The one true bug (not a design choice) is the `_recover_stuck_entries` being startup-only, but it only bites under DB failure during delivery attempt recording — a scenario that won't occur in a controlled demo environment.

---

## Persistence Layer Issues

### 1. Current State

#### ORM Models (`src/health_ally/persistence/models.py`)

Ten ORM model classes: `Patient` (line 54), `PatientGoal` (86), `PatientConsentSnapshot` (104), `AuditEvent` (120), `ScheduledJob` (136), `OutboxEntry` (166), `DeliveryAttempt` (192), `ClinicianAlert` (214), `SafetyDecisionRecord` (229), `ProcessedEvent` (246).

Key settings confirmed:
- `expire_on_commit=False` at `db.py:40` — required; multiple sites read ORM scalar attributes after session close
- `lazy="raise"` on all relationships (`models.py:101`, `117`, `210`) — no traversal is possible out-of-session without explicit load
- `pool_pre_ping=True` PostgreSQL only (`db.py:31`); SQLite gets `check_same_thread=False` (`db.py:29`)
- Single Alembic migration: `alembic/versions/d325a08b4b9d_initial_schema.py`

---

### 2. Bugs Found

---

#### PERSISTENCE-BUG-1 (HIGH): Three tables in migration have no ORM model — `--autogenerate` footgun

**Severity: Not demo-blocking today, but a schema management footgun.**

`alembic/versions/d325a08b4b9d_initial_schema.py` creates three tables with no corresponding model in `models.py`:

- `messages` (migration:48–59)
- `tool_invocations` (migration:114–126)
- `conversation_threads` (migration:127–137) — has FK to `patients.id`

No references to these table names exist anywhere in `src/`. They are vestigial from an earlier design iteration. If `alembic revision --autogenerate` is run, it will emit `DROP TABLE` for all three, potentially destroying any externally-written data.

---

#### PERSISTENCE-BUG-2 (HIGH): `ScheduledJob` constructed with `scheduled_at=job.get("scheduled_at")` — `None` causes IntegrityError and full transaction rollback in `save_patient_context`

**Severity: Demo-blocking if any agent tool emits a malformed scheduled_job effect.**

`context.py:240`: `scheduled_at=job.get("scheduled_at")` — can be `None` if the effect dict is malformed or the key is missing. `ScheduledJob.scheduled_at` is `Mapped[datetime]` (non-optional, `models.py:145`), confirmed `NOT NULL` at `migration:186`.

On PostgreSQL: `IntegrityError` rolls back the entire `session.begin()` block at `context.py:104` — outbound message, goal, alerts, phase transition, and all other effects for that invocation are lost silently (only visible in logs).

On SQLite (local dev): aiosqlite may silently accept `NULL` in a `NOT NULL` column, masking the bug until the PostgreSQL deploy.

The `# type: ignore[arg-type]` at `context.py:240` acknowledges the type-unsafety. No runtime guard exists.

---

#### PERSISTENCE-BUG-3 (MEDIUM): ORM attributes read after session close — safe today, brittle coupling

`context.py:58-84`: `Patient` fetched inside `async with ctx.session_factory() as session:` (no `session.begin()`). Session exits at line 60. Attributes `patient.phase`, `patient.unanswered_count`, `patient.last_outreach_at`, `patient.last_patient_response_at` read at lines 73-82 after session close.

Similarly `_get_patient` (`state.py:176-188`) returns a `Patient` from a closed session; callers use `patient.id` and `patient.phase` in new sessions at `state.py:107`, `131`, `156`.

Both patterns are **currently safe** because `expire_on_commit=False` (`db.py:40`) prevents attribute expiry on session close. No relationships are traversed. No runtime bug — architectural smell only.

---

#### PERSISTENCE-BUG-4 (MEDIUM): `SKIP LOCKED` in scheduler and delivery worker crashes silently on SQLite — background workers dead on every poll in local dev

`scheduler.py:111` and `delivery_worker.py:126-127` both use `.with_for_update(skip_locked=True)`. SQLite via aiosqlite raises `OperationalError` (syntax error). The workers catch `Exception` in the poll loop (`scheduler.py:72`, `delivery_worker.py:86`) and retry indefinitely, crashing on each poll.

**Demo impact**: `POST /v1/demo/seed-patient` → chat → GET state endpoints work without background workers. But `POST /v1/demo/trigger-followup/{patient_id}` marks a job due and expects the scheduler to fire it within ~36 seconds — on SQLite the scheduler is crashing and the job never fires.

---

#### PERSISTENCE-BUG-5 (LOW, KNOWN): Identical outbound message text causes `delivery_key` collision → IntegrityError → full `save_patient_context` rollback

`context.py:261-274`: `delivery_key = f"{patient_id}:msg:{sha256(outbound)[:16]}"`. If the LLM produces identical text twice for the same patient, the second `OutboxEntry` INSERT violates `UNIQUE` on `outbox_entries.delivery_key` (`migration:73`). Full transaction rollback — phase transitions, goals, everything lost. Already noted in MEMORY.md.

---

#### PERSISTENCE-BUG-6 (CONFIRMED NON-ISSUE): `OutboxEntry` and `ClinicianAlert` have no FK to `patients.id`

By design — intentional so records survive patient deletion (`models.py:166-173`, `214-219`). Consistent with the `AuditEvent` docstring at `models.py:121`: "No FK to patients (survives deletion)".

---

#### PERSISTENCE-BUG-7 (CONFIRMED NON-ISSUE): `AuditEvent` created in `demo.py` without `metadata_`

`demo.py:130-135` omits `metadata_`. Column is `Mapped[dict | None]` (`models.py:130`) — nullable. Column is aliased `mapped_column("metadata")` to avoid collision with `DeclarativeBase.metadata`. `NULL` is written correctly.

---

### 3. Constraints

1. **`expire_on_commit=False` is mandatory** (`db.py:40`) — multiple callsites read ORM scalars after session close. Reverting this breaks `context.py:73-82` and all `_get_patient` callers immediately.
2. **`lazy="raise"` on all relationships must remain** — prevents silent N+1 in async context.
3. **`metadata_` / `"metadata"` aliasing must be preserved** — `metadata` is a reserved name on `DeclarativeBase`.
4. **Advisory lock acquired at call sites, not in graph nodes** (`locking.py:36-67`) — prevents idle-in-transaction during LLM calls.
5. **`SKIP LOCKED` is PostgreSQL-only** — SQLite dev cannot run scheduler or delivery workers.
6. **Single migration, no down-revision** — schema fixes require a new migration file.

---

### 4. Options

**Option A: Guard PERSISTENCE-BUG-2 only (minimum viable fix)**

In `context.py`, before constructing `ScheduledJob`:

```python
if job.get("scheduled_at") is None:
    logger.error("scheduled_job_missing_scheduled_at", job=job)
    continue
```

Prevents IntegrityError and full-transaction rollback. The malformed job is dropped and logged rather than destroying the entire write. No schema change required.

**Option B: Option A + new migration to drop orphaned tables (PERSISTENCE-BUG-1)**

New Alembic migration drops `messages`, `tool_invocations`, `conversation_threads`. Safe (no ORM or application code references them). Eliminates the `--autogenerate` footgun. Must be applied before next deploy.

**Option C: Option B + SQLite startup warning for PERSISTENCE-BUG-4**

At app startup, if `settings.is_sqlite` and `settings.app_mode` in `("worker", "all")`, emit a structured log warning that background workers will crash and `trigger-followup` will not fire. Does not fix the incompatibility, but surfaces it immediately at startup rather than via repeated poll errors.

---

### 5. Recommendation

**Option B** for the demo:

1. Add `scheduled_at` None guard in `context.py` (PERSISTENCE-BUG-2) — prevents silent full-transaction rollback during a live demo where a malformed scheduled_job effect could destroy all effects of an invocation.
2. New Alembic migration to drop orphaned tables (PERSISTENCE-BUG-1) — eliminates the `--autogenerate` footgun before the team's next schema iteration.

The critical demo path (`seed-patient` → chat → GET phase/goals/alerts) does not require background workers and works correctly on SQLite. PERSISTENCE-BUG-4 is only demo-blocking if the demo explicitly exercises `trigger-followup` against SQLite; if the demo targets Railway/PostgreSQL, it is not present. PERSISTENCE-BUG-3 and PERSISTENCE-BUG-5 require no immediate action.
