"""Adherence tool — get_adherence_summary."""

# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState


@tool
def get_adherence_summary(
    state: Annotated[dict[str, Any], InjectedState],
) -> str:
    """Get a summary of the patient's exercise adherence.

    Returns completion rates, streaks, and recent activity.
    """
    # M3 stub — returns realistic mock data
    return (
        "Exercise adherence summary:\n"
        "- Overall completion: 75% (15/20 sessions)\n"
        "- Current streak: 3 days\n"
        "- Best streak: 5 days\n"
        "- Most consistent: Straight leg raises (90%)\n"
        "- Needs attention: Heel slides (50%)"
    )
