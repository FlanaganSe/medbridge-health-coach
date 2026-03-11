"""Patient repository."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from health_coach.persistence.models import Patient
from health_coach.persistence.repositories.base import BaseRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class PatientRepository(BaseRepository[Patient]):
    """Repository for Patient entities."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Patient)

    async def get_by_external_id(self, tenant_id: str, external_patient_id: str) -> Patient | None:
        """Find a patient by tenant and external ID."""
        stmt = select(Patient).where(
            Patient.tenant_id == tenant_id,
            Patient.external_patient_id == external_patient_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
