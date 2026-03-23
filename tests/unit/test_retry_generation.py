"""Tests for retry_generation — safety retry path."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from health_ally.agent.context import CoachContext
from health_ally.agent.nodes.retry import retry_generation
from health_ally.domain.consent import FakeConsentService
from health_ally.domain.scheduling import CoachConfig
from health_ally.integrations.model_gateway import FakeModelGateway


def _make_config(*, model_gateway: FakeModelGateway | None = None) -> dict:  # type: ignore[type-arg]
    """Build a LangGraph config dict with FakeModelGateway."""
    ctx = CoachContext(
        session_factory=MagicMock(),  # type: ignore[arg-type]
        engine=MagicMock(),  # type: ignore[arg-type]
        consent_service=FakeConsentService(logged_in=True, consented=True),
        settings=MagicMock(),  # type: ignore[arg-type]
        coach_config=CoachConfig(),
        model_gateway=model_gateway or FakeModelGateway(),
    )
    return {"configurable": {"ctx": ctx, "thread_id": str(uuid.uuid4())}}


def _make_state(*, safety_retry_count: int = 0) -> dict:  # type: ignore[type-arg]
    """Build a minimal PatientState dict for retry_generation."""
    return {
        "patient_id": str(uuid.uuid4()),
        "tenant_id": "t1",
        "phase": "onboarding",
        "messages": [],
        "safety_retry_count": safety_retry_count,
    }


async def test_retry_increments_safety_retry_count() -> None:
    """Retry generation increments safety_retry_count by 1."""
    state = _make_state(safety_retry_count=1)
    config = _make_config()

    result = await retry_generation(state, config)

    assert result["safety_retry_count"] == 2


async def test_retry_produces_outbound_message() -> None:
    """Retry generation with successful LLM call sets outbound_message."""
    state = _make_state()
    config = _make_config(
        model_gateway=FakeModelGateway(
            responses=["Great, let's focus on your exercise goals!"],
        ),
    )

    result = await retry_generation(state, config)

    assert result["outbound_message"] == "Great, let's focus on your exercise goals!"
    assert result["safety_retry_count"] == 1


async def test_retry_on_llm_error_returns_no_message() -> None:
    """Retry generation when LLM raises returns None outbound_message."""
    state = _make_state()
    # Create a gateway that will raise on ainvoke
    gateway = FakeModelGateway(responses=["unused"])
    config = _make_config(model_gateway=gateway)

    # Patch the coach model to raise
    original_get = gateway.get_chat_model

    class _RaisingModel:
        def bind_tools(self, *_a: object, **_k: object) -> _RaisingModel:
            return self

        async def ainvoke(self, *_a: object, **_k: object) -> None:
            raise RuntimeError("LLM unavailable")

    def patched_get(purpose: str):  # type: ignore[no-untyped-def]
        if purpose == "coach":
            return _RaisingModel()
        return original_get(purpose)

    gateway.get_chat_model = patched_get  # type: ignore[assignment]

    result = await retry_generation(state, config)

    assert result["outbound_message"] is None
    assert result["safety_retry_count"] == 1
