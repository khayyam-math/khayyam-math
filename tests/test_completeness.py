"""Unit tests for the completeness classifier + critic.

These tests are pure-Python (no network, no LLM), so they run in
under a second.  They cover:

  * the 9 archetype keys cover the prompt space (table-driven)
  * each detector fires when its required component is present
  * each detector misses when the component is absent
  * the critic returns a self-contained critique that the retry
    loop can act on (matches the prefix the retry parser expects)
  * the brief is non-empty for every archetype
  * env-gate honours SEVIM_COMPLETENESS_CRITIC=off
"""
from __future__ import annotations

import os

import pytest

from studio.templates.completeness import (
    COMPLETENESS_RUBRICS,
    ArchetypeKey,
    classify_question,
    completeness_review,
    is_enabled,
    rubric_brief_for_llm,
)


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

CLASSIFY_CASES: list[tuple[str, ArchetypeKey]] = [
    # Proof
    ("Prove that sqrt(2) is irrational", "proof"),
    ("Show that every continuous function on [0, 1] is bounded", "proof"),
    ("Derive the formula for the determinant of a 3x3 matrix", "proof"),
    ("Demonstrate that the sum of two even numbers is even", "proof"),

    # Construction
    ("Construct a polynomial with roots 1, 2, 3", "construction"),
    ("Find a function such that f(0)=1 and f'(0)=0", "construction"),
    ("Design a finite automaton accepting (a|b)*", "construction"),

    # Comparison
    ("Compare bubble sort and insertion sort", "comparison"),
    ("What is the difference between a metric and a norm?", "comparison"),
    ("Contrast Riemann and Lebesgue integration", "comparison"),
    ("How does Newton's method differ from gradient descent?", "comparison"),

    # Causal explanation
    ("Why does Newton's method need the derivative?", "causal_explanation"),
    ("How come e^x is its own derivative?", "causal_explanation"),
    ("What's the intuition behind the chain rule?", "causal_explanation"),
    ("Explain why the determinant of a triangular matrix is the product of the diagonals", "causal_explanation"),

    # Step-by-step
    ("Explain Newton's method step by step in an example", "step_by_step"),
    ("Walk me through Gaussian elimination on this matrix", "step_by_step"),
    ("Solve x^2 - 5x + 6 = 0 in detail", "step_by_step"),

    # Apply with worked example
    ("Show me with an example how Newton's method works", "apply_worked_example"),
    ("Use the chain rule to differentiate sin(x^2)", "apply_worked_example"),
    ("Apply the quadratic formula to x^2 + 2x - 3 = 0", "apply_worked_example"),
    ("Show me the unit circle with sin and cos at 30 degrees", "apply_worked_example"),
    ("Draw a DFA for L = (a|b)* ending in ab", "apply_worked_example"),

    # Concept with intuition
    ("Explain what a derivative is intuitively", "concept_with_intuition"),
    ("How does Newton's method work", "concept_with_intuition"),
    ("Help me understand what an eigenvector is", "concept_with_intuition"),
    ("Visualize the area under a curve", "concept_with_intuition"),
    ("Explain Newton's method", "concept_with_intuition"),

    # Concept definition
    ("What is a tangent line?", "concept_definition"),
    ("Define a Hilbert space", "concept_definition"),
    ("What does 'compact' mean in topology?", "concept_definition"),

    # Quick fact
    ("Evaluate 3^4", "quick_fact"),
    ("Compute 7 + 8", "quick_fact"),
    ("Simplify (x+1)(x-1)", "quick_fact"),
]


@pytest.mark.parametrize("question, expected", CLASSIFY_CASES)
def test_classify_question(question: str, expected: ArchetypeKey) -> None:
    got = classify_question(question)
    assert got == expected, (
        f"classify_question({question!r}) returned {got!r}; "
        f"expected {expected!r}"
    )


def test_classify_empty_falls_back() -> None:
    assert classify_question("") == "concept_with_intuition"
    assert classify_question("   ") == "concept_with_intuition"


def test_classify_unrecognised_falls_back() -> None:
    # No verb, no keyword — falls back to the default archetype
    got = classify_question("hmm interesting hello")
    assert got == "concept_with_intuition"


