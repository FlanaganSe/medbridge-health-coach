"""Re-engagement agent node — backoff sequence and warm re-engagement."""

# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from langchain_core.messages import AIMessage

from health_coach.agent.context import get_coach_context
from health_coach.agent.effects import accumulate_effects
from health_coach.agent.prompts.re_engaging import build_re_engaging_prompt
from health_coach.agent.state import PatientState  # noqa: TC001
from health_coach.agent.tools.adherence import get_adherence_summary
from health_coach.agent.tools.goal import get_program_summary, set_goal
from health_coach.domain.backoff import next_backoff_delay, should_transition_to_dormant
from health_coach.domain.scheduling import CoachConfig, add_jitter, calculate_send_time

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

    from health_coach.agent.state import PendingEffects

logger = structlog.stdlib.get_logger()

RE_ENGAGING_TOOLS = [set_goal, get_program_summary, get_adherence_summary]


async def reengagement_agent(
    state: PatientState,
    config: RunnableConfig,
) -> dict[str, object]:
    """Re-engagement agent — manages backoff sequence and patient return.

    On scheduler invocations:
    - Detects continued no-response
    - Increments unanswered_count
    - On 3rd unanswered → clinician alert + transition to DORMANT
    - Otherwise → sends re-engagement message + schedules next backoff outreach

    On patient invocations:
    - Welcomes patient back warmly
    - Transitions RE_ENGAGING → ACTIVE via patient_responded event
    - Schedules new follow-up cadence
    """
    ctx = get_coach_context(config)
    invocation_source = state.get("invocation_source")

    # Scheduler-initiated: check for continued no-response
    if invocation_source == "scheduler":
        unanswered = state.get("unanswered_count", 0) + 1
        max_unanswered = ctx.coach_config.max_unanswered

        if should_transition_to_dormant(unanswered, max_unanswered):
            return _handle_dormant_transition(state, unanswered)

        # Not yet dormant — send re-engagement message and schedule next
        result = await _generate_re_engaging_message(state, config)
        result["pending_effects"] = _accumulate_backoff_job(state, unanswered, ctx.coach_config)
        result["unanswered_count"] = unanswered
        return result

    # Patient-initiated: welcome back + transition to ACTIVE
    result = await _generate_re_engaging_message(state, config)
    effects = _accumulate_patient_return(state, ctx.coach_config)
    result["pending_effects"] = effects
    return result


async def _generate_re_engaging_message(
    state: PatientState,
    config: RunnableConfig,
) -> dict[str, object]:
    """Generate a re-engagement message using the LLM."""
    ctx = get_coach_context(config)
    patient_id = state["patient_id"]
    invocation_source = state.get("invocation_source")

    system_prompt = build_re_engaging_prompt(invocation_source)
    coach_model = ctx.model_gateway.get_chat_model("coach")
    model_with_tools = coach_model.bind_tools(RE_ENGAGING_TOOLS, parallel_tool_calls=False)

    messages = list(state.get("messages", []))

    try:
        response = await model_with_tools.ainvoke(
            [{"role": "system", "content": system_prompt}, *messages]
        )
    except Exception:
        logger.exception("reengagement_agent_error", patient_id=patient_id)
        return {"outbound_message": None}

    # Tool calls → graph loops through tool_node; defer outbound_message
    # to the final (no-tools) invocation so raw tool JSON isn't streamed.
    has_tool_calls = bool(getattr(response, "tool_calls", None))
    content = None if has_tool_calls else (str(response.content) if response.content else None)

    return {
        "messages": [response],
        "outbound_message": content,
    }


def _handle_dormant_transition(
    state: PatientState,
    unanswered: int,
) -> dict[str, object]:
    """Handle transition to DORMANT after max unanswered messages."""
    patient_id = state["patient_id"]
    content_hash = hashlib.sha256(f"{patient_id}:dormant".encode()).hexdigest()[:16]
    idempotency_key = f"{patient_id}:missed_third:{content_hash}"

    effects = accumulate_effects(
        state,
        phase_event="missed_third_message",
        alerts=[
            {
                "reason": (
                    f"Patient unresponsive after {unanswered} outreach "
                    "attempts — transitioning to dormant"
                ),
                "priority": "routine",
                "idempotency_key": idempotency_key,
            },
        ],
        audit_events=[
            {
                "event_type": "dormant_transition",
                "outcome": "dormant",
                "metadata": {"unanswered_count": unanswered},
            },
        ],
    )

    return {
        "unanswered_count": unanswered,
        "pending_effects": effects,
        "outbound_message": None,
        "messages": [AIMessage(content="")],
    }


def _accumulate_backoff_job(
    state: PatientState,
    unanswered: int,
    coach_config: CoachConfig,
) -> PendingEffects:
    """Accumulate the next backoff follow-up job."""
    patient_id = state["patient_id"]

    delay = next_backoff_delay(unanswered, coach_config.backoff_base_days)
    now = datetime.now(UTC)
    base_time = now + delay

    send_time = calculate_send_time(
        base_time,
        "America/New_York",  # TODO: get from patient context
        coach_config.quiet_hours_start,
        coach_config.quiet_hours_end,
    )
    send_time = add_jitter(send_time, coach_config.max_jitter_minutes)

    content_hash = hashlib.sha256(f"{patient_id}:backoff:{unanswered}".encode()).hexdigest()[:16]
    idempotency_key = f"{patient_id}:backoff_followup:{content_hash}"

    return accumulate_effects(
        state,
        scheduled_jobs=[
            {
                "job_type": "backoff_followup",
                "idempotency_key": idempotency_key,
                "scheduled_at": send_time,
                "metadata": {"unanswered_count": unanswered},
            }
        ],
    )


def _accumulate_patient_return(
    state: PatientState,
    coach_config: CoachConfig,
) -> PendingEffects:
    """Accumulate effects for patient return: phase transition + new follow-up."""
    from datetime import timedelta

    patient_id = state["patient_id"]

    now = datetime.now(UTC)
    base_time = now + timedelta(days=coach_config.follow_up_days[0])
    send_time = calculate_send_time(
        base_time,
        "America/New_York",
        coach_config.quiet_hours_start,
        coach_config.quiet_hours_end,
    )
    send_time = add_jitter(send_time, coach_config.max_jitter_minutes)

    content_hash = hashlib.sha256(f"{patient_id}:return_followup".encode()).hexdigest()[:16]
    idempotency_key = f"{patient_id}:return_followup:{content_hash}"

    return accumulate_effects(
        state,
        phase_event="patient_responded",
        audit_events=[
            {
                "event_type": "patient_returned",
                "outcome": "active",
                "metadata": {},
            },
        ],
        scheduled_jobs=[
            {
                "job_type": "day_2_followup",
                "idempotency_key": idempotency_key,
                "scheduled_at": send_time,
                "metadata": {"follow_up_day": 2, "source": "re_engagement"},
            }
        ],
    )
