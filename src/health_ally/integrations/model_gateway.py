"""ModelGateway — LLM factory with fallback support."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

    from health_ally.domain.safety_types import ClassifierOutput
    from health_ally.settings import Settings


class ModelGateway(ABC):
    """Abstract LLM factory for coach, classifier, and extractor models."""

    @abstractmethod
    def get_chat_model(self, purpose: str) -> BaseChatModel:
        """Return a configured LLM for the given purpose.

        Args:
            purpose: One of "coach", "classifier", "extractor".
        """


class AnthropicModelGateway(ModelGateway):
    """Production model gateway using ChatAnthropic with optional fallback."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def get_chat_model(self, purpose: str) -> BaseChatModel:
        """Return a ChatAnthropic model, with fallback if configured."""
        from langchain_anthropic import ChatAnthropic

        model_name = (
            self._settings.safety_classifier_model
            if purpose == "classifier"
            else self._settings.default_model
        )

        primary = ChatAnthropic(
            model=model_name,  # type: ignore[call-arg]
            max_tokens=self._settings.max_tokens,  # type: ignore[call-arg]
            max_retries=0,
            api_key=self._settings.anthropic_api_key.get_secret_value(),  # type: ignore[arg-type]
        )

        fallback = self._build_fallback()
        if fallback is not None:
            return primary.with_fallbacks([fallback])  # type: ignore[return-value]

        return primary

    def _build_fallback(self) -> BaseChatModel | None:
        """Build fallback model — only when a real LLM fallback is available.

        FakeListChatModel is not viable as a with_fallbacks() target because
        it doesn't support bind_tools() or with_structured_output(), which
        causes RunnableWithFallbacks to crash when propagating those calls.
        When no PHI-approved fallback provider is configured, return None
        and let the primary model's own error handling (retry, fail-escalate)
        take over.
        """
        if not self._settings.fallback_phi_approved:
            return None

        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model="gpt-4o",
            max_retries=0,
            api_key=self._settings.openai_api_key.get_secret_value(),  # type: ignore[arg-type]
        )


class FakeModelGateway(ModelGateway):
    """Test model gateway returning fake chat models.

    For "coach" purpose: returns FakeListChatModel (does NOT support
    bind_tools — tests must construct AIMessage(tool_calls=[...]) directly).

    For "classifier" purpose: returns a FakeClassifierModel that supports
    with_structured_output() and returns a configurable ClassifierOutput.
    """

    def __init__(
        self,
        responses: list[str] | None = None,
        classifier_output: ClassifierOutput | None = None,
    ) -> None:
        self._responses = responses or ["I'm a test response."]
        self._classifier_output = classifier_output

    def get_chat_model(self, purpose: str) -> BaseChatModel:
        """Return a fake model appropriate for the purpose."""
        if purpose == "classifier":
            return _FakeClassifierModel(output=self._classifier_output)  # type: ignore[return-value]

        return _FakeCoachModel(responses=list(self._responses))  # type: ignore[return-value]


class _FakeCoachModel:
    """Fake coach model that supports bind_tools (no-op).

    Wraps FakeListChatModel but overrides bind_tools to return self,
    since FakeListChatModel raises NotImplementedError for bind_tools.
    """

    def __init__(self, responses: list[str]) -> None:
        from langchain_core.language_models.fake_chat_models import (
            FakeListChatModel,
        )

        self._model = FakeListChatModel(responses=responses)

    def bind_tools(self, *_args: object, **_kwargs: object) -> _FakeCoachModel:
        """No-op bind_tools — returns self."""
        return self

    async def ainvoke(self, *args: object, **kwargs: object) -> object:
        """Delegate to wrapped model."""
        return await self._model.ainvoke(*args, **kwargs)  # type: ignore[arg-type]

    def invoke(self, *args: object, **kwargs: object) -> object:
        """Delegate to wrapped model."""
        return self._model.invoke(*args, **kwargs)  # type: ignore[arg-type]


class _FakeClassifierModel:
    """Minimal fake that supports with_structured_output for ClassifierOutput.

    Not a real BaseChatModel — only implements the subset needed
    by crisis_check and safety_gate nodes.
    """

    def __init__(self, output: ClassifierOutput | None = None) -> None:
        self._output = output

    def with_structured_output(self, schema: Any) -> _FakeStructuredOutput:  # noqa: ANN401
        """Return a fake structured output runnable."""
        return _FakeStructuredOutput(output=self._output)


class _FakeStructuredOutput:
    """Fake runnable that returns a fixed ClassifierOutput on ainvoke."""

    def __init__(self, output: ClassifierOutput | None = None) -> None:
        self._output = output

    async def ainvoke(self, *_args: object, **_kwargs: object) -> ClassifierOutput:
        """Return the pre-configured ClassifierOutput."""
        if self._output is not None:
            return self._output

        return _default_safe_output()


def _default_safe_output() -> ClassifierOutput:
    """Build a default SAFE ClassifierOutput for testing."""
    from health_ally.domain.safety_types import (
        ClassifierOutput,
        CrisisLevel,
        SafetyDecision,
    )

    return ClassifierOutput(
        decision=SafetyDecision.SAFE,
        crisis_level=CrisisLevel.NONE,
        confidence=0.95,
        reasoning="Test classification — safe by default",
    )
