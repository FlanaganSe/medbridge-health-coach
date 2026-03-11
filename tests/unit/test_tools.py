"""Tests for agent tools — verifies tool schema and Command pattern."""

from __future__ import annotations

from health_ally.agent.tools.adherence import get_adherence_summary
from health_ally.agent.tools.clinician import alert_clinician
from health_ally.agent.tools.goal import get_program_summary, set_goal
from health_ally.agent.tools.reminder import set_reminder


def test_set_goal_llm_schema() -> None:
    """set_goal exposes only goal_text and raw_patient_text to the LLM."""
    schema = set_goal.tool_call_schema.model_json_schema()
    props = schema["properties"]
    assert "goal_text" in props
    assert "raw_patient_text" in props
    # Injected params should NOT appear in LLM-facing schema
    assert "state" not in props
    assert "tool_call_id" not in props


def test_get_program_summary_llm_schema() -> None:
    """get_program_summary has no LLM-visible parameters."""
    schema = get_program_summary.tool_call_schema.model_json_schema()
    props = schema.get("properties", {})
    assert "state" not in props


def test_set_reminder_llm_schema() -> None:
    """set_reminder exposes reminder_time and reminder_message to the LLM."""
    schema = set_reminder.tool_call_schema.model_json_schema()
    props = schema["properties"]
    assert "reminder_time" in props
    assert "reminder_message" in props
    assert "state" not in props


def test_get_adherence_summary_llm_schema() -> None:
    """get_adherence_summary has no LLM-visible parameters."""
    schema = get_adherence_summary.tool_call_schema.model_json_schema()
    props = schema.get("properties", {})
    assert "state" not in props


def test_alert_clinician_llm_schema() -> None:
    """alert_clinician exposes reason and priority to the LLM."""
    schema = alert_clinician.tool_call_schema.model_json_schema()
    props = schema["properties"]
    assert "reason" in props
    assert "priority" in props
    assert "state" not in props


def test_get_program_summary_returns_string() -> None:
    """get_program_summary returns a stub exercise program summary."""
    result = get_program_summary.invoke({"state": {}})
    assert isinstance(result, str)
    assert "exercise" in result.lower()


def test_get_adherence_summary_returns_string() -> None:
    """get_adherence_summary returns a stub adherence summary."""
    result = get_adherence_summary.invoke({"state": {}})
    assert isinstance(result, str)
    assert "adherence" in result.lower()
