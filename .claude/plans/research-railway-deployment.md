# Railway Deployment Research

## 1. Current State

### What Already Exists

**`railway.toml`** (project root, lines 1–8):
```toml
[build]
builder = "dockerfile"

[deploy]
healthcheckPath = "/health/live"
healthcheckTimeout = 30
startCommand = "alembic upgrade head && python -c 'import asyncio; from health_coach.persistence.db import run_bootstrap; from health_coach.settings import Settings; asyncio.run(run_bootstrap(Settings()))' && python -m health_coach"
```

**`Dockerfile`** (project root, lines 1–42):
- Two-stage build: builder (`python:3.12-slim`) + runtime (`python:3.12-slim`)
- uv pinned at `ghcr.io/astral-sh/uv:0.10` for dep caching
- Installs only prod deps (`--no-dev`) in stage 1
- Copies `.venv`, `src/`, `alembic/`, `alembic.ini` to stage 2
- Runs as non-root `appuser` (uid/gid 1000)
- Exposes port 8000
- CMD: `python -m health_coach` (which defaults to `--mode all`)

**What "all" mode does** (`__main__.py:57–163`):
- Reads `APP_MODE` env var or CLI `--mode` flag
- Mode `all` = uvicorn HTTP server + background workers in one process
- Mode `api` = HTTP server only
- Mode `worker` = background workers only (no HTTP)

---

## 2. All Environment Variables

### Source: `settings.py:11–80`

| Variable | Default | Required | Notes |
|---|---|---|---|
| `ENVIRONMENT` | `"dev"` | No | Must be `"staging"` or `"prod"` for Railway prod; controls webhook secret enforcement, consent service, demo route exposure |
| `DEBUG` | `False` | No | Keep `False` in prod |
| `DATABASE_URL` | `"sqlite+aiosqlite:///./health_coach.db"` | **Yes for prod** | Railway sets `DATABASE_URL` automatically for linked PostgreSQL plugin. The `normalize_postgres_scheme` validator rewrites `postgresql://` and `postgres://` to `postgresql+psycopg://` — Railway's format is handled |
| `DB_POOL_SIZE` | `5` | No | Pool A (SQLAlchemy). Adjust for load |
| `DB_MAX_OVERFLOW` | `5` | No | Pool A overflow |
| `LANGGRAPH_POOL_SIZE` | `3` | No | Pool B (psycopg3) for LangGraph checkpointer |
| `LOG_LEVEL` | `"INFO"` | No | |
| `LOG_FORMAT` | `"console"` | No | Set to `"json"` for Railway (structured JSON logs are searchable) |
| `ANTHROPIC_API_KEY` | `""` | **Yes** | Powers coach, safety classifier, goal extractor; `SecretStr` |
| `OPENAI_API_KEY` | `""` | No | Only used when `FALLBACK_PHI_APPROVED=True` |
| `DEFAULT_MODEL` | `"claude-sonnet-4-6"` | No | Override for cost/speed tradeoffs |
| `SAFETY_CLASSIFIER_MODEL` | `"claude-haiku-4-5-20251001"` | No | Haiku 3 retires Apr 20 2026 — do NOT change to `claude-3-haiku` |
| `MAX_TOKENS` | `1024` | No | ChatAnthropic `max_tokens` |
| `FALLBACK_PHI_APPROVED` | `False` | No | Set `True` only if OpenAI has signed BAA; enables gpt-4o fallback |
| `QUIET_HOURS_START` | `21` | No | Hour (0–23) |
| `QUIET_HOURS_END` | `8` | No | Hour (0–23) |
| `DEFAULT_TIMEZONE` | `"America/New_York"` | No | |
| `SCHEDULER_POLL_INTERVAL_SECONDS` | `30` | No | |
| `DELIVERY_POLL_INTERVAL_SECONDS` | `5` | No | |
| `MEDBRIDGE_API_URL` | `""` | No* | *Required for real consent checking in staging/prod; omitting falls back to `FakeConsentService` with a warning |
| `MEDBRIDGE_API_KEY` | `""` | No* | *Required with `MEDBRIDGE_API_URL`; `SecretStr` |
| `MEDBRIDGE_WEBHOOK_SECRET` | `""` | No* | *Required in non-dev environments — webhook handler raises HTTP 500 if missing when `ENVIRONMENT != "dev"` (see `webhooks.py:60–63`) |
| `APP_MODE` | `"all"` | No | `all` / `api` / `worker` |
| `HOST` | `"0.0.0.0"` | No | Already correct for containers |
| `PORT` | `8000` | No | Matches `EXPOSE 8000` in Dockerfile |
| `CORS_ORIGINS` | `["http://localhost:5173"]` | No | Set appropriately if demo UI is accessed cross-origin in staging |

