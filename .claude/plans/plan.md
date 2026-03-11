# Plan: Railway Deployment + CI Fix

## Summary

Deploy the Health Coach to Railway as a single service (backend + demo UI) with PostgreSQL, and fix CI so it catches real bugs. Four milestones, each independently verifiable: (1) fix 3 runtime bugs that crash or fail on Railway, (2) fix CI pipeline that currently runs 0 integration tests, (3) bundle demo UI into Docker image served from FastAPI same-origin, (4) create deployment artifacts and deploy to Railway.

`ENVIRONMENT=dev` on Railway — this is a demo deployment that needs demo routes. Same origin serving eliminates CORS entirely. Conservative pool sizes for free-tier PostgreSQL.

---

## Milestones

### M1: Fix Runtime Bugs
- [x] Bug 1 — Parse `reminder_time` to `datetime` in `set_reminder` → verify: `uv run ruff check . && uv run pyright .`
- [x] Bug 2 — Add LLM call to `dormant_node` for patient return + conditional edge in graph.py → verify: `uv run pytest tests/integration/test_graph_routing.py -v`
- [x] Bug 3 — Pass `externalPatientId` prop through `DemoControls` → verify: `cd demo-ui && npx tsc --noEmit`
- [x] Full verify — 180 tests pass, 0 pyright errors, 0 ruff errors, TS clean

### M2: Fix CI Pipeline
- [x] Remove dead `-m integration` marker, remove unused PG service, pin Python 3.12

### M3: Bundle Demo UI into Docker Image
- [x] Add `aiofiles` dependency
- [x] Add Node.js build stage to Dockerfile + COPY static
- [x] Mount StaticFiles in main.py
- [x] Docker build + smoke test: health/live OK, root serves UI (200)

### M4: Deployment Artifacts + Railway Deploy
- [x] Create `.env.example`
- [x] Full quality gate: 180 tests pass, 0 errors, lock consistent
- [ ] **MANUAL**: Railway setup (create project, add PG plugin, connect GitHub, set env vars)

---

## Milestone 1: Fix Runtime Bugs

Three bugs that crash or produce broken behavior on Railway PostgreSQL. All are clear, verified, and have precise fixes.

### Bug 1: `set_reminder` stores raw string as `scheduled_at` — crashes on PostgreSQL

**File:** `src/health_coach/agent/tools/reminder.py`

**Root cause:** Line 45 stores `reminder_time` (an ISO 8601 string from the LLM) directly. `ScheduledJob.scheduled_at` is `Mapped[datetime]`. psycopg3 strict typing rejects the string. All 4 other callers of `scheduled_at` pass `datetime` objects.

**Fix:** Parse the string to `datetime` at the source (line 45):

```python
# Line 1-2: add import
from datetime import datetime

# Line 45: change
# Before:
"scheduled_at": reminder_time,
# After:
"scheduled_at": datetime.fromisoformat(reminder_time),
```

`datetime.fromisoformat` handles all ISO 8601 formats including timezone offsets in Python 3.12+. No new dependency needed.

### Bug 2: Dormant patient return produces no reply — silent failure

**Files:** `src/health_coach/agent/nodes/dormant.py`, `src/health_coach/agent/graph.py`

**Root cause:** When a DORMANT patient sends a message:
1. `phase_router` → `dormant_node` (correct)
2. `dormant_node` sets `phase_event: "patient_returned"` and `outbound_message: None` (line 50)
3. Static edge `graph.add_edge("dormant_node", "save_patient_context")` (graph.py:133)
4. Phase transitions DORMANT → RE_ENGAGING, but **no response is generated**

Cannot route through `reengagement_agent` — it fires `phase_event: "patient_responded"` which is invalid for DORMANT phase (not in `_TRANSITIONS`), causing `PhaseTransitionError`.

**Fix:** Add LLM call to `dormant_node` for patient-initiated invocations, route through safety_gate.

