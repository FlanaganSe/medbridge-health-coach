"""Active phase agent node — follow-up coaching with no-response detection."""

# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog
from langchain_core.messages import AIMessage

from health_coach.agent.context import get_coach_context
from health_coach.agent.effects import accumulate_effects
from health_coach.agent.prompts.active import build_active_prompt
from health_coach.agent.state import PatientState  # noqa: TC001
from health_coach.agent.tools.adherence import get_adherence_summary
from health_coach.agent.tools.clinician import alert_clinician
from health_coach.agent.tools.goal import get_program_summary, set_goal
from health_coach.agent.tools.reminder import set_reminder
from health_coach.domain.scheduling import CoachConfig, add_jitter, calculate_send_time

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

    from health_coach.agent.state import PendingEffects

logger = structlog.stdlib.get_logger()

ACTIVE_TOOLS = [
    set_goal,
    get_program_summary,
    get_adherence_summary,
    set_reminder,
    alert_clinician,
]

# Map job_type to the next follow-up day in the chain
_NEXT_FOLLOWUP: dict[str, int | None] = {
    "day_2_followup": 5,
    "day_5_followup": 7,
    "day_7_followup": None,  # End of cadence
}


async def active_agent(
    state: PatientState,
    config: RunnableConfig,
) -> dict[str, object]:
    """Active phase agent — coaching follow-ups with no-response detection.

    On scheduler invocations:
    - Detects no-response since last outreach
    - First unanswered → triggers phase transition to RE_ENGAGING
    - Schedules next follow-up in the chain (Day 2 → 5 → 7)

    On patient invocations:
    - Responds conversationally with coaching support
    """
    ctx = get_coach_context(config)
    patient_id = state["patient_id"]
    invocation_source = state.get("invocation_source")

    # Detect no-response on scheduler-initiated invocations
    if invocation_source == "scheduler":
        no_response = _detect_no_response(state)
        if no_response:
            return _handle_unanswered_outreach(state)

    system_prompt = build_active_prompt("check_in")

    # Get coach model and bind tools
    coach_model = ctx.model_gateway.get_chat_model("coach")
    model_with_tools = coach_model.bind_tools(ACTIVE_TOOLS, parallel_tool_calls=False)

    messages = list(state.get("messages", []))

    try:
        response = await model_with_tools.ainvoke(
            [{"role": "system", "content": system_prompt}, *messages]
        )
        content = str(response.content) if response.content else None
    except Exception:
        logger.exception("active_agent_error", patient_id=patient_id)
        return {"outbound_message": None}

    # Accumulate next follow-up job if scheduler-initiated
    result: dict[str, object] = {
        "messages": [response],
        "outbound_message": content,
    }

    if invocation_source == "scheduler":
        effects = _accumulate_followup_job(state, ctx.coach_config)
        if effects:
            result["pending_effects"] = effects

    return result


def _detect_no_response(state: PatientState) -> bool:
    """Check if patient hasn't responded since last outreach."""
    last_outreach = state.get("last_outreach_at")
    if not last_outreach:
        return False

    last_response = state.get("last_patient_response_at")
    if not last_response:
        return True  # Never responded

    return last_response < last_outreach


def _handle_unanswered_outreach(state: PatientState) -> dict[str, object]:
    """Handle first unanswered outreach — transition to RE_ENGAGING."""
    unanswered = state.get("unanswered_count", 0) + 1

    effects = accumulate_effects(
        state,
        phase_event="unanswered_outreach",
        audit_events=[
            {
                "event_type": "unanswered_detected",
                "outcome": "re_engaging",
                "metadata": {"unanswered_count": unanswered},
            },
        ],
    )

    return {
        "unanswered_count": unanswered,
        "pending_effects": effects,
        "outbound_message": None,
        # Empty AIMessage so tools_condition can inspect without error
        "messages": [AIMessage(content="")],
    }


def _accumulate_followup_job(
    state: PatientState,
    coach_config: CoachConfig,
) -> PendingEffects | None:
    """Accumulate the next follow-up job in pending_effects (chain scheduling)."""
    patient_id = state["patient_id"]

    # Determine which follow-up day is next based on metadata
    metadata = state.get("_job_metadata") or {}
    current_day: int = int(metadata.get("follow_up_day", 2))  # type: ignore[arg-type]
    next_day = _NEXT_FOLLOWUP.get(f"day_{current_day}_followup")

    if next_day is None:
        return None  # End of cadence

    now = datetime.now(UTC)
    target_days = next_day - current_day if current_day else next_day
    base_time = now + timedelta(days=target_days)

    send_time = calculate_send_time(
        base_time,
        "America/New_York",  # TODO: get from patient context
        coach_config.quiet_hours_start,
        coach_config.quiet_hours_end,
    )
    send_time = add_jitter(send_time, coach_config.max_jitter_minutes)

    content_hash = hashlib.sha256(f"{patient_id}:day_{next_day}".encode()).hexdigest()[:16]
    idempotency_key = f"{patient_id}:day_{next_day}_followup:{content_hash}"

    return accumulate_effects(
        state,
        scheduled_jobs=[
            {
                "job_type": f"day_{next_day}_followup",
                "idempotency_key": idempotency_key,
                "scheduled_at": send_time,
                "metadata": {"follow_up_day": next_day},
            }
        ],
    )