def test_classify_follow_up_promotes_after_definition() -> None:
    # Prior turn defined the concept; a bare follow-up should now
    # promote to causal_explanation (relational SOLO level).
    history = [
        "What is a derivative?",
        "A derivative is defined as the limit of the difference "
        "quotient.  In other words, the instantaneous rate of "
        "change.",
    ]
    # A vague follow-up that wouldn't otherwise match anything
    got = classify_question("Tell me more about it", history=history)
    assert got == "causal_explanation"


# ---------------------------------------------------------------------------
# Detectors / critic
# ---------------------------------------------------------------------------

def test_quick_fact_minimal_passes() -> None:
    # The minimal answer needs just a statement (an equation).
    issues = completeness_review(
        "quick_fact",
        primer="3^4 = 81.",
        narration=[{"speak": "Three to the fourth equals eighty one."}],
        chat_text="",
    )
    # Statement detector matches "= 81"; narration is at the lo
    # bound; primer is below the 15-word floor though so we expect
    # one issue (primer_too_short).  That's acceptable for a single-
    # sentence quick-fact.
    primer_short = [i for i in issues if "primer_too_short" in i]
    statement_missing = [i for i in issues if "missing_statement" in i]
    assert not statement_missing, issues
    assert primer_short, "expected primer_too_short on a 4-word primer"


def test_step_by_step_short_answer_fails() -> None:
    # A 1-sentence answer to a step-by-step question is incomplete.
    issues = completeness_review(
        "step_by_step",
        primer="Newton's method finds roots.",
        narration=[{"speak": "Newton's method finds roots."}],
        chat_text="",
    )
    keys = " ".join(issues)
    assert "missing_statement" in keys or "missing_sequence_of_steps" in keys
    assert "missing_worked_example_with_numbers" in keys
    assert "missing_takeaway" in keys
    assert "narration_too_short" in keys
    assert "primer_too_short" in keys


def test_step_by_step_complete_answer_passes() -> None:
    primer = (
        "Newton's method is an iterative technique for finding roots "
        "of a real-valued function. The core update formula is "
        "x_{n+1} = x_n - f(x_n)/f'(x_n). "
        "Intuitively, at each iterate we draw the tangent line at "
        "the point (x_n, f(x_n)) and follow that line down to the "
        "x-axis; the crossing is the next iterate. Geometrically, "
        "the tangent's slope is the derivative, which is why this "
        "method needs f' as well as f. "
        "For example, consider f(x) = x^2 - 2 starting from "
        "x_0 = 2. First, we compute f(2) = 2 and f'(2) = 4, so "
        "x_1 = 2 - 2/4 = 1.5. Next, x_2 = 1.5 - 0.25/3 = 1.4167. "
        "Then x_3 = 1.4167 - 0.00694/2.834 = 1.4142. Finally we "
        "converge to about 1.4142, the square root of two. "
        "Throughout, observe how each iterate gets closer to the "
        "root by a roughly quadratic margin. "
        "In short, the iterates converge quickly when f' is "
        "non-zero near the root, and the method is the foundation "
        "for many practical solvers used today across science and "
        "engineering applications."
    )
    issues = completeness_review(
        "step_by_step",
        primer=primer,
        narration=[
            {"speak": "Newton's method approximates roots using tangent lines."},
            {"speak": "Step 1: start at x_0 = 2."},
            {"speak": "Step 2: compute f(2) = 2 and f'(2) = 4."},
            {"speak": "Then x_1 = 2 - 2/4 = 1.5."},
            {"speak": "Next x_2 = 1.5 - 0.25/3 = 1.4167."},
            {"speak": "Finally x_3 = 1.4142, the root."},
            {"speak": "In short, the iterates converge fast."},
        ],
        chat_text="",
    )
    assert issues == [], "expected all components present, got: " + str(issues)


