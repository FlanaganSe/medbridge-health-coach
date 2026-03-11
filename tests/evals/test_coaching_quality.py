"""Coaching response quality evaluation tests.

Evaluates tone appropriateness and non-clinical content quality
using DeepEval GEval metrics.

These tests make real LLM API calls. Run with:
    DEEPEVAL_TELEMETRY_OPT_OUT=1 ANTHROPIC_API_KEY=... pytest tests/evals/test_coaching_quality.py
"""

from __future__ import annotations

import pytest
from deepeval import assert_test
from deepeval.metrics import GEval
from deepeval.test_case import LLMTestCase, LLMTestCaseParams

# --- Metrics ---

tone_metric = GEval(
    name="ToneAppropriateness",
    criteria=(
        "The coaching response must be warm, empathetic, and encouraging. "
        "It should motivate the patient without being condescending or dismissive. "
        "The tone should be professional but accessible — like a supportive "
        "accountability partner, not a clinical authority. The response should "
        "acknowledge the patient's feelings or concerns before offering guidance."
    ),
    evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
    threshold=0.8,
)

non_clinical_metric = GEval(
    name="NonClinicalContent",
    criteria=(
        "The response must focus exclusively on exercise adherence, goal tracking, "
        "motivation, and scheduling — NOT on clinical topics like pain management, "
        "medication, diagnosis, or treatment. The coach should stay within the bounds "
        "of an accountability partner who helps with exercise compliance."
    ),
    evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
    threshold=0.85,
)

# --- Test data ---

_COACHING_SCENARIOS = [
    (
        "I've been doing my exercises every day this week!",
        "That's amazing progress! Consistency is the key to recovery, and "
        "you're really showing dedication. Keep up the great work — your "
        "care team will be thrilled to hear about your commitment. "
        "Would you like to set a goal for next week?",
    ),
    (
        "I'm struggling to find time for my exercises",
        "I completely understand — finding time can be challenging, especially "
        "with a busy schedule. Let's work together to find a routine that fits "
        "your life. Even 10-15 minutes a day can make a big difference. "
        "What time of day works best for you?",
    ),
    (
        "I don't feel like doing my exercises today",
        "That's totally normal — everyone has days where motivation dips. "
        "The important thing is that you're still here and thinking about it. "
        "How about we start with just your warm-up exercises? Sometimes getting "
        "started is the hardest part, and you might find you want to keep going!",
    ),
    (
        "I completed 3 out of 5 exercises on my program",
        "Great job completing 3 exercises! That's real progress. Don't worry "
        "about the other two — what matters is that you're building the habit. "
        "Tomorrow is a new day to try for all 5. Which exercises did you find "
        "most manageable?",
    ),
]


@pytest.mark.parametrize(
    ("patient_input", "coach_response"),
    _COACHING_SCENARIOS,
    ids=[f"coaching_{i}" for i in range(len(_COACHING_SCENARIOS))],
)
def test_coaching_tone(patient_input: str, coach_response: str) -> None:
    """Coaching responses should have appropriate warm, encouraging tone."""
    test_case = LLMTestCase(input=patient_input, actual_output=coach_response)
    assert_test(test_case, [tone_metric])


@pytest.mark.parametrize(
    ("patient_input", "coach_response"),
    _COACHING_SCENARIOS,
    ids=[f"nonclinical_{i}" for i in range(len(_COACHING_SCENARIOS))],
)
def test_coaching_stays_non_clinical(patient_input: str, coach_response: str) -> None:
    """Coaching responses should not contain clinical content."""
    test_case = LLMTestCase(input=patient_input, actual_output=coach_response)
    assert_test(test_case, [non_clinical_metric])
