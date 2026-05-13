"""Router tests — focused on dispatch + arg-validation, not the
classifier itself (the classifier is an LLM call and would make
tests flaky + expensive)."""
from __future__ import annotations

import pytest

from studio.templates.router import render_template


def test_dispatch_to_matrix_multiplication():
    out = render_template(
        "matrix_multiplication",
        {"a": [[1, 2], [3, 4]], "b": [[5, 6], [7, 8]]},
    )
    assert out is not None
    svg, narration = out
    assert "<svg" in svg and "</svg>" in svg
    assert len(narration) >= 3


def test_dispatch_to_determinant():
    out = render_template("matrix_determinant", {"a": [[3, 8], [4, 6]]})
    assert out is not None
    svg, _ = out
    assert "-14" in svg  # 3*6 - 8*4


def test_dispatch_unknown_template_returns_none():
    out = render_template("matrix_eigendecomposition", {"a": [[1, 2], [3, 4]]})
    assert out is None


def test_dispatch_bad_args_returns_none():
    # Multiplication needs both a AND b.
    out = render_template("matrix_multiplication", {"a": [[1, 2]]})
    assert out is None


def test_dispatch_validates_template_internal_constraints():
    # Determinant requires square; rectangular should fail.
    out = render_template("matrix_determinant", {"a": [[1, 2, 3], [4, 5, 6]]})
    assert out is None
