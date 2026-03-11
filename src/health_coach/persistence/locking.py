"""Patient-scoped advisory lock for graph invocation serialization.

Uses pg_advisory_lock (session-level) on a dedicated AUTOCOMMIT connection.
This prevents concurrent graph invocations for the same patient from interleaving,
while keeping the connection idle (not idle-in-transaction) during LLM calls.

SQLite: no-op — SQLite's global write lock provides equivalent serialization.
"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncEngine


def _patient_lock_key(patient_id: str) -> int:
    """Derive a deterministic 32-bit lock key from patient_id.

    Uses hashlib.sha256 (NOT Python's hash()) because hash() is salted
    per process via PYTHONHASHSEED, producing different values across
    API server and worker processes.
    """
    digest = hashlib.sha256(patient_id.encode()).digest()[:4]
    return int.from_bytes(digest, "big") & 0x7FFFFFFF


@asynccontextmanager
async def patient_advisory_lock(
    engine: AsyncEngine,
    patient_id: str,
) -> AsyncGenerator[None, None]:
    """Acquire a patient-scoped advisory lock for the duration of graph execution.

    Must be called at the graph invocation call site (chat endpoint, webhook
    handler, scheduler job handler), NOT inside graph nodes.

    The connection uses AUTOCOMMIT to prevent SQLAlchemy 2.x autobegin,
    keeping the connection truly idle (not idle-in-transaction) during
    5-30s LLM calls.
    """
    if "sqlite" in str(engine.url):
        yield
        return

    lock_key = _patient_lock_key(patient_id)

    async with engine.connect() as conn:
        await conn.execution_options(isolation_level="AUTOCOMMIT")  # type: ignore[arg-type]
        await conn.execute(
            text("SELECT pg_advisory_lock(:key)"),
            {"key": lock_key},
        )
        try:
            yield
        finally:
            await conn.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": lock_key},
            )