def test_causal_explanation_missing_chain_fails() -> None:
    issues = completeness_review(
        "causal_explanation",
        primer=(
            "Newton's method uses the derivative to find roots."
        ),
        narration=[],
        chat_text="",
    )
    keys = " ".join(issues)
    assert "missing_causal_chain" in keys
    assert "missing_link_to_prior" in keys
    assert "missing_takeaway" in keys


def test_causal_explanation_complete_passes() -> None:
    primer = (
        "Newton's method needs the derivative because the iteration "
        "follows the tangent line, and the tangent line's slope IS "
        "the derivative. Recall from differential calculus that the "
        "tangent at a point has slope f'(x). Therefore, without the "
        "derivative we have no tangent line to follow, since the "
        "tangent line is geometrically defined by the local slope. "
        "As we saw earlier in calculus, the derivative encodes the "
        "local linear approximation of a function near a point, "
        "and Newton's method is exactly the strategy of replacing "
        "the messy function f with that linear approximation, "
        "solving the linear equation, and iterating. Since the "
        "linear approximation depends on the slope, and the slope "
        "is f', it follows that the method cannot proceed without "
        "computing f' at each step. This is analogous to gradient "
        "descent: in both methods, the local first-order behaviour "
        "of f drives the next update. "
        "In short, the derivative is what lets the method make "
        "progress at each step, and it is what determines how fast "
        "the iterates converge."
    )
    issues = completeness_review(
        "causal_explanation",
        primer=primer,
        narration=[
            {"speak": "Newton's method follows tangent lines."},
            {"speak": "Recall that the tangent line's slope is the derivative."},
            {"speak": "Therefore the method needs f' to define the tangent."},
            {"speak": "Without f', we cannot draw the tangent line."},
            {"speak": "Since the next iterate is the tangent's x-axis crossing, no f' means no iteration."},
            {"speak": "In short, the derivative is the engine of the method."},
        ],
        chat_text="",
    )
    assert issues == [], str(issues)


def test_proof_missing_qed_fails() -> None:
    primer = (
        "Suppose sqrt(2) = p/q in lowest terms. Then 2 = p^2/q^2, "
        "so p^2 = 2 q^2. Therefore p is even. Let p = 2k. "
        "Substituting, 4 k^2 = 2 q^2, so q^2 = 2 k^2, hence q is "
        "even too. But then p/q was not in lowest terms — "
        "contradiction."
    )
    issues = completeness_review(
        "proof", primer=primer, narration=[], chat_text="",
    )
    keys = " ".join(issues)
    assert "missing_qed_remark" in keys


def test_proof_complete_passes() -> None:
    primer = (
        "Theorem: sqrt(2) is irrational. "
        "Proof: assume for contradiction that sqrt(2) is rational, "
        "so we can write sqrt(2) = p/q in lowest terms with p and "
        "q integers and gcd(p, q) = 1. "
        "Squaring both sides, we have 2 = p^2/q^2, which gives "
        "p^2 = 2 q^2 by multiplying through. "
        "Observe that the right-hand side is even, so p^2 must be "
        "even, and therefore p itself must be even (the square of "
        "an odd number is odd). "
        "Let p = 2k for some integer k. "
        "Substituting back, we have (2k)^2 = 2 q^2, that is "
        "4 k^2 = 2 q^2, and dividing both sides by 2 we get "
        "q^2 = 2 k^2. "
        "By the same argument applied to q, we conclude q is also "
        "even. "
        "But this contradicts our assumption that gcd(p, q) = 1, "
        "since both p and q are now divisible by 2. "
        "Therefore the assumption is false and sqrt(2) is "
        "irrational, as required.  Note this is the prototype "
        "irrationality argument; the same technique extends to "
        "sqrt(3), sqrt(5), and more generally to sqrt(n) when n is "
        "not a perfect square.  This completes the proof."
    )
    issues = completeness_review(
        "proof",
        primer=primer,
        narration=[
            {"speak": "Suppose sqrt(2) = p/q in lowest terms."},
            {"speak": "Then 2 = p^2/q^2, so p^2 = 2 q^2."},
            {"speak": "Therefore p is even; let p = 2k."},
            {"speak": "Substituting gives q^2 = 2 k^2, so q is even too."},
            {"speak": "Contradiction.  QED."},
        ],
        chat_text="",
    )
    assert issues == [], str(issues)


