"""Active phase prompt templates — tone-adapted for follow-up context."""

from __future__ import annotations

from health_ally.agent.prompts.system import ACTIVE_PROMPT

CHECK_IN_AUGMENTATION = (
    "\n\n## Tone: Check-in\n"
    "This is a routine follow-up. Ask how their exercises are going "
    "and reference their stated goal. Keep it brief and warm."
)

_TONE_MAP: dict[str, str] = {
    "check_in": CHECK_IN_AUGMENTATION,
}


def build_active_prompt(tone: str = "check_in") -> str:
    """Build the active phase system prompt with tone augmentation."""
    augmentation = _TONE_MAP.get(tone, CHECK_IN_AUGMENTATION)
    return ACTIVE_PROMPT + augmentation
