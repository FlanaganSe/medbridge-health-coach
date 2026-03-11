"""Coach context for LangGraph runtime dependency injection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

    from health_coach.domain.consent import ConsentService
    from health_coach.domain.scheduling import CoachConfig
    from health_coach.settings import Settings


@dataclass
class CoachContext:
    """Runtime context passed to graph nodes via configurable["ctx"].

    Provides access to database sessions, services, and configuration
    without coupling nodes to global state.
    """

    session_factory: async_sessionmaker[AsyncSession]
    engine: AsyncEngine
    consent_service: ConsentService
    settings: Settings
    coach_config: CoachConfig


def get_coach_context(config: RunnableConfig) -> CoachContext:
    """Extract CoachContext from a LangGraph RunnableConfig."""
    ctx: CoachContext = config["configurable"]["ctx"]  # type: ignore[typeddict-item]
    return ctx
