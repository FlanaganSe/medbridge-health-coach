"""Read-only state query endpoints for patient data.

All endpoints are tenant-scoped via auth context.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
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


@router.get("/{patient_id}/phase")
async def get_patient_phase(
    patient_id: str,
    request: Request,
    auth: AuthContext = Depends(get_auth_context),  # noqa: B008
) -> dict[str, str]:
    """Get a patient's current phase."""
    patient = await _get_patient(request, patient_id, auth.tenant_id)
    return {"patient_id": patient_id, "phase": patient.phase}


@router.get("/{patient_id}/goals")
async def get_patient_goals(
    patient_id: str,
    request: Request,
    auth: AuthContext = Depends(get_auth_context),  # noqa: B008
) -> dict[str, Any]:
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

    return {
        "patient_id": patient_id,
        "goals": [
            {
                "id": str(g.id),
                "goal_text": g.goal_text,
                "confirmed_at": g.confirmed_at.isoformat() if g.confirmed_at else None,
                "created_at": g.created_at.isoformat(),
            }
            for g in goals
        ],
    }


@router.get("/{patient_id}/safety-decisions")
async def get_safety_decisions(
    patient_id: str,
    request: Request,
    auth: AuthContext = Depends(get_auth_context),  # noqa: B008
) -> dict[str, Any]:
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

    return {
        "patient_id": patient_id,
        "decisions": [
            {
                "id": str(d.id),
                "decision": d.decision,
                "source": d.source,
                "confidence": d.confidence,
                "created_at": d.created_at.isoformat(),
            }
            for d in decisions
        ],
    }


@router.get("/{patient_id}/alerts")
async def get_clinician_alerts(
    patient_id: str,
    request: Request,
    auth: AuthContext = Depends(get_auth_context),  # noqa: B008
) -> dict[str, Any]:
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

    return {
        "patient_id": patient_id,
        "alerts": [
            {
                "id": str(a.id),
                "reason": a.reason,
                "priority": a.priority,
                "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
                "created_at": a.created_at.isoformat(),
            }
            for a in alerts
        ],
    }


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
