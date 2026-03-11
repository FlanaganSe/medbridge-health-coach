"""Helpers for accumulating pending effects in graph nodes.

Replaces the repetitive get-or-default → spread → append pattern
used across 8+ node instances.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from health_ally.agent.state import PatientState, PendingEffects


def accumulate_effects(
    state: PatientState,
    *,
    alerts: list[dict[str, object]] | None = None,
    audit_events: list[dict[str, object]] | None = None,
    scheduled_jobs: list[dict[str, object]] | None = None,
    safety_decisions: list[dict[str, object]] | None = None,
    outbox_entries: list[dict[str, object]] | None = None,
    phase_event: str | None = None,
    goal: dict[str, object] | None = None,
) -> PendingEffects:
    """Merge new items into the state's existing pending_effects.

    Each list-type field appends to existing items.
    Scalar fields (phase_event, goal) overwrite.
    """
    current: PendingEffects = state.get("pending_effects") or {}

    result: PendingEffects = {**current}  # type: ignore[typeddict-item]

    if alerts:
        result["alerts"] = [*current.get("alerts", []), *alerts]
    if audit_events:
        result["audit_events"] = [*current.get("audit_events", []), *audit_events]
    if scheduled_jobs:
        result["scheduled_jobs"] = [*current.get("scheduled_jobs", []), *scheduled_jobs]
    if safety_decisions:
        result["safety_decisions"] = [*current.get("safety_decisions", []), *safety_decisions]
    if outbox_entries:
        result["outbox_entries"] = [*current.get("outbox_entries", []), *outbox_entries]
    if phase_event is not None:
        result["phase_event"] = phase_event
    if goal is not None:
        result["goal"] = goal

    return result
