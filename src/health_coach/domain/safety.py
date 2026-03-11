"""Safety policy constants — deterministic fallback messages.

These are hardcoded strings, not LLM-generated. They are the last
line of defense when the safety pipeline blocks or detects a crisis.
"""

from __future__ import annotations

SAFE_FALLBACK_MESSAGE = (
    "I appreciate your patience. For any health-related questions, "
    "please reach out to your care team directly. "
    "I'm here to help you stay on track with your exercises!"
)

CRISIS_RESPONSE_MESSAGE = (
    "I'm concerned about what you've shared. "
    "If you or someone you know is in crisis, "
    "please contact the 988 Suicide & Crisis Lifeline by calling or texting 988. "
    "Your care team has also been notified. "
    "You are not alone, and help is available right now."
)

CLINICAL_REDIRECT_MESSAGE = (
    "That's a great question, but it's outside my area of expertise. "
    "I'd recommend reaching out to your care team — "
    "they can give you the best guidance on that. "
    "In the meantime, I'm here to help you stay on track with your exercises!"
)
