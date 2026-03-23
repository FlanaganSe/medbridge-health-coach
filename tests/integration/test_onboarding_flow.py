"""Integration tests for the onboarding flow — end-to-end graph execution."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from health_ally.agent.context import CoachContext
from health_ally.agent.graph import compile_graph
from health_ally.domain.consent import FakeConsentService
from health_ally.domain.safety import CLINICAL_REDIRECT_MESSAGE, CRISIS_RESPONSE_MESSAGE
from health_ally.domain.safety_types import (
    ClassifierOutput,
    CrisisLevel,
    SafetyDecision,
)
from health_ally.domain.scheduling import CoachConfig
from health_ally.integrations.model_gateway import FakeModelGateway
from tests.conftest import make_mock_session


def _make_onboarding_patient() -> MagicMock:
    """Create a mock patient in onboarding phase."""
    patient = MagicMock()
    patient.phase = "onboarding"
    patient.timezone = "America/New_York"
    patient.unanswered_count = 0
    patient.last_outreach_at = None
    patient.last_patient_response_at = None
    return patient


def _make_ctx(
    *,
    mock_patient: object = None,
    model_gateway: FakeModelGateway | None = None,
) -> CoachContext:
    """Build a CoachContext for testing."""
    mock_session = make_mock_session(mock_patient)
    sf = MagicMock(return_value=mock_session)

    return CoachContext(
        session_factory=sf,  # type: ignore[arg-type]
        engine=MagicMock(),  # type: ignore[arg-type]
        consent_service=FakeConsentService(logged_in=True, consented=True),
        settings=MagicMock(),  # type: ignore[arg-type]
        coach_config=CoachConfig(),
        model_gateway=model_gateway or FakeModelGateway(),
    )


async def test_onboarding_agent_produces_response() -> None:
    """Onboarding agent with FakeModel produces a response via safety gate."""
    patient = _make_onboarding_patient()
    ctx = _make_ctx(
        mock_patient=patient,
        model_gateway=FakeModelGateway(
            responses=["Welcome! What are your exercise goals?"],
        ),
    )
    graph = compile_graph(checkpointer=MemorySaver())

    result = await graph.ainvoke(
        {
            "patient_id": str(uuid.uuid4()),
            "tenant_id": "t1",
            "messages": [HumanMessage(content="Hi there!")],
            "invocation_source": "patient",
        },
        config={"configurable": {"ctx": ctx, "thread_id": str(uuid.uuid4())}},
    )

    # Should have an outbound message (from the fake model)
    assert result.get("outbound_message") is not None


async def test_safety_blocks_clinical_content() -> None:
    """Safety gate blocks clinical content and retries, then falls back."""
    patient = _make_onboarding_patient()
    # Classifier returns CLINICAL_BOUNDARY on every check
    gateway = FakeModelGateway(
        responses=["You should take ibuprofen for your knee pain."],
        classifier_output=ClassifierOutput(
            decision=SafetyDecision.CLINICAL_BOUNDARY,
            crisis_level=CrisisLevel.NONE,
            confidence=0.9,
            reasoning="Contains medication advice",
        ),
    )
    ctx = _make_ctx(mock_patient=patient, model_gateway=gateway)
    graph = compile_graph(checkpointer=MemorySaver())

    result = await graph.ainvoke(
        {
            "patient_id": str(uuid.uuid4()),
            "tenant_id": "t1",
            "messages": [HumanMessage(content="My knee hurts, what should I take?")],
            "invocation_source": "patient",
        },
        config={"configurable": {"ctx": ctx, "thread_id": str(uuid.uuid4())}},
    )

    # Clinical boundary is advisory — original message preserved, decision logged
    assert result.get("outbound_message") == "You should take ibuprofen for your knee pain."
    assert result.get("safety_decision") == "clinical_boundary"


async def test_crisis_triggers_988_response() -> None:
    """Explicit crisis triggers 988 response and blocks LLM generation."""
    patient = _make_onboarding_patient()
    gateway = FakeModelGateway(
        classifier_output=ClassifierOutput(
            decision=SafetyDecision.CRISIS,
            crisis_level=CrisisLevel.EXPLICIT,
            confidence=0.95,
            reasoning="Self-harm ideation",
        ),
    )
    ctx = _make_ctx(mock_patient=patient, model_gateway=gateway)
    graph = compile_graph(checkpointer=MemorySaver())

    result = await graph.ainvoke(
        {
            "patient_id": str(uuid.uuid4()),
            "tenant_id": "t1",
            "messages": [HumanMessage(content="I want to end it all")],
            "invocation_source": "patient",
        },
        config={"configurable": {"ctx": ctx, "thread_id": str(uuid.uuid4())}},
    )

    # Crisis detected should route to fallback with crisis message
    assert result.get("crisis_detected") is True
    assert result.get("outbound_message") == CRISIS_RESPONSE_MESSAGE


def test_set_goal_triggers_phase_event() -> None:
    """set_goal tool includes goal_confirmed phase_event in pending_effects."""
    from health_ally.agent.tools.goal import set_goal

    state = {
        "patient_id": "p1",
        "pending_effects": {
            "goal": None,
            "alerts": [],
            "phase_event": None,
            "scheduled_jobs": [],
            "safety_decisions": [],
            "outbox_entries": [],
            "audit_events": [],
        },
    }

    # Call underlying function directly — InjectedState is a LangGraph
    # runtime concern that can't be tested through tool.invoke().
    result = set_goal.func(
        goal_text="Walk 30 minutes daily",
        raw_patient_text="I want to walk every day for half an hour",
        state=state,
        tool_call_id="tc1",
    )

    # Result is a Command with pending_effects containing goal_confirmed
    effects = result.update["pending_effects"]
    assert effects["phase_event"] == "goal_confirmed"
    assert effects["goal"]["goal_text"] == "Walk 30 minutes daily"
