"""Patient phase lifecycle as a StrEnum."""

from __future__ import annotations

from enum import StrEnum


class PatientPhase(StrEnum):
    """Deterministic patient lifecycle phases.

    Transitions are application-controlled, never LLM-driven.
    See phase_machine.py for the transition adjacency map.
    """

    PENDING = "pending"
    ONBOARDING = "onboarding"
    ACTIVE = "active"
    RE_ENGAGING = "re_engaging"
    DORMANT = "dormant"
