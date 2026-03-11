"""Shared test fixtures."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from health_coach.main import create_app
from health_coach.persistence.db import create_session_factory
from health_coach.settings import Settings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from fastapi import FastAPI

TEST_DATABASE_URL = "sqlite+aiosqlite://"


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        environment="dev",
        log_level="DEBUG",
        log_format="console",
    )


@pytest.fixture(scope="session")
async def engine() -> AsyncGenerator[AsyncEngine, None]:
    eng = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as sess:
        yield sess


@pytest.fixture
async def app(settings: Settings, engine: AsyncEngine) -> FastAPI:
    application = create_app(settings)
    application.state.engine = engine
    application.state.session_factory = create_session_factory(engine)
    application.state.langgraph_pool = None
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac
