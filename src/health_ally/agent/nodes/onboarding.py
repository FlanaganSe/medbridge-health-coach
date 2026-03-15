"""Onboarding agent node — LLM-powered onboarding conversation."""

# pyright: reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportAttributeAccessIssue=false

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from langchain_core.messages import AIMessage
from langgraph.config import get_stream_writer

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
        writer = get_stream_writer()
        full_response = None
        async for chunk in model_with_tools.astream(
            [{"role": "system", "content": system_prompt}, *messages]
        ):
            text = extract_text_content(chunk.content) if chunk.content else ""
            if text and not getattr(chunk, "tool_call_chunks", None):
                writer({"type": "token", "content": text})
            full_response = chunk if full_response is None else full_response + chunk
        response = full_response if full_response is not None else AIMessage(content="")
    except Exception:
        logger.exception("onboarding_agent_error", patient_id=patient_id)
        # Return empty to trigger fallback via safety gate
        return {
            "outbound_message": None,
        }

    has_tool_calls = bool(getattr(response, "tool_calls", None))
    # Tool calls → graph loops through tool_node; defer outbound_message
    # to the final (no-tools) invocation so raw tool JSON isn't streamed.
    content = None if has_tool_calls else (extract_text_content(response.content) or None)

    logger.info(
        "onboarding_agent_response",
        patient_id=patient_id,
        has_tool_calls=has_tool_calls,
    )

    return {
        "messages": [response],
        "outbound_message": content,
    }
