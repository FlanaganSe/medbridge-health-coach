# Deployment & CI Research

Investigation date: 2026-03-11

---

## PART 1: RAILWAY DEPLOYMENT — Complete Checklist

### 1.1 Railway Services Required

**Minimum (demo/staging): 1 web service + 1 PostgreSQL plugin**

| Service | Type | Config |
|---|---|---|
| `health-coach` | Web service | `APP_MODE=all` (default) — runs HTTP + scheduler + delivery workers in one process |
| PostgreSQL | Railway plugin | Attach to project — auto-injects `DATABASE_URL` |

The app supports split mode (`APP_MODE=api` + `APP_MODE=worker`) for production, but single-service `all` mode is correct for demo.

### 1.2 Environment Variables — COMPLETE LIST

#### MUST SET in Railway (deployment fails or is broken without these)

| Variable | Value | Why Required |
|---|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-...` (secret) | Powers coach LLM, safety classifier, goal extractor. Every graph invocation fails without it. |
| `DATABASE_URL` | **Auto-injected by Railway PostgreSQL plugin** | No manual action. Railway's `postgres://` format auto-rewritten to `postgresql+psycopg://` by `settings.py:62-70` validator. |
| `ENVIRONMENT` | `dev` for demo (exposes demo routes), `staging` for shared | Controls: demo route exposure (`main.py:231`), webhook secret enforcement (`webhooks.py:60-63`), consent service selection. **If set to non-dev, `MEDBRIDGE_WEBHOOK_SECRET` becomes mandatory.** |
| `CORS_ORIGINS` | `["https://<your-railway-domain>"]` | **BLOCKER**: defaults to `["http://localhost:5173"]`. All browser requests from deployed UI fail without this. Format: JSON array string. |
| `LOG_FORMAT` | `json` | Railway log search needs structured JSON. Default `console` is human-readable but unsearchable. |

#### CONDITIONALLY REQUIRED

| Variable | When Required | Default |
|---|---|---|
| `MEDBRIDGE_WEBHOOK_SECRET` | When `ENVIRONMENT != "dev"` — webhook handler returns HTTP 500 if absent | `""` |
| `MEDBRIDGE_API_URL` | For real consent checking (non-dev). Omitting falls back to `FakeConsentService` with warning. | `""` |
| `MEDBRIDGE_API_KEY` | With `MEDBRIDGE_API_URL` | `""` |

#### OPTIONAL (have safe defaults)

| Variable | Default | Notes |
|---|---|---|
| `PORT` | `8000` | App reads `settings.port`. Railway also defaults to 8000. Match by coincidence — safe to leave. |
| `HOST` | `0.0.0.0` | Correct for containers. |
| `DEBUG` | `False` | Keep False. |
| `DEFAULT_MODEL` | `claude-sonnet-4-6` | Override for cost/speed. |
| `SAFETY_CLASSIFIER_MODEL` | `claude-haiku-4-5-20251001` | Do NOT change to `claude-3-haiku` (retires Apr 20 2026). |
| `MAX_TOKENS` | `1024` | ChatAnthropic explicit max_tokens. |
| `DB_POOL_SIZE` | `5` | Pool A (SQLAlchemy). |
| `DB_MAX_OVERFLOW` | `5` | Pool A overflow. |
| `LANGGRAPH_POOL_SIZE` | `3` | Pool B (psycopg3 for checkpointer). |
| `LOG_LEVEL` | `INFO` | |
| `QUIET_HOURS_START` | `21` | Hour (0-23). |
| `QUIET_HOURS_END` | `8` | Hour (0-23). |
| `DEFAULT_TIMEZONE` | `America/New_York` | |
| `SCHEDULER_POLL_INTERVAL_SECONDS` | `30` | |
| `DELIVERY_POLL_INTERVAL_SECONDS` | `5` | |
| `FALLBACK_PHI_APPROVED` | `False` | Enables OpenAI fallback; requires `OPENAI_API_KEY` + signed BAA. |
| `OPENAI_API_KEY` | `""` | Only used when `FALLBACK_PHI_APPROVED=True`. |
| `APP_MODE` | `all` | `all`/`api`/`worker`. |

### 1.3 Startup Sequence (what happens on deploy)

`railway.toml` `startCommand` runs three steps in series:

```
alembic upgrade head && python -c '..run_bootstrap..' && python -m health_coach
```

**Step 1: `alembic upgrade head`**
- Creates/migrates 12+ domain tables (patients, patient_goals, scheduled_jobs, outbox_entries, etc.)
- Reads `DATABASE_URL` from env via `alembic/env.py:26-32`
- Idempotent — safe on every deploy
- Uses `NullPool` (no lingering connections)

