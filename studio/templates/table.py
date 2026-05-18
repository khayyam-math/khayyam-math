"""Deterministic table / grid template.

Renders any tabular figure — truth tables, Cayley tables, modular
arithmetic tables, Karnaugh maps — as a pixel-perfect grid.  The
router's classifier extracts the structured data (headers + rows);
this template owns every coordinate, so columns line up and cells
never overlap, which the LLM-SVG path fails at for dense grids.

Returns ``(svg, narration_script)`` like the other templates.
"""
from __future__ import annotations

from typing import List, Tuple


def _esc(s: object) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def data_table(
    headers: List[object],
    rows: List[List[object]],
    *,
    title: str = "",
    row_header: bool = False,
    canvas_w: int | None = None,
    canvas_h: int | None = None,
) -> Tuple[str, List[dict]]:
    """Render a table.

    ``headers``      top row, drawn bold on a shaded band.
    ``rows``         data rows (each a list of cells).
    ``row_header``   when True the first cell of every row is also
                     drawn bold/shaded — for Cayley and modular tables
                     whose first column lists the row labels.
    """
    headers = [str(h) for h in (headers or [])]
    rows = [[str(c) for c in r] for r in (rows or [])]
    if not headers or not rows:
        raise ValueError("table needs headers and at least one row")
    n_cols = len(headers)
    # Pad/trim every row to the header width.
    rows = [(r + [""] * n_cols)[:n_cols] for r in rows]

    pad = 12
    fs = 15
    row_h = 32
    # Per-column width from the widest cell in that column.
    col_w: List[float] = []
    for c in range(n_cols):
        longest = max([len(headers[c])]
                      + [len(r[c]) for r in rows])
        col_w.append(max(46.0, longest * fs * 0.62 + 2 * pad))
    table_w = sum(col_w)
    n_rows = len(rows)
    margin = 28.0
    title_band = 46.0 if title else 14.0
    W = table_w + 2 * margin
    H = title_band + (n_rows + 1) * row_h + 2 * margin

    x0 = margin
    y0 = title_band + margin

    def col_x(c: int) -> float:
        return x0 + sum(col_w[:c])

    parts: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" '
        f'height="{H:.0f}">'
    ]
    if title:
        parts.append(
            f'<text id="title" x="{W/2:.0f}" y="32" font-size="22" '
            f'text-anchor="middle" font-family="serif" fill="#111">'
            f'{_esc(title)}</text>')

    def cell(cx: float, cy: float, w: float, text: str, gid: str,
             *, header: bool) -> None:
        fill = "#e8eef7" if header else "white"
        parts.append(
            f'<rect id="{gid}" x="{cx:.1f}" y="{cy:.1f}" '
            f'width="{w:.1f}" height="{row_h}" fill="{fill}" '
            f'stroke="#444" stroke-width="1"/>')
        weight = ' font-weight="bold"' if header else ""
        parts.append(
            f'<text x="{cx + w/2:.1f}" y="{cy + row_h/2 + fs/3:.1f}" '
            f'font-size="{fs}" text-anchor="middle" '
            f'font-family="serif" fill="#111"{weight}>'
            f'{_esc(text)}</text>')

    # Header row.
    for c in range(n_cols):
        cell(col_x(c), y0, col_w[c], headers[c],
             f"col_header_{c}", header=True)
    # Data rows.
    for r in range(n_rows):
        cy = y0 + (r + 1) * row_h
        for c in range(n_cols):
            is_h = row_header and c == 0
            cell(col_x(c), cy, col_w[c], rows[r][c],
                 f"cell_{r}_{c}", header=is_h)
    parts.append("</svg>")
    svg = "".join(parts)

    narration: List[dict] = []
    if title:
        narration.append({"speak": f"This is {title}.",
                           "highlight": ["title"]})
    narration.append({
        "speak": ("The top row lists the columns: "
                  + ", ".join(headers[:6])
                  + ("." if len(headers) <= 6 else ", and more.")),
        "highlight": [f"col_header_{c}" for c in range(min(n_cols, 6))]})
    if n_rows:
        first = rows[0]
        narration.append({
            "speak": ("Read each row across. The first row is "
                      + ", ".join(first[:6]) + "."),
            "highlight": [f"cell_0_{c}" for c in range(min(n_cols, 6))]})
    if n_cols >= 2:
        narration.append({
            "speak": ("The final column gives the result for each "
                      "row — that is what the table is for."),
            "highlight": [f"cell_{r}_{n_cols-1}"
                          for r in range(min(n_rows, 8))]})
    narration.append({
        "speak": (f"In total the table has {n_rows} rows and "
                  f"{n_cols} columns."),
        "highlight": ["title"] if title else ["col_header_0"]})
    return svg, narration
