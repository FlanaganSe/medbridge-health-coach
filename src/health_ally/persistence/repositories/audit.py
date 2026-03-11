"""Audit repository — append-only, no update or delete."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from health_ally.persistence.models import AuditEvent
from health_ally.persistence.repositories.base import BaseRepository

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession


class AuditRepository(BaseRepository[AuditEvent]):
    """Append-only repository for audit events."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, AuditEvent)

    async def update(self, entity: AuditEvent, **values: object) -> AuditEvent:
        """Audit events are immutable — updates are not allowed."""
        msg = "Audit events are immutable"
        raise NotImplementedError(msg)

    async def list_by_patient(
        self,
        patient_id: uuid.UUID,
        *,
        event_type: str | None = None,
    ) -> Sequence[AuditEvent]:
        """List audit events for a patient, optionally filtered by type."""
        stmt = select(AuditEvent).where(AuditEvent.patient_id == patient_id)
        if event_type is not None:
            stmt = stmt.where(AuditEvent.event_type == event_type)
        stmt = stmt.order_by(AuditEvent.created_at.desc())
        result = await self._session.execute(stmt)
        return result.scalars().all()
