"""Content extraction for LangChain AIMessage objects.

langchain-anthropic returns AIMessage.content as a list of content blocks
(not a plain string) when tools are bound during streaming. This module
provides a normalizer that handles both formats.
"""

from __future__ import annotations


def extract_text_content(content: str | list[str | dict[str, object]]) -> str:
    """Extract plain text from AIMessage.content.

    When ``ChatAnthropic`` streams with tools bound, each chunk's ``.content``
    is a ``list[dict]`` (e.g. ``[{"type": "text", "text": "Hi", "index": 0}]``)
    rather than a plain ``str``.  Accumulating chunks via ``+`` preserves this
    list form.  Calling ``str()`` on the list produces its Python ``repr()``,
    which is the wrong output for user-facing messages.

    This function normalizes both formats to a plain string:
    - ``str`` → returned as-is
    - ``list`` → text blocks extracted and joined
    """
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif block.get("type") == "text":
            text = block.get("text", "")
            if isinstance(text, str) and text:
                parts.append(text)
    return "".join(parts)
