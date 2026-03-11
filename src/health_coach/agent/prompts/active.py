"""Active phase prompt templates — tone-adapted for follow-up context."""

from __future__ import annotations

from health_coach.agent.prompts.system import ACTIVE_PROMPT

CELEBRATION_AUGMENTATION = (
    "\n\n## Tone: Celebration\n"
    "The patient has been doing their exercises consistently! "
    "Lead with genuine celebration of their effort and progress. "
    "Reference specific exercises or adherence data if available."
)

NUDGE_AUGMENTATION = (
    "\n\n## Tone: Gentle Nudge\n"
    "We don't have recent exercise data for this patient. "
    "Gently check in on how they're doing without being pushy. "
    "Acknowledge that life gets busy and re-affirm their goal."
)

CHECK_IN_AUGMENTATION = (
    "\n\n## Tone: Check-in\n"
    "This is a routine follow-up. Ask how their exercises are going "
    "and reference their stated goal. Keep it brief and warm."
)

_TONE_MAP: dict[str, str] = {
    "celebration": CELEBRATION_AUGMENTATION,
    "nudge": NUDGE_AUGMENTATION,
    "check_in": CHECK_IN_AUGMENTATION,
}


def build_active_prompt(tone: str = "check_in") -> str:
    """Build the active phase system prompt with tone augmentation."""
    augmentation = _TONE_MAP.get(tone, CHECK_IN_AUGMENTATION)
    return ACTIVE_PROMPT + augmentation
