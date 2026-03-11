"""Scheduling domain logic: quiet hours, timezone, jitter, cadence config."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field


class CoachConfig(BaseModel):
    """Configuration for coaching cadence and behavior."""

    follow_up_days: list[int] = Field(default=[2, 5, 7])
    max_unanswered: int = 3
    backoff_base_days: int = 2
    quiet_hours_start: int = Field(default=21, ge=0, le=23)
    quiet_hours_end: int = Field(default=8, ge=0, le=23)
    max_jitter_minutes: int = 30
    max_messages_per_day: int = 3
    onboarding_timeout_hours: int = 72


def calculate_send_time(
    base_time: datetime,
    patient_tz: str,
    quiet_start: int,
    quiet_end: int,
) -> datetime:
    """Calculate the next valid send time, shifting out of quiet hours.

    All inputs/outputs are UTC. Quiet hours are evaluated in the patient's
    local timezone.

    Args:
        base_time: Proposed send time in UTC.
        patient_tz: IANA timezone string (e.g., "America/New_York").
        quiet_start: Hour (0-23) when quiet hours begin in patient's timezone.
        quiet_end: Hour (0-23) when quiet hours end in patient's timezone.

    Returns:
        Adjusted send time in UTC, shifted to after quiet hours if necessary.
    """
    tz = ZoneInfo(patient_tz)
    local_time = base_time.astimezone(tz)
    local_hour = local_time.hour

    if _in_quiet_hours(local_hour, quiet_start, quiet_end):
        # Shift to quiet_end on the next day if needed
        next_valid = local_time.replace(hour=quiet_end, minute=0, second=0, microsecond=0)
        if next_valid <= local_time:
            next_valid += timedelta(days=1)
        return next_valid.astimezone(UTC)

    return base_time


def _in_quiet_hours(hour: int, start: int, end: int) -> bool:
    """Check if an hour falls within quiet hours.

    Handles overnight ranges (e.g., 21:00 to 08:00).
    """
    if start < end:
        return start <= hour < end
    # Overnight: quiet from start to midnight, or midnight to end
    return hour >= start or hour < end


def add_jitter(scheduled_at: datetime, max_jitter_minutes: int = 30) -> datetime:
    """Add uniform random jitter to a scheduled time."""
    jitter = timedelta(minutes=random.uniform(0, max_jitter_minutes))  # noqa: S311
    return scheduled_at + jitter