### Minimum viable set for Railway prod:
```
DATABASE_URL         (auto-injected by Railway PostgreSQL plugin)
ANTHROPIC_API_KEY    (secret)
ENVIRONMENT          staging | prod
LOG_FORMAT           json
MEDBRIDGE_WEBHOOK_SECRET  (required if ENVIRONMENT != dev)
```

---

## 3. External Service Dependencies

| Service | How Used | Where Configured |
|---|---|---|
| **PostgreSQL** | Domain DB (Pool A via SQLAlchemy) + LangGraph checkpointer (Pool B via psycopg3) | `DATABASE_URL` |
| **Anthropic API** | Coach LLM, safety classifier, goal extractor | `ANTHROPIC_API_KEY` |
| **OpenAI API** | Fallback LLM only, opt-in | `OPENAI_API_KEY` + `FALLBACK_PHI_APPROVED=True` |
| **MedBridge Go API** | Consent checks (GET `/api/v1/patients/{id}/consent`) | `MEDBRIDGE_API_URL` + `MEDBRIDGE_API_KEY` |

---

## 4. Database Connection Details

### How the app discovers the database URL
- `settings.py:25`: default is SQLite
- `settings.py:62–70`: `normalize_postgres_scheme` validator rewrites `postgresql://` and `postgres://` → `postgresql+psycopg://` automatically
- Railway PostgreSQL plugin injects `DATABASE_URL` in the form `postgresql://user:pass@host:port/db` — the validator handles this

### Two pools are created on PostgreSQL

**Pool A** (`db.py:24–35`) — SQLAlchemy `AsyncEngine`:
- Used for all domain DB reads/writes (patient records, jobs, outbox, audit)
- `pool_pre_ping=True`, configurable size
- URL scheme: `postgresql+psycopg://`

**Pool B** (`db.py:43–65`) — psycopg3 `AsyncConnectionPool`:
- Used exclusively by LangGraph `AsyncPostgresSaver`
- `autocommit=True`, `prepare_threshold=0`, `row_factory=dict_row` (mandatory)
- `open=False` in constructor; opened explicitly in lifespan via `await langgraph_pool.open(wait=True)`
- `create_langgraph_pool()` strips `+psycopg` back to plain `postgresql://` for psycopg3

### What happens if `DATABASE_URL` is SQLite
- Pool B is `None` (returns early at `db.py:48`)
- Checkpointer falls back to `MemorySaver` (state lost on restart)
- `startup_recovery`, scheduler, and delivery worker use SKIP LOCKED which **crashes on SQLite** (memory notes confirm `scheduler.py:111`, `delivery_worker.py:126`)
- Advisory lock is skipped (SQLite branch in `locking.py:49`)
- Conclusion: SQLite is dev-only; Railway deployment must use PostgreSQL

---

## 5. Startup Sequence

The `startCommand` in `railway.toml:7` runs three steps in series:

**Step 1: `alembic upgrade head`**
- Reads `DATABASE_URL` from env via `alembic/env.py:26–32`
- Creates all domain tables (9 tables in `d325a08b4b9d` migration)
- Idempotent — safe to run on every deploy
- Uses `NullPool` during migration to avoid keeping connections

