"""Database engine, session factory, and LangGraph pool management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from health_coach.settings import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """Create the async SQLAlchemy engine."""
    kwargs: dict[str, object] = {}

    if settings.is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_pre_ping"] = True
        kwargs["pool_size"] = settings.db_pool_size
        kwargs["max_overflow"] = settings.db_max_overflow

    return create_async_engine(settings.database_url, **kwargs)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory with expire_on_commit=False."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_langgraph_pool(settings: Settings) -> AsyncConnectionPool | None:
    """Create psycopg3 connection pool for LangGraph checkpointer.

    Returns None when using SQLite (tests use InMemorySaver instead).
    """
    if not settings.is_postgres:
        return None

    from psycopg_pool import AsyncConnectionPool

    raw_url = settings.database_url.replace("postgresql+psycopg://", "postgresql://")
    return AsyncConnectionPool(
        conninfo=raw_url,
        min_size=2,
        max_size=settings.langgraph_pool_size,
        open=False,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
        },
    )


@asynccontextmanager
async def get_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional async session scope."""
    async with session_factory() as session:
        yield session


async def run_bootstrap(settings: Settings) -> None:
    """Bootstrap LangGraph checkpoint tables.

    Runs checkpointer.setup() to create required tables.
    Should be called after Alembic migrations, not at every app startup.
    """
    if settings.is_postgres:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        pool = await create_langgraph_pool(settings)
        if pool is not None:
            await pool.open(wait=True)
            try:
                checkpointer = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
                await checkpointer.setup()
            finally:
                await pool.close()
    else:
        pass  # InMemorySaver has no setup
