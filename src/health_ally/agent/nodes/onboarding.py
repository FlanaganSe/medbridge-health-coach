"""Onboarding agent node — LLM-powered onboarding conversation."""

# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportAttributeAccessIssue=false

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from health_ally.agent.content import extract_text_content
from health_ally.agent.context import get_coach_context
from health_ally.agent.prompts.onboarding import build_onboarding_prompt
from health_ally.agent.state import PatientState  # noqa: TC001
from health_ally.agent.tools.goal import get_program_summary, set_goal

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

logger = structlog.stdlib.get_logger()

# Tools available during onboarding
ONBOARDING_TOOLS = [set_goal, get_program_summary]


async def onboarding_agent(
    state: PatientState,
    config: RunnableConfig,
) -> dict[str, object]:
    """Run the onboarding conversation agent.

    Constructs system prompt with patient context, binds tools,
    and invokes the LLM. The LLM may call set_goal to record
    the patient's exercise goal.
    """
    ctx = get_coach_context(config)
    patient_id = state["patient_id"]

    # Build system prompt with context
    system_prompt = build_onboarding_prompt(
        invocation_source=state.get("invocation_source"),
    )

    # Get coach model with tools bound
    coach_model = ctx.model_gateway.get_chat_model("coach")
    model_with_tools = coach_model.bind_tools(
        ONBOARDING_TOOLS,
        parallel_tool_calls=False,
    )

    # Build message list
    messages = list(state.get("messages", []))

    try:
        response = await model_with_tools.ainvoke(
            [{"role": "system", "content": system_prompt}, *messages]
        )
    except Exception:
        logger.exception("onboarding_agent_error", patient_id=patient_id)
        return {"draft_message": None}

    has_tool_calls = bool(getattr(response, "tool_calls", None))
    # Tool calls → graph loops through tool_node; defer draft_message
    # to the final (no-tools) invocation.
    content = None if has_tool_calls else (extract_text_content(response.content) or None)

    logger.info(
        "onboarding_agent_response",
        patient_id=patient_id,
        has_tool_calls=has_tool_calls,
    )

    return {
        "messages": [response],
        "draft_message": content,
    }
