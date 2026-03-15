"""Tests for extract_text_content — AIMessage.content normalization."""

from __future__ import annotations

import pytest

from health_ally.agent.content import extract_text_content


class TestStringContent:
    """When content is already a plain string (e.g. ainvoke without tools)."""

    def test_plain_string_passthrough(self) -> None:
        assert extract_text_content("Hello world") == "Hello world"

    def test_empty_string_returns_empty(self) -> None:
        assert extract_text_content("") == ""

    def test_multiline_string(self) -> None:
        text = "Line 1\nLine 2\nLine 3"
        assert extract_text_content(text) == text


class TestListContent:
    """When content is a list of content blocks (Anthropic streaming with tools)."""

    def test_single_text_block(self) -> None:
        content: list[str | dict[str, object]] = [
            {"type": "text", "text": "Hello world", "index": 0},
        ]
        assert extract_text_content(content) == "Hello world"

    def test_multiple_text_blocks_joined(self) -> None:
        content: list[str | dict[str, object]] = [
            {"type": "text", "text": "Hello ", "index": 0},
            {"type": "text", "text": "world!", "index": 0},
        ]
        assert extract_text_content(content) == "Hello world!"

    def test_tool_use_blocks_ignored(self) -> None:
        content: list[str | dict[str, object]] = [
            {"type": "text", "text": "Let me check that.", "index": 0},
            {"type": "tool_use", "id": "tc_1", "name": "get_adherence_summary", "input": {}},
        ]
        assert extract_text_content(content) == "Let me check that."

    def test_empty_list_returns_empty(self) -> None:
        assert extract_text_content([]) == ""

    def test_text_block_with_empty_text(self) -> None:
        content: list[str | dict[str, object]] = [
            {"type": "text", "text": "", "index": 0},
        ]
        assert extract_text_content(content) == ""

    def test_string_elements_in_list(self) -> None:
        """LangChain type allows list[str | dict] — handle string elements."""
        content: list[str | dict[str, object]] = ["Hello ", "world!"]
        assert extract_text_content(content) == "Hello world!"

    def test_none_text_value_skipped(self) -> None:
        """A text block with None text is safely skipped."""
        content: list[str | dict[str, object]] = [
            {"type": "text", "text": None},
            {"type": "text", "text": "Visible", "index": 0},
        ]
        assert extract_text_content(content) == "Visible"

    def test_thinking_blocks_ignored(self) -> None:
        content: list[str | dict[str, object]] = [
            {"type": "thinking", "text": "internal reasoning"},
            {"type": "text", "text": "Visible response", "index": 0},
        ]
        assert extract_text_content(content) == "Visible response"


class TestReprRegression:
    """Verify the exact bug scenario — str() on a list produced repr."""

    def test_does_not_produce_repr(self) -> None:
        """The exact content shape from Anthropic streaming accumulation."""
        content: list[str | dict[str, object]] = [
            {
                "type": "text",
                "text": "It looks like things have been a bit of a struggle lately",
                "index": 0,
            },
        ]
        result = extract_text_content(content)
        # Must NOT contain the repr artifacts
        assert "[{" not in result
        assert "'type'" not in result
        assert "'index'" not in result
        assert result == "It looks like things have been a bit of a struggle lately"

    @pytest.mark.parametrize(
        "content",
        [
            "plain string",
            [{"type": "text", "text": "from list", "index": 0}],
            ["bare", " strings"],
        ],
        ids=["str", "list-dict", "list-str"],
    )
    def test_always_returns_str(self, content: str | list[str | dict[str, object]]) -> None:
        result = extract_text_content(content)
        assert isinstance(result, str)
