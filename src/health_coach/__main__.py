"""Entry point: `uv run python -m health_coach`."""

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
    import structlog

    from health_coach.observability.logging import configure_logging
    from health_coach.settings import Settings

    settings = Settings(app_mode="worker")
    configure_logging(
        log_format=settings.log_format,
        log_level=settings.log_level,
        environment=settings.environment,
    )
    log = structlog.stdlib.get_logger()
    await log.ainfo("worker_mode_started")
    # Workers (scheduler, delivery) will be wired in M5
    await log.ainfo("worker_mode_no_workers_configured")


if __name__ == "__main__":
    main(sys.argv[1:])
