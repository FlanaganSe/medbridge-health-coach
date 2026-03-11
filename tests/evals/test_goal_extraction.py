"""Goal extraction accuracy evaluation tests.

Evaluates whether the system correctly extracts structured goals
from free-text patient input using DeepEval GEval metrics with
Anthropic Claude as judge.

These tests make real LLM API calls. Run with:
    DEEPEVAL_TELEMETRY_OPT_OUT=1 ANTHROPIC_API_KEY=... pytest tests/evals/test_goal_extraction.py
"""

from __future__ import annotations

import pytest
from deepeval import assert_test
from deepeval.metrics import GEval
from deepeval.models import AnthropicModel
from deepeval.test_case import LLMTestCase, LLMTestCaseParams

# --- Metrics (using Anthropic Claude as judge, not OpenAI) ---

_judge = AnthropicModel(model="claude-haiku-4-5-20251001")

goal_extraction_metric = GEval(
    name="GoalExtractionAccuracy",
    model=_judge,
    criteria=(
        "Given a patient's free-text description of their exercise goal, "
        "the extracted goal must accurately capture: (1) the specific activity "
        "or exercise mentioned, (2) the frequency or target if stated, and "
        "(3) the patient's intent. The extraction should be concise and "
        "structured, not a paraphrase of the entire input. Minor wording "
        "differences are acceptable as long as the meaning is preserved."
    ),
    evaluation_params=[
        LLMTestCaseParams.INPUT,
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.EXPECTED_OUTPUT,
    ],
    threshold=0.7,
)

# --- Test data ---

_GOAL_EXTRACTION_CASES = [
    (
        "I want to be able to walk for 30 minutes without stopping by the end of the month",
        "Walk 30 minutes continuously",
        "Walk 30 minutes without stopping by end of month",
    ),
    (
        "My goal is to do my knee exercises three times a week like my therapist said",
        "Complete knee exercises 3x/week",
        "Knee exercises three times per week (per therapist recommendation)",
    ),
    (
        "I just want to get back to playing tennis with my friends",
        "Return to tennis",
        "Resume playing tennis recreationally",
    ),
    (
        "I need to strengthen my core so my back stops hurting during work",
        "Core strengthening to reduce back pain at work",
        "Core strengthening to reduce back pain during work",
    ),
    (
        "I'd like to do all five exercises on my home program every day",
        "Complete all 5 HEP exercises daily",
        "Complete all 5 home exercise program exercises daily",
    ),
]


@pytest.mark.parametrize(
    ("patient_input", "extracted_goal", "expected_goal"),
    _GOAL_EXTRACTION_CASES,
    ids=[f"goal_{i}" for i in range(len(_GOAL_EXTRACTION_CASES))],
)
def test_goal_extraction_accuracy(
    patient_input: str, extracted_goal: str, expected_goal: str
) -> None:
    """Extracted goals should accurately capture the patient's intent."""
    test_case = LLMTestCase(
        input=patient_input,
        actual_output=extracted_goal,
        expected_output=expected_goal,
    )
    assert_test(test_case, [goal_extraction_metric])
