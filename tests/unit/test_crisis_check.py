"""Tests for the crisis check node — input-side crisis pre-check."""

from __future__ import annotations

import uuid

from langchain_core.messages import HumanMessage

from health_coach.agent.nodes.crisis_check import crisis_check
from health_coach.domain.safety_types import (
    ClassifierOutput,
    CrisisLevel,
    SafetyDecision,
)


def _make_config(*, classifier_output: ClassifierOutput | None = None) -> dict:  # type: ignore[type-arg]
    """Build a minimal config for crisis_check tests."""
    from unittest.mock import AsyncMock, MagicMock

    from health_coach.agent.context import CoachContext
    from health_coach.domain.consent import FakeConsentService
    from health_coach.domain.scheduling import CoachConfig
    from health_coach.integrations.model_gateway import FakeModelGateway

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin = MagicMock(return_value=AsyncMock())

    ctx = CoachContext(
        session_factory=MagicMock(return_value=mock_session),  # type: ignore[arg-type]
        engine=MagicMock(),  # type: ignore[arg-type]
        consent_service=FakeConsentService(logged_in=True, consented=True),
        settings=MagicMock(),  # type: ignore[arg-type]
        coach_config=CoachConfig(),
        model_gateway=FakeModelGateway(classifier_output=classifier_output),
    )
    return {"configurable": {"ctx": ctx, "thread_id": str(uuid.uuid4())}}


async def test_crisis_check_skips_scheduler_invocation() -> None:
    """Crisis check skips when invocation_source is not 'patient'."""
    result = await crisis_check(
        {
            "patient_id": str(uuid.uuid4()),
            "tenant_id": "t1",
            "invocation_source": "scheduler",
            "messages": [HumanMessage(content="hello")],
        },
        _make_config(),
    )
    assert result["crisis_detected"] is False


async def test_crisis_check_no_crisis() -> None:
    """Normal message returns no crisis."""
    result = await crisis_check(
        {
            "patient_id": str(uuid.uuid4()),
            "tenant_id": "t1",
            "invocation_source": "patient",
            "messages": [HumanMessage(content="I did my exercises today!")],
        },
        _make_config(
            classifier_output=ClassifierOutput(
                decision=SafetyDecision.SAFE,
                crisis_level=CrisisLevel.NONE,
                confidence=0.95,
                reasoning="Normal exercise discussion",
            )
        ),
    )
    assert result["crisis_detected"] is False


async def test_crisis_check_possible_crisis() -> None:
    """Possible crisis creates routine alert in pending_effects."""
    result = await crisis_check(
        {
            "patient_id": str(uuid.uuid4()),
            "tenant_id": "t1",
            "invocation_source": "patient",
            "messages": [HumanMessage(content="I'm feeling really down lately")],
        },
        _make_config(
            classifier_output=ClassifierOutput(
                decision=SafetyDecision.SAFE,
                crisis_level=CrisisLevel.POSSIBLE,
                confidence=0.7,
                reasoning="Vague distress signals",
            )
        ),
    )
    assert result["crisis_detected"] is False
    effects = result.get("pending_effects", {})
    alerts = effects.get("alerts", [])
    assert len(alerts) == 1
    assert alerts[0]["priority"] == "routine"


async def test_crisis_check_explicit_crisis() -> None:
    """Explicit crisis writes durable alert and sets crisis_detected."""
    result = await crisis_check(
        {
            "patient_id": str(uuid.uuid4()),
            "tenant_id": "t1",
            "invocation_source": "patient",
            "messages": [HumanMessage(content="I want to hurt myself")],
        },
        _make_config(
            classifier_output=ClassifierOutput(
                decision=SafetyDecision.CRISIS,
                crisis_level=CrisisLevel.EXPLICIT,
                confidence=0.95,
                reasoning="Self-harm ideation detected",
            )
        ),
    )
    assert result["crisis_detected"] is True


async def test_crisis_check_empty_messages() -> None:
    """No messages returns no crisis."""
    result = await crisis_check(
        {
            "patient_id": str(uuid.uuid4()),
            "tenant_id": "t1",
            "invocation_source": "patient",
            "messages": [],
        },
        _make_config(),
    )
    assert result["crisis_detected"] is False
