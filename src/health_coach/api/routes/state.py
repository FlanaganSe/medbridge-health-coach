"""Read-only state query endpoints for patient data.

All endpoints are tenant-scoped via auth context.
"""

from __future__ import annotations

import uuid
from datetime import datetime  # noqa: TC003 — Pydantic needs runtime access

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from health_coach.api.dependencies import AuthContext, get_auth_context
from health_coach.persistence.models import (
    ClinicianAlert,
    Patient,
    PatientGoal,
    SafetyDecisionRecord,
)

logger = structlog.stdlib.get_logger()
router = APIRouter(prefix="/v1/patients", tags=["state"])


# --- Response models ---


class PhaseResponse(BaseModel):
    patient_id: str
    phase: str


class GoalItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    goal_text: str
    confirmed_at: datetime | None
    created_at: datetime


class GoalsResponse(BaseModel):
    patient_id: str
    goals: list[GoalItem]


class SafetyDecisionItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    decision: str
    source: str
    confidence: float | None
    created_at: datetime


class SafetyDecisionsResponse(BaseModel):
    patient_id: str
    decisions: list[SafetyDecisionItem]


class AlertItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reason: str
    priority: str
    acknowledged_at: datetime | None
    created_at: datetime


class AlertsResponse(BaseModel):
    patient_id: str
    alerts: list[AlertItem]


# --- Endpoints ---


@router.get("/{patient_id}/phase", response_model=PhaseResponse)
async def get_patient_phase(
    patient_id: str,
    request: Request,
    auth: AuthContext = Depends(get_auth_context),  # noqa: B008
) -> PhaseResponse:
    """Get a patient's current phase."""
    patient = await _get_patient(request, patient_id, auth.tenant_id)
    return PhaseResponse(patient_id=patient_id, phase=patient.phase)


@router.get("/{patient_id}/goals", response_model=GoalsResponse)
async def get_patient_goals(
    patient_id: str,
    request: Request,
    auth: AuthContext = Depends(get_auth_context),  # noqa: B008
) -> GoalsResponse:
    """Get a patient's goals."""
    patient = await _get_patient(request, patient_id, auth.tenant_id)
    session_factory = request.app.state.session_factory

    async with session_factory() as session:
        result = await session.execute(
            select(PatientGoal)
            .where(PatientGoal.patient_id == patient.id)
            .order_by(PatientGoal.created_at.desc())
        )
        goals = list(result.scalars().all())

    return GoalsResponse(
        patient_id=patient_id,
        goals=[GoalItem.model_validate(g) for g in goals],
    )


@router.get("/{patient_id}/safety-decisions", response_model=SafetyDecisionsResponse)
async def get_safety_decisions(
    patient_id: str,
    request: Request,
    auth: AuthContext = Depends(get_auth_context),  # noqa: B008
) -> SafetyDecisionsResponse:
    """Get a patient's safety decision history."""
    patient = await _get_patient(request, patient_id, auth.tenant_id)
    session_factory = request.app.state.session_factory

    async with session_factory() as session:
        result = await session.execute(
            select(SafetyDecisionRecord)
            .where(SafetyDecisionRecord.patient_id == patient.id)
            .order_by(SafetyDecisionRecord.created_at.desc())
            .limit(50)
        )
        decisions = list(result.scalars().all())

    return SafetyDecisionsResponse(
        patient_id=patient_id,
        decisions=[SafetyDecisionItem.model_validate(d) for d in decisions],
    )


@router.get("/{patient_id}/alerts", response_model=AlertsResponse)
async def get_clinician_alerts(
    patient_id: str,
    request: Request,
    auth: AuthContext = Depends(get_auth_context),  # noqa: B008
) -> AlertsResponse:
    """Get clinician alerts for a patient."""
    patient = await _get_patient(request, patient_id, auth.tenant_id)
    session_factory = request.app.state.session_factory

    async with session_factory() as session:
        result = await session.execute(
            select(ClinicianAlert)
            .where(ClinicianAlert.patient_id == patient.id)
            .order_by(ClinicianAlert.created_at.desc())
            .limit(50)
        )
        alerts = list(result.scalars().all())

    return AlertsResponse(
        patient_id=patient_id,
        alerts=[AlertItem.model_validate(a) for a in alerts],
    )


async def _get_patient(
    request: Request,
    patient_id: str,
    tenant_id: str,
) -> Patient:
    """Retrieve a patient, scoped by tenant. Raises 404 if not found."""
    session_factory = request.app.state.session_factory

    async with session_factory() as session:
        try:
            pid = uuid.UUID(patient_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid patient_id format") from exc

        result = await session.execute(
            select(Patient).where(
                Patient.id == pid,
                Patient.tenant_id == tenant_id,
            )
        )
        patient = result.scalars().first()

    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")

    return patient
