"""Re-engagement prompt template — warm, low-pressure tone."""

from __future__ import annotations

from health_ally.agent.prompts.system import RE_ENGAGING_PROMPT

SCHEDULER_RE_ENGAGING_AUGMENTATION = (
    "\n\n## Context: Proactive Re-engagement\n"
    "This is a proactive outreach to a patient who hasn't responded recently. "
    "Keep the message short — one warm sentence and one simple question. "
    "Don't list everything they've missed. Make it easy to reply."
)

PATIENT_RE_ENGAGING_AUGMENTATION = (
    "\n\n## Context: Patient Returned\n"
    "The patient has reached out after a period of inactivity. "
    "Welcome them back warmly. Reference their previous goal. "
    "Don't dwell on the gap — focus on moving forward together."
)


def build_re_engaging_prompt(invocation_source: str | None = None) -> str:
    """Build re-engagement system prompt based on invocation source."""
    if invocation_source == "scheduler":
        return RE_ENGAGING_PROMPT + SCHEDULER_RE_ENGAGING_AUGMENTATION
    return RE_ENGAGING_PROMPT + PATIENT_RE_ENGAGING_AUGMENTATION
