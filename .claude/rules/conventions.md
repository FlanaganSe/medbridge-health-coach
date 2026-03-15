---
description: Code style and established patterns.
---
# Conventions

- Test files: `test_foo.py` in `tests/` directory mirroring `src/` structure
- `__all__` for public API exports in `__init__.py` files
- Type hints on all public functions
- Formatter handles formatting — don't bikeshed
- Pydantic models for data validation and serialization
- Async-first: prefer `async def` for I/O-bound operations

## Established Patterns

- **Content extraction**: Use `extract_text_content()` from `agent/content.py` to get text from `AIMessage.content` — never `str(response.content)`. See `agent/nodes/active.py`.
