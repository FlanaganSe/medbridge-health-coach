"""Dormant node — handles patient return or logs no-op for scheduler."""

# pyright: reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from health_ally.agent.content import extract_text_content
from health_ally.agent.context import get_coach_context
from health_ally.agent.effects import accumulate_effects
from health_ally.agent.prompts.system import get_system_prompt
from health_ally.agent.state import PatientState  # noqa: TC001

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
        # Generate welcome-back response via LLM
        ctx = get_coach_context(config)
        system_prompt = get_system_prompt("dormant")
        coach_model = ctx.model_gateway.get_chat_model("coach")
        messages = list(state.get("messages", []))

        try:
            response = await coach_model.ainvoke(
                [{"role": "system", "content": system_prompt}, *messages]
            )
            content = extract_text_content(response.content) or None  # type: ignore[union-attr]
        except Exception:
            logger.exception("dormant_welcome_back_error", patient_id=patient_id)
            # On LLM failure, do NOT transition phase — leave patient in DORMANT
            # so the next message attempt can succeed
            return {"outbound_message": None}

        # Only accumulate phase transition after successful LLM response
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

        logger.info(
            "dormant_patient_returned",
            patient_id=patient_id,
        )

        return {
            "pending_effects": effects,
            "outbound_message": content,
            "messages": [response],
        }

    logger.info(
        "dormant_no_outreach",
        patient_id=patient_id,
        invocation_source=invocation_source,
    )
    return {"outbound_message": None}
