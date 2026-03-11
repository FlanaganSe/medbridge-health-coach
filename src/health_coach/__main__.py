"""Entry point: `uv run python -m health_coach`."""

# pyright: reportUnknownVariableType=false

from __future__ import annotations

import argparse
import sys
from typing import Literal


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="MedBridge Health Coach")
    parser.add_argument(
        "--mode",
        choices=["api", "worker", "all"],
        default="all",
        help="Run mode: api (HTTP only), worker (background only), all (default)",
    )
    parser.add_argument("--host", default=None, help="Override host")
    parser.add_argument("--port", type=int, default=None, help="Override port")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Run the application in the specified mode."""
    args = parse_args(argv)
    mode: Literal["api", "worker", "all"] = args.mode

    import os

    os.environ.setdefault("APP_MODE", mode)

    if mode == "worker":
        import asyncio

        asyncio.run(_run_worker())
    else:
        import uvicorn

        from health_coach.settings import Settings

        settings = Settings(app_mode=mode)
        host = args.host or settings.host
        port = args.port or settings.port

        uvicorn.run(
            "health_coach.main:create_app",
            host=host,
            port=port,
            factory=True,
            log_level=settings.log_level.lower(),
        )


async def _run_worker() -> None:
    """Run background workers without HTTP server."""
    import asyncio

    import structlog

    from health_coach.agent.context import CoachContext
    from health_coach.agent.graph import compile_graph
    from health_coach.domain.consent import FakeConsentService
    from health_coach.domain.scheduling import CoachConfig
    from health_coach.integrations.model_gateway import AnthropicModelGateway
    from health_coach.observability.logging import configure_logging
    from health_coach.orchestration.jobs import (
        FollowupJobHandler,
        JobDispatcher,
        OnboardingTimeoutHandler,
    )
    from health_coach.orchestration.reconciliation import startup_recovery
    from health_coach.orchestration.scheduler import SchedulerWorker
    from health_coach.persistence.db import create_engine, create_session_factory
    from health_coach.settings import Settings

    settings = Settings(app_mode="worker")
    configure_logging(
        log_format=settings.log_format,
        log_level=settings.log_level,
        environment=settings.environment,
    )
    log = structlog.stdlib.get_logger()
    await log.ainfo("worker_mode_started")

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    coach_config = CoachConfig()
    model_gateway = AnthropicModelGateway(settings)

    # Compile graph with appropriate checkpointer
    from langgraph.checkpoint.memory import MemorySaver

    graph = compile_graph(checkpointer=MemorySaver())

    def ctx_factory(
        session_factory: object,
        engine: object,
    ) -> CoachContext:
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

    # Startup reconciliation
    await startup_recovery(session_factory)

    # Start scheduler
    scheduler = SchedulerWorker(
        session_factory=session_factory,
        engine=engine,
        dispatcher=dispatcher,
        poll_interval_seconds=settings.scheduler_poll_interval_seconds,
    )

    shutdown_event = scheduler.shutdown_event

    try:
        await log.ainfo("worker_running")
        await scheduler.run()
    except asyncio.CancelledError:
        shutdown_event.set()
    finally:
        await engine.dispose()
        await log.ainfo("worker_shutdown")


if __name__ == "__main__":
    main(sys.argv[1:])
