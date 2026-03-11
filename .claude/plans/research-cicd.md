# CI/CD Research — GitHub Actions

## 1. Current State

### Existing Workflow Files

Three workflows exist at `.github/workflows/`:

**`ci.yml`** (`.github/workflows/ci.yml:1-72`) — triggers on push/PR to `main`:
- `lint` job: `uv run ruff check .` + `uv run ruff format --check .`
- `typecheck` job: `uv run pyright .`
- `test-unit` job: `uv run pytest tests/unit/ tests/safety/ tests/contract/ -v --tb=short`
- `test-integration` job: postgres:16-alpine service, `DATABASE_URL` env, `uv run pytest tests/integration/ -v --tb=short -m integration`
- `docker-build` job: bare `docker build -t health-coach .`

All jobs use `astral-sh/setup-uv@v7` with `enable-cache: true`. No Python version pin — relies on runner default. No explicit `python-version` input to setup-uv.

**`eval.yml`** (`.github/workflows/eval.yml:1-23`) — triggers on push to `main` and `workflow_dispatch`:
- Single `evals` job, `timeout-minutes: 15`
- `uv run pytest tests/evals/ -v --tb=short`
- Env: `ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}`, `DEEPEVAL_TELEMETRY_OPT_OUT: "1"`

**`deploy.yml`** (`.github/workflows/deploy.yml:1-123`) — triggers on `v*` tags and `workflow_dispatch`:
- `ci-gate` job: re-runs ruff + pyright + unit/safety/contract tests inline
- `build` job: ghcr.io image push using `docker/build-push-action@v6`, metadata via `docker/metadata-action@v5`
- `migration-check` job: postgres:16-alpine service, `uv run alembic upgrade head` + `uv run alembic check`
- `deploy` job: stub that writes a `$GITHUB_STEP_SUMMARY` only — no actual deploy command

### No Other Quality Gates

No `Makefile` exists. No `.pre-commit-config.yaml` exists. No `scripts/` directory found. No Python version matrix — all jobs use `ubuntu-latest` runner default.

---

## 2. Command Reference (exact)

### Lint
```
uv run ruff check .
uv run ruff format --check .
```
Config: `pyproject.toml` `[tool.ruff]` — target `py312`, line length 99, `src = ["src", "tests"]`, extends-exclude `alembic/versions`. (`pyproject.toml:56-71`)

### Type Check
```
uv run pyright .
```
Config: `pyrightconfig.json` — strict mode on `src/health_coach` and `tests/`, Python 3.12, venv at `.venv`. Tests execution environment relaxes 10 pyright rules (no `reportUnknownParameterType` etc.). (`pyrightconfig.json:1-25`)

### Unit Tests (no database needed)
```
uv run pytest tests/unit/ tests/safety/ tests/contract/ -v --tb=short
```
All unit tests use SQLite in-memory (`sqlite+aiosqlite://`) or pure mocks. No PostgreSQL dependency. (`tests/conftest.py:27` sets `TEST_DATABASE_URL = "sqlite+aiosqlite://"`)

Test directories covered:
- `tests/unit/` — 20 files, all pure mocks or SQLite
- `tests/safety/` — 2 files, no DB at all
- `tests/contract/` — 1 file, no DB

### Integration Tests (PostgreSQL required)
```
uv run pytest tests/integration/ -v --tb=short -m integration
```
Requires `DATABASE_URL=postgresql+psycopg://health_coach:password@localhost:5432/health_coach_test`

Integration test files and their actual DB requirements:
- `test_graph_routing.py` — uses MemorySaver + mocks, no real DB
- `test_graph_thread.py` — uses MemorySaver + mocks, no real DB
- `test_onboarding_flow.py` — uses MemorySaver + mocks, no real DB
- `test_followup_lifecycle.py` — uses MemorySaver + mocks, no real DB
- `test_backoff_dormant.py` — pure logic, no DB
- `test_chat_endpoint.py` — ASGITransport + mocked graph, SQLite
- `test_webhook_endpoint.py` — ASGITransport + mocked session, SQLite
- `test_locking.py` — only tests `_patient_lock_key()` math, no DB

**Important finding**: None of the current `tests/integration/` files actually bear the `@pytest.mark.integration` marker. The `-m integration` flag in the CI command filters for that marker, which means the integration job currently runs ZERO tests. The `integration` marker is defined at `pyproject.toml:50-52` but never applied in any test file examined. This is a latent bug.

### Eval Tests (LLM API key required — excluded from default run)
```
ANTHROPIC_API_KEY=... DEEPEVAL_TELEMETRY_OPT_OUT=1 uv run pytest tests/evals/ -v --tb=short
```
pyproject.toml `addopts = "--ignore=tests/evals"` excludes them from `pytest` with no args. (`pyproject.toml:54`)

