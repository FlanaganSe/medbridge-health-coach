"""Retry generation node — re-invokes LLM with tighter safety constraints."""

# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from langchain_core.messages import AIMessage

from health_ally.agent.context import get_coach_context
from health_ally.agent.prompts.system import get_system_prompt
from health_ally.agent.state import PatientState  # noqa: TC001

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

logger = structlog.stdlib.get_logger()

RETRY_AUGMENTATION = (
    "IMPORTANT: Your previous response was flagged for potentially containing "
    "clinical advice or content outside your role as an exercise accountability "
    "coach. Please try again, focusing ONLY on exercise motivation, goal "
    "tracking, and encouragement. Do NOT discuss symptoms, diagnoses, "
    "medications, or treatment plans. If the patient asked a clinical "
    "question, redirect them to their care team."
)


async def retry_generation(
    state: PatientState,
    config: RunnableConfig,
) -> dict[str, object]:
    """Re-invoke the LLM with augmented safety constraints.

    Injects safety augmentation into the system prompt (not persisted)
    and re-generates the response. Increments safety_retry_count.
    """
    ctx = get_coach_context(config)
    patient_id = state["patient_id"]
    phase = state.get("phase", "onboarding")
    retry_count = state.get("safety_retry_count", 0) + 1

    logger.info(
        "retry_generation",
        patient_id=patient_id,
        retry_count=retry_count,
    )

    # Get coach model and system prompt
    coach_model = ctx.model_gateway.get_chat_model("coach")
    system_prompt = get_system_prompt(phase)

    # Build messages: system prompt (augmented with safety constraints) + existing messages
    augmented_prompt = f"{system_prompt}\n\n{RETRY_AUGMENTATION}"
    messages = list(state.get("messages", []))

    try:
        response = await coach_model.ainvoke(
            [{"role": "system", "content": augmented_prompt}, *messages]
        )
        content = str(response.content)
    except Exception:
        logger.exception("retry_generation_error", patient_id=patient_id)
        # On failure, let fallback handle it
        content = ""

    return {
        "safety_retry_count": retry_count,
        "outbound_message": content if content else None,
        "messages": [AIMessage(content=content)],
    }
