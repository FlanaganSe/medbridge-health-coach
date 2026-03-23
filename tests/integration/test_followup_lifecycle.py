"""Integration tests for follow-up lifecycle — scheduling and chain scheduling."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from health_ally.agent.context import CoachContext
from health_ally.agent.graph import compile_graph
from health_ally.domain.consent import FakeConsentService
from health_ally.domain.scheduling import CoachConfig
from health_ally.integrations.model_gateway import FakeModelGateway
from tests.conftest import make_mock_session


def _make_active_patient() -> MagicMock:
    """Create a mock patient in active phase."""
    patient = MagicMock()
    patient.phase = "active"
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


async def test_active_agent_produces_response() -> None:
    """Active agent with FakeModel produces a coaching response."""
    patient = _make_active_patient()
    ctx = _make_ctx(
        mock_patient=patient,
        model_gateway=FakeModelGateway(
            responses=["Great job on your exercises! Keep it up!"],
        ),
    )
    graph = compile_graph(checkpointer=MemorySaver())

    result = await graph.ainvoke(
        {
            "patient_id": str(uuid.uuid4()),
            "tenant_id": "t1",
            "messages": [HumanMessage(content="I did my exercises today!")],
            "invocation_source": "patient",
        },
        config={"configurable": {"ctx": ctx, "thread_id": str(uuid.uuid4())}},
    )

    assert result.get("outbound_message") is not None


async def test_active_agent_scheduler_triggers_unanswered() -> None:
    """Active agent on scheduler invocation always triggers unanswered outreach."""
    patient = _make_active_patient()
    patient.unanswered_count = 0

    ctx = _make_ctx(mock_patient=patient)
    graph = compile_graph(checkpointer=MemorySaver())

    result = await graph.ainvoke(
        {
            "patient_id": str(uuid.uuid4()),
            "tenant_id": "t1",
            "messages": [],
            "invocation_source": "scheduler",
        },
        config={"configurable": {"ctx": ctx, "thread_id": str(uuid.uuid4())}},
    )

    # Should detect unanswered and transition to re_engaging
    assert result.get("unanswered_count", 0) >= 1


async def test_set_goal_schedules_day_2_followup() -> None:
    """set_goal tool accumulates Day 2 follow-up job in pending_effects."""
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

    result = set_goal.func(
        goal_text="Walk 30 minutes daily",
        raw_patient_text="I want to walk every day",
        state=state,
        tool_call_id="tc1",
    )

    effects = result.update["pending_effects"]
    assert effects["phase_event"] == "goal_confirmed"
    assert len(effects["scheduled_jobs"]) == 1
    assert effects["scheduled_jobs"][0]["job_type"] == "day_2_followup"