**Step 2: `python -c 'asyncio.run(run_bootstrap(Settings()))'`**
- `db.py:95–113`: calls `AsyncPostgresSaver(pool).setup()`
- Creates 3 LangGraph checkpoint tables: `checkpoints`, `checkpoint_writes`, `checkpoint_blobs`
- Also idempotent (setup is safe to re-run)
- Only runs on PostgreSQL; skips on SQLite

**Step 3: `python -m health_coach`**
- Reads `APP_MODE` env var (default `"all"`)
- In mode `all`: starts uvicorn + background workers
- FastAPI lifespan also calls `AsyncPostgresSaver(langgraph_pool).setup()` again (`main.py:60`) — redundant but harmless

### Full in-process lifespan sequence (`main.py:36–93`)
1. `configure_logging()` — structlog setup
2. `create_engine(settings)` — Pool A
3. `create_session_factory(engine)` — session maker
4. `create_langgraph_pool(settings)` — Pool B (or None)
5. `await langgraph_pool.open(wait=True)` — opens Pool B
6. `AsyncPostgresSaver(langgraph_pool).setup()` — checkpoint tables
7. `_setup_graph_and_context()` — compiles LangGraph, creates context factory
8. If `app_mode == "all"`: spawns background worker `asyncio.Task`
9. Yield (serve requests)
10. On shutdown: cancel worker task, close Pool B, dispose Pool A

---

## 6. Railway Services Needed

### Option A: Single service (current design, `app_mode=all`)
- One Railway service running both HTTP server and background workers
- `APP_MODE=all` (default)
- Simplest deployment, adequate for demo/staging
- Risk: if the service restarts, workers restart too — any in-flight job lost (handled by `startup_recovery`)
- No horizontal scaling of workers independently

### Option B: Two services (HTTP + worker)
- Service 1: `APP_MODE=api` — HTTP only
- Service 2: `APP_MODE=worker` — workers only
- Both services connect to the same PostgreSQL database
- SKIP LOCKED in scheduler/delivery worker is already safe for multiple worker instances
- Advisory lock key is `hashlib.sha256`-based (cross-process deterministic) — safe across instances
- Railway "Worker" service type (no public port needed for service 2)

### Option C: Three services (HTTP + scheduler + delivery)
- Overkill for current scale; background workers are lightweight async tasks

**Minimum for Railway:** 1 service + 1 PostgreSQL plugin

**Recommended for production:** 2 services (api + worker) + 1 PostgreSQL plugin

---

## 7. Health Check Endpoints

`health.py:15–56`:

| Path | Behavior | Railway uses |
|---|---|---|
| `GET /health/live` | Always 200 `{"status": "ok"}`, no DB check | healthcheckPath in `railway.toml` |
| `GET /health/ready` | Checks Pool A (SQLAlchemy `SELECT 1`) + Pool B (psycopg3 `SELECT 1`). Returns 503 if either is unavailable | Readiness only |

Railway is configured to use `/health/live` (`railway.toml:5`), which is correct — liveness never checks DB, preventing Railway from cycling a healthy pod that has a transient DB blip.

---

## 8. Port Configuration

- `settings.py:58–59`: `host = "0.0.0.0"`, `port = 8000`
- `Dockerfile:40`: `EXPOSE 8000`
- `__main__.py:48–53`: uvicorn bound to `settings.host:settings.port`
- Railway detects the exposed port and routes traffic to it
- Railway also injects a `PORT` env var — **the app does NOT read `PORT` from env**; it reads `settings.port` which defaults to 8000. These will match as long as Railway's internal port is set to 8000 (or override `PORT=8000`).

**Potential issue:** Railway injects `PORT` env var expecting the app to bind to it. The current code ignores `PORT` — it only reads `settings.port`. Since `settings.port` defaults to `8000` and Railway default port is also `8000`, this works by coincidence. To be safe, either: (a) set `PORT=8000` in Railway vars, or (b) update `__main__.py` to read `int(os.environ.get("PORT", settings.port))`.

