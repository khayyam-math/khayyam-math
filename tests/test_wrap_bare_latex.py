"""Unit tests for the primer's bare-LaTeX wrapper + truncation safety net.

Covers:
  * Idempotence on already-wrapped text.
  * Wrapping of bare ``\\theta`` / ``a_{ij}`` / ``x^{2}`` patterns.
  * Skipping of math regions (``$..$``, ``$$..$$``, ``\\(..\\)``, ``\\[..\\]``).
  * Truncation safety net: odd-count ``$$`` or ``$`` at end of primer
    is trimmed to a clean ellipsis instead of leaving raw LaTeX on
    screen.  Regression for 2026-06-07 field report where a 2x3 X 3x4
    matrix-multiplication primer hit ``max_tokens`` mid-formula and
    showed ``$$ $C_{24}$ = (`` to the user.
"""
from __future__ import annotations

from studio.express import wrap_bare_latex


# ── Idempotence + bare-LaTeX wrapping ───────────────────────────────


def test_already_wrapped_passes_through_unchanged():
    text = "The angle $\\theta$ satisfies $\\sin^2\\theta + \\cos^2\\theta = 1$."
    assert wrap_bare_latex(text) == text


def test_bare_backslash_command_gets_wrapped():
    text = "The angle \\theta is the rotation angle."
    out = wrap_bare_latex(text)
    assert "$\\theta$" in out


def test_bare_subscript_gets_wrapped():
    text = "Each entry a_{ij} is the i,j-th element."
    out = wrap_bare_latex(text)
    assert "$a_{ij}$" in out


def test_math_region_skipped_no_double_wrap():
    text = "Inside math: $a_{ij}$ stays; outside: a_{kl} becomes wrapped."
    out = wrap_bare_latex(text)
    assert out.count("$a_{ij}$") == 1
    assert "$a_{kl}$" in out


# ── Truncation safety net (the 2026-06-07 regression) ───────────────


def test_truncated_double_dollar_block_gets_trimmed_with_ellipsis():
    # Simulates a primer that hit max_tokens at `$$ $C_{24}$ = (`.
    text = (
        "First, compute $C_{11}$ which equals 173. "
        "- For $C_{24}$: $$ $C_{24}$ = ("
    )
    out = wrap_bare_latex(text)
    # The dangling `$$ $C_{24}$ = (` must be gone.
    assert "$$ $C_{24}$" not in out, out
    # Ellipsis marker present.
    assert "…" in out, out
    # The earlier completed sentence survives.
    assert "$C_{11}$" in out
    assert "173" in out


def test_truncated_inline_dollar_gets_trimmed():
    text = "We need $\\theta = \\pi/4$ for symmetry, and the next angle is $\\phi"
    out = wrap_bare_latex(text)
    assert "$\\theta = \\pi/4$" in out  # completed pair survives
    assert "\\phi" not in out or "$\\phi$" in out, out  # raw unclosed gone
    # Ellipsis added when truncation detected.
    assert "…" in out


def test_balanced_double_dollar_passes_through():
    text = "The matrix $$C = \\begin{bmatrix} 1 & 2 \\\\ 3 & 4 \\end{bmatrix}$$ is invertible."
    out = wrap_bare_latex(text)
    assert out == text  # idempotent on balanced display math


def test_no_dollars_passes_through():
    text = "Plain prose with no math at all."
    assert wrap_bare_latex(text) == text


def test_empty_string_passes_through():
    assert wrap_bare_latex("") == ""