**Step 2: `run_bootstrap(Settings())`**
- Creates 3 LangGraph checkpoint tables: `checkpoints`, `checkpoint_writes`, `checkpoint_blobs`
- Calls `AsyncPostgresSaver(pool).setup()` — idempotent

**Step 3: `python -m health_coach`**
- In `all` mode: starts uvicorn HTTP server + background scheduler/delivery workers as asyncio tasks
- Lifespan sequence: configure_logging → create_engine (Pool A) → create_session_factory → create_langgraph_pool (Pool B) → open Pool B → setup checkpointer (redundant, harmless) → compile graph → spawn background workers → serve

### 1.4 Database Architecture

**Two connection pools to the SAME PostgreSQL database:**

| Pool | Library | Purpose | Key Config |
|---|---|---|---|
| Pool A | SQLAlchemy AsyncEngine | Domain DB (patients, goals, jobs, audit) | `pool_pre_ping=True`, `expire_on_commit=False`, `lazy="raise"` |
| Pool B | psycopg3 AsyncConnectionPool | LangGraph checkpointer | `autocommit=True`, `prepare_threshold=0`, `row_factory=dict_row` |

Pool B strips `+psycopg` from the URL back to plain `postgresql://` for psycopg3 compatibility (`db.py:54`).

### 1.5 Health Checks

| Endpoint | Behavior | Railway Config |
|---|---|---|
| `GET /health/live` | Always 200 `{"status": "ok"}`, no DB check | **Used** — `railway.toml:5` |
| `GET /health/ready` | Checks both Pool A and Pool B, returns 503 if either down | Available but not used by Railway |

### 1.6 What Already Works

- Dockerfile is correct (two-stage, non-root user, port 8000, cached uv deps)
- `railway.toml` startCommand handles migrations + bootstrap + app start
- Health check path configured correctly
- Database URL normalization handles Railway's `postgres://` format
- `PYTHONUNBUFFERED=1` set in Dockerfile (no log buffering)
- SSE streaming works through Railway's proxy (`X-Accel-Buffering: no` header set)
- Advisory lock guard handles SQLite gracefully
- `alembic upgrade head` is idempotent

### 1.7 Deployment Blockers

| # | Severity | Issue | Fix |
|---|---|---|---|
| **D-1** | **BLOCKER** | `CORS_ORIGINS` defaults to localhost — deployed browser requests fail | Set env var to include deployed UI origin |
| **D-2** | **BLOCKER** | Demo UI uses relative URLs + Vite proxy — fails on different host | Serve built demo UI from FastAPI via `StaticFiles`, OR use local-only demo |
| **D-3** | **DECISION** | `ENVIRONMENT=dev` exposes destructive unauthenticated demo routes publicly | Acceptable for private demo; set `staging` for shared environments |
| **D-4** | **GAP** | No `.env.example` file | Create it (`.gitignore` already has `!.env.example` negation) |
| **D-5** | **GAP** | `LOG_FORMAT` defaults to `console` | Set to `json` in Railway |
| **D-6** | **INFO** | Orphan tables in migration (`messages`, `tool_invocations`, `conversation_threads`) | Harmless — schema drift from earlier design |

### 1.8 Demo UI Deployment Options

The demo UI (`demo-ui/`) is a standalone Vite SPA. The Python backend does NOT serve it. All fetch calls use relative URLs (`/v1/chat`, `/v1/demo/...`) that only work through Vite's dev proxy.

**Option A — Serve from FastAPI (RECOMMENDED for Railway demo)**
- Add `npm run build` to Dockerfile, copy `demo-ui/dist/` into image
- Mount `StaticFiles(directory="demo-ui/dist", html=True)` in `main.py` gated to `environment == "dev"`
- All API calls stay relative — no CORS issue, single Railway domain
- Requires `aiofiles` pip dependency
- Trade-off: demo UI bundled into backend image

**Option B — Separate Railway static service**
- Deploy `demo-ui/` as separate Railway service
- Needs `VITE_API_URL` env var; all fetch calls must be updated to use it
- `CORS_ORIGINS` must include static site's domain
- Two services, two domains — more config
- Trade-off: cleaner separation but more deployment complexity

**Option C — Local-only (current state)**
- Run `npm run dev` locally, Vite proxy to Railway backend
- Zero deployment changes
- Trade-off: demo not shareable via URL

### 1.9 Pre-Deploy Checklist

