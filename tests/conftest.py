"""Shared test fixtures and helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

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
from health_coach.persistence.models import Base
from health_coach.settings import Settings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from fastapi import FastAPI

TEST_DATABASE_URL = "sqlite+aiosqlite://"


def make_mock_session(mock_patient: object = None) -> AsyncMock:
    """Create a mock async session for graph integration tests.

    The mock supports async context manager, `.get()`, `.begin()`, `.add()`,
    and `.execute()`. Use `mock_patient` to control what `.get()` returns.
    """
    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_patient)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock(return_value=AsyncMock())
    mock_session.begin().__aenter__ = AsyncMock(return_value=None)
    mock_session.begin().__aexit__ = AsyncMock(return_value=None)
    return mock_session


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
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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
