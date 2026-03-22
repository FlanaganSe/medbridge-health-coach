"""Langfuse tracing integration (optional)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def langfuse_config(
    *,
    enabled: bool,
    user_id: str,
    session_id: str,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Build config dict entries for Langfuse tracing.

    Returns {"callbacks": [...], "metadata": {...}, "tags": [...]} when enabled,
    empty dict when disabled or langfuse not installed.
    Creates a fresh CallbackHandler per call for async isolation.
    """
    if not enabled:
        return {}
    try:
        from langfuse.langchain import CallbackHandler
    except ImportError:
        logger.warning("langfuse.langchain.CallbackHandler unavailable; tracing disabled")
        return {}

    handler = CallbackHandler()
    metadata: dict[str, Any] = {
        "langfuse_user_id": user_id,
        "langfuse_session_id": session_id,
    }
    result: dict[str, Any] = {"callbacks": [handler], "metadata": metadata}
    if tags:
        result["tags"] = tags
    return result


def langfuse_shutdown() -> None:
    """Flush and shut down the Langfuse client if active."""
    try:
        from langfuse import get_client

        get_client().shutdown()
    except ImportError:
        pass
    except Exception:
        logger.warning("langfuse shutdown failed", exc_info=True)
