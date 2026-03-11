# FastAPI + SQLAlchemy 2.0 Async: Implementation Research

**Date:** 2026-03-10
**Status:** Research — input for planning
**Scope:** Concrete implementation patterns for FastAPI >= 0.115, SQLAlchemy 2.0 async, Pydantic v2, Alembic >= 1.14, psycopg3. Healthcare context with pyright strict mode.

---

## 1. FastAPI App Structure — Lifespan Context Manager

### Pattern (current — `@app.on_event` is deprecated and removed in future Starlette)

```python
# src/health_coach/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from health_coach.persistence.db import engine, async_session_factory, lg_pool
from health_coach.api.routes import chat, health, webhooks

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    # Pool A: SQLAlchemy engine (app queries, repos, audit)
    # Engine is created at module import time; just open/warm here if needed.

    # Pool B: psycopg3 AsyncConnectionPool for LangGraph checkpointer + Store
    await lg_pool.open()

    yield  # App is live and serving requests

    # --- Shutdown ---
    await lg_pool.close()
    await engine.dispose()

app = FastAPI(lifespan=lifespan)
app.include_router(health.router)
app.include_router(chat.router, prefix="/v1")
app.include_router(webhooks.router, prefix="/webhooks")
```

**Key rules:**
- Use `@asynccontextmanager` + `lifespan=` parameter. `@app.on_event("startup")` is deprecated as of FastAPI 0.95 / Starlette 0.20 and will eventually be removed.
- Anything stored on `app.state` inside lifespan is available for the entire app lifecycle.
- The lifespan runs only for the main app, not for mounted sub-applications.

