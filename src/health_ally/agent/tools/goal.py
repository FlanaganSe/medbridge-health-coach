"""Goal-related tools — set_goal, get_program_summary."""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportMissingTypeArgument=false, reportUnknownParameterType=false
# pyright: reportAttributeAccessIssue=false

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from health_ally.domain.scheduling import add_jitter, calculate_send_time

if TYPE_CHECKING:
    from health_ally.agent.state import PendingEffects


@tool
def set_goal(
    goal_text: str,
    raw_patient_text: str,
    state: Annotated[dict[str, Any], InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Set or update the patient's exercise goal.

    Args:
        goal_text: The refined, structured goal statement.
        raw_patient_text: The patient's original words describing their goal.
    """
    patient_id = state.get("patient_id", "")
    content_hash = hashlib.sha256(goal_text.encode()).hexdigest()[:16]
    idempotency_key = f"{patient_id}:goal:{content_hash}"

    current_effects: PendingEffects = state.get("pending_effects") or {}

    # Schedule Day 2 follow-up (chain scheduling: only first job)
    now = datetime.now(UTC)
    base_time = now + timedelta(days=2)
    send_time = calculate_send_time(base_time, "America/New_York", 21, 8)
    send_time = add_jitter(send_time)

    day2_hash = hashlib.sha256(f"{patient_id}:day_2".encode()).hexdigest()[:16]
    day2_key = f"{patient_id}:day_2_followup:{day2_hash}"

    existing_jobs: list[dict[str, object]] = list(current_effects.get("scheduled_jobs", []))
    existing_jobs.append(
        {
            "job_type": "day_2_followup",
            "idempotency_key": day2_key,
            "scheduled_at": send_time,
            "metadata": {"follow_up_day": 2},
        }
    )

    updated_effects: PendingEffects = {
        **current_effects,  # type: ignore[typeddict-item]
        "goal": {
            "goal_text": goal_text,
            "raw_patient_text": raw_patient_text,
            "idempotency_key": idempotency_key,
        },
        "phase_event": "goal_confirmed",
        "scheduled_jobs": existing_jobs,
    }

    return Command(
        update={
            "pending_effects": updated_effects,
            "messages": [
                ToolMessage(
                    content=f"Goal set: {goal_text}",
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )


@tool
def get_program_summary(
    state: Annotated[dict[str, Any], InjectedState],
) -> str:
    """Get a summary of the patient's assigned exercise program.

    Returns a text summary of the patient's current exercises, frequency,
    and any special instructions from their care team.
    """
    # M3 stub — returns realistic mock data
    return (
        "Current exercise program:\n"
        "1. Straight leg raises — 3 sets of 10, daily\n"
        "2. Quad sets — 3 sets of 10, daily\n"
        "3. Heel slides — 2 sets of 15, every other day\n"
        "Prescribed by: Dr. Smith\n"
        "Program start: 2 weeks ago"
    )