`dormant.py` changes:
```python
# Add config parameter and LLM call for patient path:
async def dormant_node(
    state: PatientState,
    config: RunnableConfig,          # NEW — needed for get_coach_context
) -> dict[str, object]:

    # For patient-initiated (line 29 block):
    # After setting pending_effects, generate a welcome-back message
    ctx = get_coach_context(config)
    system_prompt = build_re_engaging_prompt("patient")
    coach_model = ctx.model_gateway.get_chat_model("coach")
    messages = list(state.get("messages", []))
    response = await coach_model.ainvoke(
        [{"role": "system", "content": system_prompt}, *messages]
    )
    content = str(response.content) if response.content else None
    return {
        "pending_effects": updated_effects,
        "outbound_message": content,
        "messages": [response],
    }

    # Scheduler path (line 53): unchanged — returns outbound_message: None
```

New imports needed in `dormant.py`:
```python
from health_coach.agent.context import get_coach_context
from health_coach.agent.prompts.re_engaging import build_re_engaging_prompt
if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
```

`graph.py` changes:
```python
# Add route function (near line 56):
def _dormant_route(state: PatientState) -> str:
    """Route dormant_node output: safety_gate if message, save if not."""
    if state.get("outbound_message"):
        return "safety_gate"
    return "save_patient_context"

# Replace line 133:
# Before:
graph.add_edge("dormant_node", "save_patient_context")
# After:
graph.add_conditional_edges(
    "dormant_node",
    _dormant_route,
    {"safety_gate": "safety_gate", "save_patient_context": "save_patient_context"},
)
```

### Bug 3: Demo UI re-seed creates duplicate patient

**Files:** `demo-ui/src/App.tsx`, `demo-ui/src/components/DemoControls.tsx`

**Root cause:** `App.tsx:61` passes `effectivePatientId` (internal DB UUID after first seed) as `patientId` to `DemoControls`. `DemoControls:46` uses this as `external_patient_id` in the seed request. On re-seed, the internal UUID is sent as the external ID → new patient created.

**Fix:** Pass the raw external patient ID separately.

`App.tsx` change:
```tsx
// Line 60-64:
<DemoControls
  patientId={effectivePatientId}
  externalPatientId={patientId}     // ADD: always the dropdown UUID
  tenantId={tenantId}
  onPatientSeeded={handlePatientSeeded}
/>
```

`DemoControls.tsx` changes:
```tsx
// Interface (line 3-7):
interface DemoControlsProps {
  patientId: string;
  externalPatientId: string;       // ADD
  tenantId: string;
  onPatientSeeded: (id: string) => void;
}

// Destructuring (around line 20):
export function DemoControls({
  patientId,
  externalPatientId,               // ADD
  tenantId,
  onPatientSeeded,
}: DemoControlsProps) {

// Seed call body (line 46):
// Before:
external_patient_id: patientId,
// After:
external_patient_id: externalPatientId,
```

**Verify Milestone 1:**
```bash
uv run ruff check . && uv run ruff format --check .
uv run pyright .
uv run pytest tests/unit/ tests/safety/ tests/contract/ -v --tb=short
uv run pytest tests/integration/ -v --tb=short
cd demo-ui && npx tsc --noEmit && cd ..
```

---

## Milestone 2: Fix CI Pipeline

The integration test job passes vacuously with 0 tests. Python version is unpinned.

**File:** `.github/workflows/ci.yml`

### Change 1: Remove dead `-m integration` marker filter

Line 65:
```yaml
# Before:
- run: uv run pytest tests/integration/ -v --tb=short -m integration
# After:
- run: uv run pytest tests/integration/ -v --tb=short
```

No integration test applies `@pytest.mark.integration`. With the filter removed, all 8+ integration tests run. They all use MemorySaver + mocks or SQLite — no PostgreSQL needed.

### Change 2: Remove unused PostgreSQL service

Remove the `services:` block (lines 43-56) and `env:` block (lines 57-58) from the `test-integration` job. No test uses them.

