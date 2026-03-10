# Researcher Memory

## Project Context
- Python-only backend service, no frontend code exists
- Patient UI lives in MedBridge Go (external app) — not this repo
- Stack: Python 3.12+, LangGraph, FastAPI assumed, PostgreSQL/SQLite, uv, pytest, ruff, pyright
- docs/requirements.md has the full functional requirements
- docs/decisions.md is an append-only ADR log (currently empty)

## Research Findings (saved for future planning sessions)
- See `.claude/plans/research-frontend-ux.md` for full frontend/UX research
- See `.claude/plans/research-backend-infra.md` for full backend/infra research (2026-03-08)
- Recommendation: defer frontend entirely (Phase 1), use HTMX+FastAPI for clinician dashboard (Phase 2), Next.js+assistant-ui only if complexity demands it (Phase 3)
- assistant-ui has first-class LangGraph integration — the right choice if a React chat UI is ever needed
- Tailwind v4 released Jan 2025; shadcn/ui is the dominant component library for React internal tools
- HTMX+FastAPI is a legitimate production stack for read-mostly internal dashboards in Python teams

## Backend Infrastructure Key Decisions (2026-03-08)
- **psycopg3 required** — langgraph-checkpoint-postgres depends on it; cannot swap for asyncpg
- **SQLModel** preferred over raw SQLAlchemy 2 for ~5-entity domain (eliminates ORM+Pydantic duplication)
- **FastAPI** over Litestar — entire LangGraph ecosystem assumes FastAPI; throughput irrelevant vs LLM latency
- **SSE** over WebSocket — coaching is server-push only; simpler, HTTP/1.1-compatible
- **APScheduler 4.x** for scheduling — in-process, no broker, SQLAlchemy job store reuses same PostgreSQL
- **LangGraph open-source has no built-in cron** — scheduling is LangGraph Platform-only feature
- **LangSmith Deployment** = renamed from "LangGraph Platform/Cloud" (October 2025)
- **TypedDict for state**, not Pydantic — node updates are incremental, Pydantic overhead is unnecessary
- **pytest-asyncio 1.0** (May 2025) has breaking changes — set asyncio_mode = "auto" in pyproject.toml
- **DeepEval** for AI evals (not Ragas) — Ragas is RAG-specific; this is an agent/chatbot, not RAG
- **Railway** for initial deployment — managed Postgres, Docker support, low ops overhead

## AI Architecture Key Decisions (updated 2026-03-10)
- See `.claude/plans/research-ai-architecture.md` for original research; `.claude/plans/research.md` for 2026-03-10 update
- **Single StateGraph + conditional phase router** (not subgraphs) — 5 phases fit in one graph
- **create_react_agent deprecated** in langgraph-prebuilt v1.0 — emits warning; removed in v2.0; use explicit StateGraph + ToolNode
- **New `create_agent` in `langchain.agents`** (LangChain 1.0+) — high-level tier; NOT used here (need graph-level control for phase routing)
- **Safety classifier = normal node** after LLM, before delivery (NOT an interrupt())
- **Consent gate = first node** in every entry path — synchronous DB lookup, no LLM
- **Structured outputs** confirmed for claude-sonnet-4-5 and opus-4-1; verify for claude-sonnet-4-6
- **Recommended LLM**: claude-sonnet-4-6 (HIPAA BAA via API; strongest clinical refusals)
- **Fallback LLM**: GPT-4o (BAA via baa@openai.com; zero-data-retention endpoint required)
- **LangGraph versions** (2026-03-10): langgraph 1.1.0; langgraph-prebuilt 1.0.8; langgraph-checkpoint 4.0.1; checkpoint-postgres 3.0.4
- **AsyncPostgresSaver** — use `.from_conn_string()` factory; requires psycopg3 with autocommit=True, dict_row
- **InMemorySaver** is correct dev checkpointer class (NOT MemorySaver); `from langgraph.checkpoint.memory import InMemorySaver`
- **Runtime object (LangGraph 1.0+)** — idiomatic Store + context injection in nodes via `Runtime[Context]` param; declare `context_schema=` on StateGraph; pass `context=` at ainvoke; replaces config.configurable for deps
- **ToolNode.afunc breaking change** in langgraph-prebuilt 1.0.2 — custom subclasses must add `runtime` param to `afunc`
- **LangGraph 1.1.0 streaming v2** — `version="v2"` in stream()/astream() gives typed StreamPart; opt-in, backwards-compatible