---

## 9. Logging Configuration

- `observability/logging.py:93–147`: structlog with PHI scrubbing
- Default `LOG_FORMAT="console"` — Railway should use `LOG_FORMAT=json` for structured log search
- Default `LOG_LEVEL="INFO"` — appropriate for production
- Logs go to `stderr` via `StreamHandler` (`logging.py:138`)
- Railway captures stdout/stderr automatically
- `PYTHONUNBUFFERED=1` is set in `Dockerfile:37` — ensures logs are not buffered

---

## 10. Hardcoded Values That Need Review

### `webhooks.py:31–46` — `_insert_on_conflict_ignore()`
The function has a broken `try/except ImportError` guard to detect SQLite vs PostgreSQL. `sqlalchemy.dialects.postgresql` is always importable (it's in SQLAlchemy core), so the `except ImportError` branch is unreachable. The PostgreSQL insert runs on SQLite and would crash. On Railway with PostgreSQL this is not a problem — the PostgreSQL path runs correctly. However if someone ever tests webhooks locally with SQLite they get a crash. This is a pre-existing bug, not a deploy blocker.

### `channels.py:17–32` — Both channels are always `MockNotificationChannel` and `MockAlertChannel`
The factory always returns mock implementations regardless of `settings.environment`. The code comment says "Reserved for future channel_type setting." In production, patient messages and clinician alerts are logged but never actually delivered to MedBridge Go. This is intentional for the current demo scope but would need real channel implementations before clinical use.

### `dependencies.py:22–33` — Auth is header-based
`X-Patient-ID` and `X-Tenant-ID` headers are trusted directly with no cryptographic verification. The comment says "Production: swap for JWT/API key validation." This is a security gap for production.

### `demo.py` — Demo routes exposed only when `ENVIRONMENT=dev`
`main.py:231–234`: demo router is conditionally added only in `dev` environment. Setting `ENVIRONMENT=staging` or `ENVIRONMENT=prod` correctly excludes these destructive endpoints.

---

## 11. Known Issues and Deployment Blockers

### Blocker 1: `uv.lock` must be committed
`Dockerfile:10`: `COPY pyproject.toml uv.lock ./` — the `uv.lock` file must be in the repo for the `--frozen` flag to work. Verify it is committed and up-to-date.

### Blocker 2: `alembic/versions/` partial schema drift
The initial migration `d325a08b4b9d` creates tables including `messages`, `tool_invocations`, `conversation_threads` that appear to be from an earlier design — they do not correspond to any current SQLAlchemy model in `models.py`. The models in `models.py` are: `patients`, `patient_goals`, `patient_consent_snapshots`, `audit_events`, `scheduled_jobs`, `outbox_entries`, `delivery_attempts`, `clinician_alerts`, `safety_decisions`, `processed_events`. The migration creates additional tables (`messages`, `tool_invocations`, `conversation_threads`) that do not exist as ORM models. Running `alembic upgrade head` will create these orphan tables — harmless but indicative of drift.

### Blocker 3: `PORT` env var not read
Railway injects `PORT`. The app ignores it and uses `settings.port=8000`. Works by coincidence at 8000. See section 8 above.

### Blocker 4: `uv` version in Dockerfile
`Dockerfile:6`: `COPY --from=ghcr.io/astral-sh/uv:0.10 /uv /usr/local/bin/uv` — pins to uv 0.10.x. MEMORY.md notes `astral-sh/setup-uv@v7` is current in CI. The Dockerfile pinning is fine for reproducibility but `0.10` should be verified to still be a valid tag. As of March 2026 this is likely fine but worth checking.

### Non-blocker: SKIP LOCKED with SQLite
Scheduler and delivery worker use `.with_for_update(skip_locked=True)`. SQLite does not support this. Since Railway uses PostgreSQL, this is not a deployment issue — but local dev with SQLite would crash the workers. The `APP_MODE=all` in SQLite mode is broken by design. This is documented in MEMORY.md.

---

## 12. Alembic Migration Details

- `alembic/env.py:26–32`: reads `DATABASE_URL` from `Settings()` — will pick up Railway's injected `DATABASE_URL`
- Uses `NullPool` during migration (correct — no connection pool during migration)
- `run_migrations_online()` calls `asyncio.run()` (synchronous wrapper) — works correctly
- The `startCommand` runs `alembic upgrade head` before the app starts, ensuring tables exist before any connection pool opens

---

## 13. LangGraph Checkpointer on Railway

Source: `db.py:43–65`, `main.py:55–60`

1. `create_langgraph_pool()` strips `postgresql+psycopg://` back to `postgresql://` for psycopg3 (`db.py:54`)
2. Pool B uses `open=False` in constructor; opened with `await pool.open(wait=True)` in lifespan
3. `AsyncPostgresSaver(pool).setup()` creates: `checkpoints`, `checkpoint_writes`, `checkpoint_blobs` tables
4. One persistent thread per patient: `thread_id = f"patient-{patient_id}"` (`chat.py:50`, `webhooks.py:134`)
5. Checkpoint rows contain conversation history (PHI) — the Railway PostgreSQL database must be treated as a PHI data store

---

## 14. Options for Railway Service Topology

### Option A: Single Service (simplest)
- 1 Railway web service
- `APP_MODE=all` (default)
- PostgreSQL plugin attached
- Pro: zero configuration overhead, easy deploys
- Con: no independent worker scaling, shared failure domain

### Option B: Two Services (recommended for production)
- Service 1 (web): `APP_MODE=api`, public port 8000
- Service 2 (worker): `APP_MODE=worker`, no public port, Railway "worker" type
- Same PostgreSQL plugin referenced by both services via `DATABASE_URL`
- Pro: independent restart/scaling, clear separation
- Con: two services to configure and monitor; `startCommand` for worker service should NOT run `alembic upgrade head` (only the web service should run migrations)

### Option C: Two Services + Read Replica (future)
- Overkill at current scale; skip

---

## 15. Recommendation

**For current Railway deployment (demo/staging): Option A (single service).**

The `railway.toml` already implements Option A correctly. The `startCommand` runs migrations, bootstraps LangGraph tables, then starts the app in `all` mode.

**Required env vars to set in Railway dashboard:**

| Variable | Value |
|---|---|
| `DATABASE_URL` | auto-injected by PostgreSQL plugin — no manual action needed |
| `ANTHROPIC_API_KEY` | secret — set as Railway secret variable |
| `ENVIRONMENT` | `staging` or `prod` |
| `LOG_FORMAT` | `json` |
| `MEDBRIDGE_WEBHOOK_SECRET` | required when `ENVIRONMENT != dev`; set as Railway secret |

**Optional but recommended:**

| Variable | Value | Reason |
|---|---|---|
| `PORT` | `8000` | Explicit; avoids reliance on coincidental default match |
| `CORS_ORIGINS` | `["https://your-demo-ui.railway.app"]` | Replace localhost default if demo UI is deployed |
| `DB_POOL_SIZE` | `10` | Increase for Railway Hobby tier (128 connection limit) |
| `LANGGRAPH_POOL_SIZE` | `5` | Increase proportionally |

**What does NOT need changing:**
- Dockerfile is correct as-is
- `railway.toml` startCommand is correct
- `healthcheckPath = "/health/live"` is correct
- Port 8000 works correctly
- Database URL normalization works with Railway's `postgres://` format

**One action item before deploy:**
- Confirm `uv.lock` is committed and up to date (`uv lock --check`)
- Set `MEDBRIDGE_WEBHOOK_SECRET` in Railway or set `ENVIRONMENT=dev` to bypass webhook signature enforcement
