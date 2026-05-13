"""Smoke + correctness tests for the matrix-family templates."""
from __future__ import annotations

import re
import pytest

from studio.templates import (
    matrix_multiplication, matrix_transpose,
    matrix_determinant, matrix_inverse, system_of_equations,
)


def _ids(svg: str) -> set[str]:
    return set(re.findall(r'id="([^"]+)"', svg))


# ── matrix_multiplication ─────────────────────────────────────────


def test_mul_2x2_correct_product():
    svg, narr = matrix_multiplication([[1, 2], [3, 4]], [[5, 6], [7, 8]])
    # canvas_h is now dynamic (tight-fit to content) — just check the
    # width is 900 and viewBox is present.
    assert 'viewBox="0 0 900' in svg
    # Product entries should appear: c00=19, c01=22, c10=43, c11=50.
    for v in ("19", "22", "43", "50"):
        assert f">{v}<" in svg
    ids = _ids(svg)
    for required in ("matrix_a", "matrix_b", "matrix_c",
                     "matrix_a_outer", "matrix_b_outer", "matrix_c_outer",
                     "op_times", "op_equals", "title"):
        assert required in ids
    assert len(narr) >= 3


def test_mul_nonconformable_shows_error():
    svg, narr = matrix_multiplication([[1, 2], [3, 4]], [[5, 6, 7]])
    assert "dim_error" in svg
    # Some phrase tells the learner the product is undefined / not defined.
    assert any(("undefined" in p["speak"].lower())
               or ("not defined" in p["speak"].lower())
               for p in narr)


def test_mul_5x5_fits_viewbox():
    a = [[i + j for j in range(5)] for i in range(5)]
    b = [[(i + 1) * (j + 1) for j in range(5)] for i in range(5)]
    svg, _ = matrix_multiplication(a, b)
    assert "<svg" in svg and "</svg>" in svg
    # Cell text exists for the result top-left (5x5 product).
    # sum over k of a[0][k]*b[k][0] = 0*1+1*2+2*3+3*4+4*5 = 40
    assert ">40<" in svg


# ── matrix_transpose ──────────────────────────────────────────────


def test_transpose_2x3_swaps_dimensions():
    svg, narr = matrix_transpose([[1, 2, 3], [4, 5, 6]])
    # Original 2 rows × 3 cols → transpose 3 rows × 2 cols.
    assert "matrix_a" in _ids(svg)
    assert "matrix_at" in _ids(svg)
    # cell_matrix_at_2_1 must exist (row 2 col 1 of transpose).
    assert "cell_matrix_at_2_1" in _ids(svg)
    # Value at A[0][1] (= 2) should be at A^T[1][0].
    # We can't easily check pixel positions, but cell ids exist.
    assert len(narr) >= 3


# ── matrix_determinant ────────────────────────────────────────────


def test_det_2x2_value_and_formula():
    svg, narr = matrix_determinant([[3, 8], [4, 6]])
    # det = 3*6 - 8*4 = 18 - 32 = -14.
    assert "= -14" in svg
    assert "det_formula" in _ids(svg)


def test_det_3x3_value():
    svg, _ = matrix_determinant([[6, 1, 1], [4, -2, 5], [2, 8, 7]])
    # det = -306 (standard textbook example).
    assert "-306" in svg


def test_det_singular_says_no_inverse():
    svg, narr = matrix_determinant([[1, 2], [2, 4]])
    assert "= 0" in svg
    assert any("singular" in p["speak"].lower() for p in narr)


# ── matrix_inverse ────────────────────────────────────────────────


def test_inverse_2x2_correct_values():
    # A = [[4,7],[2,6]], det = 10, inverse = [[0.6,-0.7],[-0.2,0.4]]
    svg, _ = matrix_inverse([[4, 7], [2, 6]])
    ids = _ids(svg)
    assert "matrix_inv" in ids
    for v in ("0.6", "-0.7", "-0.2", "0.4"):
        assert f">{v}<" in svg


def test_inverse_singular_renders_error():
    svg, narr = matrix_inverse([[1, 2], [2, 4]])
    assert "singular_error" in _ids(svg)
    assert any("singular" in p["speak"].lower() for p in narr)


# ── system_of_equations ───────────────────────────────────────────


def test_system_2x2_solution_correct():
    # 2x + 3y = 8 ; x - y = 1  →  x=2.2, y=1.2
    svg, narr = system_of_equations([[2, 3], [1, -1]], [8, 1])
    ids = _ids(svg)
    for required in ("matrix_a", "vector_x", "vector_b", "op_equals",
                     "solution"):
        assert required in ids
    assert "x1 = 2.2" in svg
    assert "x2 = 1.2" in svg


def test_system_invalid_shape_raises():
    with pytest.raises(ValueError):
        system_of_equations([[1, 2, 3]], [4])  # non-square
    with pytest.raises(ValueError):
        system_of_equations([[1, 2], [3, 4]], [5])  # rhs wrong length


# ── narration sanity ──────────────────────────────────────────────


def test_every_template_has_valid_highlight_ids():
    """Every narration `highlight` id should be a real SVG element id."""
    cases = [
        matrix_multiplication([[1, 2], [3, 4]], [[5, 6], [7, 8]]),
        matrix_transpose([[1, 2, 3], [4, 5, 6]]),
        matrix_determinant([[3, 8], [4, 6]]),
        matrix_inverse([[4, 7], [2, 6]]),
        system_of_equations([[2, 3], [1, -1]], [8, 1]),
    ]
    for svg, narration in cases:
        ids = _ids(svg)
        for phrase in narration:
            highlights = phrase.get("highlight") or []
            if isinstance(highlights, str):
                highlights = [highlights]
            for hid in highlights:
                if hid:
                    assert hid in ids, (
                        f"narration highlight {hid!r} not in SVG ids "
                        f"({sorted(ids)[:8]}...)"
                    )