**Source:** [FastAPI docs — Lifespan Events](https://fastapi.tiangolo.com/advanced/events/)

---

## 2. SQLAlchemy 2.0 Async Patterns

### 2.1 DeclarativeBase with Naming Convention

Always define a naming convention on the `MetaData` object before any models are declared. This prevents Alembic from generating anonymous constraint names that break cross-database migrations.

```python
# src/health_coach/persistence/models.py
import uuid
from datetime import datetime
from sqlalchemy import MetaData, String, ForeignKey, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from health_coach.domain.phases import PatientPhase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
```

**Why:** Alembic autogenerate cannot reliably detect or rename anonymous constraints. Named constraints make ALTER TABLE migrations deterministic. This must be set before any Table objects are created.

**Source:** [Alembic — The Importance of Naming Constraints](https://alembic.sqlalchemy.org/en/latest/naming.html)

### 2.2 `Mapped[T]` + `mapped_column()` Model Syntax

```python
class Patient(Base):
    __tablename__ = "patients"

    # Primary key: use server-side UUID default for PostgreSQL
    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), index=True)
    external_patient_id: Mapped[str] = mapped_column(String(255), unique=True)
    phase: Mapped[PatientPhase] = mapped_column(
        # Use SQLAlchemy Enum with native_enum=False for portability
        String(20), default=PatientPhase.PENDING
    )
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    unanswered_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        default=func.now(), onupdate=func.now()
    )

    # Relationships with lazy="raise" to prevent accidental implicit I/O in async
    goals: Mapped[list["Goal"]] = relationship(
        "Goal", back_populates="patient", lazy="raise"
    )
    audit_events: Mapped[list["AuditEvent"]] = relationship(
        "AuditEvent", back_populates="patient", lazy="raise"
    )
```

**Critical pattern:** `lazy="raise"` forces all relationship access through explicit eager loading. In async context, any implicit lazy load raises `MissingGreenlet`. This catches bugs at development time instead of runtime.

**Nullable fields** use `Mapped[T | None]` (or `Optional[T]`):

```python
goal_text: Mapped[str | None] = mapped_column(nullable=True)
last_contact_at: Mapped[datetime | None] = mapped_column(nullable=True)
```

**JSONB for metadata blobs** (PostgreSQL-specific):

```python
metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
```

**Pyright strict note:** `Mapped[T]` works natively with pyright strict — no plugin required. The SQLAlchemy mypy plugin is deprecated since mypy >= 1.11.0. Known open issue: pyright does not validate constructor argument types for ORM models (SQLAlchemy issue #12268 — tracked upstream, not a blocker).

**Source:** [SQLAlchemy 2.0 Async docs](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)

### 2.3 Async Engine Creation

```python
# src/health_coach/persistence/db.py
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from health_coach.settings import get_settings

settings = get_settings()

# Pool A: SQLAlchemy engine — for all app queries (repos, audit, scheduled_jobs)
# psycopg3 async URL: postgresql+psycopg://...
engine = create_async_engine(
    settings.database_url,           # must be postgresql+psycopg://...
    echo=settings.db_echo,           # True in dev, False in prod
    pool_size=10,                    # base connections per process
    max_overflow=5,                  # burst capacity
    pool_pre_ping=True,              # verify connections before use (Cloud SQL needs this)
    pool_recycle=1800,               # recycle every 30 min (prevents stale connections)
)

# Session factory — create once, reuse throughout app lifetime
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,          # MANDATORY: prevents lazy-load errors post-commit
    autoflush=False,                 # Control flushing explicitly in repos
)
```

**`expire_on_commit=False` is mandatory.** Without it, accessing any attribute on a committed ORM object triggers a lazy load, which raises `MissingGreenlet` in async context. Every committed object becomes a read-only zombie without this flag.

**`pool_pre_ping=True` is mandatory for Cloud SQL / managed databases.** These services have aggressive connection idle timeouts. `pool_pre_ping` sends a lightweight "SELECT 1" before handing a connection from the pool, preventing "connection closed" errors under load.

**`pool_recycle`** prevents long-lived idle connections from being killed by the database server's timeout.

**Source:** [SQLAlchemy 2.0 Async docs](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)

### 2.4 Async Session Dependency Injection

```python
# src/health_coach/persistence/db.py (continued)
from typing import AsyncGenerator, Annotated
from fastapi import Depends

async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

# Type alias — used as the DI type throughout API routes
DbSession = Annotated[AsyncSession, Depends(get_session)]
```

Use `Annotated` + `Depends` for type-safe injection. This is the pattern FastAPI recommends as of 0.95.0 and avoids repeating the `Depends(get_session)` call at every route.

```python
# In a route:
@router.get("/patients/{patient_id}")
async def get_patient(patient_id: uuid.UUID, session: DbSession) -> PatientRead:
    repo = PatientRepository(session)
    patient = await repo.get_by_id(patient_id)
    ...
```

**Note:** FastAPI caches dependencies within a single request. If two functions in the same request both depend on `get_session`, they receive the same session object — which is correct and intentional.

### 2.5 Repository Pattern

```python
# src/health_coach/persistence/repositories/patient.py
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from health_coach.persistence.models import Patient, Goal

class PatientRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, patient_id: uuid.UUID) -> Patient | None:
        stmt = (
            select(Patient)
            .where(Patient.id == patient_id)
            # Explicitly eager-load relationships needed in this call
            .options(selectinload(Patient.goals))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_external_id(self, external_id: str) -> Patient | None:
        stmt = select(Patient).where(Patient.external_patient_id == external_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, patient: Patient) -> Patient:
        self._session.add(patient)
        await self._session.flush()  # Get DB-generated values (id, created_at) without committing
        return patient

    async def update_phase(
        self, patient_id: uuid.UUID, phase: PatientPhase
    ) -> None:
        stmt = (
            update(Patient)
            .where(Patient.id == patient_id)
            .values(phase=phase, updated_at=func.now())
        )
        await self._session.execute(stmt)
```

**Relationship loading strategies in async:**

| Strategy | When to use | Syntax |
|---|---|---|
| `selectinload` | One-to-many, avoid N+1 | `.options(selectinload(Parent.children))` |
| `joinedload` | Many-to-one (small joined result) | `.options(joinedload(Child.parent))` |
| `lazy="raise"` on model | Default on all relationships | Prevents accidental implicit I/O |
| `AsyncAttrs` mixin | Ad-hoc awaitable access | `await obj.awaitable_attrs.rel` (SQLAlchemy 2.0.13+) |
| `write_only=True` | Never-queried append-only collections | Audit events written but never iterated |

**For audit events:** use `write_only=True` on the relationship since audit events are append-only and should never be loaded into memory as a collection.

```python
audit_events: Mapped[list["AuditEvent"]] = relationship(
    "AuditEvent", back_populates="patient", write_only=True
)
```

**Source:** [SQLAlchemy 2.0 Async docs](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)

---

## 3. Pydantic v2 Integration

### 3.1 ORM → Pydantic Conversion

```python
# src/health_coach/persistence/schemas/patient.py
from pydantic import BaseModel, ConfigDict, Field
from pydantic import UUID4
import uuid
from datetime import datetime
from health_coach.domain.phases import PatientPhase

class PatientBase(BaseModel):
    external_patient_id: str
    timezone: str = "UTC"

class PatientCreate(PatientBase):
    tenant_id: uuid.UUID

class PatientRead(PatientBase):
    # from_attributes=True enables model_validate(orm_object) conversion
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    phase: PatientPhase
    unanswered_count: int
    created_at: datetime
    updated_at: datetime

class PatientUpdate(BaseModel):
    phase: PatientPhase | None = None
    unanswered_count: int | None = None
    timezone: str | None = None
```

Usage in route:

```python
patient_orm = await repo.get_by_id(patient_id)
return PatientRead.model_validate(patient_orm)   # replaces v1 .from_orm()
```

**Deprecation note:** Pydantic v1 `orm_mode = True` and `.from_orm()` are removed in v2. Use `ConfigDict(from_attributes=True)` and `model_validate()`.

### 3.2 Pydantic Settings

```python
# src/health_coach/settings.py
from functools import lru_cache
from pydantic import SecretStr, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",  # allows HEALTH_COACH__DB__URL style nesting
        case_sensitive=False,
        secrets_dir="/run/secrets",   # Docker/Kubernetes secret files
        extra="ignore",              # ignore unknown env vars (CI safety)
    )

    # Database — async URL for app, sync URL for Alembic
    database_url: str   # postgresql+psycopg://user:pass@host:5432/db
    database_url_sync: str  # postgresql+psycopg://user:pass@host:5432/db (sync for Alembic)

    # LangGraph psycopg3 pool — same DB, different pool lifecycle
    # Typically same as database_url; separated for pool sizing control
    langgraph_db_url: str

    # LLM
    anthropic_api_key: SecretStr
    openai_api_key: SecretStr | None = None
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-4-6"
    safety_model: str = "claude-haiku-4-5"

    # App behavior
    db_echo: bool = False
    environment: str = "development"   # development | staging | production

    @field_validator("database_url", "database_url_sync", mode="before")
    @classmethod
    def ensure_psycopg_scheme(cls, v: str) -> str:
        """Ensure URL uses postgresql+psycopg:// (psycopg3 dialect)."""
        if v.startswith("postgresql://") or v.startswith("postgres://"):
            return v.replace("postgresql://", "postgresql+psycopg://", 1).replace(
                "postgres://", "postgresql+psycopg://", 1
            )
        return v

@lru_cache
def get_settings() -> Settings:
    return Settings()
```

**Priority order** (highest to lowest):
1. Direct constructor kwargs (testing overrides)
2. Environment variables
3. `.env` file
4. Secrets directory (`/run/secrets/<field_name>`)

**`SecretStr`** prevents API keys from being printed in logs or repr. Access the actual value with `.get_secret_value()` only at the point of use.

**`@lru_cache`** ensures Settings is instantiated once. In tests, clear with `get_settings.cache_clear()`.

**Source:** [Pydantic Settings docs](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)

---

## 4. Alembic Async Migration Setup

### 4.1 Initialization

```bash
alembic init -t async alembic
```

The `-t async` flag generates an `env.py` that uses `async_engine_from_config`. This is the canonical starting point as of Alembic >= 1.7.

### 4.2 `alembic.ini`

```ini
[alembic]
script_location = alembic
file_template = %%(year)d_%%(month).2d_%%(day).2d_%%(hour).2d%%(minute).2d-%%(rev)s_%%(slug)s
sqlalchemy.url = postgresql+psycopg://%(DB_USER)s:%(DB_PASS)s@%(DB_HOST)s/%(DB_NAME)s
```

The URL in `alembic.ini` is overridden in `env.py` by reading from settings, so this is just a fallback.

### 4.3 `alembic/env.py` (Complete Pattern)

```python
import asyncio
from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context

# Import your Base so autogenerate sees all models
from health_coach.persistence.models import Base
from health_coach.settings import get_settings

config = context.config
settings = get_settings()

# Override sqlalchemy.url from settings (not alembic.ini hardcoded value)
# Use SYNC psycopg3 URL for migrations — same scheme, no asyncpg
config.set_main_option("sqlalchemy.url", settings.database_url_sync)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without DB connection (generates SQL output)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations via async engine, wrapping sync Alembic API."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # NullPool: no pooling during migrations
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry point for online migrations."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

### 4.4 Two URLs: Async App vs. Sync Migration Engine

| Purpose | Driver | URL scheme |
|---|---|---|
| App (SQLAlchemy async engine) | `psycopg` async | `postgresql+psycopg://...` |
| Alembic migrations (async_engine_from_config) | `psycopg` async | `postgresql+psycopg://...` |
| Fallback if psycopg3 async not available | `psycopg` sync (psycopg3 supports both) | `postgresql+psycopg://...` |

**psycopg3 supports both sync and async via the same package.** The URL scheme is `postgresql+psycopg://` for both — no separate `asyncpg` driver needed. Alembic's async template calls `run_sync()` internally, so migrations run synchronously through the async connection layer.

**Important:** `NullPool` in migrations prevents connection pool overhead and lifecycle issues when running via `asyncio.run()`.

**psycopg3 URL format gotcha:** Tools like CloudSQL Proxy, Heroku, or Railway may generate `postgresql://` or `postgres://` URLs. Always normalize to `postgresql+psycopg://` before use. The `field_validator` in Settings handles this automatically.

**Source:** [Alembic Cookbook — Asyncio](https://alembic.sqlalchemy.org/en/latest/cookbook.html)

### 4.5 Migration Workflow

```bash
# After changing models:
alembic revision --autogenerate -m "add_patient_table"

# ALWAYS review the generated migration before applying:
# - Check for missed renames (autogenerate sees DROP + ADD, not RENAME)
# - Check enum type changes
# - Check server_default expressions

# Apply to local dev:
alembic upgrade head

# Dry-run SQL output only:
alembic upgrade head --sql
```

**Autogenerate limitations:**
- Cannot detect column renames (generates DROP + ADD)
- Cannot detect table renames
- May miss some PostgreSQL-specific features (JSONB ops, partial indexes, expressions)
- Always review before applying to production

---

## 5. Connection Pool Management

### 5.1 Two Pools Architecture

This project requires **two independent, incompatible pools** (from FINAL_CONSOLIDATED_RESEARCH.md §17.8 and §6.3):

| | Pool A | Pool B |
|---|---|---|
| **Type** | SQLAlchemy `AsyncAdaptedQueuePool` | psycopg3 `AsyncConnectionPool` |
| **Created by** | `create_async_engine()` | `AsyncConnectionPool(conninfo=...)` |
| **Used by** | App queries, repositories, audit, scheduled_jobs | LangGraph checkpointer + Store |
| **Lifecycle** | Engine manages internally | Must call `await pool.open()` / `await pool.close()` |
| **Connection requirements** | Standard SQLAlchemy connections | `autocommit=True`, `row_factory=dict_row` |
| **Sharing** | Cannot share with Pool B — incompatible lifecycle | Cannot share with Pool A — different config |

**Pool B setup (LangGraph):**

```python
# src/health_coach/persistence/db.py
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres import AsyncPostgresStore

# Pool B: psycopg3 — for LangGraph checkpointer and Store only
lg_pool = AsyncConnectionPool(
    conninfo=settings.langgraph_db_url,
    max_size=20,
    open=False,   # We open manually in lifespan to control timing
    kwargs={
        "autocommit": True,
        "row_factory": dict_row,
    },
)

# These are created at module level but usable only after lg_pool.open()
checkpointer = AsyncPostgresSaver(lg_pool)
store = AsyncPostgresStore(lg_pool)
```

**In the lifespan:**

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await lg_pool.open()       # Pool B opens first
    yield
    await lg_pool.close()      # Pool B closes first
    await engine.dispose()     # Pool A disposes after
```

**Pool A sizing guidelines:**

```
pool_size = 10   # Handles steady-state load per process
max_overflow = 5  # Burst headroom
```

For production: `pool_size` × workers should not exceed PostgreSQL's `max_connections` (default 100). Cloud SQL often sets 100-500; scale accordingly.

**Pool B sizing:**

```
max_size = 20   # LangGraph uses connections per graph invocation
```

### 5.2 LangGraph Pool Setup Migration

`checkpointer.setup()` creates the LangGraph checkpoint tables. Call this once, from a dedicated migration script, NOT at app startup:

```bash
# One-time setup (run after Alembic migrations)
python -m health_coach.scripts.setup_langgraph_tables
```

```python
# src/health_coach/scripts/setup_langgraph_tables.py
import asyncio
from health_coach.persistence.db import lg_pool, checkpointer, store

async def main() -> None:
    await lg_pool.open()
    await checkpointer.setup()
    await store.setup()
    await lg_pool.close()

if __name__ == "__main__":
    asyncio.run(main())
```

**Source:** [psycopg3 pool docs](https://www.psycopg.org/psycopg3/docs/advanced/pool.html)

---

## 6. SSE Streaming

### 6.1 Core Pattern

```python
# src/health_coach/api/routes/chat.py
import json
from typing import AsyncGenerator
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from health_coach.agent.graph import compiled_graph
from health_coach.persistence.db import DbSession

router = APIRouter(tags=["chat"])


async def _sse_event(data: dict) -> str:
    """Format a dict as an SSE event line."""
    return f"data: {json.dumps(data)}\n\n"


async def _stream_graph(
    patient_id: str,
    thread_id: str,
    message: str,
) -> AsyncGenerator[str, None]:
    """Stream LangGraph events as SSE-formatted strings."""
    config = {"configurable": {"thread_id": thread_id}}
    initial_state = {"patient_id": patient_id, "user_message": message}

    try:
        async for event in compiled_graph.astream_events(
            initial_state,
            config=config,
            version="v2",   # v2 is recommended; v1 is legacy
        ):
            kind = event.get("event", "")

            # Token-level streaming (for UI typewriter effect)
            if kind == "on_chat_model_stream":
                chunk = event["data"].get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    yield await _sse_event({"type": "token", "content": chunk.content})

            # Tool invocations
            elif kind == "on_tool_start":
                yield await _sse_event({
                    "type": "tool_start",
                    "tool": event.get("name"),
                })
            elif kind == "on_tool_end":
                yield await _sse_event({
                    "type": "tool_end",
                    "tool": event.get("name"),
                })

            # Graph completion
            elif kind == "on_chain_end" and event.get("name") == "LangGraph":
                yield await _sse_event({"type": "done"})

    except Exception as e:
        yield await _sse_event({"type": "error", "message": "Internal error"})
        # Log the real error with structlog (no PHI in the SSE response)
        raise


@router.post("/chat/stream")
async def chat_stream(
    patient_id: str,
    thread_id: str,
    message: str,
    session: DbSession,
) -> StreamingResponse:
    return StreamingResponse(
        _stream_graph(patient_id, thread_id, message),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",   # Required for nginx buffering bypass
        },
    )
```

### 6.2 SSE Protocol Notes

- Each event: `data: <json>\n\n` (two newlines terminate an event)
- Named events: `event: token\ndata: {...}\n\n`
- Keepalive: `:\n\n` (comment line, client ignores, prevents timeout)
- Client reconnects automatically with `Last-Event-ID` header — handle idempotently

### 6.3 `astream_events` vs. `astream`

| Method | Use case | Output |
|---|---|---|
| `astream_events(version="v2")` | Token streaming, tool visibility, fine-grained UI feedback | Dict events with `event`, `name`, `data` keys |
| `astream(stream_mode="updates")` | Node-level state updates only | State diffs per node |
| `astream(stream_mode="messages")` | Message-level chunks (simpler) | `(message_chunk, metadata)` tuples |

Use `astream_events(version="v2")` for the richest SSE experience. Use `astream(stream_mode="updates")` for simpler webhook-style endpoints.

### 6.4 Pyright Strict Typing for Async Generators

```python
from typing import AsyncGenerator

# This annotation is compatible with pyright strict:
async def gen() -> AsyncGenerator[str, None]:
    yield "data: hello\n\n"

# StreamingResponse accepts AsyncIterable[str | bytes] — generator satisfies this
```

Known issue: pyright strict can flag `AsyncGenerator` in some edge cases (issue #5411). If encountered, annotate the function return type explicitly and add `# type: ignore[return-value]` on the `StreamingResponse` line if needed.

**Source:** [FastAPI SSE + LangGraph streaming guide](https://dev.to/kasi_viswanath/streaming-ai-agent-with-fastapi-langgraph-2025-26-guide-1nkn)

---

## 7. Health Endpoints

### 7.1 Liveness vs. Readiness

| Endpoint | Checks | Kubernetes behavior |
|---|---|---|
| `GET /health/live` | Process is running, basic sanity | Restart pod if fails |
| `GET /health/ready` | DB connected, LG pool alive, scheduler running | Remove from load balancer if fails |

```python
# src/health_coach/api/routes/health.py
import time
from fastapi import APIRouter
from sqlalchemy import text
from health_coach.persistence.db import engine, lg_pool

router = APIRouter(tags=["health"])

@router.get("/health/live")
async def liveness() -> dict[str, str]:
    """Always returns 200 if process is running."""
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness() -> dict[str, object]:
    """Returns 200 only when all dependencies are healthy."""
    checks: dict[str, str] = {}
    overall_ok = True

    # Check Pool A (SQLAlchemy / app DB)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["db_pool_a"] = "ok"
    except Exception as e:
        checks["db_pool_a"] = f"error: {type(e).__name__}"
        overall_ok = False

    # Check Pool B (psycopg3 / LangGraph)
    try:
        async with await lg_pool.getconn() as conn:
            await conn.execute("SELECT 1")
        checks["db_pool_b"] = "ok"
    except Exception as e:
        checks["db_pool_b"] = f"error: {type(e).__name__}"
        overall_ok = False

    status_code = 200 if overall_ok else 503
    return JSONResponse(
        content={"status": "ok" if overall_ok else "degraded", "checks": checks},
        status_code=status_code,
    )
```

### 7.2 Schema Compatibility Check (Optional, Run-Once)

For deployment safety, run a startup check that verifies migration state:

```python
# In lifespan, before yield:
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.config import Config as AlembicConfig

async def check_migrations_current() -> None:
    alembic_cfg = AlembicConfig("alembic.ini")
    script = ScriptDirectory.from_config(alembic_cfg)
    head = script.get_current_head()
    async with engine.connect() as conn:
        ctx = MigrationContext.configure(await conn.get_raw_connection())
        current = ctx.get_current_revision()
    if current != head:
        raise RuntimeError(f"Migrations not current: current={current}, head={head}")
```

Only run this in production. In tests, use `alembic upgrade head` in test fixtures.

---

## 8. Error Handling

### 8.1 Global Exception Handlers

```python
# src/health_coach/main.py
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors(), "body": exc.body},
    )

@app.exception_handler(Exception)
async def unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    # structlog captures exc_info automatically in middleware
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )
```

**Rule:** Never expose internal error details or stack traces in API responses. Log them with structlog; return only a safe generic message to the client.

### 8.2 Domain-Level Error Hierarchy

```python
# src/health_coach/domain/errors.py

class HealthCoachError(Exception):
    """Base for all domain errors."""

class ConsentError(HealthCoachError):
    """Patient has not consented or consent check failed."""

class PatientNotFoundError(HealthCoachError):
    """No patient found for given ID."""

class SafetyError(HealthCoachError):
    """Message failed safety classifier."""

class PhaseTransitionError(HealthCoachError):
    """Invalid or disallowed phase transition requested."""
```

Map domain errors to HTTP status codes in route handlers, not in domain code:

```python
try:
    patient = await repo.get_by_id(patient_id)
except PatientNotFoundError:
    raise HTTPException(status_code=404, detail="Patient not found")
except ConsentError:
    raise HTTPException(status_code=403, detail="Consent not verified")
```

### 8.3 Structlog Integration

```python
# src/health_coach/observability/logging.py
import structlog
import logging

def configure_logging(environment: str) -> None:
    shared_processors = [
        structlog.contextvars.merge_contextvars,      # Request-scoped context
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if environment == "development":
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer()            # Human-readable in dev
        ]
    else:
        processors = shared_processors + [
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer()        # Machine-parseable in prod
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(level=logging.INFO)
```

**Request context middleware:**

```python
# src/health_coach/api/middleware.py
import uuid
import time
import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = structlog.get_logger()

class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        structlog.contextvars.clear_contextvars()
        request_id = str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        start = time.perf_counter_ns()
        try:
            response: Response = await call_next(request)
            elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
            logger.info(
                "request_complete",
                status_code=response.status_code,
                elapsed_ms=round(elapsed_ms, 2),
            )
            return response
        except Exception:
            elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
            logger.exception("unhandled_exception", elapsed_ms=round(elapsed_ms, 2))
            raise
```

**PHI safety in logs:** Never log `patient_name`, `phone`, `email`, `message_content`, or any free-text that could contain PHI. Log only opaque UUIDs (`patient_id`, `conversation_id`) and operational metadata.

**Source:** [Structlog + FastAPI integration](https://wazaari.dev/blog/fastapi-structlog-integration)

---

## 9. Key Constraints / Mandatory Settings Summary

These are non-negotiable for async correctness and HIPAA safety:

| Setting | Value | Reason |
|---|---|---|
| `expire_on_commit` | `False` | Post-commit attribute access in async |
| `pool_pre_ping` | `True` | Cloud SQL idle connection management |
| `lazy` on relationships | `"raise"` (default) | Prevents accidental implicit I/O |
| Database URL scheme | `postgresql+psycopg://` | psycopg3 dialect; must be explicit |
| Alembic pool | `NullPool` | No pooling in migration subprocess |
| `autocommit` on LG pool | `True` | Required by LangGraph checkpointer |
| `row_factory` on LG pool | `dict_row` | Required by LangGraph checkpointer |
| `SecretStr` | All API keys | Prevents secrets in repr/logs |
| Structlog level | Never log message content | HIPAA PHI minimization |
| Naming convention | On `Base.metadata` | Alembic constraint name determinism |

---

## 10. Open Issues and Caveats (March 2026)

1. **pyright + SQLAlchemy ORM constructors (issue #12268):** pyright strict does not validate argument types passed to ORM model constructors. `Patient(phase=123)` will not raise a type error. Use repository methods with typed parameters to enforce correctness.

2. **`add_conditional_edges` type annotation (issue #6540):** LangGraph's `add_conditional_edges` requires `# type: ignore[arg-type]` in pyright strict. Still open upstream.

3. **`total=False` on LangGraph TypedDict state:** Using `total=False` causes pyright to flag partial-return issues on nodes. Pattern: use `total=True` (default) and annotate optional fields as `T | None`.

4. **`astream_events` v2 is stable, v1 is legacy.** Always use `version="v2"`.

5. **psycopg3 pool `open=False` + manual `open()`:** Use `open=False` in the constructor and `await lg_pool.open()` in lifespan. This is the pattern for FastAPI lifecycle compatibility — prevents pool being opened before the event loop is established.

6. **Alembic + `asyncio.run()` + existing event loop:** If calling Alembic programmatically from within an async context (e.g., test setup), `asyncio.run()` will fail ("event loop already running"). Use `nest_asyncio` or move migration calls to `asyncio.run()` at the process boundary. Prefer CLI invocation in CI.

---

## Sources

- [FastAPI — Lifespan Events](https://fastapi.tiangolo.com/advanced/events/)
- [SQLAlchemy 2.0 — Asyncio Extension](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- [Alembic — Cookbook (Async)](https://alembic.sqlalchemy.org/en/latest/cookbook.html)
- [Alembic — Naming Constraints](https://alembic.sqlalchemy.org/en/latest/naming.html)
- [Pydantic Settings docs](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
- [psycopg3 Connection Pool docs](https://www.psycopg.org/psycopg3/docs/advanced/pool.html)
- [FastAPI SSE + LangGraph streaming guide (2025-26)](https://dev.to/kasi_viswanath/streaming-ai-agent-with-fastapi-langgraph-2025-26-guide-1nkn)
- [Structlog + FastAPI integration](https://wazaari.dev/blog/fastapi-structlog-integration)
- [FastAPI + SQLAlchemy 2.0 async patterns (Dec 2025)](https://dev-faizan.medium.com/fastapi-sqlalchemy-2-0-modern-async-database-patterns-7879d39b6843)
- [Alembic async setup — berkkaraal.com](https://berkkaraal.com/blog/2024/09/19/setup-fastapi-project-with-async-sqlalchemy-2-alembic-postgresql-and-docker/)
- [SQLAlchemy issue #12268 — pyright ORM constructor inference](https://github.com/sqlalchemy/sqlalchemy/issues/12268)
- [psycopg3 + psycopg_pool — Using with SQLAlchemy](https://github.com/sqlalchemy/sqlalchemy/discussions/12522)
