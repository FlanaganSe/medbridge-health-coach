"""ModelGateway — LLM factory with fallback support."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

    from health_coach.settings import Settings


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
        """Build fallback model — safe message unless PHI approved for OpenAI."""
        if not self._settings.fallback_phi_approved:
            # No BAA signed — fallback is a deterministic safe model
            from langchain_core.language_models.fake_chat_models import (
                FakeListChatModel,
            )

            return FakeListChatModel(
                responses=[
                    "I'm having trouble processing your request right now. "
                    "Please reach out to your care team if you need immediate help."
                ],
            )

        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model="gpt-4o",
            max_retries=0,
            api_key=self._settings.openai_api_key.get_secret_value(),  # type: ignore[arg-type]
        )


class FakeModelGateway(ModelGateway):
    """Test model gateway returning fake chat models.

    Note: GenericFakeChatModel does NOT support bind_tools().
    Tests must construct AIMessage(tool_calls=[...]) directly.
    """

    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = responses or ["I'm a test response."]

    def get_chat_model(self, purpose: str) -> BaseChatModel:
        """Return a FakeListChatModel for testing."""
        from langchain_core.language_models.fake_chat_models import (
            FakeListChatModel,
        )

        return FakeListChatModel(responses=list(self._responses))
