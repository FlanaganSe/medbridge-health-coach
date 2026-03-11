# Researcher Memory — Health Ally (demo-ui workspace)

## Project Layout
- Backend source: `/Users/seanflanagan/proj/medbridge-health-coach/src/health_ally/`
- Demo UI: `/Users/seanflanagan/proj/medbridge-health-coach/demo-ui/`
- Plans output: `/Users/seanflanagan/proj/medbridge-health-coach/demo-ui/.claude/plans/`

## Key Orchestration Patterns
- Scheduler claims jobs with `FOR UPDATE SKIP LOCKED`, marks "processing" in one transaction, dispatches outside it. `startup_recovery` resets stranded "processing" jobs on next boot.
- Delivery worker: `_recover_stuck_entries` is called ONLY at startup (not periodically). Stuck "delivering" entries only recover on restart.
- Advisory lock: session-level PG advisory lock on AUTOCOMMIT connection, no-op for SQLite. Key derived from `hashlib.sha256`, not `hash()`.
- `save_patient_context` is the sole domain DB writer for graph nodes. Outbox entry delivery_key = `{patient_id}:msg:{sha256(message)[:16]}` — identical message text causes IntegrityError + full rollback.

## Known Issues Found (2026-03-11)
- `_recover_stuck_entries` is startup-only — periodic recovery missing (delivery_worker.py:77)
- Unknown job type dispatched → silently marked "completed" (jobs.py:72-76 + scheduler.py:172)
- Webhook dedup: TOCTOU between SELECT and INSERT for ProcessedEvent (webhooks.py:81-106)
- Demo trigger-followup has up to ~36s scheduler latency before job fires

## Dev Environment Behavior
- `environment=dev` or `is_sqlite=True` → FakeConsentService (always allowed)
- All channels are Mock (MockNotificationChannel, MockAlertChannel) — no external transport
- HMAC webhook signature verification skipped if `medbridge_webhook_secret` not set in dev
