# PHI Data Flow Documentation

**Last updated:** 2026-03-10
**Status:** MVP — controlled launch

## Overview

This document maps where Protected Health Information (PHI) enters, flows through, and exits the Health Ally system. It satisfies PRD AC-13 (PHI-safe logging) and supports HIPAA compliance review.

## PHI Classification

| Data Element | PHI? | Where Stored | Retention |
|---|---|---|---|
| Patient name, email, phone | Yes | **Not stored** — lives in MedBridge Go only | N/A |
| Patient messages (free text) | Yes | LangGraph checkpointer (conversation replay) | TBD — retention policy pending |
| Coach responses (generated text) | Yes (may reference health) | LangGraph checkpointer | TBD |
| Patient goals (structured) | Yes | `patient_goals` table | Duration of care |
| Safety decision reasoning | Likely (LLM may quote patient text) | `safety_decision_records` table | 6 years (HIPAA) |
| Audit events | Metadata only | `audit_events` table | 6 years (HIPAA) |
| Clinician alert reasons | Possibly | `clinician_alerts` table | 6 years (HIPAA) |
| Patient phase | No (enum) | `patients` table | Duration of care |
| Consent snapshots | No (boolean) | `patient_consent_snapshots` table | 6 years |

## Data Flow Diagram

```
┌──────────────────────────────────────────────────────┐
│                    ENTRY POINTS                       │
│                                                       │
│  MedBridge Go Webhook ──► POST /webhooks/medbridge    │
│  (patient messages,         (HMAC verified)           │
│   consent changes)                                    │
│                                                       │
│  Demo Chat UI ──────► POST /v1/chat                   │
│  (dev/staging only)      (SSE streaming)              │
│                                                       │
│  Scheduler ──────────► graph.ainvoke()                 │
│  (proactive outreach)    (no patient input)            │
└──────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────┐
│                 PROCESSING LAYER                      │
│                                                       │
│  consent_gate ─► crisis_check ─► phase_router         │
│       │              │               │                │
│  Consent DB      LLM classifier   Pure Python         │
│  (no PHI in      (PHI in prompt   (no PHI)            │
│   log output)     memory only)                        │
│                      │                                │
│              ┌───────┴────────┐                       │
│         EXPLICIT crisis    NONE/POSSIBLE               │
│              │                    │                    │
│         Durable alert        Phase nodes               │
│         (DB write)          (LLM generation)           │
│                                  │                    │
│                            safety_gate                 │
│                           (LLM classifier)             │
│                                  │                    │
│                        save_patient_context             │
│                        (atomic DB write)               │
└──────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────┐
│                   EXIT POINTS                         │
│                                                       │
│  Outbox ──► Delivery Worker ──► NotificationChannel   │
│  (DB table)   (consent re-check)  (push notification) │
│                                                       │
│  Outbox ──► Delivery Worker ──► AlertChannel          │
│              (no consent check)   (clinician webhook) │
│                                                       │
│  Structured Logs ──► Log aggregator                   │
│  (NO PHI — scrubbed)                                  │
│                                                       │
│  LangGraph Checkpointer ──► PostgreSQL                │
│  (conversation state — contains PHI)                  │
└──────────────────────────────────────────────────────┘
```

## PHI Boundaries

### Where PHI EXISTS in memory
1. **LLM prompt/response** — patient messages and coach responses pass through LLM calls
2. **LangGraph state dict** — `messages` field contains conversation history during graph execution
3. **Outbox payload** — `outbox_entries.payload` contains the coach message text
4. **Clinician alert** — `clinician_alerts.reason` may contain patient text snippets (truncated to 200 chars)

### Where PHI is STORED persistently
1. **LangGraph checkpointer** — conversation replay blobs in PostgreSQL
2. **`patient_goals` table** — structured goal text extracted from patient input
3. **`outbox_entries` table** — message payload (delivered then retained for audit)
4. **`clinician_alerts` table** — alert reason text
5. **`safety_decision_records` table** — classifier reasoning

### Where PHI must NEVER appear
1. **Structured logs** — `scrub_phi_fields` processor strips known PHI fields as defense-in-depth
2. **Request/response middleware logs** — bodies are never logged (code-level prohibition)
3. **Error tracking** — exception messages are logged but message content is not bound to structlog context
4. **Metrics** — only counts, durations, and enum values

## Logging Safeguards

### structlog Processor Chain
```
merge_contextvars → add_log_level → add_logger_name → TimeStamper
    → otel_trace → StackInfoRenderer → format_exc_info → scrub_phi_fields
```

`scrub_phi_fields` runs **last** — after `format_exc_info` renders exception text into string form, so PHI in exception messages is also scrubbed. The processor:
- **Strips known PHI field names**: `message_content`, `patient_name`, `email`, `phone`, `body`, `diagnosis`, `medication`, `treatment`, `symptoms`, etc.
- **Pattern-matches values**: SSN patterns (`\d{3}-\d{2}-\d{4}`), email addresses
- **Replaces with `[REDACTED]`** — not deleted, so log structure is preserved for debugging

### Code-Level Prohibitions
- Request/response bodies are never bound to structlog context
- `RequestLoggingMiddleware` clears contextvars per-request (prevents cross-request PHI bleed in async)
- Log statements bind only opaque identifiers: `patient_id` (UUID), `tenant_id`, `job_id`, `outbox_id`

## Compliance Notes

- **AC-13**: Verified by `scrub_phi_fields` processor + codebase audit showing no PHI in log statements
- **AC-15**: Non-production workflows use `FakeConsentService` and synthetic data only
- **HIPAA Audit Trail**: `audit_events` table is append-only (no UPDATE/DELETE in application code)
- **Retention**: Audit events have 6-year HIPAA minimum. Conversation checkpoint retention policy is TBD (requires organizational decision — see Open Questions in plan.md)
