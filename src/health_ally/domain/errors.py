"""Domain-specific errors."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from health_ally.domain.phases import PatientPhase


class PhaseTransitionError(Exception):
    """Raised when an invalid phase transition is attempted."""

    def __init__(
        self,
        current: PatientPhase,
        event: str,
        *,
        message: str | None = None,
    ) -> None:
        self.current = current
        self.event = event
        msg = message or f"Invalid transition: {current} + {event}"
        super().__init__(msg)


class ConsentDeniedError(Exception):
    """Raised when patient consent check fails."""

    def __init__(self, patient_id: str, reason: str) -> None:
        self.patient_id = patient_id
        self.reason = reason
        super().__init__(f"Consent denied for {patient_id}: {reason}")
