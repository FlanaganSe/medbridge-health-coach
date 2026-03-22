"""Tests for Langfuse tracing helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from health_ally.observability.langfuse import langfuse_config, langfuse_shutdown


def test_langfuse_config_disabled() -> None:
    """Returns empty dict when enabled=False."""
    result = langfuse_config(enabled=False, user_id="p1", session_id="s1")
    assert result == {}


def test_langfuse_config_import_error() -> None:
    """Returns empty dict when langfuse is not importable."""
    with patch.dict("sys.modules", {"langfuse.langchain": None}):
        result = langfuse_config(enabled=True, user_id="p1", session_id="s1")
    assert result == {}


def test_langfuse_config_enabled() -> None:
    """Returns callbacks + metadata + tags when enabled."""
    mock_handler = MagicMock()
    mock_cls = MagicMock(return_value=mock_handler)

    with patch.dict(
        "sys.modules",
        {"langfuse": MagicMock(), "langfuse.langchain": MagicMock(CallbackHandler=mock_cls)},
    ):
        result = langfuse_config(
            enabled=True,
            user_id="patient-42",
            session_id="thread-99",
            tags=["scheduler", "day_2_followup"],
        )

    mock_cls.assert_called_once_with()
    assert result["callbacks"] == [mock_handler]
    assert result["metadata"] == {
        "langfuse_user_id": "patient-42",
        "langfuse_session_id": "thread-99",
    }
    assert result["tags"] == ["scheduler", "day_2_followup"]


def test_langfuse_config_enabled_no_tags() -> None:
    """Omits tags key when tags is None."""
    mock_handler = MagicMock()
    mock_cls = MagicMock(return_value=mock_handler)

    with patch.dict(
        "sys.modules",
        {"langfuse": MagicMock(), "langfuse.langchain": MagicMock(CallbackHandler=mock_cls)},
    ):
        result = langfuse_config(
            enabled=True,
            user_id="p1",
            session_id="s1",
        )

    assert "tags" not in result
    assert "callbacks" in result
    assert "metadata" in result


def test_langfuse_shutdown_import_error() -> None:
    """Doesn't raise when langfuse is not installed."""
    with patch.dict("sys.modules", {"langfuse": None}):
        langfuse_shutdown()  # Should not raise


def test_langfuse_shutdown_swallows_exception() -> None:
    """Swallows non-ImportError exceptions from the Langfuse client."""
    mock_client = MagicMock()
    mock_client.shutdown.side_effect = RuntimeError("connection refused")
    mock_langfuse = MagicMock(get_client=MagicMock(return_value=mock_client))

    with patch.dict("sys.modules", {"langfuse": mock_langfuse}):
        langfuse_shutdown()  # Should not raise