The job becomes:
```yaml
  test-integration:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v7
        with:
          enable-cache: true
          python-version: "3.12"
      - run: uv sync --frozen
      - run: uv run pytest tests/integration/ -v --tb=short
```

### Change 3: Pin Python 3.12 across all jobs

Add `python-version: "3.12"` to every `setup-uv` step. Currently the Python version floats based on runner defaults.

```yaml
- uses: astral-sh/setup-uv@v7
  with:
    enable-cache: true
    python-version: "3.12"   # ADD to all 5 jobs
```

**Verify Milestone 2:**
```bash
# Locally validate integration tests actually run:
uv run pytest tests/integration/ -v --tb=short 2>&1 | head -5
# Should show "collected N items" where N > 0
```

---

## Milestone 3: Bundle Demo UI into Docker Image

The demo UI is a Vite SPA that currently only works through Vite's dev proxy. On Railway, there's no Vite dev server. Serve the built static files from FastAPI on the same origin — eliminates CORS issues entirely, all relative URLs work.

### Step 3a: Add `aiofiles` dependency

```bash
uv add aiofiles
```

FastAPI's `StaticFiles` requires `aiofiles` at runtime. This updates `pyproject.toml` and `uv.lock`.

### Step 3b: Add Node.js build stage to Dockerfile

Insert between the builder and runtime stages (after line 20, before line 22):

```dockerfile
# --- UI build stage ---
FROM node:22-slim AS ui-builder
WORKDIR /app/demo-ui
COPY demo-ui/package.json demo-ui/package-lock.json ./
RUN npm ci --ignore-scripts
COPY demo-ui/ ./
RUN npm run build
```

In the runtime stage, add after line 33 (after alembic.ini COPY):
```dockerfile
COPY --from=ui-builder /app/demo-ui/dist /app/static
```

### Step 3c: Mount StaticFiles in main.py

After the demo router inclusion (line 234), inside the `if settings.environment == "dev"` block:

```python
if settings.environment == "dev":
    from health_coach.api.routes.demo import router as demo_router

    app.include_router(demo_router)

    # Serve demo UI static files when available (built by Dockerfile)
    import pathlib

    static_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "static"
    if not static_dir.is_dir():
        static_dir = pathlib.Path("/app/static")
    if static_dir.is_dir():
        from starlette.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
```

**Key design decisions:**
- Mounted at `"/"` — FastAPI routes (registered first via `include_router`) take priority over the catch-all `StaticFiles` mount
- `html=True` enables SPA fallback: unmatched paths serve `index.html`
- `pathlib.Path(...).is_dir()` guard → no-ops gracefully in local dev (no `/app/static` directory)
- Checks both relative path (for testing) and absolute path (for Docker)
- Inside `environment == "dev"` gate — never exposed in prod/staging
- Must be the LAST thing added to the app (catch-all mount)

**Verify Milestone 3:**
```bash
# Build Docker image:
docker build -t health-coach .

# Run locally (workers will log errors for SQLite SKIP LOCKED — expected):
docker run -p 8000:8000 -e ENVIRONMENT=dev health-coach

# In another terminal:
curl http://localhost:8000/health/live        # → {"status":"ok"}
curl http://localhost:8000/                    # → HTML (demo UI index.html)
curl http://localhost:8000/v1/demo/seed-patient -X POST \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":"demo","external_patient_id":"test-123"}'
# → JSON response with patient_id
```

---

## Milestone 4: Deployment Artifacts + Railway Deploy

### Step 4a: Create `.env.example`

**File:** `.env.example` (new — `.gitignore` already has `!.env.example` negation at line 50)

