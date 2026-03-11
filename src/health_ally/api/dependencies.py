"""FastAPI dependency injection factories.

Dev/demo: Header-based identity via X-Patient-ID and X-Tenant-ID.
Production: Swap for JWT/API-key auth when MedBridge Go contract is finalized.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException


@dataclass(frozen=True)
class AuthContext:
    """Authenticated request context."""

    patient_id: str
    tenant_id: str


async def get_auth_context(
    x_patient_id: str = Header(..., alias="X-Patient-ID"),
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
) -> AuthContext:
    """Extract auth context from request headers.

    Dev/demo mode: trusts X-Patient-ID and X-Tenant-ID headers directly.
    Production: replace with JWT or API key validation.
    """
    if not x_patient_id or not x_tenant_id:
        raise HTTPException(status_code=401, detail="Missing auth headers")
    return AuthContext(patient_id=x_patient_id, tenant_id=x_tenant_id)
