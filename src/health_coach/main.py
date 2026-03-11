"""FastAPI application with lifespan management."""

# pyright: reportUnknownVariableType=false

from __future__ import annotations

import asyncio
import contextlib
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


async def _run_background_workers(
    session_factory: object,
    engine: object,
    settings: Settings,
) -> None:
    """Run scheduler worker as a background task (all mode only)."""
    from health_coach.agent.context import CoachContext
    from health_coach.agent.graph import compile_graph
    from health_coach.domain.consent import FakeConsentService
    from health_coach.domain.scheduling import CoachConfig
    from health_coach.integrations.model_gateway import AnthropicModelGateway
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

    from langgraph.checkpoint.memory import MemorySaver

    graph = compile_graph(checkpointer=MemorySaver())

    def ctx_factory(session_factory: object, engine: object) -> CoachContext:
        return CoachContext(
            session_factory=session_factory,  # type: ignore[arg-type]
            engine=engine,  # type: ignore[arg-type]
            consent_service=FakeConsentService(logged_in=True, consented=True),
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

    await startup_recovery(session_factory)  # type: ignore[arg-type]

    scheduler = SchedulerWorker(
        session_factory=session_factory,  # type: ignore[arg-type]
        engine=engine,  # type: ignore[arg-type]
        dispatcher=dispatcher,
        poll_interval_seconds=settings.scheduler_poll_interval_seconds,
        coach_config=coach_config,
    )

    await logger.ainfo("background_workers_started")
    await scheduler.run()


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
