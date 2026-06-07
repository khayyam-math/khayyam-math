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


def test_mul_narration_highlights_rows_columns_not_whole_matrices():
    """Dot-product narration must highlight ROW of A + COLUMN of B,
    not the full ``matrix_a`` / ``matrix_b`` groups.

    Regression for 2026-06-06: on a 3x4 X 4x5 multiplication the entire
    matrices pulsed at once, leaving the learner with no visual cue for
    which row/column actually contract on each phrase.
    """
    a = [[i * 4 + j + 1 for j in range(4)] for i in range(3)]
    b = [[i * 5 + j + 1 for j in range(5)] for i in range(4)]
    _, narr = matrix_multiplication(a, b)
    # Pull the worked-example phrase (the one that names C[1,1]).
    worked = [p for p in narr if "C row 1 column 1" in p["speak"]]
    assert worked, [p["speak"] for p in narr]
    hi = worked[0]["highlight"]
    # Must include every cell of row 0 of A and every cell of col 0 of B.
    for j in range(4):
        assert f"cell_matrix_a_0_{j}" in hi, hi
    for i in range(4):
        assert f"cell_matrix_b_{i}_0" in hi, hi
    # Must include the result cell.
    assert "cell_matrix_c_0_0" in hi, hi
    # Must NOT highlight the whole-matrix groups for the dot-product phrase.
    assert "matrix_a" not in hi and "matrix_b" not in hi, hi

    # Second worked example: row 1 of A + column 1 of B + C[1,1].
    second = [p for p in narr if "C row 2 column 2" in p["speak"]]
    assert second, [p["speak"] for p in narr]
    hi2 = second[0]["highlight"]
    for j in range(4):
        assert f"cell_matrix_a_1_{j}" in hi2, hi2
    for i in range(4):
        assert f"cell_matrix_b_{i}_1" in hi2, hi2
    assert "cell_matrix_c_1_1" in hi2, hi2

    # Dot-product rule phrase (no specific entry yet) highlights row 0 + col 0.
    rule = [p for p in narr if "dot product of row i of A" in p["speak"]
            and "Worked example" not in p["speak"]
            and "general rule" not in p["speak"].lower()]
    assert rule, [p["speak"] for p in narr]
    hi3 = rule[0]["highlight"]
    assert "matrix_a" not in hi3 and "matrix_b" not in hi3, hi3
    assert any(h.startswith("cell_matrix_a_0_") for h in hi3), hi3
    assert any(h.startswith("cell_matrix_b_") and h.endswith("_0") for h in hi3), hi3


def test_mul_dimension_check_visualises_counts_correctly():
    """The dimension-check phrase claims 'columns of A equals rows of B'.
    The highlighted geometry must visualise those counts: a ROW of A
    has cols_a cells (matching 'columns of A'); a COLUMN of B has
    rows_b cells (matching 'rows of B').  Regression for 2026-06-07
    field report: an earlier version highlighted a COLUMN of A + ROW
    of B, so the visible counts were the inverse of what the narration
    said (rows_a vs cols_a, cols_b vs rows_b).
    """
    a = [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]]   # 3x4: 3 rows, 4 cols
    b = [[1, 2, 3, 4, 5],
         [6, 7, 8, 9, 10],
         [11, 12, 13, 14, 15],
         [16, 17, 18, 19, 20]]                          # 4x5: 4 rows, 5 cols
    _, narr = matrix_multiplication(a, b)
    dim_check = [p for p in narr if "dimension requirement" in p["speak"]]
    assert dim_check, [p["speak"] for p in narr]
    hi = dim_check[0]["highlight"]
    # A-side highlight: cols_a = 4 cells, all sharing one row index.
    a_cells = [h for h in hi if h.startswith("cell_matrix_a_")]
    assert len(a_cells) == 4, (a_cells, "should be cols_a=4 cells (= length of a row of A)")
    a_row_indices = {h.split("_")[3] for h in a_cells}
    a_col_indices = {h.split("_")[4] for h in a_cells}
    assert len(a_row_indices) == 1, ("expected a single row of A", a_row_indices)
    assert a_col_indices == {"0", "1", "2", "3"}, (
        "expected all column indices in the highlighted row", a_col_indices,
    )
    # B-side highlight: rows_b = 4 cells, all sharing one column index.
    b_cells = [h for h in hi if h.startswith("cell_matrix_b_")]
    assert len(b_cells) == 4, (b_cells, "should be rows_b=4 cells (= length of a column of B)")
    b_row_indices = {h.split("_")[3] for h in b_cells}
    b_col_indices = {h.split("_")[4] for h in b_cells}
    assert len(b_col_indices) == 1, ("expected a single column of B", b_col_indices)
    assert b_row_indices == {"0", "1", "2", "3"}, (
        "expected all row indices in the highlighted column", b_row_indices,
    )


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
