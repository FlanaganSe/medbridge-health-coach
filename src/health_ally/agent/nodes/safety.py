"""Safety gate node — classifies outbound messages for safety before delivery."""

# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from health_ally.agent.context import get_coach_context
from health_ally.agent.effects import accumulate_effects
from health_ally.agent.prompts.safety import SAFETY_CLASSIFIER_PROMPT
from health_ally.agent.state import PatientState  # noqa: TC001
from health_ally.domain.safety_types import ClassifierOutput, SafetyDecision

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

logger = structlog.stdlib.get_logger()


async def safety_gate(
    state: PatientState,
    config: RunnableConfig,
) -> dict[str, object]:
    """Classify the outbound message for safety before delivery.

    Uses a lightweight classifier model (Haiku) to evaluate the
    agent's response. Accumulates safety decision in pending_effects.
    """
    outbound = state.get("outbound_message", "")
    if not outbound:
        # No message to classify — pass through as safe
        return {"safety_decision": SafetyDecision.SAFE.value}

    ctx = get_coach_context(config)
    patient_id = state["patient_id"]

    classifier_model = ctx.model_gateway.get_chat_model("classifier")
    structured_model = classifier_model.with_structured_output(ClassifierOutput)

    try:
        result: ClassifierOutput = await structured_model.ainvoke(  # type: ignore[assignment]
            [
                {"role": "system", "content": SAFETY_CLASSIFIER_PROMPT},
                {"role": "user", "content": f"Classify this outbound message:\n\n{outbound}"},
            ]
        )
    except Exception:
        logger.exception("safety_classifier_error", patient_id=patient_id)
        # Fail-safe: block on classifier failure (prefer false positive)
        result = ClassifierOutput(
            decision=SafetyDecision.CLINICAL_BOUNDARY,
            confidence=0.0,
            reasoning="Classifier error — blocking as precaution",
        )

    logger.info(
        "safety_gate_result",
        patient_id=patient_id,
        decision=result.decision,
        confidence=result.confidence,
    )

    effects = accumulate_effects(
        state,
        safety_decisions=[
            {
                "decision": result.decision.value,
                "source": "classifier",
                "confidence": result.confidence,
                "reasoning": result.reasoning,
            }
        ],
        audit_events=[
            {
                "event_type": "safety_classification",
                "outcome": result.decision.value,
                "metadata": {
                    "confidence": result.confidence,
                    "reasoning": result.reasoning,
                    "retry_count": state.get("safety_retry_count", 0),
                },
            }
        ],
    )

    return {
        "safety_decision": result.decision.value,
        "pending_effects": effects,
    }


def safety_route(state: PatientState) -> str:
    """Route based on safety decision."""
    decision = state.get("safety_decision", "safe")

    if decision == SafetyDecision.SAFE.value:
        return "save_patient_context"
    if decision == SafetyDecision.CLINICAL_BOUNDARY.value:
        retry_count = state.get("safety_retry_count", 0)
        if retry_count < 1:
            return "retry_generation"
        return "fallback_response"
    # crisis or jailbreak — never retry
    return "fallback_response"