- [ ] `uv.lock` is committed and up-to-date (`uv lock --check`)
- [ ] Add PostgreSQL plugin to Railway project
- [ ] Set required env vars: `ANTHROPIC_API_KEY`, `ENVIRONMENT`, `CORS_ORIGINS`, `LOG_FORMAT`
- [ ] Set `MEDBRIDGE_WEBHOOK_SECRET` if `ENVIRONMENT != dev`
- [ ] Choose demo UI strategy (Option A/B/C above)
- [ ] If Option A: update Dockerfile to build and serve demo UI
- [ ] Deploy and verify `/health/live` returns 200
- [ ] Verify `/health/ready` returns 200 (both DB pools connected)
- [ ] Test chat endpoint via curl or demo UI

---

## PART 2: GITHUB ACTIONS CI — Complete Analysis

### 2.1 Current Workflows

Three workflow files exist at `.github/workflows/`:

**`ci.yml`** — triggers on push/PR to `main`:
| Job | Command | Status |
|---|---|---|
| `lint` | `uv run ruff check .` + `uv run ruff format --check .` | Working |
| `typecheck` | `uv run pyright .` | Working |
| `test-unit` | `uv run pytest tests/unit/ tests/safety/ tests/contract/ -v --tb=short` | Working |
| `test-integration` | `uv run pytest tests/integration/ -v --tb=short -m integration` | **BUG: runs 0 tests** |
| `docker-build` | `docker build -t health-coach .` | Working (no cache) |

**`eval.yml`** — triggers on push to `main` and `workflow_dispatch`:
| Job | Command | Status |
|---|---|---|
| `evals` | `uv run pytest tests/evals/ -v --tb=short` | Working (makes real API calls) |

**`deploy.yml`** — triggers on `v*` tags and `workflow_dispatch`:
| Job | Status |
|---|---|
| `ci-gate` | Re-runs ruff + pyright + tests (duplication, but OK for tags) |
| `build` | Pushes to ghcr.io |
| `migration-check` | Runs `alembic upgrade head` + `alembic check` against PG service |
| `deploy` | **Stub** — writes step summary only, no actual deploy |

### 2.2 Critical Bug: Integration Tests Run Zero Tests

`ci.yml:65`: `uv run pytest tests/integration/ -v --tb=short -m integration`

The `-m integration` flag filters for `@pytest.mark.integration` marker, but **no test file in `tests/integration/` applies this marker**. The marker is defined in `pyproject.toml:50-52` but never used. The job passes vacuously with 0 tests collected.

**Fix:** Remove `-m integration` from the command. All integration tests use MemorySaver + mocks or ASGITransport with SQLite — no PostgreSQL needed. The PG service container in this job is also unnecessary.

### 2.3 GitHub Secrets Required

| Secret | Used By | Required? |
|---|---|---|
| `ANTHROPIC_API_KEY` | `eval.yml:21` — LLM eval judge | Only for eval job |
| `GITHUB_TOKEN` | `deploy.yml:45` — ghcr.io push | Automatic (no setup) |

**That's it.** No other secrets are referenced in any workflow.

### 2.4 Which Tests Make LLM Calls?

**Tests that DO make LLM calls (exclude from CI):**
- `tests/evals/test_safety_evals.py` — 11 parametrized cases, real Anthropic API
- `tests/evals/test_coaching_quality.py` — 8 parametrized cases, real Anthropic API
- `tests/evals/test_goal_extraction.py` — 5 parametrized cases, real Anthropic API
- All use `AnthropicModel(model="claude-haiku-4-5-20251001")` as DeepEval judge

**These are ALREADY excluded** from default `pytest` via `addopts = "--ignore=tests/evals"` in `pyproject.toml:54`. The `evals/conftest.py` also auto-skips if `ANTHROPIC_API_KEY` is absent.

**Tests that do NOT make LLM calls (safe for CI):**
- All `tests/unit/` — pure mocks, no DB
- All `tests/safety/` — mock classifier, no API calls
- All `tests/contract/` — schema validation only
- All `tests/integration/` — MemorySaver + mocks or ASGITransport with SQLite

### 2.5 PostgreSQL Dependency in Tests

**No current test actually requires PostgreSQL.** Every test uses:
- `sqlite+aiosqlite://` in-memory engine, OR
- Pure mocks with no DB, OR
- `MemorySaver` (LangGraph in-memory checkpoint)

The PG service container in `ci.yml:test-integration` spins up and sits idle. It can be removed.

### 2.6 Exact Commands for CI

