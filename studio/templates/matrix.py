"""Deterministic matrix-figure templates.

Each function returns ``(svg, narration_script)`` ready to feed
into the existing express pipeline.  Layout is computed from the
matrices' dimensions; the LLM never picks coordinates.

Design constraints (from the failure modes in production):
  * Every element has a stable ``id`` so narration ``highlight`` can
    point at it.
  * Outer rect of each matrix tightly bounds its cells (4-px margin).
  * Inter-matrix gaps are deterministic (60 px between matrices, 40
    px around operation symbols).
  * Total layout is centred horizontally in the canvas viewBox so a
    small matrix doesn't sit off to one side.
  * Cell size scales DOWN with matrix dimension so a 5×5 matrix fits
    in the same horizontal budget as a 2×2.

Mobile note: the canvas viewer (canvas.html) already scales the SVG
to ``min-width: 640px`` on phones with internal pan/scroll.  These
templates target a 900×650 viewBox; on a 375-px iPhone the SVG
renders at ~71% scale with margin scroll for anything that overflows.
"""
from __future__ import annotations

from typing import List, Optional, Tuple


def _fmt(v: float | int) -> str:
    """Render a matrix entry compactly: int → '3', float → '3.14'."""
    if isinstance(v, (int,)) or (isinstance(v, float) and v.is_integer()):
        return str(int(v))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _pick_cell(max_dim: int) -> int:
    """Cell size that keeps three same-dim matrices + ops in ~900-px budget."""
    if max_dim <= 2:
        return 56
    if max_dim <= 3:
        return 48
    if max_dim <= 4:
        return 40
    return 34


def _matrix_block(mid: str, m: List[List[float | int]],
                  x: float, y: float, rows: int, cols: int,
                  cell: int, pad: int = 4, font: int | None = None,
                  empty: bool = False) -> str:
    """Render one matrix as a <g> with stable ids on every cell.

    Shared by every matrix template: tight outer rect (4-px padding by
    default), faint cell grid lines, centred glyph text per cell.
    Ids: <mid>_outer for the rect, cell_<mid>_<i>_<j> for each entry.
    """
    if font is None:
        font = max(14, int(cell * 0.45))
    parts: List[str] = []
    parts.append(
        f'<rect id="{mid}_outer" x="{x:.0f}" y="{y:.0f}" '
        f'width="{cols * cell + 2 * pad}" '
        f'height="{rows * cell + 2 * pad}" '
        f'fill="white" stroke="#222" stroke-width="2"/>'
    )
    for i in range(1, rows):
        yy = y + pad + i * cell
        parts.append(
            f'<line x1="{x + pad:.0f}" y1="{yy:.0f}" '
            f'x2="{x + pad + cols * cell:.0f}" y2="{yy:.0f}" '
            f'stroke="#888" stroke-width="1"/>'
        )
    for j in range(1, cols):
        xx = x + pad + j * cell
        parts.append(
            f'<line x1="{xx:.0f}" y1="{y + pad:.0f}" '
            f'x2="{xx:.0f}" y2="{y + pad + rows * cell:.0f}" '
            f'stroke="#888" stroke-width="1"/>'
        )
    for i in range(rows):
        for j in range(cols):
            cx = x + pad + j * cell + cell // 2
            cy = y + pad + i * cell + cell // 2 + font // 3
            content = ("?" if empty else
                       (m[i][j] if isinstance(m[i][j], str) else _fmt(m[i][j])))
            parts.append(
                f'<text id="cell_{mid}_{i}_{j}" '
                f'x="{cx:.0f}" y="{cy:.0f}" '
                f'font-size="{font}" text-anchor="middle" '
                f'font-family="serif" fill="#111">{content}</text>'
            )
    return f'<g id="{mid}">' + "".join(parts) + "</g>"


def _op_text(oid: str, x: float, y: float, glyph: str, size: int = 32) -> str:
    return (
        f'<text id="{oid}" x="{x:.0f}" y="{y:.0f}" '
        f'font-size="{size}" text-anchor="middle" font-family="serif" '
        f'fill="#111">{glyph}</text>'
    )


def _svg_open(w: int, h: int, title: str | None = None) -> str:
    head = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}">'
    if title:
        head += (
            f'<text id="title" x="{w // 2}" y="50" '
            f'font-size="26" text-anchor="middle" font-family="serif" '
            f'fill="#111">{title}</text>'
        )
    return head