## Integrations and Operations Key Decisions (2026-03-08)
- See `.claude/plans/research-integrations-ops.md` for full details
- **APScheduler stable = 3.11.2** (Dec 2025); APScheduler 4.x is STILL alpha (4.0.0a6, Apr 2025) — do NOT use 4.x in production; use 3.11.2.
- **Twilio chatbot integrations NOT HIPAA eligible** — only the SMS transport API is; AI pipeline needs its own BAA
- **Anthropic HIPAA BAA:** requires sales-assisted Enterprise plan; standard API tier (even paid) insufficient for PHI in production
- **OpenAI HIPAA BAA:** email baa@openai.com; requires zero-data-retention endpoints; most requests approved
- **Langfuse v3 self-hosting CHANGED:** v3 now requires ClickHouse + Redis/Valkey + S3 + PostgreSQL (4 services). Prior note "self-hosted on project PostgreSQL" was v2 behaviour — no longer valid.
- **Observability Phase 1:** Defer Langfuse/Phoenix to Phase 2. Use structlog + OTEL only. Phoenix OSS (single Docker container) is better fit than Langfuse v3 for Railway-style deployments if added later.
- **HIPAA audit logs:** 6-year minimum retention; append-only `audit_events` table in PostgreSQL; never delete rows
- **Event bus Phase 1:** PostgreSQL LISTEN/NOTIFY + asyncio.Queue (zero new infra); Phase 2: Redis Streams with consumer groups
- **Feature flags Phase 1:** Pydantic Settings/env vars; Phase 2: Unleash self-hosted (AGPL-3, Python SDK)
- **Multi-tenancy:** Shared schema + `tenant_id` + PostgreSQL RLS; silo (DB-per-tenant) upgrade path available without app code changes
- **Notification abstraction:** `NotificationChannel` ABC + registry pattern for SMS/push extensibility
- **Patient engagement cadence:** Day 2/5/7 (requirements) is evidence-based; text messages have 90-98% open rate vs 20% email
- **Backoff scheduling:** per-patient one-shot APScheduler jobs with jitter (±30%) to avoid thundering herd
- **Clinician alerts:** Webhook POST to MedBridge Go backend (primary) + Twilio SMS fallback; priority routing (urgent vs. routine)

## Stack Audit Decisions (2026-03-10)
- See `.claude/plans/research-stack-audit-2026-03.md` for full findings
- **stamina over tenacity** — stamina 25.2.0 wraps tenacity; structlog retry instrumentation automatic; retries disabled in tests; use this, not raw tenacity
- **slowapi DROPPED** — abandoned (no PyPI releases 12+ months). Use in-process sliding window middleware or app-layer idempotency controls
- **pytest-asyncio 1.0:** `event_loop` fixture removed; use `loop_scope` on markers; `asyncio_mode = "auto"` still correct
- **8 tools confirmed best-in-class:** DeepEval, structlog, FastAPI, respx, Pydantic Settings, hypothesis, uvicorn

## Type Checker Decision (2026-03-10)
- See `.claude/plans/research-type-checker.md` for full research
- **Recommendation: switch from mypy to pyright** — greenfield project, no switching cost
- **SQLAlchemy mypy plugin is deprecated, broken on mypy >= 1.11.0** — do NOT use it
- **Modern SQLAlchemy 2.0 Mapped[T] + mapped_column works natively in both tools** — no plugin needed
- **Pydantic v2 works in pyright via PEP 681 / @dataclass_transform natively** — no plugin needed
- **LangGraph TypedDict partial returns cause [typeddict-item] warnings in mypy** — fundamental friction, no clean fix
- **No mypy-specific plugins exist for LangGraph or FastAPI** — plugin advantage of mypy is moot for this stack
- **langchain-core ships py.typed** — both tools read inline types, no stubs gap
- **pyright versions:** 1.1.408 (Jan 2026); **mypy version:** 1.19.1 (Dec 2025)
- **If staying on mypy:** must add `plugins = pydantic.mypy` in config; do NOT add sqlalchemy plugin

## Key Constraints (from immutable.md)
1. Never generate clinical advice — redirect to care team
2. Verify consent on every interaction (not just thread creation)
3. Phase transitions are deterministic application code, never LLM-decided