```bash
# Lint
uv run ruff check .
uv run ruff format --check .

# Type check
uv run pyright .

# Unit + safety + contract tests (no DB, no API key)
uv run pytest tests/unit/ tests/safety/ tests/contract/ -v --tb=short

# Integration tests (no DB, no API key — currently)
uv run pytest tests/integration/ -v --tb=short

# Evals (requires ANTHROPIC_API_KEY — separate workflow)
ANTHROPIC_API_KEY=... DEEPEVAL_TELEMETRY_OPT_OUT=1 uv run pytest tests/evals/ -v --tb=short

# Docker build
docker build -t health-coach .

# Install
uv sync --frozen
```

### 2.7 CI Fixes Needed

| # | Issue | Fix |
|---|---|---|
| **CI-1** | `-m integration` runs 0 tests | Remove `-m integration` from command |
| **CI-2** | PG service in integration job is unused | Remove postgres service block |
| **CI-3** | Python version not pinned | Add `python-version: "3.12"` to setup-uv |
| **CI-4** | Docker build has no cache | Use `docker/build-push-action@v6` with `cache-from: type=gha` |
| **CI-5** | deploy.yml deploy job is a stub | Wire up Railway CLI deploy or leave as-is for now |

### 2.8 No Missing Quality Gates

- `pyproject.toml` ruff config: target `py312`, line-length 99, extends-exclude `alembic/versions`
- `pyrightconfig.json`: strict on `src/health_coach` and `tests/`, Python 3.12
- No pre-commit hooks, no Makefile — all checks run through `uv run` in CI
- `asyncio_mode = "auto"` + `asyncio_default_fixture_loop_scope = "session"` in `pyproject.toml`

---

## PART 3: KNOWN BUGS THAT AFFECT DEPLOYMENT

### 3.1 Runtime Crash: `set_reminder` stores raw string as `scheduled_at`

**Severity: HIGH — crashes on PostgreSQL when the LLM calls `set_reminder`**

`reminder.py:45` stores `reminder_time` (a raw ISO 8601 string from the LLM) into pending effects. `context.py:240` passes it to `ScheduledJob(scheduled_at=str)`. PostgreSQL with `DateTime(timezone=True)` and psycopg3 strict typing raises an error. All other callers store `datetime` objects.

**Fix:** Parse at source in `reminder.py:45`:
```python
from dateutil.parser import isoparse
"scheduled_at": isoparse(reminder_time).astimezone(UTC),
```

### 3.2 Demo-Blocking: Dormant patient return produces no reply

**Severity: DEMO-BLOCKING**

When a DORMANT patient sends a message, `dormant_node` transitions phase to RE_ENGAGING but generates no response. The patient sees silence. On the next invocation the re-engagement agent runs, but the first message is lost.

**Fix:** Add LLM call to `dormant_node` for patient-initiated invocations (Option A from research).

### 3.3 Demo UI: Re-seeding creates duplicate patient

**Severity: DEMO-BLOCKING**

After first seed, `effectivePatientId` becomes the internal DB UUID. Clicking "Seed Patient" again sends this internal UUID as `external_patient_id`, creating a new duplicate patient.

**Fix:** Pass raw dropdown `patientId` (external ID) to seed call, not `effectivePatientId`.

### 3.4 SQLite/PostgreSQL: `_insert_on_conflict_ignore` wrong dialect detection

`webhooks.py:29-46` uses `try/except ImportError` to detect SQLite vs PostgreSQL. Since `sqlalchemy.dialects.postgresql` is always importable, the SQLite branch is unreachable. **Not a Railway deployment blocker** (PostgreSQL path works), but breaks local SQLite demo.

---

## PART 4: SUMMARY — WHAT MUST HAPPEN

### For Railway Deployment (ordered)

1. **Choose demo UI strategy** — Option A (serve from FastAPI) recommended
2. **Fix `set_reminder` string→datetime crash** (`reminder.py:45`) — will crash on first reminder tool call
3. **Fix CI integration tests** (remove `-m integration`) — currently testing nothing
4. **Set Railway env vars**: `ANTHROPIC_API_KEY`, `ENVIRONMENT`, `CORS_ORIGINS`, `LOG_FORMAT`
5. **Verify `uv.lock` is committed** (`uv lock --check`)
6. **Deploy and verify** health checks, chat endpoint

### For GitHub Actions CI (ordered)

1. **Fix `-m integration`** — remove marker filter so integration tests actually run
2. **Remove unused PG service** from integration job
3. **Pin Python version** to `3.12` in setup-uv
4. **Optionally**: add Docker build cache, wire deploy job to Railway CLI

### Known Acceptable Gaps (not blocking demo)

- Auth is header-based trust (dev stub)
- Notification/alert channels are mock implementations
- Orphan tables in migration (harmless)
- Double `AsyncPostgresSaver.setup()` call (idempotent)
- Sidebar polls 4 endpoints every 2s (acceptable for single-user demo)
