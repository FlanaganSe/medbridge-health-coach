"""Pending node — initiates PENDING → ONBOARDING transition."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from health_coach.agent.context import get_coach_context
from health_coach.agent.state import PatientState  # noqa: TC001

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig


WELCOME_MESSAGE = (
    "Hi! I'm your exercise accountability coach from MedBridge. "
    "I'm here to help you stay on track with your home exercise program. "
    "I'd love to learn about your exercise goals — "
    "what would you most like to achieve with your exercises?"
)


async def pending_node(
    state: PatientState,
    config: RunnableConfig,
) -> dict[str, object]:
    """Initiate onboarding for a new patient.

    Sets phase_event for PENDING → ONBOARDING transition,
    creates onboarding timeout job, and records template safety decision.
    """
    ctx = get_coach_context(config)
    patient_id = state["patient_id"]
    now = datetime.now(UTC)

    # Build delivery key for welcome message
    content_hash = hashlib.sha256(WELCOME_MESSAGE.encode()).hexdigest()[:16]
    delivery_key = f"{patient_id}:welcome:{content_hash}"

    # Build onboarding timeout job
    timeout_at = now + timedelta(hours=ctx.coach_config.onboarding_timeout_hours)
    timeout_key = f"{patient_id}:onboarding_timeout:{now.date().isoformat()}"

    effects = {
        "goal": None,
        "alerts": [],
        "phase_event": "onboarding_initiated",
        "scheduled_jobs": [
            {
                "job_type": "onboarding_timeout",
                "idempotency_key": timeout_key,
                "scheduled_at": timeout_at,
                "metadata": {"reason": "no_response_72h"},
            }
        ],
        "safety_decisions": [
            {
                "decision": "safe",
                "source": "template",
                "confidence": 1.0,
                "reasoning": "Template welcome message — pre-approved, static",
            }
        ],
        "outbox_entries": [
            {
                "delivery_key": delivery_key,
                "message_type": "patient_message",
                "priority": 0,
                "channel": "default",
                "payload": {"message_ref_id": str(uuid.uuid4())},
            }
        ],
        "audit_events": [],
        "cancel_pending_jobs": False,
    }

    return {
        "outbound_message": WELCOME_MESSAGE,
        "pending_effects": effects,
    }
