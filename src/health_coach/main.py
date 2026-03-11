"""FastAPI application with lifespan management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

import structlog
from fastapi import FastAPI

from health_coach.api.routes.health import router as health_router
from health_coach.observability.logging import configure_logging
from health_coach.persistence.db import (
    create_engine,
    create_langgraph_pool,
    create_session_factory,
)
from health_coach.settings import Settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifecycle: startup and shutdown."""
    settings: Settings = app.state.settings

    configure_logging(
        log_format=settings.log_format,
        log_level=settings.log_level,
        environment=settings.environment,
    )
    logger = structlog.stdlib.get_logger()

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    langgraph_pool = await create_langgraph_pool(settings)

    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.langgraph_pool = langgraph_pool

    if langgraph_pool is not None:
        await langgraph_pool.open(wait=True)
        await logger.ainfo("langgraph_pool_opened")

    await logger.ainfo(
        "app_started",
        mode=settings.app_mode,
        database=("postgres" if settings.is_postgres else "sqlite"),
    )

    yield

    if langgraph_pool is not None:
        await langgraph_pool.close()
        await logger.ainfo("langgraph_pool_closed")

    await engine.dispose()
    await logger.ainfo("app_shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    if settings is None:
        settings = Settings()

    app = FastAPI(
        title="MedBridge Health Coach",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings

    app.include_router(health_router)

    return app
