"""Reminder tool — set_reminder."""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportMissingTypeArgument=false, reportUnknownParameterType=false
# pyright: reportAttributeAccessIssue=false

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

if TYPE_CHECKING:
    from health_ally.agent.state import PendingEffects


@tool
def set_reminder(
    reminder_time: str,
    reminder_message: str,
    state: Annotated[dict[str, Any], InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Schedule a reminder for the patient's exercises.

    Args:
        reminder_time: ISO 8601 datetime string for when to send the reminder.
        reminder_message: The message to include in the reminder.
    """
    patient_id = state.get("patient_id", "")
    content_hash = hashlib.sha256(f"{reminder_time}:{reminder_message}".encode()).hexdigest()[:16]
    idempotency_key = f"{patient_id}:reminder:{content_hash}"

    current_effects: PendingEffects = state.get("pending_effects") or {}
    existing_jobs: list[dict[str, object]] = list(current_effects.get("scheduled_jobs", []))

    try:
        scheduled_at = datetime.fromisoformat(reminder_time)
    except ValueError:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=(
                            f"Invalid reminder time format: '{reminder_time}'. "
                            "Please provide a valid ISO 8601 datetime string "
                            "(e.g., '2024-01-15T09:00:00')."
                        ),
                        tool_call_id=tool_call_id,
                    )
                ],
            }
        )

    existing_jobs.append(
        {
            "job_type": "reminder",
            "idempotency_key": idempotency_key,
            "scheduled_at": scheduled_at,
            "metadata": {"message": reminder_message},
        }
    )

    updated_effects: PendingEffects = {
        **current_effects,  # type: ignore[typeddict-item]
        "scheduled_jobs": existing_jobs,
    }

    return Command(
        update={
            "pending_effects": updated_effects,
            "messages": [
                ToolMessage(
                    content=f"Reminder scheduled for {reminder_time}: {reminder_message}",
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )
