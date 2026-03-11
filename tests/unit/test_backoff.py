"""Tests for backoff logic — exponential delay and dormant transition."""

from __future__ import annotations

from datetime import timedelta

from health_ally.domain.backoff import next_backoff_delay, should_transition_to_dormant


def test_backoff_first_attempt() -> None:
    """First attempt uses base delay."""
    delay = next_backoff_delay(1, base_days=2)
    assert delay == timedelta(days=2)


def test_backoff_second_attempt() -> None:
    """Second attempt doubles the delay."""
    delay = next_backoff_delay(2, base_days=2)
    assert delay == timedelta(days=4)


def test_backoff_third_attempt() -> None:
    """Third attempt quadruples the base."""
    delay = next_backoff_delay(3, base_days=2)
    assert delay == timedelta(days=8)


def test_backoff_capped_at_14_days() -> None:
    """Large attempt numbers are capped at 14 days."""
    delay = next_backoff_delay(10, base_days=2)
    assert delay == timedelta(days=14)


def test_should_not_transition_below_threshold() -> None:
    """Below threshold → no transition."""
    assert not should_transition_to_dormant(2, max_unanswered=3)


def test_should_transition_at_threshold() -> None:
    """At threshold → transition."""
    assert should_transition_to_dormant(3, max_unanswered=3)


def test_should_transition_above_threshold() -> None:
    """Above threshold → still transition."""
    assert should_transition_to_dormant(5, max_unanswered=3)
