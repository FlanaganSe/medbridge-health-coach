"""Tests for scheduling domain logic."""

from datetime import UTC, datetime, timedelta

from health_ally.domain.scheduling import (
    CoachConfig,
    add_jitter,
    calculate_send_time,
)


def test_calculate_send_time_not_in_quiet_hours() -> None:
    """2 PM ET is not in quiet hours (9 PM - 8 AM), should pass through."""
    base = datetime(2026, 3, 10, 18, 0, tzinfo=UTC)  # 2 PM ET
    result = calculate_send_time(base, "America/New_York", quiet_start=21, quiet_end=8)
    assert result == base


def test_calculate_send_time_in_quiet_hours_evening() -> None:
    """11 PM ET is in quiet hours, should shift to 8 AM next day."""
    base = datetime(2026, 3, 11, 3, 0, tzinfo=UTC)  # 11 PM ET (Mar 10)
    result = calculate_send_time(base, "America/New_York", quiet_start=21, quiet_end=8)
    # Should be shifted to 8 AM ET next day
    assert result.hour == 12  # 8 AM ET = 12 UTC (EDT offset -4)
    assert result > base


def test_calculate_send_time_in_quiet_hours_early_morning() -> None:
    """5 AM ET is in quiet hours, should shift to 8 AM same day."""
    base = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)  # 5 AM ET
    result = calculate_send_time(base, "America/New_York", quiet_start=21, quiet_end=8)
    assert result > base


def test_calculate_send_time_boundary_start() -> None:
    """9 PM ET exactly is start of quiet hours — should be shifted."""
    base = datetime(2026, 3, 11, 1, 0, tzinfo=UTC)  # 9 PM ET
    result = calculate_send_time(base, "America/New_York", quiet_start=21, quiet_end=8)
    assert result > base


def test_calculate_send_time_boundary_end() -> None:
    """8 AM ET exactly is end of quiet hours — should pass through."""
    base = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)  # 8 AM ET
    result = calculate_send_time(base, "America/New_York", quiet_start=21, quiet_end=8)
    assert result == base


def test_calculate_send_time_different_timezone() -> None:
    """Test with Pacific timezone."""
    # 11 PM PT → in quiet hours
    base = datetime(2026, 3, 11, 7, 0, tzinfo=UTC)  # 11 PM PT (PST -8)
    result = calculate_send_time(base, "America/Los_Angeles", quiet_start=21, quiet_end=8)
    assert result > base


def test_add_jitter_positive() -> None:
    """Jitter should add positive time delta."""
    base = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    result = add_jitter(base, max_jitter_minutes=30)
    assert result >= base
    assert result <= base + timedelta(minutes=30)


def test_add_jitter_zero() -> None:
    """Zero jitter should return same time."""
    base = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    result = add_jitter(base, max_jitter_minutes=0)
    assert result == base


def test_coach_config_defaults() -> None:
    config = CoachConfig()
    assert config.follow_up_days == [2, 5, 7]
    assert config.max_unanswered == 3
    assert config.quiet_hours_start == 21
    assert config.quiet_hours_end == 8
    assert config.max_jitter_minutes == 30
    assert config.onboarding_timeout_hours == 72