All three eval files make real Anthropic API calls via `AnthropicModel(model="claude-haiku-4-5-20251001")` as the DeepEval judge:
- `tests/evals/test_safety_evals.py` — 11 parametrized test cases
- `tests/evals/test_coaching_quality.py` — 8 parametrized test cases
- `tests/evals/test_goal_extraction.py` — 5 parametrized test cases

The `tests/evals/conftest.py` `_skip_without_api_key` fixture auto-skips all eval tests when `ANTHROPIC_API_KEY` is not set.

### Install
```
uv sync --frozen
```
Installs all deps including dev group. The `--frozen` flag uses `uv.lock` exactly — no version drift. (`ci.yml:17`)

---

## 3. Python Version

- `pyproject.toml:5`: `requires-python = ">=3.12"`
- `pyrightconfig.json:4`: `"pythonVersion": "3.12"`
- `Dockerfile:3,21`: `FROM python:3.12-slim`
- No Python version matrix used or needed — 3.12 is the sole target.
- `setup-uv@v7` reads `requires-python` from `pyproject.toml` automatically and installs the matching Python. No explicit `python-version` needed in workflow steps.

---

## 4. Environment Variables Required

### For unit/safety/contract tests: None required
`Settings` has all defaults. `database_url` defaults to `sqlite+aiosqlite:///./health_coach.db` and tests override it in-process.

### For integration tests:
- `DATABASE_URL` — PostgreSQL connection string (`postgresql+psycopg://...`)

### For eval tests:
- `ANTHROPIC_API_KEY` — real Anthropic key for DeepEval judge model
- `DEEPEVAL_TELEMETRY_OPT_OUT=1` — must be numeric `1`, not `"YES"` (`tests/evals/conftest.py:15`)

### For deploy workflow:
- `GITHUB_TOKEN` — automatic, used for ghcr.io push (`deploy.yml:45`)
- No other secrets required for the current stub deploy step

### Settings fields that are optional but callable at runtime:
`anthropic_api_key`, `openai_api_key`, `medbridge_api_key`, `medbridge_webhook_secret` — all default to empty string, so tests that mock the LLM layer do not need them. (`settings.py:37-54`)

---

## 5. GitHub Secrets Needed

| Secret | Used By | Required For |
|--------|---------|--------------|
| `ANTHROPIC_API_KEY` | `eval.yml:21` | LLM eval job (evals only) |
| `GITHUB_TOKEN` | `deploy.yml:45` | ghcr.io image push (automatic, no setup needed) |

No other secrets are currently referenced. `OPENAI_API_KEY`, `MEDBRIDGE_API_KEY`, etc. are not referenced in any workflow file — they come from the runtime environment only.

---

## 6. PostgreSQL Dependency Analysis

**Unit tests: SQLite only.** All `tests/unit/` and `tests/safety/` and `tests/contract/` tests use one of:
- `sqlite+aiosqlite://` in-memory engine (e.g. `test_repositories.py:14-20`)
- Pure mocks with no DB at all
- `tests/conftest.py` session-scoped engine on `sqlite+aiosqlite://`

**Integration tests: PostgreSQL not actually exercised by any current test.** As noted above, the `-m integration` marker is defined but never applied to any test. All `tests/integration/` tests use MemorySaver (LangGraph in-memory checkpoint) and mock sessions — they work on SQLite too.

**The only test that would genuinely require PostgreSQL** would test `pg_advisory_lock` with a real connection or `SKIP LOCKED` queries. Currently `test_locking.py` only tests the hash key derivation function — no real PG lock.

**Scheduler and delivery worker tests in `tests/unit/`** mock all DB interactions. The `test_scheduler.py` and `test_delivery_worker.py` use `MagicMock`/`AsyncMock` throughout.

**Conclusion**: The entire test suite can run on SQLite. The PG service in `ci.yml:test-integration` is present but currently unused by any marked test.

---

## 7. Caching Strategy

Current approach: `astral-sh/setup-uv@v7` with `enable-cache: true`. This caches the uv download cache (pip package cache layer) keyed on `uv.lock` contents. The `.venv` itself is NOT cached — it is re-created from the cache on each run. This is the recommended pattern for uv in CI per uv docs.

The `Dockerfile` uses `--mount=type=cache,target=/root/.cache/uv` for BuildKit layer caching. The Docker build step in CI (`docker-build` job) does not use `docker/build-push-action` with cache — it is a bare `docker build` with no cache mounts or registry cache.

---

