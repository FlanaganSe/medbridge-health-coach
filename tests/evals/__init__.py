"""LLM evaluation tests using DeepEval.

These tests make real LLM API calls and should only be run in CI on main branch
or manually during development. They require ANTHROPIC_API_KEY to be set.

IMPORTANT: DEEPEVAL_TELEMETRY_OPT_OUT=1 must be set (numeric 1, not YES).
"""