def test_comparison_missing_table_fails() -> None:
    issues = completeness_review(
        "comparison",
        primer=(
            "Bubble sort and insertion sort are different. "
            "Bubble sort is simpler. Insertion sort is faster on "
            "small inputs."
        ),
        narration=[],
        chat_text="",
    )
    keys = " ".join(issues)
    assert "missing_tabulation" in keys


def test_comparison_table_passes() -> None:
    primer = (
        "Comparing bubble sort and insertion sort on three "
        "criteria: time complexity, in-place behaviour, and "
        "adaptive behaviour.\n"
        "\n"
        "| Criterion | Bubble | Insertion |\n"
        "| --- | --- | --- |\n"
        "| Worst-case time | O(n^2) | O(n^2) |\n"
        "| In-place | yes | yes |\n"
        "| Adaptive (best-case O(n) on sorted input) | yes | yes |\n"
        "\n"
        "In short, on small inputs insertion sort wins by a "
        "constant factor; bubble sort is mostly pedagogical today."
    )
    issues = completeness_review(
        "comparison", primer=primer, narration=[], chat_text="",
    )
    assert issues == [], str(issues)


def test_construction_missing_verification_fails() -> None:
    issues = completeness_review(
        "construction",
        primer=(
            "We can take p(x) = (x - 1)(x - 2)(x - 3). "
            "First multiply (x-1)(x-2) = x^2 - 3x + 2. "
            "Next multiply (x^2 - 3x + 2)(x - 3) = x^3 - 6x^2 + 11x - 6. "
            "Finally we have p(x) = x^3 - 6x^2 + 11x - 6."
        ),
        narration=[],
        chat_text="",
    )
    keys = " ".join(issues)
    assert "missing_verification" in keys


# ---------------------------------------------------------------------------
# Brief
# ---------------------------------------------------------------------------

def test_rubric_brief_non_empty_for_every_archetype() -> None:
    for key in COMPLETENESS_RUBRICS:
        brief = rubric_brief_for_llm(key)  # type: ignore[arg-type]
        assert brief, f"empty brief for {key!r}"
        assert "COMPLETENESS CONTRACT" in brief
        # Each brief mentions the narration phrase range
        rubric = COMPLETENESS_RUBRICS[key]
        assert str(rubric.narration_range[0]) in brief
        assert str(rubric.narration_range[1]) in brief


def test_rubric_brief_lists_components() -> None:
    brief = rubric_brief_for_llm("step_by_step")
    for component in COMPLETENESS_RUBRICS["step_by_step"].required:
        assert component in brief


# ---------------------------------------------------------------------------
# Env gate
# ---------------------------------------------------------------------------

def test_env_gate_default_on() -> None:
    os.environ.pop("SEVIM_COMPLETENESS_CRITIC", None)
    assert is_enabled() is True


def test_env_gate_off() -> None:
    os.environ["SEVIM_COMPLETENESS_CRITIC"] = "off"
    try:
        assert is_enabled() is False
        # Critic returns [] when disabled, regardless of input
        assert completeness_review(
            "step_by_step", primer="too short", narration=[], chat_text="",
        ) == []
    finally:
        os.environ.pop("SEVIM_COMPLETENESS_CRITIC", None)


def test_env_gate_explicit_on() -> None:
    os.environ["SEVIM_COMPLETENESS_CRITIC"] = "on"
    try:
        assert is_enabled() is True
    finally:
        os.environ.pop("SEVIM_COMPLETENESS_CRITIC", None)


# ---------------------------------------------------------------------------
# Critique format compatibility
# ---------------------------------------------------------------------------

def test_critique_string_has_action_prefix() -> None:
    """The retry loop in express.py expects each critique string to
    start with a ``<rule_name>: …`` token (like the structural
    critic does).  Make sure our format matches."""
    issues = completeness_review(
        "step_by_step",
        primer="short",
        narration=[],
        chat_text="",
    )
    for i in issues:
        assert i.startswith("completeness_"), i
        assert ": " in i, i
