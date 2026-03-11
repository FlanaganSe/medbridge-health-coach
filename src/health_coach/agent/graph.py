"""StateGraph construction and compilation for the patient coaching workflow."""

# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
# pyright: reportMissingTypeArgument=false
# pyright: reportUnknownParameterType=false

from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from health_coach.agent.nodes.active import active_agent
from health_coach.agent.nodes.consent import consent_gate, consent_route
from health_coach.agent.nodes.context import load_patient_context, save_patient_context
from health_coach.agent.nodes.crisis_check import crisis_check
from health_coach.agent.nodes.dormant import dormant_node
from health_coach.agent.nodes.fallback import fallback_response
from health_coach.agent.nodes.history import manage_history
from health_coach.agent.nodes.onboarding import onboarding_agent
from health_coach.agent.nodes.pending import pending_node
from health_coach.agent.nodes.re_engaging import reengagement_agent
from health_coach.agent.nodes.retry import retry_generation
from health_coach.agent.nodes.router import phase_router
from health_coach.agent.nodes.safety import safety_gate, safety_route
from health_coach.agent.state import PatientState
from health_coach.agent.tools.adherence import get_adherence_summary
from health_coach.agent.tools.clinician import alert_clinician
from health_coach.agent.tools.goal import get_program_summary, set_goal
from health_coach.agent.tools.reminder import set_reminder

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.graph.state import CompiledStateGraph

# All tools available to agent nodes
TOOLS = [set_goal, get_program_summary, set_reminder, get_adherence_summary, alert_clinician]


_PHASE_TO_AGENT: dict[str, str] = {
    "onboarding": "onboarding_agent",
    "active": "active_agent",
    "re_engaging": "reengagement_agent",
}


def _crisis_route(state: PatientState) -> str:
    """Route based on crisis_detected flag."""
    if state.get("crisis_detected"):
        return "fallback_response"
    return "manage_history"


def _dormant_route(state: PatientState) -> str:
    """Route dormant_node output: safety_gate if message generated, save if not."""
    if state.get("outbound_message"):
        return "safety_gate"
    return "save_patient_context"


def _tool_return_route(state: PatientState) -> str:
    """Route from tool_node back to the originating agent based on phase."""
    phase = state.get("phase", "onboarding")
    return _PHASE_TO_AGENT.get(phase, "onboarding_agent")


def build_graph() -> StateGraph:  # type: ignore[type-arg]
    """Build the patient coaching StateGraph.

    Returns the uncompiled graph — call compile_graph() to get a runnable.
    """
    tool_node = ToolNode(TOOLS)

    graph = StateGraph(PatientState)

    # --- Add nodes ---
    graph.add_node("consent_gate", consent_gate)
    graph.add_node("load_patient_context", load_patient_context)
    graph.add_node("crisis_check", crisis_check)
    graph.add_node("manage_history", manage_history)
    graph.add_node("pending_node", pending_node)
    graph.add_node("onboarding_agent", onboarding_agent)
    graph.add_node("active_agent", active_agent)
    graph.add_node("reengagement_agent", reengagement_agent)
    graph.add_node("dormant_node", dormant_node)
    graph.add_node("tool_node", tool_node)
    graph.add_node("safety_gate", safety_gate)
    graph.add_node("retry_generation", retry_generation)
    graph.add_node("fallback_response", fallback_response)
    graph.add_node("save_patient_context", save_patient_context)

    # --- Entry point ---
    graph.set_entry_point("consent_gate")

    # --- Edges ---
    # consent_gate → conditional: allowed → load_patient_context, denied → END
    graph.add_conditional_edges(
        "consent_gate",
        consent_route,
        {
            "load_patient_context": "load_patient_context",
            "__end__": END,
        },
    )

    # load_patient_context → crisis_check
    graph.add_edge("load_patient_context", "crisis_check")

    # crisis_check → crisis_route
    graph.add_conditional_edges(
        "crisis_check",
        _crisis_route,
        {
            "fallback_response": "fallback_response",
            "manage_history": "manage_history",
        },
    )

    # manage_history → phase_router
    graph.add_conditional_edges(
        "manage_history",
        phase_router,
        {
            "pending_node": "pending_node",
            "onboarding_agent": "onboarding_agent",
            "active_agent": "active_agent",
            "reengagement_agent": "reengagement_agent",
            "dormant_node": "dormant_node",
        },
    )

    # --- Phase-specific paths ---

    # PENDING → save directly (template message, no safety gate needed)
    graph.add_edge("pending_node", "save_patient_context")

    # DORMANT → safety_gate if welcome-back message, save if scheduler no-op
    graph.add_conditional_edges(
        "dormant_node",
        _dormant_route,
        {"safety_gate": "safety_gate", "save_patient_context": "save_patient_context"},
    )

    # ONBOARDING → tools_condition loop → safety_gate
    graph.add_conditional_edges(
        "onboarding_agent",
        tools_condition,
        {"tools": "tool_node", "__end__": "safety_gate"},
    )

    # ACTIVE → tools_condition loop → safety_gate
    graph.add_conditional_edges(
        "active_agent",
        tools_condition,
        {"tools": "tool_node", "__end__": "safety_gate"},
    )

    # RE_ENGAGING → tools_condition loop → safety_gate
    graph.add_conditional_edges(
        "reengagement_agent",
        tools_condition,
        {"tools": "tool_node", "__end__": "safety_gate"},
    )

    # tool_node → route back to originating agent based on phase
    graph.add_conditional_edges(
        "tool_node",
        _tool_return_route,
        {
            "onboarding_agent": "onboarding_agent",
            "active_agent": "active_agent",
            "reengagement_agent": "reengagement_agent",
        },
    )

    # Safety pipeline
    graph.add_conditional_edges(
        "safety_gate",
        safety_route,
        {
            "save_patient_context": "save_patient_context",
            "retry_generation": "retry_generation",
            "fallback_response": "fallback_response",
        },
    )

    # Retry loops back to safety_gate
    graph.add_edge("retry_generation", "safety_gate")

    # Fallback → save
    graph.add_edge("fallback_response", "save_patient_context")

    # Save → END
    graph.add_edge("save_patient_context", END)

    return graph


def compile_graph(
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Build and compile the graph with the given checkpointer."""
    graph = build_graph()
    return graph.compile(checkpointer=checkpointer)  # type: ignore[arg-type]
