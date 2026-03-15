"""Onboarding system prompt — composes from the base system prompt."""

from __future__ import annotations

from health_ally.agent.prompts.system import ONBOARDING_PROMPT

_GOAL_INSTRUCTIONS = """
When the patient shares their goal:
1. Summarize what you heard them say about their goal in 1-2 \
sentences and ask if you captured it correctly
2. Only after the patient confirms, use the set_goal tool with:
   - goal_text: A refined, structured version of their goal
   - raw_patient_text: Their exact words

Do NOT make up a goal for the patient. Wait for them to share one.

{context_section}\
"""


def build_onboarding_prompt(
    *,
    patient_name: str | None = None,
    exercises_summary: str | None = None,
    invocation_source: str | None = None,
) -> str:
    """Build the onboarding system prompt with patient context."""
    context_parts: list[str] = []

    if patient_name:
        context_parts.append(f"Patient name: {patient_name}")

    if exercises_summary:
        context_parts.append(f"Assigned exercises:\n{exercises_summary}")

    if invocation_source == "scheduler":
        context_parts.append(
            "This is a proactive outreach — the patient has not sent a message. "
            "Initiate the conversation warmly."
        )

    context_section = ""
    if context_parts:
        context_section = "\n## Patient Context\n" + "\n".join(context_parts) + "\n"

    return ONBOARDING_PROMPT + _GOAL_INSTRUCTIONS.format(context_section=context_section)
