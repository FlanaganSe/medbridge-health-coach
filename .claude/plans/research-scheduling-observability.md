# Research: Scheduling, Outbox, and Observability

**Date:** 2026-03-10
**Scope:** Three interconnected systems for M5 (Durable follow-up) and M7 (Release hardening)
**Input:** FINAL_CONSOLIDATED_RESEARCH.md §10, §12, §13; external docs fetched during this session

---

## 1. Current State

Prior research established the high-level direction for all three systems. This document fills in the implementation detail needed to build them.

### What is already decided (FINAL_CONSOLIDATED_RESEARCH.md §10, §12, §13)

- Scheduler: `scheduled_jobs` table + async polling worker — no APScheduler, no cloud scheduler for v1 (`FINAL_CONSOLIDATED_RESEARCH.md:800-805`)
- Schema skeleton already specified: `id`, `patient_id`, `tenant_id`, `job_type`, `scheduled_at`, `status`, `idempotency_key`, `attempts`, `max_attempts`, `metadata`, `created_at`, `started_at`, `completed_at`, `error` (`FINAL_CONSOLIDATED_RESEARCH.md:807-827`)
- Outbox pattern: graph writes to `outbox` table, delivery worker polls and delivers (`FINAL_CONSOLIDATED_RESEARCH.md:974-983`)
- Observability: structlog JSON + OTEL + append-only `audit_events` table (`FINAL_CONSOLIDATED_RESEARCH.md:1012-1040`)
- Audit: REVOKE UPDATE/DELETE enforced at PostgreSQL level (`FINAL_CONSOLIDATED_RESEARCH.md:928`)
- Two connection pools: Pool A (SQLAlchemy) for app queries, Pool B (psycopg3) for LangGraph checkpointer — do NOT share (project MEMORY.md)

---

## 2. System 1: PostgreSQL-Backed Job Scheduling

### 2.1 SELECT FOR UPDATE SKIP LOCKED — Mechanism

`SKIP LOCKED` was introduced in PostgreSQL 9.5. When a `SELECT ... FOR UPDATE` encounters a row already locked by another transaction, `SKIP LOCKED` causes that row to be skipped (not waited upon). This gives each worker an exclusive, non-overlapping batch of jobs with zero blocking between workers.

**Key property:** SKIP LOCKED provides an intentionally inconsistent view of the table — only unlocked rows are visible to the claiming transaction. This is exactly what queue-style work distribution needs.

