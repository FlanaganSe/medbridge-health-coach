"""DeepEval fixtures and configuration.

IMPORTANT: DEEPEVAL_TELEMETRY_OPT_OUT=1 (numeric 1, NOT 'YES').

Uses Anthropic Claude as the GEval judge model (not OpenAI).
"""

from __future__ import annotations

import os

# Ensure telemetry is opt-out before any deepeval import triggers it
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")


def pytest_ignore_collect() -> bool:
    """Skip eval test collection entirely when no API key is present.

    This prevents DeepEval's AnthropicModel() from being instantiated
    at module level during import, which crashes without a key.
    """
    return not os.environ.get("ANTHROPIC_API_KEY")
