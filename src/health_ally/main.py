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
from fastapi.middleware.cors import CORSMiddleware

from health_ally.api.middleware.logging import RequestLoggingMiddleware
from health_ally.api.routes.chat import router as chat_router
from health_ally.api.routes.health import router as health_router
from health_ally.api.routes.state import router as state_router
from health_ally.api.routes.webhooks import router as webhook_router
from health_ally.observability.logging import configure_logging
from health_ally.persistence.db import (
    create_engine,
    create_langgraph_pool,
    create_session_factory,
)
from health_ally.settings import Settings


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
        # Ensure LangGraph checkpoint tables exist (idempotent)
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        await AsyncPostgresSaver(langgraph_pool).setup()  # type: ignore[arg-type]
        await logger.ainfo("langgraph_pool_opened")

    # Set up graph and context factory for API endpoints
    _setup_graph_and_context(app, session_factory, engine, settings, langgraph_pool)

    # Start background workers in "all" mode
    worker_task: asyncio.Task[None] | None = None
    if settings.app_mode == "all":
        worker_task = asyncio.create_task(
            _run_background_workers(session_factory, engine, settings, langgraph_pool),
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

    from health_ally.observability.langfuse import langfuse_shutdown

    langfuse_shutdown()
    await engine.dispose()
    await logger.ainfo("app_shutdown")


def _setup_graph_and_context(
    app: FastAPI,
    session_factory: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
    settings: Settings,
    langgraph_pool: object | None = None,
) -> None:
    """Set up graph and context factory on app.state for API endpoints."""
    from health_ally.agent.context import create_context_factory
    from health_ally.agent.graph import compile_graph
    from health_ally.domain.scheduling import CoachConfig
    from health_ally.integrations.consent_factory import create_consent_service
    from health_ally.integrations.model_gateway import AnthropicModelGateway
    from health_ally.persistence.db import create_checkpointer

    coach_config = CoachConfig()
    model_gateway = AnthropicModelGateway(settings)
    consent_service = create_consent_service(settings)
    checkpointer = create_checkpointer(langgraph_pool)
    graph = compile_graph(checkpointer=checkpointer)  # type: ignore[arg-type]

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
    langgraph_pool: object | None = None,
) -> None:
    """Run scheduler and delivery workers as background tasks (all mode only)."""
    from health_ally.agent.context import create_context_factory
    from health_ally.agent.graph import compile_graph
    from health_ally.domain.scheduling import CoachConfig
    from health_ally.integrations.channels import (
        create_alert_channel,
        create_notification_channel,
    )
    from health_ally.integrations.consent_factory import create_consent_service
    from health_ally.integrations.model_gateway import AnthropicModelGateway
    from health_ally.orchestration.delivery_worker import DeliveryWorker
    from health_ally.orchestration.jobs import (
        FollowupJobHandler,
        JobDispatcher,
        OnboardingTimeoutHandler,
        ReminderJobHandler,
    )
    from health_ally.orchestration.reconciliation import startup_recovery
    from health_ally.orchestration.scheduler import SchedulerWorker
    from health_ally.persistence.db import create_checkpointer

    logger = structlog.stdlib.get_logger()

    coach_config = CoachConfig()
    model_gateway = AnthropicModelGateway(settings)
    consent_service = create_consent_service(settings)

    checkpointer = create_checkpointer(langgraph_pool)
    graph = compile_graph(checkpointer=checkpointer)  # type: ignore[arg-type]

    ctx_factory = create_context_factory(
        consent_service=consent_service,
        settings=settings,
        coach_config=coach_config,
        model_gateway=model_gateway,
    )

    followup_handler = FollowupJobHandler(
        graph=graph, ctx_factory=ctx_factory, langfuse_enabled=settings.langfuse_enabled
    )
    timeout_handler = OnboardingTimeoutHandler()
    reminder_handler = ReminderJobHandler()
    dispatcher = JobDispatcher(
        followup_handler=followup_handler,
        timeout_handler=timeout_handler,
        reminder_handler=reminder_handler,
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
        notification_channel=create_notification_channel(settings),
        alert_channel=create_alert_channel(settings),
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
        title="Health Ally",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings

    # Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestLoggingMiddleware)

    # Routes
    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(state_router)
    app.include_router(webhook_router)

    if settings.environment == "dev":
        from health_ally.api.routes.demo import router as demo_router

        app.include_router(demo_router)

        # Serve demo UI static files when available (built into Docker image)
        import pathlib

        static_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "static"
        if not static_dir.is_dir():
            static_dir = pathlib.Path("/app/static")
        if static_dir.is_dir():
            from starlette.staticfiles import StaticFiles

            app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app