**SQLAlchemy 2.0 async syntax** (confirmed via sqlalchemy/sqlalchemy#10460 discussion):

```python
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

async def claim_due_jobs(
    session: AsyncSession,
    batch_size: int = 10,
) -> list[ScheduledJob]:
    stmt = (
        select(ScheduledJob)
        .where(ScheduledJob.status == "pending")
        .where(ScheduledJob.scheduled_at <= func.now())
        .order_by(ScheduledJob.scheduled_at)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
```

The `with_for_update(skip_locked=True)` method compiles to `FOR UPDATE SKIP LOCKED` on the PostgreSQL dialect. It behaves identically in async and sync contexts.

**Important:** The claim and the status update to `processing` must happen inside the same transaction. The lock is only held for the transaction's duration — commit or rollback releases it.

### 2.2 Recommended Table Schema (refined)

```sql
CREATE TABLE scheduled_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id UUID NOT NULL REFERENCES patients(id),
    tenant_id UUID NOT NULL,
    job_type VARCHAR(50) NOT NULL,
    -- e.g. 'day_2_followup', 'day_5_followup', 'day_7_followup',
    --      'backoff_check', 'dormant_transition', 'reconciliation'
    idempotency_key VARCHAR(255) NOT NULL,
    -- Stable, deterministic key — see §2.5
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    -- pending | processing | completed | failed | dead
    scheduled_at TIMESTAMPTZ NOT NULL,
    -- UTC. Quiet-hours logic applied BEFORE inserting.
    attempts INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 3,
    metadata JSONB,
    -- References to domain state IDs, job_type-specific params.
    -- Never embed raw message content or PHI here.
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    failed_at TIMESTAMPTZ,
    error TEXT,
    -- Last error message, for operator dead-letter query
    CONSTRAINT uq_idempotency_key UNIQUE (idempotency_key)
);

-- Partial index for the poll query — only covers the hot path
CREATE INDEX idx_scheduled_jobs_due ON scheduled_jobs (scheduled_at, id)
    WHERE status = 'pending';

-- Operator query: find dead-letter jobs
CREATE INDEX idx_scheduled_jobs_dead ON scheduled_jobs (patient_id, created_at)
    WHERE status = 'dead';
```

**Why `status = 'dead'` instead of a separate table:** A single table with a `dead` status is simpler than a separate dead-letter table and is just as queryable. At scale, partition by `status` or archive completed rows to a history table.

### 2.3 Full Poll Worker Pattern

```python
import asyncio
import random
import structlog
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = structlog.get_logger()

POLL_INTERVAL_SECONDS = 30
BATCH_SIZE = 10
STALE_PROCESSING_TIMEOUT_MINUTES = 10  # See §2.4 reconciliation


async def poll_loop(
    session_factory: async_sessionmaker[AsyncSession],
    shutdown_event: asyncio.Event,
) -> None:
    """
    Main poll loop. Runs until shutdown_event is set.
    Each iteration claims a batch of due jobs, processes them
    concurrently, then sleeps. Exceptions in individual jobs
    do not crash the loop.
    """
    log.info("scheduler.worker.started")
    while not shutdown_event.is_set():
        try:
            await _poll_once(session_factory)
        except Exception:
            log.exception("scheduler.worker.poll_error")
        # Jitter: ±20% of poll interval to spread load across workers
        jitter = random.uniform(-0.2 * POLL_INTERVAL_SECONDS, 0.2 * POLL_INTERVAL_SECONDS)
        sleep_time = max(1.0, POLL_INTERVAL_SECONDS + jitter)
        try:
            await asyncio.wait_for(
                asyncio.shield(shutdown_event.wait()),
                timeout=sleep_time,
            )
        except asyncio.TimeoutError:
            pass  # Normal — sleep elapsed without shutdown
    log.info("scheduler.worker.stopped")


async def _poll_once(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        async with session.begin():
            jobs = await claim_due_jobs(session, BATCH_SIZE)
            if not jobs:
                return
            # Mark all claimed jobs as processing atomically
            for job in jobs:
                job.status = "processing"
                job.started_at = func.now()
                job.attempts += 1
            # Commit the status change before processing
            # This releases the SKIP LOCKED row lock
        # Process outside the lock transaction
        tasks = [asyncio.create_task(_execute_job(session_factory, job)) for job in jobs]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for job, result in zip(jobs, results):
            if isinstance(result, Exception):
                log.error(
                    "scheduler.job.uncaught_error",
                    job_id=str(job.id),
                    job_type=job.job_type,
                    exc_info=result,
                )


async def _execute_job(
    session_factory: async_sessionmaker[AsyncSession],
    job: ScheduledJob,
) -> None:
    log = structlog.get_logger().bind(
        job_id=str(job.id),
        job_type=job.job_type,
        patient_id=str(job.patient_id),
    )
    try:
        await dispatch_job(job)  # Domain-specific dispatch
        async with session_factory() as session:
            async with session.begin():
                await session.merge(job)
                job.status = "completed"
                job.completed_at = func.now()
        log.info("scheduler.job.completed")
    except Exception as exc:
        log.exception("scheduler.job.failed", attempts=job.attempts, max_attempts=job.max_attempts)
        async with session_factory() as session:
            async with session.begin():
                await session.merge(job)
                if job.attempts >= job.max_attempts:
                    job.status = "dead"
                    job.failed_at = func.now()
                    job.error = str(exc)
                    log.error("scheduler.job.dead_lettered")
                else:
                    job.status = "pending"
                    # Exponential backoff for retry
                    backoff_seconds = 60 * (2 ** job.attempts)
                    job.scheduled_at = func.now() + timedelta(seconds=backoff_seconds)
```

**Graceful shutdown** — use FastAPI lifespan with `asyncio.Event`:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    shutdown_event = asyncio.Event()
    worker_task = asyncio.create_task(
        poll_loop(session_factory, shutdown_event)
    )
    yield
    shutdown_event.set()
    try:
        await asyncio.wait_for(worker_task, timeout=30.0)
    except asyncio.TimeoutError:
        log.warning("scheduler.worker.shutdown_timeout")
        worker_task.cancel()
```

### 2.4 Job Lifecycle and Reconciliation

**Lifecycle states:**

```
pending → processing → completed
                     ↘ pending (retry, scheduled_at bumped by backoff)
                     ↘ dead (max_attempts exceeded)
```

**The stale `processing` problem:** If the worker process crashes after claiming a job (changing it to `processing`) but before completing it, the job is stuck in `processing` indefinitely. It will never be re-claimed by `SELECT ... WHERE status = 'pending'`.

**Solution — startup reconciliation** (run once at worker startup, before the poll loop):

```python
async def reconcile_stale_jobs(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """
    Reset processing jobs that have been stuck for longer than the stale timeout.
    Run once at startup. This is safe because:
    - Worker was not running (process crashed) so no lock contention
    - Idempotency keys prevent double-execution if the job was actually completed
      via a side effect that succeeded before the crash
    """
    stale_cutoff = datetime.now(UTC) - timedelta(minutes=STALE_PROCESSING_TIMEOUT_MINUTES)
    async with session_factory() as session:
        async with session.begin():
            stmt = (
                select(ScheduledJob)
                .where(ScheduledJob.status == "processing")
                .where(ScheduledJob.started_at < stale_cutoff)
                .with_for_update(skip_locked=True)
            )
            result = await session.execute(stmt)
            stale = result.scalars().all()
            for job in stale:
                job.status = "pending"
                job.scheduled_at = func.now()  # Immediately due for retry
                log.warning(
                    "scheduler.reconciliation.reset_stale",
                    job_id=str(job.id),
                    job_type=job.job_type,
                )
    log.info("scheduler.reconciliation.complete", stale_count=len(stale))
```

**Why startup-only reconciliation is sufficient here:** At the scale of this MVP (day-granularity scheduling, low patient volume), a crash will be detected quickly and the service restarted. For higher-scale multi-worker deployments, add a periodic reconciliation sweep every few minutes.

### 2.5 Idempotency Keys

**Pattern:** Construct a deterministic, stable key from the logical job identity — not a random UUID.

```python
def make_followup_key(patient_id: str, job_type: str, reference_date: date) -> str:
    """
    Stable key for a scheduled follow-up job.
    Re-scheduling the same logical job produces the same key, so INSERT
    with ON CONFLICT DO NOTHING prevents duplicates without a query first.

    Examples:
      patient_id:day_2_followup:2026-03-10
      patient_id:day_5_followup:2026-03-10
      patient_id:backoff_check:2026-03-10:attempt_2
    """
    return f"{patient_id}:{job_type}:{reference_date.isoformat()}"
```

**Inserting with idempotency:**

```python
async def schedule_job(session: AsyncSession, job: ScheduledJob) -> bool:
    """
    Returns True if the job was newly scheduled, False if it already existed.
    """
    stmt = pg_insert(ScheduledJob).values(
        **job.__dict__
    ).on_conflict_do_nothing(index_elements=["idempotency_key"])
    result = await session.execute(stmt)
    return result.rowcount > 0
```

This uses PostgreSQL's `INSERT ... ON CONFLICT DO NOTHING`, which is atomic and requires no optimistic lock or pre-query check.

### 2.6 Timezone, Quiet Hours, and DST

**Storage:** Always store `scheduled_at` as `TIMESTAMPTZ` (UTC) in PostgreSQL. Store the patient's IANA timezone string (e.g., `"America/New_York"`) in the patient profile. Never store timezone-naive datetimes.

**Python:** Use `zoneinfo.ZoneInfo` (stdlib, Python 3.9+). Add `tzdata` as a dependency for cross-platform compatibility (required on Windows, recommended on containers that may strip system tzdata).

**Quiet hours calculation pattern:**

```python
from datetime import datetime, date, timedelta, time
from zoneinfo import ZoneInfo
import random

QUIET_HOURS_START = time(21, 0)  # 9 PM local
QUIET_HOURS_END = time(8, 0)     # 8 AM local
PREFERRED_SEND_HOUR = 10         # 10 AM local (default)

def calculate_send_time(
    base_date: date,
    patient_tz: str,
    preferred_hour: int = PREFERRED_SEND_HOUR,
    jitter_minutes: int = 30,
) -> datetime:
    """
    Compute a UTC-aware scheduled_at for a given date and patient timezone.
    Respects quiet hours and adds jitter.

    Args:
        base_date: The calendar date the message should go out (in patient local time).
        patient_tz: IANA timezone string from patient profile.
        preferred_hour: Target local hour (0-23).
        jitter_minutes: Maximum random jitter in minutes.

    Returns:
        UTC-aware datetime safe to store in scheduled_at.
    """
    tz = ZoneInfo(patient_tz)
    # Build naive local datetime at preferred hour
    local_dt = datetime(
        base_date.year, base_date.month, base_date.day,
        preferred_hour, 0, 0,
        tzinfo=tz,
    )
    # Add random jitter to spread load
    jitter = timedelta(minutes=random.randint(0, jitter_minutes))
    local_dt += jitter

    # Enforce quiet hours: if in quiet window, bump to 8 AM next day
    local_time = local_dt.time()
    if local_time >= QUIET_HOURS_START or local_time < QUIET_HOURS_END:
        if local_time >= QUIET_HOURS_START:
            # After 9 PM — schedule for 8 AM tomorrow
            next_day = base_date + timedelta(days=1)
        else:
            # Before 8 AM — schedule for 8 AM today
            next_day = base_date
        local_dt = datetime(
            next_day.year, next_day.month, next_day.day,
            QUIET_HOURS_END.hour, 0, 0,
            tzinfo=tz,
        )
        # Re-add jitter after quiet-hours adjustment
        local_dt += timedelta(minutes=random.randint(0, jitter_minutes))

    # Convert to UTC — ZoneInfo handles DST transitions correctly
    return local_dt.astimezone(ZoneInfo("UTC"))
```

**DST safety:** `ZoneInfo` handles DST transitions correctly when you construct a timezone-aware datetime directly with `tzinfo=ZoneInfo(tz)` and then call `.astimezone(UTC)`. The stdlib handles the ambiguous fold case with `fold=0` (pre-transition) as default.

**Jitter rationale:** For day-scale scheduling, 0–30 minutes of uniform random jitter is sufficient to prevent thundering herd when many patients have the same day offset (e.g., everyone enrolled on the same day getting their Day 2 followup simultaneously). Larger jitter (up to 60 minutes) is appropriate for larger cohorts.

### 2.7 Exponential Backoff for Unanswered Outreach

```
attempt 0 → schedule: now + 2 days (Day 2 followup)
attempt 1 → no response → schedule: now + 2 days (Day 4)
attempt 2 → no response → schedule: now + 4 days (Day 8)
attempt 3 → no response → alert_clinician + DORMANT transition
```

Each backoff creates a new `scheduled_jobs` row with a new idempotency key scoped to the attempt number (e.g., `{patient_id}:backoff_check:{enrollment_date}:attempt_2`). If the patient responds, cancel pending backoff jobs by setting their status to `completed` with a `cancelled_by_patient_response` note in the metadata.

---

## 2 Options: Custom Worker vs Procrastinate

| Criterion | Custom `scheduled_jobs` worker | Procrastinate 3.7.x |
|---|---|---|
| Dependencies | Zero new deps | `procrastinate` package |
| Idempotency keys | Explicit application code | Built-in `queueing_lock` |
| Custom job table schema | Full control | Fixed schema |
| SQLAlchemy async integration | Native | Procrastinate uses asyncpg directly |
| SKIP LOCKED | Manually implemented | Built-in |
| Reconciliation on restart | Write yourself | Built-in |
| Day-scale scheduling precision | More than sufficient | More than sufficient |
| Audit integration | Same DB, same transaction | Same DB |
| Operator tooling | SQL queries | Built-in admin + Django admin |

**Recommendation:** Custom worker. At the MVP scale (day-granularity, low volume), the custom pattern is 150–200 lines of well-tested Python. It is transparent, fully typed, and lives in the same ORM / session lifecycle as the rest of the app. Procrastinate is the right choice only if the custom worker starts to accumulate complexity (PRD §8.2: "revisit Procrastinate only if custom scheduling code starts to dominate implementation complexity"). The custom approach avoids a second async driver (asyncpg) alongside psycopg3.

---

## 3. System 2: Outbox Pattern

### 3.1 What the Outbox Solves

Without the outbox: if the graph generates a message and calls the delivery API in the same execution path, a crash after generation but before delivery produces a lost message. If delivery is called before the graph state is committed, a crash after delivery but before commit produces a duplicate.

The outbox solves this by making message generation and outbox record insertion happen in one atomic transaction with the rest of the domain state write. The delivery worker runs separately.

### 3.2 Table Schema

```sql
CREATE TABLE outbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    delivery_key VARCHAR(255) NOT NULL,
    -- Stable idempotency key; same pattern as scheduled_jobs
    -- e.g. "{patient_id}:followup_message:{job_id}:{attempt}"
    message_type VARCHAR(50) NOT NULL,
    -- 'patient_message' | 'clinician_alert'
    priority VARCHAR(20) NOT NULL DEFAULT 'routine',
    -- 'urgent' (crisis) | 'routine'
    channel VARCHAR(50) NOT NULL,
    -- 'sms' | 'push' | 'webhook' | 'email'
    payload JSONB NOT NULL,
    -- Delivery-specific envelope. NO raw patient message content in prod logs.
    -- For patient_message: {recipient_id, message_ref_id} — reference, not content
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    -- pending | delivering | delivered | failed | dead
    attempts INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 5,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at TIMESTAMPTZ,
    failed_at TIMESTAMPTZ,
    next_attempt_at TIMESTAMPTZ,
    delivery_receipt JSONB,
    -- Provider-side receipt (e.g., Twilio SID, push notification receipt)
    error TEXT,
    CONSTRAINT uq_outbox_delivery_key UNIQUE (delivery_key)
);

CREATE INDEX idx_outbox_pending ON outbox (priority DESC, created_at)
    WHERE status = 'pending' AND (next_attempt_at IS NULL OR next_attempt_at <= now());
```

**`sequence_id` vs `created_at` ordering:** Use `created_at` for ordering at this scale. A `BIGSERIAL sequence_id` is useful when concurrent inserts need strict ordering guarantees (event sourcing). For a simple polling delivery worker at MVP scale, `created_at` is sufficient.

### 3.3 Writing to the Outbox from LangGraph

**Transaction boundary rule:** The outbox INSERT must be in the same SQLAlchemy transaction as any domain state change that logically causes the message. If the graph node calls `set_goal()`, the goal record INSERT and the outbox INSERT must commit atomically.

```python
# Inside a LangGraph tool or node (runs in Pool A — SQLAlchemy session)
async def write_outbox_message(
    session: AsyncSession,
    patient_id: str,
    job_id: str,
    message_ref_id: str,
    channel: str,
    priority: str = "routine",
) -> None:
    """
    Write an outbound message intent to the outbox.
    Called within the same session.begin() as any related domain writes.
    """
    delivery_key = f"{patient_id}:followup_message:{job_id}"
    entry = OutboxEntry(
        patient_id=patient_id,
        delivery_key=delivery_key,
        message_type="patient_message",
        priority=priority,
        channel=channel,
        payload={"message_ref_id": message_ref_id},
        # message_ref_id points to the stored message in a messages table
        # NOT the raw text — raw text stays out of the outbox
    )
    session.add(entry)
    # No flush/commit here — caller owns the transaction
```

**Do NOT deliver directly from graph nodes.** The node's role ends at writing to the outbox. Actual delivery happens in the delivery worker.

### 3.4 Delivery Worker

The delivery worker is structurally identical to the job scheduler worker — same SKIP LOCKED pattern, same session-per-task approach. Key differences:

- **Poll interval:** 5–10 seconds (patient messages need faster delivery than day-scale job scheduling)
- **Batch size:** Smaller (5–10 entries) to minimize per-worker transaction duration
- **Urgent priority:** Process `priority = 'urgent'` entries first (ORDER BY priority DESC, created_at ASC)
- **Retry backoff:** Exponential, capped at 5 minutes for routine; urgent alerts retry immediately up to `max_attempts`

```python
async def _poll_outbox_once(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        async with session.begin():
            stmt = (
                select(OutboxEntry)
                .where(OutboxEntry.status == "pending")
                .where(
                    (OutboxEntry.next_attempt_at == None) |
                    (OutboxEntry.next_attempt_at <= func.now())
                )
                .order_by(OutboxEntry.priority.desc(), OutboxEntry.created_at.asc())
                .limit(5)
                .with_for_update(skip_locked=True)
            )
            result = await session.execute(stmt)
            entries = result.scalars().all()
            for entry in entries:
                entry.status = "delivering"
                entry.attempts += 1
```

### 3.5 Clinician Alert Durability

Urgent alerts (crisis signals, third unanswered message) follow the same outbox flow but:

1. Written immediately when the crisis/disengagement condition is detected
2. `priority = 'urgent'` ensures they are processed first by the delivery worker
3. `max_attempts` set higher (e.g., 10) for urgent alerts
4. If all attempts fail, `status = 'dead'` — the dead-letter entry is visibly queryable by operators
5. A separate operator alert (e.g., PagerDuty webhook, email) can be triggered when urgent alerts enter dead status

**Crash scenario:** If the process crashes after writing the urgent alert to the outbox but before delivery, the alert survives in the DB and will be delivered when the worker restarts. This is the core value of the outbox over direct delivery.

### 3.6 Delivery Worker vs Job Scheduler: Difference Summary

| Concern | Job Scheduler | Outbox Delivery Worker |
|---|---|---|
| What it processes | Scheduled jobs (Day 2 followup, etc.) | Pending outbound messages |
| Who writes entries | Application code, scheduler logic | LangGraph nodes, alert handlers |
| Poll interval | 30s (day-scale) | 5–10s (message delivery latency) |
| Priority ordering | `scheduled_at ASC` | `priority DESC, created_at ASC` |
| Entry semantics | "Do this work at this time" | "Deliver this message" |

Both use the same SKIP LOCKED pattern. Both can run in the same worker process or separate processes depending on load.

---

## 4. System 3: Observability Stack

### 4.1 structlog Configuration

**Production-ready processor chain:**

```python
import logging
import sys
import structlog
from opentelemetry import trace

def add_otel_trace_context(
    logger: object, method_name: str, event_dict: dict
) -> dict:
    """
    Inject OpenTelemetry trace_id and span_id into every log event.
    Enables log-to-trace correlation in any OTEL-compatible backend.
    """
    span = trace.get_current_span()
    if span and span.get_span_context().is_valid:
        ctx = span.get_span_context()
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def configure_logging(*, dev_mode: bool = False) -> None:
    """
    Call once at application startup (before any loggers are created).
    dev_mode=True: pretty-printed console output with colors
    dev_mode=False: JSON output for log aggregation pipelines
    """
    shared_processors: list[structlog.types.Processor] = [
        # Must be first: merge contextvars (request_id, patient_id, phase, etc.)
        structlog.contextvars.merge_contextvars,
        # Standard enrichment
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        # OTEL trace correlation
        add_otel_trace_context,
        # Exception rendering
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if dev_mode:
        processors = shared_processors + [structlog.dev.ConsoleRenderer(colors=True)]
    else:
        processors = shared_processors + [structlog.processors.JSONRenderer()]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Bridge stdlib logging through structlog for third-party libraries
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO,
    )
    # Suppress uvicorn's duplicate access logs
    logging.getLogger("uvicorn.access").disabled = True
```

**Standard fields on every log line** (from `FINAL_CONSOLIDATED_RESEARCH.md:1022`):
- `timestamp` (ISO 8601, UTC)
- `level`
- `logger` (module name)
- `service` (set via `bind_contextvars` at startup)
- `patient_id` (hashed/pseudonymized UUID — never the real value in logs)
- `request_id`
- `phase`
- `node_name` (inside LangGraph nodes)
- `trace_id` / `span_id` (from OTEL processor above)
- `job_id` (inside job worker context)

### 4.2 Context Binding Pattern (FastAPI Middleware)

```python
import uuid
import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Clear any context from a previous request (critical for async workers)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=str(uuid.uuid4()),
            method=request.method,
            path=request.url.path,
            service="health-coach",
        )
        response: Response = await call_next(request)
        structlog.contextvars.bind_contextvars(status_code=response.status_code)
        return response
```

**Async safety note (from fastapi/fastapi#4696):** In Starlette-based apps (FastAPI), context variables set in a synchronous context do NOT propagate into async handler contexts. Always use `structlog.contextvars` (which uses Python's native `contextvars.ContextVar`) rather than thread-local storage. `clear_contextvars()` at the start of each request is mandatory to prevent context bleed between requests.

**Binding patient context inside graph nodes:**

```python
import structlog

async def onboarding_node(state: PatientState) -> PatientState:
    # Bind node-specific context — available to all log calls within this node
    structlog.contextvars.bind_contextvars(
        node_name="onboarding_node",
        phase=state["phase"],
        # patient_id is a pseudonymized UUID — safe to log
        patient_id=str(state["patient_id"]),
    )
    log = structlog.get_logger()
    log.info("node.entered")
    # ... node logic ...
    return state
```

### 4.3 OTEL Instrumentation Setup

**Packages needed:**
```
opentelemetry-sdk
opentelemetry-api
opentelemetry-exporter-otlp-proto-grpc
opentelemetry-instrumentation-fastapi
opentelemetry-instrumentation-sqlalchemy
opentelemetry-instrumentation-httpx
```

**Programmatic setup** (preferred over `opentelemetry-instrument` CLI wrapper for production control):

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

def configure_otel(service_name: str, otlp_endpoint: str | None = None) -> None:
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    if otlp_endpoint:
        exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)

    # Auto-instrument SQLAlchemy (generates spans for every query)
    # Must be called AFTER the engine is created
    SQLAlchemyInstrumentor().instrument()

    # Auto-instrument outbound HTTP (LLM API calls, webhook deliveries)
    HTTPXClientInstrumentor().instrument()


def configure_fastapi_otel(app: FastAPI) -> None:
    FastAPIInstrumentor.instrument_app(app)
```

**Call order:** `configure_otel()` before `configure_fastapi_otel()`, and both before the first request is processed. Call `SQLAlchemyInstrumentor().instrument(engine=engine)` after the engine is created in the lifespan.

**PHI constraint on OTEL spans:** Do not set span attributes that contain patient names, contact info, or raw message content. Use opaque IDs (`patient_id` as UUID, `job_id`, `request_id`) as span attributes. If using a hosted OTEL backend (e.g., Datadog, Honeycomb), verify BAA coverage before sending spans.

### 4.4 Audit Events Table

**Schema:**

```sql
CREATE TABLE audit_events (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type VARCHAR(50) NOT NULL,
    -- consent_check | message_sent | message_blocked | safety_decision
    -- phase_transition | clinician_alert | tool_invocation | goal_set
    -- job_scheduled | job_completed | job_dead_lettered
    patient_id UUID NOT NULL,
    -- Opaque UUID — matches patient record. NEVER log names here.
    tenant_id UUID,
    conversation_id UUID,
    actor VARCHAR(100),
    -- 'system' | 'graph:node_name' | 'worker:job_type'
    outcome VARCHAR(50) NOT NULL,
    -- pass | fail | blocked | escalated | completed | cancelled
    metadata JSONB,
    -- Event-type-specific safe fields. No raw message content.
    -- For safety_decision: {classifier_version, decision, retry_attempted}
    -- For phase_transition: {from_phase, to_phase, trigger}
    -- For consent_check: {check_source, failure_reason}
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    service_version VARCHAR(50)
    -- App version tag for audit trail reproducibility
);

-- Immutability enforced at DB level (run in migration, as superuser or table owner):
-- REVOKE UPDATE, DELETE ON audit_events FROM health_coach_app;
-- REVOKE TRUNCATE ON audit_events FROM health_coach_app;

-- Query indexes
CREATE INDEX idx_audit_events_patient ON audit_events (patient_id, occurred_at DESC);
CREATE INDEX idx_audit_events_type ON audit_events (event_type, occurred_at DESC);
CREATE INDEX idx_audit_events_time ON audit_events (occurred_at DESC);
```

**Enforcing immutability:**

```sql
-- Run as DB superuser after creating the table.
-- The app connects as 'health_coach_app' role.
REVOKE UPDATE ON audit_events FROM health_coach_app;
REVOKE DELETE ON audit_events FROM health_coach_app;
REVOKE TRUNCATE ON audit_events FROM health_coach_app;

-- Optional belt-and-suspenders: PostgreSQL row-level trigger
-- Prevents UPDATE/DELETE even from roles with broader grants.
CREATE OR REPLACE FUNCTION audit_events_immutable()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only';
END;
$$;

CREATE TRIGGER enforce_audit_immutability
BEFORE UPDATE OR DELETE ON audit_events
FOR EACH ROW EXECUTE FUNCTION audit_events_immutable();
```

**Why both REVOKE and trigger:** REVOKE is the primary control and the correct place. The trigger adds defense-in-depth against privilege escalation or accidental `GRANT` in a future migration.

**Emitting audit events from LangGraph nodes:**

```python
import structlog
from datetime import datetime, timezone

log = structlog.get_logger()

async def emit_audit_event(
    session: AsyncSession,
    *,
    event_type: str,
    patient_id: str,
    outcome: str,
    conversation_id: str | None = None,
    actor: str = "system",
    metadata: dict | None = None,
) -> None:
    """
    Append an audit event. Must be called within an open session.begin() block.
    The caller owns the transaction — this function does not commit.
    """
    event = AuditEvent(
        event_type=event_type,
        patient_id=patient_id,
        conversation_id=conversation_id,
        actor=actor,
        outcome=outcome,
        metadata=metadata or {},
        occurred_at=datetime.now(timezone.UTC),
    )
    session.add(event)
    # Structural log for immediate observability (not HIPAA audit)
    log.info(
        "audit_event.emitted",
        event_type=event_type,
        patient_id=patient_id,  # UUID only — safe to log
        outcome=outcome,
        actor=actor,
    )
```

**Rule:** Every call to `emit_audit_event()` must be inside a `session.begin()` block that also includes the domain operation being audited. This ensures the audit event and the domain state change commit atomically. If the domain operation fails and rolls back, the audit event also rolls back — no orphan audit events.

**What to put in `metadata`:**

| `event_type` | Safe metadata fields | Excluded |
|---|---|---|
| `consent_check` | `check_source`, `failure_reason` | Names, contact info |
| `safety_decision` | `classifier_version`, `decision`, `retry_attempted`, `fallback_used` | Message content |
| `phase_transition` | `from_phase`, `to_phase`, `trigger` | Message content |
| `clinician_alert` | `alert_type` (urgent/routine), `channel`, `delivery_key` | Message content, patient name |
| `tool_invocation` | `tool_name`, `success`, `job_id` | Tool arguments with PHI |
| `job_scheduled` | `job_type`, `scheduled_at`, `idempotency_key` | Patient name |
| `job_completed` | `job_type`, `duration_ms`, `idempotency_key` | — |

### 4.5 PHI-Safe Logging

**Rules (from PRD §5.2 and FINAL_CONSOLIDATED_RESEARCH.md §11.2):**

1. **Never log:** Patient name, DOB, phone number, email, address, raw message text, full goal text, program details that identify the patient
2. **Always use opaque UUIDs:** `patient_id` as UUID in all log fields — never the name or contact info
3. **Hashing for correlation:** If you need to correlate across systems using a stable key, hash the patient UUID with HMAC-SHA256 using a service-level secret key. Never log the raw UUID if it could cross a trust boundary.
4. **Message content:** Reference by `message_ref_id` (a UUID pointing to the stored message). Never log message text in operational logs.
5. **Goal text:** Store in the DB; log only `goal_set` event with `patient_id` and `outcome`. The goal text itself is PHI.

**Processor to scrub accidental PHI leaks (defense-in-depth):**

```python
_PHI_FIELDS = frozenset({"name", "email", "phone", "dob", "message", "goal", "content"})

def scrub_phi_fields(
    logger: object, method_name: str, event_dict: dict
) -> dict:
    """
    Defense-in-depth: redact known PHI field names from log events.
    This is a safety net, NOT a substitute for not logging PHI in the first place.
    """
    for key in _PHI_FIELDS:
        if key in event_dict:
            event_dict[key] = "[REDACTED]"
    return event_dict
```

Add `scrub_phi_fields` to the processor chain BEFORE `JSONRenderer`.

### 4.6 Health Endpoints

**Liveness** (`/health/live`): "Is the process running and not deadlocked?" Should never depend on external services. Returns 200 if the process itself is healthy.

**Readiness** (`/health/ready`): "Can this instance serve traffic right now?" Must check all required dependencies. Returns 503 if any check fails — orchestrators (Kubernetes, Cloud Run) will stop routing traffic to this instance.

```python
from fastapi import FastAPI, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

app = FastAPI()

@app.get("/health/live", tags=["health"])
async def liveness() -> dict:
    """
    Liveness probe. Never fails unless the process itself is broken.
    Do NOT add database checks here.
    """
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def readiness(
    session: AsyncSession = Depends(get_db_session),
    scheduler_running: bool = Depends(get_scheduler_status),
) -> Response:
    """
    Readiness probe. Checks: DB connectivity, schema presence, scheduler worker.
    Returns 503 if any check fails — do NOT return 200 with error details.
    """
    checks: dict[str, bool] = {}

    # Database connectivity
    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:
        checks["database"] = False

    # Scheduler worker running
    checks["scheduler"] = scheduler_running

    all_ok = all(checks.values())
    status_code = 200 if all_ok else 503
    return Response(
        content=json.dumps({"status": "ok" if all_ok else "degraded", "checks": checks}),
        status_code=status_code,
        media_type="application/json",
    )
```

**Important distinctions from PRD §9.4:**
- Liveness must not depend on a live model-provider call
- Readiness covers DB, schema compatibility, and required internal worker dependencies
- Do NOT add a live LLM API check to the readiness probe — this would cause cascading failures during model provider outages

---

## 5. Constraints

1. **Two connection pools must remain separate** — Pool A (SQLAlchemy async) for app queries, Pool B (psycopg3) for LangGraph checkpointer. Mixing them causes lifecycle management conflicts. (project MEMORY.md)
2. **`expire_on_commit=False`** on `async_sessionmaker` — required for async SQLAlchemy sessions to avoid accessing expired attributes after commit. (project MEMORY.md)
3. **`lazy="raise"`** on ORM relationships — prevents accidental implicit lazy-load IO in async paths. (project MEMORY.md)
4. **OTEL spans must not contain PHI** — if a hosted OTEL backend is used, BAA coverage must be confirmed before enabling.
5. **Audit events are permanent** — REVOKE + trigger enforces this. No migration may drop the audit table or alter it in a way that loses rows. HIPAA requires 6-year retention.
6. **Scheduler tests MUST run against PostgreSQL** — `SKIP LOCKED` is not supported in SQLite. Use `pytest-docker` or a real PostgreSQL instance for scheduler integration tests. (project MEMORY.md)
7. **`DEEPEVAL_TELEMETRY_OPT_OUT=1`** (not `YES`) for eval runs. (project MEMORY.md)

---

## 6. Options Summary

### Scheduling: Custom Worker vs Procrastinate

**Recommendation: Custom worker** (see §2, Options table). Procrastinate is the right escalation path if the custom code grows unwieldy.

### Outbox: Polling vs PostgreSQL LISTEN/NOTIFY

**Option A (recommended for MVP):** Simple polling (5–10s interval) with SKIP LOCKED. Straightforward, testable, no additional connection management.

**Option B:** PostgreSQL `LISTEN/NOTIFY` for event-driven wakeup, with polling as fallback. Reduces latency for urgent alerts from ~5s to near-instant. Adds complexity (a persistent LISTEN connection per worker process, asyncpg `connection.add_listener()`). Use Pool B (psycopg3) for LISTEN to avoid conflating with Pool A.

**Trade-off:** For MVP, 5s polling latency for urgent clinician alerts is acceptable. If clinical staff expect near-real-time alerts (< 1s), evaluate LISTEN/NOTIFY in M6.

### Observability: Hosted Backend vs Self-Hosted

**Option A (recommended for MVP):** Emit OTEL spans to stdout/file; aggregate via cloud-native log drain (CloudWatch, Cloud Logging). Zero additional infrastructure.

**Option B:** Self-hosted Arize Phoenix OSS (single Docker container) for LLM-specific tracing metadata. Add in Phase 2 when LLM trace data is needed for debugging.

**Option C:** Langfuse v3. Requires ClickHouse + Redis + S3 — not viable at MVP scale. (FINAL_CONSOLIDATED_RESEARCH.md:1030)

---

## 7. Recommendation

### Immediate (M5)

1. **Implement the custom `scheduled_jobs` polling worker** as specified in §2. Use the exact schema and idempotency key pattern. Write startup reconciliation before the poll loop.
2. **Implement the outbox table and delivery worker** as specified in §3. All outbound messages — including clinician alerts — go through the outbox.
3. **Write quiet-hours / timezone logic** using `zoneinfo.ZoneInfo`. Add `tzdata` to `pyproject.toml` as a runtime dependency.

### Immediate (M1 / M2 foundation)

4. **Configure structlog** using the processor chain in §4.1. Set `dev_mode` based on settings. Add the `add_otel_trace_context` processor.
5. **Create the `audit_events` table** in the first migration batch. Apply `REVOKE` and the immutability trigger in the same migration.
6. **Add OTEL instrumentation** in the lifespan startup: `SQLAlchemyInstrumentor`, `HTTPXClientInstrumentor`, `FastAPIInstrumentor`.
7. **Add `scrub_phi_fields` processor** to the structlog chain as defense-in-depth.
8. **Implement `/health/live` and `/health/ready`** as separate endpoints.

### Deferred

- LISTEN/NOTIFY for urgent alert delivery (evaluate in M6 if alert latency matters to clinicians)
- Arize Phoenix OSS LLM tracing (Phase 2, after MVP)
- Periodic reconciliation sweep (only needed at higher worker counts)

---

## Sources

- [SQLAlchemy SKIP LOCKED discussion (sqlalchemy/sqlalchemy#10460)](https://github.com/sqlalchemy/sqlalchemy/discussions/10460)
- [The Unreasonable Effectiveness of SKIP LOCKED in PostgreSQL — Inferable](https://www.inferable.ai/blog/posts/postgres-skip-locked)
- [Transactional Outbox Pattern — James Carr (2026-01-15)](https://james-carr.org/posts/2026-01-15-transactional-outbox-pattern/)
- [Structured Logging with structlog and FastAPI — Angelos Panagiotopoulos](https://www.angelospanag.me/blog/structured-logging-using-structlog-and-fastapi)
- [How to Structure Logs Properly in Python with OpenTelemetry — OneUptime (2025-01-06)](https://oneuptime.com/blog/post/2025-01-06-python-structured-logging-opentelemetry/view)
- [FastAPI Structured Logging — OneUptime (2026-02-02)](https://oneuptime.com/blog/post/2026-02-02-fastapi-structured-logging/view)
- [OpenTelemetry FastAPI Instrumentation Docs](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/fastapi/fastapi.html)
- [OpenTelemetry SQLAlchemy Instrumentation Docs](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/sqlalchemy/sqlalchemy.html)
- [structlog Context Variables Docs](https://www.structlog.org/en/stable/contextvars.html)
- [zoneinfo — IANA time zone support (Python docs)](https://docs.python.org/3/library/zoneinfo.html)
- [Mastering Exponential Backoff — Better Stack](https://betterstack.com/community/guides/monitoring/exponential-backoff/)
- [Mitigating the Thundering Herd — Medium](https://medium.com/@avnein4988/mitigating-the-thundering-herd-problem-exponential-backoff-with-jitter-b507cdf90d62)
- [PostgreSQL FOR UPDATE SKIP LOCKED — Netdata](https://www.netdata.cloud/academy/update-skip-locked/)
- [FastAPI Health Check Patterns — index.dev](https://www.index.dev/blog/how-to-implement-health-check-in-python)
- [FastAPI async dependencies with structlog.contextvars (fastapi/fastapi#5999)](https://github.com/fastapi/fastapi/discussions/5999)
- [Mastering the Outbox Pattern in Python — Medium](https://medium.com/israeli-tech-radar/mastering-the-outbox-pattern-in-python-a-reliable-approach-for-financial-systems-2a531473eaa5)
- Project internal: `FINAL_CONSOLIDATED_RESEARCH.md` §10, §12, §13
- Project internal: `prd.md` §8–9 (architecture decisions)
