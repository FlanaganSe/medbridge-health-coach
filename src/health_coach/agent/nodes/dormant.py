"""Dormant node — handles patient return or logs no-op for scheduler."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from health_coach.agent.context import get_coach_context
from health_coach.agent.effects import accumulate_effects
from health_coach.agent.prompts.re_engaging import build_re_engaging_prompt
from health_coach.agent.state import PatientState  # noqa: TC001

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

logger = structlog.stdlib.get_logger()


async def dormant_node(
    state: PatientState,
    config: RunnableConfig,
) -> dict[str, object]:
    """Handle dormant patient interaction.

    - Patient-initiated: generates welcome-back message, triggers patient_returned → RE_ENGAGING
    - Scheduler-initiated: no-op (no further proactive outreach)
    """
    patient_id = state.get("patient_id")
    invocation_source = state.get("invocation_source")

    if invocation_source == "patient":
        # Patient returned — transition to RE_ENGAGING with a welcome-back message
        effects = accumulate_effects(
            state,
            phase_event="patient_returned",
            audit_events=[
                {
                    "event_type": "patient_returned",
                    "outcome": "re_engaging",
                    "metadata": {},
                },
            ],
        )

        # Generate welcome-back response via LLM
        ctx = get_coach_context(config)
        system_prompt = build_re_engaging_prompt("patient")
        coach_model = ctx.model_gateway.get_chat_model("coach")
        messages = list(state.get("messages", []))

        try:
            response = await coach_model.ainvoke(
                [{"role": "system", "content": system_prompt}, *messages]
            )
            content = str(response.content) if response.content else None  # type: ignore[union-attr]
        except Exception:
            logger.exception("dormant_welcome_back_error", patient_id=patient_id)
            content = None
            response = None

        logger.info(
            "dormant_patient_returned",
            patient_id=patient_id,
        )

        result: dict[str, object] = {
            "pending_effects": effects,
            "outbound_message": content,
        }
        if response is not None:
            result["messages"] = [response]
        return result

    logger.info(
        "dormant_no_outreach",
        patient_id=patient_id,
        invocation_source=invocation_source,
    )
    return {"outbound_message": None}
