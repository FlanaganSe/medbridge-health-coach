"""Clinician alert tool — alert_clinician."""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportMissingTypeArgument=false, reportUnknownParameterType=false
# pyright: reportAttributeAccessIssue=false

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Annotated, Any, Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

if TYPE_CHECKING:
    from health_ally.agent.state import PendingEffects


_VALID_PRIORITIES = ("routine", "urgent")


@tool
def alert_clinician(
    reason: str,
    priority: Literal["routine", "urgent"],
    state: Annotated[dict[str, Any], InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Send an alert to the patient's clinician.

    Args:
        reason: Description of why the clinician should be alerted.
        priority: Alert priority — "routine" or "urgent".
    """
    # Runtime coercion: LLMs may pass values outside the Literal set
    coerced = priority not in _VALID_PRIORITIES
    if coerced:
        priority = "routine"  # type: ignore[assignment]

    patient_id = state.get("patient_id", "")
    content_hash = hashlib.sha256(reason.encode()).hexdigest()[:16]
    idempotency_key = f"{patient_id}:alert:{content_hash}"

    current_effects: PendingEffects = state.get("pending_effects") or {}
    existing_alerts: list[dict[str, object]] = list(current_effects.get("alerts", []))

    existing_alerts.append(
        {
            "reason": reason,
            "priority": priority,
            "idempotency_key": idempotency_key,
        }
    )

    updated_effects: PendingEffects = {
        **current_effects,  # type: ignore[typeddict-item]
        "alerts": existing_alerts,
    }

    note = " (coerced from invalid value to 'routine')" if coerced else ""
    return Command(
        update={
            "pending_effects": updated_effects,
            "messages": [
                ToolMessage(
                    content=f"Clinician alert created ({priority}){note}: {reason}",
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )
