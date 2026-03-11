"""Integration tests for graph thread persistence."""

from __future__ import annotations

import uuid

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from health_coach.agent.context import CoachContext
from health_coach.agent.graph import compile_graph
from health_coach.domain.consent import FakeConsentService
from health_coach.domain.scheduling import CoachConfig


def _make_mock_session():  # type: ignore[no-untyped-def]
    """Create a mock session that returns None for patient lookups."""
    from unittest.mock import AsyncMock, MagicMock

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=None)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock(return_value=AsyncMock())
    mock_session.begin().__aenter__ = AsyncMock(return_value=None)
    mock_session.begin().__aexit__ = AsyncMock(return_value=None)
    return mock_session


async def test_thread_persistence_across_invocations() -> None:
    """Same thread_id resumes conversation with prior messages."""
    from unittest.mock import MagicMock

    checkpointer = MemorySaver()
    graph = compile_graph(checkpointer=checkpointer)

    mock_session = _make_mock_session()
    sf = MagicMock()
    sf.return_value = mock_session

    consent_svc = FakeConsentService(logged_in=True, consented=True)
    ctx = CoachContext(
        session_factory=sf,  # type: ignore[arg-type]
        engine=MagicMock(),  # type: ignore[arg-type]
        consent_service=consent_svc,
        settings=MagicMock(),  # type: ignore[arg-type]
        coach_config=CoachConfig(),
    )

    thread_id = str(uuid.uuid4())
    patient_id = str(uuid.uuid4())
    config = {
        "configurable": {
            "ctx": ctx,
            "thread_id": thread_id,
        },
    }

    # First invocation
    result1 = await graph.ainvoke(
        {
            "patient_id": patient_id,
            "tenant_id": "t1",
            "messages": [HumanMessage(content="hello")],
            "invocation_source": "patient",
        },
        config=config,
    )

    # Should have messages from the first invocation
    assert len(result1["messages"]) >= 1

    # Second invocation with same thread_id
    result2 = await graph.ainvoke(
        {
            "patient_id": patient_id,
            "tenant_id": "t1",
            "messages": [HumanMessage(content="second message")],
            "invocation_source": "patient",
        },
        config=config,
    )

    # Should have accumulated messages from both invocations
    assert len(result2["messages"]) > len(result1["messages"])


async def test_different_threads_are_independent() -> None:
    """Different thread_ids maintain separate state."""
    from unittest.mock import MagicMock

    checkpointer = MemorySaver()
    graph = compile_graph(checkpointer=checkpointer)

    mock_session = _make_mock_session()
    sf = MagicMock()
    sf.return_value = mock_session

    consent_svc = FakeConsentService(logged_in=True, consented=True)
    ctx = CoachContext(
        session_factory=sf,  # type: ignore[arg-type]
        engine=MagicMock(),  # type: ignore[arg-type]
        consent_service=consent_svc,
        settings=MagicMock(),  # type: ignore[arg-type]
        coach_config=CoachConfig(),
    )

    patient_a = str(uuid.uuid4())
    patient_b = str(uuid.uuid4())

    config_a = {"configurable": {"ctx": ctx, "thread_id": f"patient-{patient_a}"}}
    config_b = {"configurable": {"ctx": ctx, "thread_id": f"patient-{patient_b}"}}

    result_a = await graph.ainvoke(
        {
            "patient_id": patient_a,
            "tenant_id": "t1",
            "messages": [HumanMessage(content="hello from A")],
            "invocation_source": "patient",
        },
        config=config_a,
    )

    result_b = await graph.ainvoke(
        {
            "patient_id": patient_b,
            "tenant_id": "t1",
            "messages": [HumanMessage(content="hello from B")],
            "invocation_source": "patient",
        },
        config=config_b,
    )

    # Each thread should have its own messages
    a_contents = [m.content for m in result_a["messages"]]
    b_contents = [m.content for m in result_b["messages"]]

    assert "hello from A" in a_contents
    assert "hello from B" in b_contents
    assert "hello from B" not in a_contents
    assert "hello from A" not in b_contents
