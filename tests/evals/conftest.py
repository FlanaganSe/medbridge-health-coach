"""DeepEval fixtures and configuration.

IMPORTANT: DEEPEVAL_TELEMETRY_OPT_OUT=1 (numeric 1, NOT 'YES').

Uses Anthropic Claude as the GEval judge model (not OpenAI).
"""

from __future__ import annotations

import os

import pytest

# Ensure telemetry is opt-out before any deepeval import triggers it
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")


@pytest.fixture(autouse=True)
def _skip_without_api_key() -> None:  # pyright: ignore[reportUnusedFunction]
    """Skip eval tests if no API key is configured."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set — skipping LLM eval")
