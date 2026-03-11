"""Backoff and dormant transition logic."""

from __future__ import annotations

from datetime import timedelta


def next_backoff_delay(attempt: int, base_days: int = 2) -> timedelta:
    """Calculate exponential backoff delay for re-engagement outreach.

    Args:
        attempt: Current attempt number (1-based).
        base_days: Base delay in days for the first attempt.

    Returns:
        Delay as timedelta. Capped at 14 days.
    """
    delay_days = min(base_days * (2 ** (attempt - 1)), 14)
    return timedelta(days=delay_days)


def should_transition_to_dormant(
    unanswered_count: int,
    max_unanswered: int = 3,
) -> bool:
    """Check if unanswered count warrants transition to DORMANT."""
    return unanswered_count >= max_unanswered
