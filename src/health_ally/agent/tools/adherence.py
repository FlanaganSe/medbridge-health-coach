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
    # Stub profiles — deterministic per patient for demo variety
    profiles = [
        "Exercise adherence summary:\n"
        "Overall completion: 90% (18/20 sessions)\n"
        "Current streak: 7 days\n"
        "Best streak: 12 days\n\n"
        "Exercise breakdown:\n"
        "- Straight leg raises: 95% (19/20)\n"
        "- Heel slides: 85% (17/20)\n"
        "- Quad sets: 90% (18/20)",
        "Exercise adherence summary:\n"
        "Overall completion: 62% (13/21 sessions)\n"
        "Current streak: 1 day\n"
        "Best streak: 5 days\n\n"
        "Exercise breakdown:\n"
        "- Wall squats: 71% (15/21)\n"
        "- Calf raises: 57% (12/21)\n"
        "- Hamstring curls: 57% (12/21)",
        "Exercise adherence summary:\n"
        "Overall completion: 40% (8/20 sessions)\n"
        "Current streak: 0 days\n"
        "Best streak: 3 days\n\n"
        "Exercise breakdown:\n"
        "- Shoulder flexion: 45% (9/20)\n"
        "- External rotation: 35% (7/20)\n"
        "- Pendulum swings: 40% (8/20)",
    ]
    patient_id = state.get("patient_id", "")
    index = hash(patient_id) % len(profiles)
    return profiles[index]