```env
# === REQUIRED ===
ANTHROPIC_API_KEY=sk-ant-your-key-here

# DATABASE_URL is auto-injected by Railway PostgreSQL plugin.
# For local dev with PostgreSQL:
# DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/health_coach

# === DEPLOYMENT ===
ENVIRONMENT=dev
LOG_FORMAT=json
LOG_LEVEL=INFO

# === POOL SIZES (free-tier PostgreSQL: keep total under 15) ===
DB_POOL_SIZE=3
DB_MAX_OVERFLOW=2
LANGGRAPH_POOL_SIZE=2

# === CONDITIONAL (required when ENVIRONMENT != dev) ===
# MEDBRIDGE_WEBHOOK_SECRET=your-webhook-secret
# MEDBRIDGE_API_URL=https://api.medbridge.com
# MEDBRIDGE_API_KEY=your-medbridge-key

# === OPTIONAL (safe defaults) ===
# DEFAULT_MODEL=claude-sonnet-4-6
# SAFETY_CLASSIFIER_MODEL=claude-haiku-4-5-20251001
# MAX_TOKENS=1024
# QUIET_HOURS_START=21
# QUIET_HOURS_END=8
# DEFAULT_TIMEZONE=America/New_York
# SCHEDULER_POLL_INTERVAL_SECONDS=30
# DELIVERY_POLL_INTERVAL_SECONDS=5
# APP_MODE=all
# PORT=8000
```

### Step 4b: Verify uv.lock

```bash
uv lock --check
```

### Step 4c: Railway Setup (manual)

1. **Create Railway project** (or use existing)
2. **Add PostgreSQL plugin** — auto-injects `DATABASE_URL`
3. **Connect GitHub repo** — Railway auto-deploys on push to main
4. **Set env vars in Railway dashboard:**

| Variable | Value | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-...` | Set as secret |
| `ENVIRONMENT` | `dev` | Exposes demo routes + serves demo UI |
| `LOG_FORMAT` | `json` | Structured logs for Railway log viewer |
| `DB_POOL_SIZE` | `3` | Free-tier conservative |
| `DB_MAX_OVERFLOW` | `2` | Free-tier conservative |
| `LANGGRAPH_POOL_SIZE` | `2` | Free-tier conservative |

Total max connections: 7, well within free-tier PostgreSQL limits (~20).

`DATABASE_URL` is auto-injected — do NOT set manually.

### Step 4d: Verify Deployment

```bash
# Health checks:
curl https://<app>.railway.app/health/live    # → {"status":"ok"}
curl https://<app>.railway.app/health/ready   # → {"status":"ok","database":"ok","langgraph":"ok"}

# Demo UI:
# Open https://<app>.railway.app/ in browser
# Should see Health Coach Demo UI

# End-to-end:
# 1. Click "Seed Patient" → success with phase
# 2. Send chat message → SSE streamed response
# 3. Check observability sidebar → patient state, goals
# 4. Check Railway logs → structured JSON, no errors
```

**Verify Milestone 4:**
```bash
# Full local quality gate (run before pushing):
uv run ruff check . && uv run ruff format --check . && uv run pyright . && uv run pytest tests/unit/ tests/safety/ tests/contract/ tests/integration/ -v --tb=short
```

---

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| `StaticFiles` mount at `"/"` intercepts API routes | API returns HTML | FastAPI routes registered before mount take priority; verified by curl in M3 |
| Free-tier PG connection limit hit | Pool exhaustion, 500 errors | Conservative pool sizes (7 max); single-user demo is fine |
| Dormant node LLM call fails (network/API error) | Patient gets no response | Wrap in try/except, fall back to `outbound_message: None` (same as current behavior) |
| Railway deploy fails on `npm ci` (missing lockfile) | Docker build fails | `package-lock.json` confirmed present and not gitignored |
| Node.js stage increases Docker build time | Slower deploys (~30s) | Cached after first build; acceptable |

## Not In Scope (acceptable gaps for demo)

- Auth is header-based trust (dev stub) — known, documented
- Notification/alert channels are mock implementations — intentional
- Orphan tables in migration — harmless
- `deploy.yml` deploy job is a stub — Railway auto-deploys from GitHub, no CI deploy needed
- Sidebar polls 4 endpoints every 2s — fine for single-user demo
- No custom domain — Railway auto-generated domain is sufficient
