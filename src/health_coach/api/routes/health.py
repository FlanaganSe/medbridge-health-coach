"""Health check endpoints: liveness and readiness."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Request
from sqlalchemy import text

router = APIRouter(prefix="/health", tags=["health"])
logger = structlog.stdlib.get_logger()


@router.get("/live")
async def liveness() -> dict[str, str]:
    """Liveness probe — always returns 200, no dependency checks."""
    return {"status": "ok"}


@router.get("/ready")
async def readiness(request: Request) -> dict[str, Any]:
    """Readiness probe — checks database connectivity."""
    checks: dict[str, str] = {}

    session_factory = request.app.state.session_factory
    try:
        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        logger.warning("db_health_check_failed", exc_info=True)
        checks["database"] = "unavailable"

    lg_pool = request.app.state.langgraph_pool
    if lg_pool is not None:
        try:
            async with lg_pool.connection() as conn:
                await conn.execute("SELECT 1")
            checks["langgraph_pool"] = "ok"
        except Exception:
            logger.warning("langgraph_health_check_failed", exc_info=True)
            checks["langgraph_pool"] = "unavailable"
    else:
        checks["langgraph_pool"] = "not_configured"

    all_ok = all(v in ("ok", "not_configured") for v in checks.values())
    if not all_ok:
        from fastapi.responses import JSONResponse

        return JSONResponse(  # type: ignore[return-value]
            status_code=503,
            content={"status": "unavailable", "checks": checks},
        )

    return {"status": "ok", "checks": checks}
