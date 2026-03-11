# Health Ally

AI-powered accountability partner that proactively engages patients in home exercise program (HEP) adherence through the MedBridge Go mobile app. Guides patients through onboarding, goal-setting, and scheduled follow-ups via multi-turn conversations while enforcing strict clinical safety boundaries.

**Demo:** https://medbridge-health-coach-production.up.railway.app

## Architecture

The core design principle: **deterministic policy in Python, bounded generation by LLM**. Application code controls phase transitions, safety gates, consent enforcement, and all database writes. The LLM handles conversation and tool selection within a phase — it is never trusted with state transitions or safety-critical routing.

### LangGraph Agent

A single [StateGraph](https://langchain-ai.github.io/langgraph/) with 14 nodes processes every patient interaction:

```
consent_gate → load_patient_context → crisis_check → manage_history
  → phase_router ─┬─ pending_node
                   ├─ onboarding_agent ─┐
                   ├─ active_agent ─────┤ ←→ tool_node (loop)
                   ├─ re_engaging_agent ┘
                   └─ dormant_node
  → safety_gate → save_patient_context → END
```

Side effects accumulate in a `pending_effects` dict throughout the graph and are flushed atomically by `save_patient_context`.

### Patient Lifecycle

Phase transitions are deterministic (application code only, never the LLM):

```
PENDING → ONBOARDING → ACTIVE → RE_ENGAGING → DORMANT
                          ↑                       │
                          └───────────────────────┘ (patient returns)
```

### Safety Pipeline

| Layer | Purpose | Failure Mode |
|---|---|---|
| Consent Gate | Block unauthorized outreach | Fail-closed |
| Crisis Check | Detect patient distress | Fail-escalate (alert clinician) |
| Safety Classifier | Block clinical/unsafe content | Fail-closed (block message) |
| Retry + Fallback | Recover or use safe template | Deterministic fallback |

## Stack

| Technology | Role |
|---|---|
| Python 3.12+ | Runtime |
| LangGraph | Agent orchestration |
| Claude (Anthropic) | LLM provider |
| FastAPI | HTTP API (SSE streaming) |
| SQLAlchemy (async) | ORM |
| PostgreSQL | Production database |
| SQLite | Local development database |
| Alembic | Migrations |
| Pydantic | Validation and settings |
| structlog | Logging (with PHI scrubbing) |

## Project Structure

```
src/health_ally/
├── agent/              # LangGraph graph, nodes, tools, prompts
│   ├── graph.py        # StateGraph compilation
│   ├── state.py        # PatientState TypedDict
│   ├── nodes/          # 11 graph nodes (consent, safety, phases, etc.)
│   ├── tools/          # 5 agent tools (set_goal, alert_clinician, etc.)
│   └── prompts/        # Phase-specific system prompts
├── api/                # FastAPI routes and middleware
│   └── routes/         # chat, webhooks, state, health, demo
├── domain/             # Business logic, enums, phase machine
├── integrations/       # External service adapters (MedBridge, channels)
├── persistence/        # Database models, repositories, locking
├── orchestration/      # Background workers (scheduler, delivery)
└── observability/      # Logging configuration, PHI scrubbing

tests/
├── unit/               # ~150 unit tests
├── integration/        # ~30 integration tests (PostgreSQL-backed)
├── contract/           # Webhook contract tests
└── evals/              # 24 LLM evals (excluded from default pytest run)

demo-ui/                # React + Vite dev/staging UI (bundled into Docker)
docs/                   # Requirements, ADRs, PHI data flow, intended use
```

## Getting Started

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- PostgreSQL 16+ (or use SQLite for local dev)

### Setup

```bash
# Install dependencies
uv sync

# Copy environment config
cp .env.example .env
# Edit .env — at minimum set ANTHROPIC_API_KEY

# Run database migrations (PostgreSQL)
uv run alembic upgrade head

# Start the service (API + background workers)
uv run python -m health_ally
```

The service starts at `http://localhost:8000` with SQLite by default. Set `DATABASE_URL` to a PostgreSQL connection string for full functionality (scheduled jobs require `SKIP LOCKED`).

### Docker

```bash
# Local dev with PostgreSQL
docker compose up

# Build image only
docker build -t health-ally .
```

### Modes

The service supports three run modes via `--mode`:

```bash
uv run python -m health_ally --mode api      # HTTP API only
uv run python -m health_ally --mode worker   # Background workers only
uv run python -m health_ally --mode all      # Both (default)
```

## Development

```bash
# Run tests
pytest

# Run tests with coverage
pytest --cov

# Lint and format check
ruff check . && ruff format --check .

# Type check
pyright .

# Run LLM evals (requires ANTHROPIC_API_KEY)
pytest tests/evals/
```

### Key Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./health_ally.db` | Database connection |
| `ANTHROPIC_API_KEY` | — | Required for LLM calls |
| `ENVIRONMENT` | `dev` | `dev` / `staging` / `prod` |
| `DEFAULT_MODEL` | `claude-sonnet-4-6` | Primary LLM model |
| `SAFETY_CLASSIFIER_MODEL` | `claude-haiku-4-5-20251001` | Safety classifier model |
| `APP_MODE` | `all` | `api` / `worker` / `all` |

See `src/health_ally/settings.py` for the full configuration reference.

## API

| Endpoint | Method | Description |
|---|---|---|
| `/v1/chat` | POST | Send a message (SSE streaming response) |
| `/webhooks/medbridge` | POST | Receive MedBridge events (HMAC verified) |
| `/v1/patients/{id}/phase` | GET | Current patient phase |
| `/v1/patients/{id}/goals` | GET | Patient goals |
| `/v1/patients/{id}/alerts` | GET | Clinician alerts |
| `/health/live` | GET | Liveness probe |
| `/health/ready` | GET | Readiness probe (checks DB) |

Demo routes (`/api/demo/*`) are available only when `ENVIRONMENT=dev`.

## Deployment

Deployed on [Railway](https://railway.com) via Dockerfile. The 3-stage Docker build compiles Python dependencies, builds the React demo UI, and produces a minimal runtime image.

```
railway.toml → Dockerfile build → pre-deploy (migrations + bootstrap) → start
```

Railway auto-injects `DATABASE_URL` from the PostgreSQL plugin. The settings validator rewrites `postgres://` to `postgresql+psycopg://`.

## Documentation

| Document | Description |
|---|---|
| `docs/requirements.md` | Functional requirements |
| `docs/decisions.md` | Architecture Decision Records (ADR-001–010) |
| `docs/phi-data-flow.md` | HIPAA / PHI data handling |
| `docs/intended-use.md` | Clinical boundaries and safety architecture |

## Immutable Rules

1. **Never generate clinical advice** — redirect all clinical content to the care team
2. **Verify consent on every interaction** — no outreach without logged-in + consented status
3. **Phase transitions are deterministic** — application code only, never the LLM
