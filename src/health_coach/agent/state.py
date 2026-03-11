"""LangGraph state definition for the patient coaching workflow."""

from __future__ import annotations

from typing import Annotated, Required, TypedDict

from langchain_core.messages import BaseMessage  # noqa: TC002
from langgraph.graph.message import add_messages


class PendingEffects(TypedDict, total=False):
    """Accumulated side effects flushed atomically by save_patient_context."""

    goal: dict[str, object] | None
    alerts: list[dict[str, object]]
    phase_event: str | None
    scheduled_jobs: list[dict[str, object]]
    safety_decisions: list[dict[str, object]]
    outbox_entries: list[dict[str, object]]
    audit_events: list[dict[str, object]]


class PatientState(TypedDict, total=False):
    """LangGraph state for the patient coaching workflow.

    Fields are populated by nodes and tools during graph execution.
    pending_effects is flushed atomically by save_patient_context.
    """

    patient_id: Required[str]
    tenant_id: Required[str]
    consent_verified: bool
    phase: str
    messages: Annotated[list[BaseMessage], add_messages]
    goal: str | None
    unanswered_count: int
    safety_decision: str | None
    crisis_detected: bool
    outbound_message: str | None
    delivery_status: str | None
    invocation_source: str | None  # "patient" or "scheduler"
    pending_effects: PendingEffects | None
    safety_retry_count: int
    last_outreach_at: str | None  # ISO datetime string
    last_patient_response_at: str | None  # ISO datetime string
    _job_metadata: dict[str, object] | None  # Scheduler job metadata