## 8. Gaps and Issues Found

### Issue 1: `-m integration` runs zero tests
`ci.yml:65`: `uv run pytest tests/integration/ -v --tb=short -m integration`
No test in `tests/integration/` applies `@pytest.mark.integration`. The job always collects 0 tests and passes vacuously. If the intent is to run all integration tests, the `-m integration` filter must be removed. If some integration tests should only run with PG, a subset should get the marker.

### Issue 2: Python version not pinned in CI
`setup-uv@v7` picks Python from `requires-python = ">=3.12"`, which means it installs the latest available 3.12.x on the runner. This is fine for a 3.12-only project with no upper bound, but means the exact Python patch version is not reproducible across runs.

### Issue 3: docker-build has no cache
`ci.yml:71`: `docker build -t health-coach .` — no BuildKit cache flags, no `--cache-from`. Each build is cold. On ubuntu-latest with the current two-stage Dockerfile, this takes ~2-3 minutes. Using `docker/build-push-action@v6` with `cache-from: type=gha` would halve this.

### Issue 4: deploy.yml ci-gate duplicates work
`deploy.yml:28-38` re-runs ruff, pyright, and unit tests. For tag-triggered deploys this is reasonable (no guarantee CI ran on the tag commit), but for `workflow_dispatch` it duplicates effort if CI already passed on the branch.

### Issue 5: integration test PG credentials are hardcoded
`ci.yml:58`: `DATABASE_URL: postgresql+psycopg://health_coach:password@localhost:5432/health_coach_test` — password `password` is hardcoded. This is a test-only DB with no external exposure, so it is acceptable, but worth noting it is not a secret.

---

## 9. Constraints (What Cannot Change)

1. `uv sync --frozen` — must use frozen lockfile to prevent CI from picking up unvetted deps.
2. `--ignore=tests/evals` in `addopts` — evals must not run in the default `pytest` invocation (LLM cost + latency).
3. `asyncio_mode = "auto"` + `asyncio_default_fixture_loop_scope = "session"` — required by pytest-asyncio 1.x; removing breaks all async tests.
4. Eval tests use `DEEPEVAL_TELEMETRY_OPT_OUT=1` (numeric `1`) — the string `"YES"` does not work in post-2025 deepeval.
5. `python:3.12-slim` in Dockerfile — matches `requires-python` and `pyrightconfig.json`.

---

## 10. Options for Improving CI

### Option A: Minimal fix — remove the dead `-m integration` filter
Change `ci.yml:65` from:
```
uv run pytest tests/integration/ -v --tb=short -m integration
```
to:
```
uv run pytest tests/integration/ -v --tb=short
```
Since all integration tests run fine on SQLite with mocks, the PG service is still present but simply unused. Low risk. Does not add real DB coverage but stops the job from being a no-op.

Trade-off: PG service spins up and costs ~15s for nothing. Could remove the service block too, or leave it for future real PG tests.

### Option B: Add real PG tests via marker + SQLite fallback
Apply `@pytest.mark.integration` only to tests that genuinely need PostgreSQL (advisory lock round-trip, SKIP LOCKED queries). Run unmarked integration tests without a marker filter in a separate job on SQLite. Run marked tests with the PG service.

Trade-off: requires writing new PG-dependent tests (none currently exist). More accurate but more work.

### Option C: Consolidate test jobs
Merge `test-unit` and `test-integration` into a single job that runs all non-eval tests:
```
uv run pytest tests/unit/ tests/safety/ tests/contract/ tests/integration/ -v --tb=short
```
No PG service needed since nothing actually uses it. Simpler CI, fewer jobs, same coverage.

Trade-off: loses the architectural separation between unit and integration tiers. When PG tests are eventually added, you have to re-split.

---

## 11. Recommendation

**Short term**: Apply Option A as a safe fix (remove dead `-m integration` filter) combined with removing the now-unnecessary postgres service from `test-integration`. The job becomes a straightforward run of all `tests/integration/` on the default SQLite config.

**Separately**: Pin `python-version: "3.12"` explicitly in setup-uv steps for reproducibility:
```yaml
- uses: astral-sh/setup-uv@v7
  with:
    enable-cache: true
    python-version: "3.12"
```

**For docker-build**: Add BuildKit GHA cache to cut cold-build time:
```yaml
- uses: docker/build-push-action@v6
  with:
    context: .
    push: false
    cache-from: type=gha
    cache-to: type=gha,mode=max
```

**Leave eval.yml and deploy.yml unchanged** — they are structurally correct.

The one required GitHub secret is `ANTHROPIC_API_KEY` for eval.yml. `GITHUB_TOKEN` is automatic.
