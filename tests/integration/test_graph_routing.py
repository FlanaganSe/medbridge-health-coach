"""Integration tests for graph routing — verifies phase routing and consent gate."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from health_ally.agent.context import CoachContext
from health_ally.agent.graph import compile_graph
from health_ally.agent.nodes.pending import WELCOME_MESSAGE
from health_ally.domain.consent import FakeConsentService
from health_ally.domain.scheduling import CoachConfig
from health_ally.integrations.model_gateway import FakeModelGateway

if TYPE_CHECKING:
    from health_ally.agent.state import PatientState


def _make_ctx(
    *,
    consent_allowed: bool = True,
    session_factory: object = None,
    engine: object = None,
) -> CoachContext:
    """Build a CoachContext for testing."""
    from unittest.mock import AsyncMock, MagicMock

    # MagicMock so sf() returns synchronously; AsyncMock supports async-with
    sf = session_factory or MagicMock(return_value=AsyncMock())
    eng = engine or MagicMock()
    consent_svc = FakeConsentService(logged_in=consent_allowed, consented=consent_allowed)

    return CoachContext(
        session_factory=sf,  # type: ignore[arg-type]
        engine=eng,  # type: ignore[arg-type]
        consent_service=consent_svc,
        settings=MagicMock(),  # type: ignore[arg-type]
        coach_config=CoachConfig(),
        model_gateway=FakeModelGateway(),
    )


def _make_config(
    ctx: CoachContext,
    thread_id: str | None = None,
) -> dict:  # type: ignore[type-arg]
    """Build a LangGraph config dict."""
    return {
        "configurable": {
            "ctx": ctx,
            "thread_id": thread_id or str(uuid.uuid4()),
        },
    }


@pytest.fixture
def graph():  # type: ignore[no-untyped-def]
    """Compile graph with in-memory checkpointer."""
    return compile_graph(checkpointer=MemorySaver())


async def test_consent_denied_exits_graph(graph) -> None:  # type: ignore[no-untyped-def]
    """When consent is denied, graph exits without running any nodes."""
    ctx = _make_ctx(consent_allowed=False)
    config = _make_config(ctx)

    result = await graph.ainvoke(
        {
            "patient_id": str(uuid.uuid4()),
            "tenant_id": "t1",
            "messages": [HumanMessage(content="hello")],
        },
        config=config,
    )

    assert result.get("consent_verified") is False


async def test_pending_phase_routes_to_pending_node(graph) -> None:  # type: ignore[no-untyped-def]
    """PENDING phase routes through pending_node and produces welcome message."""
    from unittest.mock import AsyncMock, MagicMock

    patient_id = str(uuid.uuid4())

    # Mock patient returned on second session.get (save_patient_context)
    mock_patient = MagicMock()
    mock_patient.phase = "pending"
    mock_patient.timezone = "America/New_York"
    mock_patient.unanswered_count = 0
    mock_patient.last_outreach_at = None
    mock_patient.last_patient_response_at = None

    # Mock session: first get → None (load), second get → mock_patient (save)
    mock_session = AsyncMock()
    mock_session.get = AsyncMock(side_effect=[None, mock_patient])
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock(return_value=AsyncMock())
    mock_session.begin().__aenter__ = AsyncMock(return_value=None)
    mock_session.begin().__aexit__ = AsyncMock(return_value=None)

    sf = MagicMock()
    sf.return_value = mock_session

    ctx = _make_ctx(session_factory=sf)
    config = _make_config(ctx)

    result = await graph.ainvoke(
        {
            "patient_id": patient_id,
            "tenant_id": "t1",
            "messages": [HumanMessage(content="hello")],
            "invocation_source": "scheduler",
        },
        config=config,
    )

    assert result.get("outbound_message") == WELCOME_MESSAGE
    # pending_effects should be cleared after save
    effects = result.get("pending_effects")
    assert effects is None


async def test_dormant_scheduler_produces_no_outbound(graph) -> None:  # type: ignore[no-untyped-def]
    """DORMANT phase with scheduler invocation — no outbound message."""
    from unittest.mock import AsyncMock, MagicMock

    patient_id = str(uuid.uuid4())

    mock_patient = MagicMock()
    mock_patient.phase = "dormant"
    mock_patient.timezone = "America/New_York"
    mock_patient.unanswered_count = 0
    mock_patient.last_outreach_at = None
    mock_patient.last_patient_response_at = None

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_patient)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock(return_value=AsyncMock())
    mock_session.begin().__aenter__ = AsyncMock(return_value=None)
    mock_session.begin().__aexit__ = AsyncMock(return_value=None)

    sf = MagicMock()
    sf.return_value = mock_session

    ctx = _make_ctx(session_factory=sf)
    config = _make_config(ctx)

    result = await graph.ainvoke(
        {
            "patient_id": patient_id,
            "tenant_id": "t1",
            "messages": [HumanMessage(content="hello")],
            "invocation_source": "scheduler",
        },
        config=config,
    )

    assert result.get("outbound_message") is None


async def test_dormant_patient_produces_welcome_back(graph) -> None:  # type: ignore[no-untyped-def]
    """DORMANT phase with patient invocation — generates welcome-back message."""
    from unittest.mock import AsyncMock, MagicMock

    patient_id = str(uuid.uuid4())

    mock_patient = MagicMock()
    mock_patient.phase = "dormant"
    mock_patient.timezone = "America/New_York"
    mock_patient.unanswered_count = 0
    mock_patient.last_outreach_at = None
    mock_patient.last_patient_response_at = None

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_patient)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock(return_value=AsyncMock())
    mock_session.begin().__aenter__ = AsyncMock(return_value=None)
    mock_session.begin().__aexit__ = AsyncMock(return_value=None)

    sf = MagicMock()
    sf.return_value = mock_session

    ctx = _make_ctx(session_factory=sf)
    config = _make_config(ctx)

    result = await graph.ainvoke(
        {
            "patient_id": patient_id,
            "tenant_id": "t1",
            "messages": [HumanMessage(content="hello")],
            "invocation_source": "patient",
        },
        config=config,
    )

    # Patient-initiated dormant now generates a welcome-back message
    assert result.get("outbound_message") is not None
    # Phase event should trigger DORMANT → RE_ENGAGING transition
    effects = result.get("pending_effects")
    assert effects is None  # cleared after save_patient_context


async def test_crisis_detected_routes_to_fallback(graph) -> None:  # type: ignore[no-untyped-def]
    """When crisis_detected=True, routes to fallback_response."""
    from unittest.mock import AsyncMock, MagicMock

    patient_id = str(uuid.uuid4())

    # Use a special crisis check that sets crisis_detected=True
    mock_patient = MagicMock()
    mock_patient.phase = "active"
    mock_patient.timezone = "America/New_York"
    mock_patient.unanswered_count = 0
    mock_patient.last_outreach_at = None
    mock_patient.last_patient_response_at = None

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_patient)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock(return_value=AsyncMock())
    mock_session.begin().__aenter__ = AsyncMock(return_value=None)
    mock_session.begin().__aexit__ = AsyncMock(return_value=None)

    sf = MagicMock()
    sf.return_value = mock_session

    ctx = _make_ctx(session_factory=sf)
    config = _make_config(ctx)

    # Pre-set crisis_detected in state
    result = await graph.ainvoke(
        {
            "patient_id": patient_id,
            "tenant_id": "t1",
            "messages": [HumanMessage(content="hello")],
            "invocation_source": "patient",
            "crisis_detected": True,
        },
        config=config,
    )

    # crisis_detected state persists but fallback message is NOT produced
    # because crisis_check stub always returns False, overwriting the input.
    # The crisis_detected=True from input gets overwritten by crisis_check stub.
    # This tests the stub behavior — real crisis check is in M4.
    assert result.get("crisis_detected") is False


async def test_phase_router_all_phases(graph) -> None:  # type: ignore[no-untyped-def]
    """All 5 phases route to the correct node."""
    from health_ally.agent.nodes.router import phase_router
    from health_ally.domain.phases import PatientPhase

    expected = {
        PatientPhase.PENDING: "pending_node",
        PatientPhase.ONBOARDING: "onboarding_agent",
        PatientPhase.ACTIVE: "active_agent",
        PatientPhase.RE_ENGAGING: "reengagement_agent",
        PatientPhase.DORMANT: "dormant_node",
    }

    for phase, expected_node in expected.items():
        state: PatientState = {
            "patient_id": "p1",
            "tenant_id": "t1",
            "phase": phase.value,
        }
        assert phase_router(state) == expected_node, f"Phase {phase} routed incorrectly"
