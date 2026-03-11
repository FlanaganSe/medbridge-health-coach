"""FastAPI application with lifespan management."""

# pyright: reportUnknownVariableType=false

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

import structlog
from fastapi import FastAPI

from health_coach.api.middleware.logging import RequestLoggingMiddleware
from health_coach.api.routes.chat import router as chat_router
from health_coach.api.routes.health import router as health_router
from health_coach.api.routes.state import router as state_router
from health_coach.api.routes.webhooks import router as webhook_router
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

    # Set up graph and context factory for API endpoints
    _setup_graph_and_context(app, session_factory, engine, settings)

    if langgraph_pool is not None:
        await langgraph_pool.open(wait=True)
        await logger.ainfo("langgraph_pool_opened")

    # Start background workers in "all" mode
    worker_task: asyncio.Task[None] | None = None
    if settings.app_mode == "all":
        worker_task = asyncio.create_task(
            _run_background_workers(session_factory, engine, settings),
            name="background_workers",
        )

    await logger.ainfo(
        "app_started",
        mode=settings.app_mode,
        database=("postgres" if settings.is_postgres else "sqlite"),
    )

    yield

    # Shutdown workers
    if worker_task is not None:
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task

    if langgraph_pool is not None:
        await langgraph_pool.close()
        await logger.ainfo("langgraph_pool_closed")

    await engine.dispose()
    await logger.ainfo("app_shutdown")


def _setup_graph_and_context(
    app: FastAPI,
    session_factory: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    settings: Settings,
) -> None:
    """Set up graph and context factory on app.state for API endpoints."""
    from langgraph.checkpoint.memory import MemorySaver

    from health_coach.agent.context import create_context_factory
    from health_coach.agent.graph import compile_graph
    from health_coach.domain.scheduling import CoachConfig
    from health_coach.integrations.consent_factory import create_consent_service
    from health_coach.integrations.model_gateway import AnthropicModelGateway

    coach_config = CoachConfig()
    model_gateway = AnthropicModelGateway(settings)
    consent_service = create_consent_service(settings)
    graph = compile_graph(checkpointer=MemorySaver())

    ctx_factory = create_context_factory(
        consent_service=consent_service,
        settings=settings,
        coach_config=coach_config,
        model_gateway=model_gateway,
    )

    app.state.graph = graph
    app.state.ctx_factory = ctx_factory


async def _run_background_workers(
    session_factory: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    settings: Settings,
) -> None:
    """Run scheduler and delivery workers as background tasks (all mode only)."""
    from langgraph.checkpoint.memory import MemorySaver

    from health_coach.agent.context import create_context_factory
    from health_coach.agent.graph import compile_graph
    from health_coach.domain.scheduling import CoachConfig
    from health_coach.integrations.alert_channel import MockAlertChannel
    from health_coach.integrations.consent_factory import create_consent_service
    from health_coach.integrations.model_gateway import AnthropicModelGateway
    from health_coach.integrations.notification import MockNotificationChannel
    from health_coach.orchestration.delivery_worker import DeliveryWorker
    from health_coach.orchestration.jobs import (
        FollowupJobHandler,
        JobDispatcher,
        OnboardingTimeoutHandler,
    )
    from health_coach.orchestration.reconciliation import startup_recovery
    from health_coach.orchestration.scheduler import SchedulerWorker

    logger = structlog.stdlib.get_logger()

    coach_config = CoachConfig()
    model_gateway = AnthropicModelGateway(settings)
    consent_service = create_consent_service(settings)

    graph = compile_graph(checkpointer=MemorySaver())

    ctx_factory = create_context_factory(
        consent_service=consent_service,
        settings=settings,
        coach_config=coach_config,
        model_gateway=model_gateway,
    )

    followup_handler = FollowupJobHandler(graph=graph, ctx_factory=ctx_factory)
    timeout_handler = OnboardingTimeoutHandler()
    dispatcher = JobDispatcher(
        followup_handler=followup_handler,
        timeout_handler=timeout_handler,
    )

    await startup_recovery(session_factory)

    scheduler = SchedulerWorker(
        session_factory=session_factory,
        engine=engine,
        dispatcher=dispatcher,
        poll_interval_seconds=settings.scheduler_poll_interval_seconds,
        coach_config=coach_config,
    )

    delivery = DeliveryWorker(
        session_factory=session_factory,
        consent_service=consent_service,
        notification_channel=MockNotificationChannel(),
        alert_channel=MockAlertChannel(),
        poll_interval_seconds=settings.delivery_poll_interval_seconds,
    )

    await logger.ainfo("background_workers_started")

    # Run both workers concurrently
    await asyncio.gather(
        scheduler.run(),
        delivery.run(),
    )


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

    # Middleware
    app.add_middleware(RequestLoggingMiddleware)

    # Routes
    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(state_router)
    app.include_router(webhook_router)

    return app
