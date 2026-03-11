"""Generic base repository."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from sqlalchemy import select

from health_ally.persistence.models import Base

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository[ModelT: Base]:
    """Generic repository with common CRUD operations.

    Callers own the transaction — repository uses flush, not commit.
    """

    def __init__(self, session: AsyncSession, model_class: type[ModelT]) -> None:
        self._session = session
        self._model_class = model_class

    async def create(self, entity: ModelT) -> ModelT:
        """Add an entity and flush (caller commits)."""
        self._session.add(entity)
        await self._session.flush()
        return entity

    async def get_by_id(self, entity_id: uuid.UUID) -> ModelT | None:
        """Get an entity by primary key."""
        return await self._session.get(self._model_class, entity_id)

    async def list_by(self, **filters: object) -> Sequence[ModelT]:
        """List entities matching filter criteria."""
        stmt = select(self._model_class).filter_by(**filters)
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def update(self, entity: ModelT, **values: object) -> ModelT:
        """Update entity attributes and flush."""
        for key, value in values.items():
            setattr(entity, key, value)
        await self._session.flush()
        return entity