def matrix_multiplication(
    a: List[List[float | int]],
    b: List[List[float | int]],
    *,
    result: Optional[List[List[float | int]]] = None,
    canvas_w: int = 900,
    canvas_h: int | None = None,
) -> Tuple[str, List[dict]]:
    """Render ``A · B = C`` as a deterministic SVG with narration.

    Parameters
    ----------
    a, b
        Matrices as nested lists (row-major).  Must be conformable
        (``a.cols == b.rows``) — otherwise we still render but the
        product slot is left blank with a "?" placeholder.
    result
        Optional explicit C matrix; if omitted we compute it.
    canvas_w, canvas_h
        Target viewBox dimensions.  Defaults match the express path.

    Returns
    -------
    svg : str
        Self-contained SVG element with stable element ids
        (``matrix_a``, ``matrix_b``, ``matrix_c``, ``cell_<m>_<i>_<j>``,
        ``op_times``, ``op_equals``).
    narration : list[dict]
        Phrase-timed script with ``highlight`` ids that reference
        elements in the SVG above.  Drop-in replacement for what
        express_figure emits.
    """
    if not a or not a[0] or not b or not b[0]:
        raise ValueError("matrices must be non-empty 2-D lists")
    rows_a, cols_a = len(a), len(a[0])
    rows_b, cols_b = len(b), len(b[0])
    conformable = (cols_a == rows_b)
    if result is None and conformable:
        result = _multiply(a, b)
    rows_c = rows_a
    cols_c = cols_b if conformable else 0

    # Per-cell pixel size: shrink the larger the matrix, with a floor
    # at 32 px so single-digit text stays readable.  Total horizontal
    # budget for the three matrices + 2 op symbols + margins ≈ 820 px
    # in a 900-px viewBox.  Op symbols + margins eat ~200 px; the
    # three matrices share the remaining 620 px proportional to their
    # column counts.
    max_dim = max(rows_a, cols_a, rows_b, cols_b, rows_c, cols_c)
    cell = _pick_cell(max_dim)

    PAD = 4         # outer-rect padding around cells (matches express tightness)
    OP_GAP = 30     # px on each side of × or = symbol
    OP_W = 30       # width allocated to the operator glyph
    FONT = max(14, int(cell * 0.45))   # cell text size

    w_a = cols_a * cell + 2 * PAD
    w_b = cols_b * cell + 2 * PAD
    w_c = cols_c * cell + 2 * PAD if conformable else cell + 2 * PAD
    total_w = w_a + OP_GAP + OP_W + OP_GAP + w_b + OP_GAP + OP_W + OP_GAP + w_c
    h_a = rows_a * cell + 2 * PAD
    h_b = rows_b * cell + 2 * PAD
    h_c = rows_c * cell + 2 * PAD
    max_h = max(h_a, h_b, h_c)
    # Auto-tight canvas height so the figure isn't lost in a tall
    # empty stage rectangle (the "useless square" user complaint).
    # Conformable case needs two extra rows for the formula + worked
    # example shown below the matrices.
    extra_rows = 80 if (conformable and result is not None) else 30
    canvas_h = max(360, 130 + max_h + extra_rows + 30)

    # Horizontal centring in the canvas.
    x0 = (canvas_w - total_w) // 2
    y_top = 110
    y_a = y_top
    y_b = y_top + (max_h - h_b) // 2
    y_c = y_top + (max_h - h_c) // 2
    y_center = y_top + max_h // 2

    x_a = x0
    x_op1 = x_a + w_a + OP_GAP
    x_b = x_op1 + OP_W + OP_GAP
    x_op2 = x_b + w_b + OP_GAP
    x_c = x_op2 + OP_W + OP_GAP

    svg_parts: List[str] = []
    svg_parts.append(_svg_open(canvas_w, canvas_h,
                               "Matrix multiplication: A &#xb7; B = C"))
    svg_parts.append(_matrix_block("matrix_a", a, x_a, y_a,
                                   rows_a, cols_a, cell, PAD, FONT))
    op_y = y_center + FONT // 3
    svg_parts.append(_op_text("op_times",
                              x_op1 + OP_W // 2, op_y, "&#xb7;"))
    svg_parts.append(_matrix_block("matrix_b", b, x_b, y_b,
                                   rows_b, cols_b, cell, PAD, FONT))
    svg_parts.append(_op_text("op_equals",
                              x_op2 + OP_W // 2, op_y, "="))
    if conformable and result is not None:
        svg_parts.append(_matrix_block("matrix_c", result, x_c, y_c,
                                       rows_c, cols_c, cell, PAD, FONT))
        # SHOW the dot-product for the top-left entry of C.  Without
        # this the narration says "C[0][0] = sum a₀ₖ·bₖ₀" but the
        # learner sees nothing.  Each term gets its own text id so
        # the audio can walk them.
        # ASCII-only text — avoids cairosvg / Safari font-fallback
        # quirks with U+2211 (∑) and tspan baseline-shift on phones.
        terms_00 = " + ".join(
            f"{_fmt(a[0][k])}*{_fmt(b[k][0])}" for k in range(cols_a)
        )
        formula_y = y_a + max_h + 40
        svg_parts.append(
            f'<text id="step_formula" x="{canvas_w // 2}" y="{formula_y}" '
            f'font-size="19" text-anchor="middle" font-family="serif" '
            f'fill="#111">General rule:  c[i,j] = '
            f'sum over k of  a[i,k] * b[k,j]</text>'
        )
        svg_parts.append(
            f'<text id="step_example" x="{canvas_w // 2}" '
            f'y="{formula_y + 30}" font-size="19" text-anchor="middle" '
            f'font-family="serif" fill="#111">Worked example:  '
            f'c[1,1] = {terms_00} = {_fmt(result[0][0])}</text>'
        )
    else:
        empty_grid = [[0] * max(cols_b, 1) for _ in range(rows_a)]
        svg_parts.append(_matrix_block("matrix_c", empty_grid, x_c, y_c,
                                       rows_a, max(cols_b, 1),
                                       cell, PAD, FONT, empty=True))
        svg_parts.append(
            f'<text id="dim_error" x="{x_c + w_c // 2:.0f}" '
            f'y="{y_c + h_c + 30:.0f}" font-size="16" text-anchor="middle" '
            f'font-family="serif" fill="#a00">dimensions mismatch</text>'
        )
    svg_parts.append("</svg>")
    svg = "".join(svg_parts)

    # Narration — phrase-timed walkthrough with highlight ids pointing
    # at the matrix groups we just emitted.
    narration: List[dict] = [
        {"speak": (f"We want to multiply matrix A, which is {rows_a} by "
                   f"{cols_a}, with matrix B, which is {rows_b} by {cols_b}."),
         "highlight": ["title"]},
        {"speak": ("First, check the dimension requirement: matrix "
                   "multiplication is defined only when the number of "
                   "columns of A equals the number of rows of B."),
         "highlight": ["matrix_a", "matrix_b"]},
    ]
    if conformable and result is not None:
        narration.extend([
            {"speak": (f"Here A has {cols_a} columns and B has {rows_b} "
                       "rows, so they match.  The product C is a "
                       f"{rows_c} by {cols_c} matrix."),
             "highlight": ["matrix_c"]},
            {"speak": (f"To compute the entry at row i and column j of C, "
                       f"take the dot product of row i of A with column j "
                       f"of B — multiply matching entries and add."),
             "highlight": ["matrix_a", "matrix_b"]},
        ])
        # Walk the top-left entry.
        terms_00 = " plus ".join(
            f"{_fmt(a[0][k])} times {_fmt(b[k][0])}"
            for k in range(cols_a)
        )
        narration.append({
            "speak": ("The general rule for the (i, j) entry of C is the "
                      "dot product of row i of A with column j of B — "
                      "the formula shown below the matrices."),
            "highlight": ["step_formula"],
        })
        narration.append({
            "speak": (f"Worked example, C row 1 column 1: {terms_00}, "
                      f"which equals {_fmt(result[0][0])} — shown below the "
                      f"formula."),
            "highlight": ["step_example", "cell_matrix_c_0_0"],
        })
        # Walk a second entry if the matrix is large enough.
        if rows_c >= 2 and cols_c >= 2:
            terms_11 = " plus ".join(
                f"{_fmt(a[1][k])} times {_fmt(b[k][1])}"
                for k in range(cols_a)
            )
            narration.append({
                "speak": (f"Another example, C row 2 column 2: {terms_11}, "
                          f"which equals {_fmt(result[1][1])}."),
                "highlight": ["cell_matrix_c_1_1"],
            })
        narration.append({
            "speak": ("Repeat the same dot-product rule for every entry "
                      "to fill in the result matrix C on the right."),
            "highlight": ["matrix_c"],
        })
    else:
        narration.append({
            "speak": (f"Here A has {cols_a} columns but B has {rows_b} rows, "
                      "which is a mismatch — so the product A times B is "
                      "not defined."),
            "highlight": ["dim_error"],
        })
    return svg, narration


def _multiply(a: List[List[float | int]],
              b: List[List[float | int]]) -> List[List[float]]:
    """Plain triple-loop matmul; small matrices only."""
    rows, k, cols = len(a), len(a[0]), len(b[0])
    out = [[0.0] * cols for _ in range(rows)]
    for i in range(rows):
        for j in range(cols):
            s = 0.0
            for kk in range(k):
                s += a[i][kk] * b[kk][j]
            out[i][j] = s
    return out


def _det(m: List[List[float | int]]) -> float:
    """Determinant via expansion-along-first-row.  O(n!) but n ≤ ~6
    in practice so it's fine."""
    n = len(m)
    if n == 1:
        return float(m[0][0])
    if n == 2:
        return float(m[0][0] * m[1][1] - m[0][1] * m[1][0])
    total = 0.0
    for j in range(n):
        minor = [[m[i][k] for k in range(n) if k != j] for i in range(1, n)]
        sign = 1.0 if j % 2 == 0 else -1.0
        total += sign * m[0][j] * _det(minor)
    return total


def _cofactor_matrix(m: List[List[float | int]]) -> List[List[float]]:
    """Cofactor matrix of square m: C_ij = (-1)^(i+j) · det(minor_ij)."""
    n = len(m)
    cof: List[List[float]] = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            minor = [[m[r][c] for c in range(n) if c != j]
                     for r in range(n) if r != i]
            sign = 1.0 if (i + j) % 2 == 0 else -1.0
            cof[i][j] = sign * (_det(minor) if minor else 1.0)
    return cof


def _adjugate(m: List[List[float | int]]) -> List[List[float]]:
    """Adjugate = transpose of the cofactor matrix."""
    cof = _cofactor_matrix(m)
    n = len(m)
    return [[cof[j][i] for j in range(n)] for i in range(n)]


def _inverse(m: List[List[float | int]]) -> List[List[float]] | None:
    """Cofactor-based inverse for square m; None if singular."""
    n = len(m)
    d = _det(m)
    if abs(d) < 1e-12:
        return None
    if n == 1:
        return [[1.0 / m[0][0]]]
    adj = _adjugate(m)
    return [[adj[i][j] / d for j in range(n)] for i in range(n)]


# ── matrix_transpose ──────────────────────────────────────────────


def matrix_transpose(
    a: List[List[float | int]],
    *,
    canvas_w: int = 900,
    canvas_h: int | None = None,
) -> Tuple[str, List[dict]]:
    """A and its transpose A^T side-by-side with an arrow."""
    if not a or not a[0]:
        raise ValueError("matrix must be non-empty")
    rows, cols = len(a), len(a[0])
    at = [[a[i][j] for i in range(rows)] for j in range(cols)]
    cell = _pick_cell(max(rows, cols))
    PAD = 4
    FONT = max(14, int(cell * 0.45))
    OP_GAP = 30
    OP_W = 50  # wider for the "T" superscript glyph

    w_a = cols * cell + 2 * PAD
    w_at = rows * cell + 2 * PAD
    total_w = w_a + OP_GAP + OP_W + OP_GAP + w_at
    h_a = rows * cell + 2 * PAD
    h_at = cols * cell + 2 * PAD
    max_h = max(h_a, h_at)
    if canvas_h is None:
        canvas_h = max(360, 130 + max_h + 60)
    x_a = (canvas_w - total_w) // 2
    x_op = x_a + w_a + OP_GAP
    x_at = x_op + OP_W + OP_GAP
    y_top = 110
    y_a = y_top + (max_h - h_a) // 2
    y_at = y_top + (max_h - h_at) // 2
    y_center = y_top + max_h // 2

    parts: List[str] = []
    parts.append(_svg_open(canvas_w, canvas_h, "Matrix transpose: A &#x2192; A&#x1d40;"))
    parts.append(_matrix_block("matrix_a", a, x_a, y_a, rows, cols,
                               cell, PAD, FONT))
    parts.append(_op_text("op_arrow",
                          x_op + OP_W // 2, y_center + FONT // 3,
                          "&#x2192;", size=32))
    parts.append(_matrix_block("matrix_at", at, x_at, y_at, cols, rows,
                               cell, PAD, FONT))
    parts.append("</svg>")
    svg = "".join(parts)
    narration = [
        {"speak": (f"We want to compute the transpose of matrix A, which "
                   f"has {rows} rows and {cols} columns."),
         "highlight": ["title"]},
        {"speak": "Here is matrix A.",
         "highlight": ["matrix_a"]},
        {"speak": ("The transpose, written A with a superscript T, is "
                   "formed by SWAPPING rows and columns: the i-th row of A "
                   "becomes the i-th column of A-transpose, and vice versa."),
         "highlight": ["matrix_a", "matrix_at"]},
        {"speak": (f"So A-transpose has {cols} rows and {rows} columns — "
                   "exactly the reverse of A's dimensions."),
         "highlight": ["matrix_at"]},
        {"speak": (f"As a check: the entry at row 1 column 1 of A — which "
                   f"is {_fmt(a[0][0])} — stays at row 1 column 1 of "
                   "A-transpose, because the main diagonal is fixed by "
                   "transposition."),
         "highlight": ["cell_matrix_at_0_0"]},
    ]
    if rows >= 2 and cols >= 2:
        narration.append({
            "speak": (f"And the off-diagonal entry at row 1 column 2 of A, "
                      f"which is {_fmt(a[0][1])}, moves to row 2 column 1 "
                      "of A-transpose — that's the row-column swap."),
            "highlight": ["cell_matrix_at_1_0"],
        })
    return svg, narration


# ── matrix_determinant ────────────────────────────────────────────


def matrix_determinant(
    a: List[List[float | int]],
    *,
    canvas_w: int = 900,
    canvas_h: int | None = None,
) -> Tuple[str, List[dict]]:
    """A with its determinant value (and 2x2 formula when applicable)."""
    n = len(a)
    if n == 0 or n != len(a[0]):
        raise ValueError("determinant requires a square matrix")
    d = _det(a)
    cell = _pick_cell(n)
    PAD = 4
    FONT = max(14, int(cell * 0.45))
    w_a = n * cell + 2 * PAD
    h_a = n * cell + 2 * PAD
    if canvas_h is None:
        canvas_h = max(360, 130 + h_a + 60)
    # Two-column layout: matrix on left, formula/value on right.
    x_a = (canvas_w // 2) - w_a - 30
    y_a = 110
    x_text = canvas_w // 2 + 30
    y_text = y_a + h_a // 2

    parts: List[str] = []
    parts.append(_svg_open(canvas_w, canvas_h, "Determinant of A"))
    parts.append(_matrix_block("matrix_a", a, x_a, y_a, n, n,
                               cell, PAD, FONT))
    # Formula text — 2×2 gets ad-bc; everything else just shows value.
    # Use a plain ASCII hyphen-minus instead of U+2212 — cairosvg's
    # default font doesn't always carry the typographic minus and
    # renders it as a placeholder box.
    if n == 2:
        formula = (f"det(A) = ({_fmt(a[0][0])})({_fmt(a[1][1])}) "
                   f"- ({_fmt(a[0][1])})({_fmt(a[1][0])}) = "
                   f"{_fmt(d)}")
    else:
        formula = f"det(A) = {_fmt(d)}"
    parts.append(
        f'<text id="det_formula" x="{x_text}" y="{y_text}" '
        f'font-size="22" font-family="serif" fill="#111">{formula}</text>'
    )
    parts.append("</svg>")
    svg = "".join(parts)
    narration = [
        {"speak": f"We want to compute the determinant of this {n} by {n} matrix A.",
         "highlight": ["title"]},
        {"speak": f"Here is matrix A.",
         "highlight": ["matrix_a"]},
    ]
    if n == 2:
        narration.extend([
            {"speak": ("For a two-by-two matrix, the determinant has a "
                       "simple closed form: it is ad minus bc, where a and "
                       "d are the diagonal entries and b and c are the "
                       "off-diagonal entries."),
             "highlight": ["matrix_a"]},
            {"speak": (f"Substituting: a equals {_fmt(a[0][0])}, "
                       f"b equals {_fmt(a[0][1])}, c equals {_fmt(a[1][0])}, "
                       f"d equals {_fmt(a[1][1])}."),
             "highlight": ["matrix_a"]},
            {"speak": (f"So the determinant equals {_fmt(a[0][0])} times "
                       f"{_fmt(a[1][1])} minus {_fmt(a[0][1])} times "
                       f"{_fmt(a[1][0])}, which simplifies to {_fmt(d)}."),
             "highlight": ["det_formula"]},
        ])
    else:
        narration.extend([
            {"speak": (f"For an {n} by {n} matrix we expand along the first "
                       "row.  Each entry is multiplied by the determinant of "
                       "the smaller submatrix you get by deleting that "
                       "entry's row and column, with alternating plus and "
                       "minus signs."),
             "highlight": ["matrix_a"]},
            {"speak": (f"Carrying out the full expansion, the determinant "
                       f"works out to {_fmt(d)}."),
             "highlight": ["det_formula"]},
        ])
    if abs(d) < 1e-9:
        narration.append({
            "speak": ("Since the determinant is zero, A is singular: its "
                      "columns are linearly dependent and A has no inverse."),
            "highlight": ["det_formula"],
        })
    else:
        narration.append({
            "speak": ("Since the determinant is non-zero, A is invertible "
                      "and the linear map it represents is one-to-one."),
            "highlight": ["det_formula"],
        })
    return svg, narration


# ── matrix_inverse ────────────────────────────────────────────────


def matrix_inverse(
    a: List[List[float | int]],
    *,
    canvas_w: int = 900,
    canvas_h: int | None = None,
) -> Tuple[str, List[dict]]:
    """Step-by-step inverse: shows A → adj(A) → A^(-1) as three real
    matrices on the canvas, plus step annotations the narration walks."""
    n = len(a)
    if n == 0 or n != len(a[0]):
        raise ValueError("inverse requires a square matrix")
    inv = _inverse(a)
    det_val = _det(a)
    # Smaller cell on this template — we render three matrices side-
    # by-side so the budget per matrix is tighter than the two-matrix
    # case.
    cell = _pick_cell(n + 1)
    PAD = 4
    FONT = max(13, int(cell * 0.45))
    OP_W = 70    # arrow / "=" label width between matrices
    OP_GAP = 20  # extra px on each side of the label

    if inv is None:
        # Singular: only show A + det(A)=0 message; no adjugate panel.
        w_a = n * cell + 2 * PAD
        h_a = n * cell + 2 * PAD
        STEP_DY = 32
        if canvas_h is None:
            canvas_h = max(360, 130 + h_a + 30 + 2 * STEP_DY + 40)
        x_a = (canvas_w - w_a) // 2
        y_a = 110
        y_steps_top = y_a + h_a + 30
        parts: List[str] = [
            _svg_open(canvas_w, canvas_h, "Matrix inverse: A and A&#x207b;&#xb9;"),
            _matrix_block("matrix_a", a, x_a, y_a, n, n, cell, PAD, FONT),
        ]
        def _step(sid: str, txt: str, row: int, color: str = "#a00") -> str:
            return (
                f'<text id="{sid}" x="{canvas_w // 2}" '
                f'y="{y_steps_top + row * STEP_DY}" font-size="20" '
                f'text-anchor="middle" font-family="serif" fill="{color}">'
                f'{txt}</text>'
            )
        parts.append(_step("step_det", "Step 1.   det(A) = 0", 0))
        parts.append(_step("singular_error",
                           "A is singular — no inverse exists", 1))
        parts.append("</svg>")
        svg = "".join(parts)
        narration = [
            {"speak": (f"We want to compute the inverse of this {n} by {n} "
                       f"matrix A."),
             "highlight": ["title"]},
            {"speak": "Here is matrix A.",
             "highlight": ["matrix_a"]},
            {"speak": ("Step one: compute the determinant of A.  In this "
                       "case the determinant is zero."),
             "highlight": ["step_det"]},
            {"speak": ("A zero determinant means A is singular — its "
                       "columns are linearly dependent."),
             "highlight": ["singular_error"]},
            {"speak": ("Because of that, A does not have a multiplicative "
                       "inverse.  No matrix B can satisfy A times B equals "
                       "the identity."),
             "highlight": ["singular_error"]},
        ]
        return svg, narration

    # Invertible case — three-matrix horizontal flow.
    adj = _adjugate(a)
    w_m = n * cell + 2 * PAD          # all three matrices share the same width
    h_m = n * cell + 2 * PAD
    total_w = w_m + OP_W + 2 * OP_GAP + w_m + OP_W + 2 * OP_GAP + w_m
    STEP_DY = 30
    n_steps = 4
    if canvas_h is None:
        canvas_h = max(440, 130 + h_m + 30 + n_steps * STEP_DY + 40)

    x_a = (canvas_w - total_w) // 2
    x_op1 = x_a + w_m + OP_GAP
    x_adj = x_op1 + OP_W + OP_GAP
    x_op2 = x_adj + w_m + OP_GAP
    x_inv = x_op2 + OP_W + OP_GAP
    y_a = 110
    y_center = y_a + h_m // 2
    y_steps_top = y_a + h_m + 30

    parts: List[str] = [
        _svg_open(canvas_w, canvas_h,
                  "Matrix inverse: A &#x2192; adj(A) &#x2192; A&#x207b;&#xb9;"),
        _matrix_block("matrix_a", a, x_a, y_a, n, n, cell, PAD, FONT),
        # Label between A and adj(A) — "cofactor + transpose" is the
        # operation that turns A into adj(A).  Avoid → arrow (cairosvg
        # font fallback issue) and stick to ASCII.
        _op_text("op_to_adj",
                 x_op1 + OP_W // 2, y_center + FONT // 3,
                 "cof", size=18),
        _matrix_block("matrix_adj", adj, x_adj, y_a, n, n, cell, PAD, FONT),
        # Between adj and A^-1: divide-by-det label.
        _op_text("op_divide",
                 x_op2 + OP_W // 2, y_center + FONT // 3,
                 f"/ {_fmt(det_val)}", size=22),
        _matrix_block("matrix_inv", inv, x_inv, y_a, n, n, cell, PAD, FONT),
    ]

    # Step annotations underneath.
    def _step(sid: str, txt: str, row: int) -> str:
        return (
            f'<text id="{sid}" x="{canvas_w // 2}" '
            f'y="{y_steps_top + row * STEP_DY}" font-size="19" '
            f'text-anchor="middle" font-family="serif" fill="#111">'
            f'{txt}</text>'
        )
    parts.append(_step("step_det",
                       f"Step 1.   det(A) = {_fmt(det_val)}   "
                       f"(non-zero, so A is invertible)", 0))
    parts.append(_step("step_adj",
                       "Step 2.   Compute the adjugate "
                       "(transpose of the cofactor matrix) — middle matrix", 1))
    parts.append(_step("step_formula",
                       f"Step 3.   A^(-1) = adj(A) / det(A) "
                       f"= adj(A) / {_fmt(det_val)}  — rightmost matrix", 2))
    parts.append(_step("step_verify",
                       f"Verify:   A * A^(-1) = I  (the "
                       f"{n}x{n} identity matrix)", 3))
    parts.append("</svg>")
    svg = "".join(parts)
    narration = [
        {"speak": (f"We want to compute the inverse of this {n} by {n} "
                   "matrix A."),
         "highlight": ["title"]},
        {"speak": "Here is matrix A on the left.",
         "highlight": ["matrix_a"]},
        {"speak": (f"Step one: compute the determinant of A — shown below.  "
                   f"It equals {_fmt(det_val)}, which is non-zero, so A is "
                   "invertible."),
         "highlight": ["step_det"]},
        {"speak": ("Step two: compute the adjugate of A — that's the middle "
                   "matrix shown here.  Each entry of the adjugate is plus "
                   "or minus the determinant of the submatrix you get by "
                   "deleting one row and column of A, and then the whole "
                   "thing is transposed."),
         "highlight": ["matrix_adj"]},
        {"speak": (f"Step three: divide every entry of the adjugate by the "
                   f"determinant {_fmt(det_val)}.  The result is A inverse, "
                   f"the rightmost matrix."),
         "highlight": ["matrix_inv"]},
        {"speak": (f"To verify, multiply A on the left by A inverse — the "
                   f"product equals the {n} by {n} identity matrix, "
                   "confirming the inverse is correct."),
         "highlight": ["step_verify"]},
    ]
    return svg, narration


# ── system_of_equations (Ax = b) ──────────────────────────────────


def system_of_equations(
    coeffs: List[List[float | int]],
    rhs: List[float | int],
    *,
    var_names: List[str] | None = None,
    show_solution: bool = True,
    canvas_w: int = 900,
    canvas_h: int | None = None,
) -> Tuple[str, List[dict]]:
    """Render Ax = b plus the solution x = A^(-1) b when applicable."""
    n = len(coeffs)
    if n == 0 or any(len(r) != n for r in coeffs):
        raise ValueError("coefficient matrix must be square and non-empty")
    if len(rhs) != n:
        raise ValueError("rhs length must equal matrix dimension")
    if var_names is None:
        var_names = ([f"x&#x208{i+1};" for i in range(n)]
                     if n <= 9 else [f"x{i+1}" for i in range(n)])
    cell = _pick_cell(n + 1)   # slightly smaller — two rows of matrices fit
    PAD = 4
    FONT = max(13, int(cell * 0.45))
    OP_GAP = 22
    OP_W = 30

    # Layout — TWO rows of matrices so the inverse and the matrix-
    # vector product are both VISIBLE, not just narrated:
    #
    #   Row 1:   [ A ]  [ x_col ]  =  [ b_col ]
    #
    #   Row 2:   x =  [ A^(-1) ]  ·  [ b_col_copy ]  =  [ solution_col ]
    #
    rhs_col = [[v] for v in rhs]
    x_col: List[List[str]] = [[var_names[i]] for i in range(n)]
    w_a = n * cell + 2 * PAD
    w_x = cell + 2 * PAD
    w_b = cell + 2 * PAD
    row1_w = w_a + OP_GAP + w_x + OP_GAP + OP_W + OP_GAP + w_b
    h_block = n * cell + 2 * PAD

    # Compute solution before laying out so we know if row 2 exists.
    solution = None
    inv = None
    if show_solution:
        inv = _inverse(coeffs)
        if inv is not None:
            solution = [sum(inv[i][k] * rhs[k] for k in range(n))
                        for i in range(n)]

    sol_col: List[List[float]] = [[v] for v in (solution or [0.0] * n)]

    # Row 2 layout:  "x =" label + A^-1 + dot + b_col + = + solution_col
    LABEL_W = 50      # space for "x ="
    DOT_W = 24        # for · between A^-1 and b
    row2_w = (LABEL_W + OP_GAP + w_a + OP_GAP + DOT_W + OP_GAP + w_b
              + OP_GAP + OP_W + OP_GAP + w_b)
    total_w = max(row1_w, row2_w)
    x0_row1 = (canvas_w - row1_w) // 2
    x0_row2 = (canvas_w - row2_w) // 2

    y_a = 110
    row_gap = 70          # extra space between rows
    y_b = y_a + h_block + row_gap + 30   # row 2 starts here (after step text)
    steps_y = y_a + h_block + 30         # step 1/2 caption row
    sol_text_y = y_b + h_block + 40      # final "Solution: x1=..." line

    if canvas_h is None:
        if solution is not None:
            canvas_h = max(440, sol_text_y + 40)
        else:
            canvas_h = max(360, y_a + h_block + 90)

    op_y_r1 = y_a + h_block // 2 + FONT // 3
    op_y_r2 = y_b + h_block // 2 + FONT // 3

    parts: List[str] = []
    parts.append(_svg_open(canvas_w, canvas_h, "System of equations: A x = b"))
    # ── Row 1: original equation ────────────────────────────────
    parts.append(_matrix_block("matrix_a", coeffs, x0_row1, y_a, n, n,
                               cell, PAD, FONT))
    parts.append(_matrix_block("vector_x", x_col,
                               x0_row1 + w_a + OP_GAP, y_a, n, 1,
                               cell, PAD, FONT))
    parts.append(_op_text(
        "op_equals",
        x0_row1 + w_a + OP_GAP + w_x + OP_GAP + OP_W // 2, op_y_r1,
        "=", size=28,
    ))
    parts.append(_matrix_block("vector_b", rhs_col,
                               x0_row1 + w_a + OP_GAP + w_x + OP_GAP
                               + OP_W + OP_GAP,
                               y_a, n, 1, cell, PAD, FONT))

    if solution is not None:
        # Step caption between rows.
        det_a = _det(coeffs)
        parts.append(
            f'<text id="step_det" x="{canvas_w // 2}" y="{steps_y}" '
            f'font-size="18" text-anchor="middle" font-family="serif" '
            f'fill="#111">Step 1. det(A) = {_fmt(det_a)} (non-zero) — '
            f'a unique solution exists, given by x = A^(-1) b</text>'
        )
        # ── Row 2: x = A^-1 · b = solution ──────────────────────
        x_pos = x0_row2
        # "x =" label
        parts.append(
            f'<text id="step_xeq_label" x="{x_pos + LABEL_W // 2}" '
            f'y="{op_y_r2}" font-size="22" text-anchor="middle" '
            f'font-family="serif" fill="#111">x =</text>'
        )
        x_pos += LABEL_W + OP_GAP
        parts.append(_matrix_block("matrix_a_inv", inv, x_pos, y_b, n, n,
                                   cell, PAD, FONT))
        x_pos += w_a + OP_GAP
        parts.append(_op_text(
            "op_dot",
            x_pos + DOT_W // 2, op_y_r2, "*", size=24,
        ))
        x_pos += DOT_W + OP_GAP
        parts.append(_matrix_block("vector_b_copy", rhs_col,
                                   x_pos, y_b, n, 1, cell, PAD, FONT))
        x_pos += w_b + OP_GAP
        parts.append(_op_text(
            "op_equals_r2",
            x_pos + OP_W // 2, op_y_r2, "=", size=24,
        ))
        x_pos += OP_W + OP_GAP
        parts.append(_matrix_block("vector_solution", sol_col, x_pos, y_b,
                                   n, 1, cell, PAD, FONT))
        # Solution line below row 2.
        plain = ",   ".join(f"x{i+1} = {_fmt(solution[i])}" for i in range(n))
        parts.append(
            f'<text id="solution" x="{canvas_w // 2}" y="{sol_text_y}" '
            f'font-size="20" text-anchor="middle" font-family="serif" '
            f'fill="#111">{plain}</text>'
        )
    parts.append("</svg>")
    svg = "".join(parts)
    narration = [
        {"speak": (f"We have a system of {n} linear equations in {n} unknowns, "
                   "written in matrix form as A times x equals b."),
         "highlight": ["title"]},
        {"speak": (f"A is the {n} by {n} coefficient matrix — each row holds "
                   "the coefficients of one equation."),
         "highlight": ["matrix_a"]},
        {"speak": (f"x is the column vector of the {n} unknowns we want "
                   "to find."),
         "highlight": ["vector_x"]},
        {"speak": "b is the column vector of the right-hand-side constants.",
         "highlight": ["vector_b"]},
    ]
    if solution is not None:
        narration.extend([
            {"speak": (f"Step one: check whether A is invertible — its "
                       f"determinant is non-zero, so a unique solution "
                       f"exists.  This is shown in the line below."),
             "highlight": ["step_det"]},
            {"speak": ("Step two: write the solution explicitly as x equals "
                       "A inverse times b.  Both factors are shown below — "
                       "A inverse on the left, b in the middle, and their "
                       "product on the right."),
             "highlight": ["matrix_a_inv", "vector_b_copy",
                           "vector_solution"]},
            {"speak": ("The leftmost matrix is A inverse, computed by the "
                       "cofactor formula: adjugate divided by determinant."),
             "highlight": ["matrix_a_inv"]},
            {"speak": ("Multiplying A inverse by b row-by-row gives the "
                       "rightmost column — that is the solution vector x."),
             "highlight": ["vector_solution"]},
            {"speak": ("The individual values for each unknown are summarised "
                       "below."),
             "highlight": ["solution"]},
        ])
    else:
        narration.append({
            "speak": ("The coefficient matrix A is singular, meaning its "
                      "determinant is zero.  Depending on whether b lies in "
                      "the column space of A, the system has either no "
                      "solutions or infinitely many — never a unique one."),
            "highlight": ["matrix_a"],
        })
    return svg, narration
