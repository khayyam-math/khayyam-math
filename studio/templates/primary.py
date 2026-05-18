"""Primary-school arithmetic templates.

The math-coverage sweep scored primary-school figures lowest (6.1/10):
a place-value chart and a multiplication "dot array" are both simple,
exact figures that the LLM-SVG path kept getting wrong (miscounted
dots, missing digits).  These render them deterministically.

  place_value(number)            -> (svg, narration)
  multiplication_array(a, b)     -> (svg, narration)
"""
from __future__ import annotations

from typing import List, Tuple


def _esc(s: object) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


_PLACE_NAMES = [
    "Ones", "Tens", "Hundreds", "Thousands", "Ten Thousands",
    "Hundred Thousands", "Millions", "Ten Millions",
]
_PLACE_FILL = ["#e8f0fa", "#eaf5ea", "#fdf0e3", "#f3eafa", "#fdeaea",
               "#e6f6f6", "#f5f0e0"]


def place_value(number: object) -> Tuple[str, List[dict]]:
    """Render a place-value chart: one column per digit with its
    place name, the digit, and the value it contributes."""
    n = int(number)
    if not (0 <= n <= 99_999_999):
        raise ValueError("number out of range for place_value")
    digits = str(n)
    L = len(digits)
    cw, m = 150.0, 30.0
    hh, dh, vh = 44.0, 86.0, 48.0
    title_h = 56.0
    W = 2 * m + L * cw
    H = title_h + hh + dh + vh + 108.0
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" '
        f'height="{H:.0f}">',
        f'<rect width="{W:.0f}" height="{H:.0f}" fill="white"/>',
        f'<text id="title" x="{W / 2:.0f}" y="36" font-size="22" '
        f'text-anchor="middle" font-family="serif" font-weight="bold" '
        f'fill="#111">Place Value of {n}</text>',
    ]
    narration: List[dict] = [{
        "speak": f"Let's break the number {n} down by place value.",
        "highlight": ["title"]}]
    parts: List[str] = []
    y0 = title_h
    for i, d in enumerate(digits):
        place = L - 1 - i
        val = int(d) * (10 ** place)
        x = m + i * cw
        col = _PLACE_FILL[place % len(_PLACE_FILL)]
        out.append(
            f'<rect x="{x:.1f}" y="{y0:.1f}" width="{cw:.1f}" '
            f'height="{hh}" fill="{col}" stroke="#888" '
            f'stroke-width="1"/>')
        out.append(
            f'<text x="{x + cw / 2:.1f}" y="{y0 + 28:.1f}" '
            f'font-size="15" text-anchor="middle" font-family="serif" '
            f'font-weight="bold" fill="#1a3a5c">'
            f'{_PLACE_NAMES[place]}</text>')
        out.append(
            f'<rect id="digit_{i}" x="{x:.1f}" y="{y0 + hh:.1f}" '
            f'width="{cw:.1f}" height="{dh}" fill="white" '
            f'stroke="#888" stroke-width="1"/>')
        out.append(
            f'<text x="{x + cw / 2:.1f}" '
            f'y="{y0 + hh + dh / 2 + 20:.1f}" font-size="52" '
            f'text-anchor="middle" font-family="serif" '
            f'fill="#111">{d}</text>')
        out.append(
            f'<rect x="{x:.1f}" y="{y0 + hh + dh:.1f}" '
            f'width="{cw:.1f}" height="{vh}" fill="{col}" '
            f'stroke="#888" stroke-width="1"/>')
        out.append(
            f'<text x="{x + cw / 2:.1f}" '
            f'y="{y0 + hh + dh + 31:.1f}" font-size="18" '
            f'text-anchor="middle" font-family="serif" '
            f'fill="#333">{val}</text>')
        if val:
            parts.append(str(val))
        narration.append({
            "speak": (f"The digit {d} sits in the "
                      f"{_PLACE_NAMES[place].lower()} place, so it is "
                      f"worth {val}."),
            "highlight": [f"digit_{i}"]})
    expanded = " + ".join(parts) if parts else "0"
    cy = y0 + hh + dh + vh + 42
    out.append(
        f'<text x="{W / 2:.1f}" y="{cy:.1f}" font-size="20" '
        f'text-anchor="middle" font-family="serif" fill="#111">'
        f'{n} = {_esc(expanded)}</text>')
    narration.append({
        "speak": f"Adding those place values gives {n} back.",
        "highlight": []})
    out.append("</svg>")
    return "".join(out), narration


def multiplication_array(a: object, b: object) -> Tuple[str, List[dict]]:
    """Render a × b as a rectangular array of dots."""
    a, b = int(a), int(b)
    if not (1 <= a <= 12 and 1 <= b <= 12):
        raise ValueError("factors out of range for multiplication_array")
    sp, r = 40.0, 13.0
    m, leftpad = 34.0, 100.0
    title_h = 54.0
    grid_w, grid_h = b * sp, a * sp
    W = max(380.0, leftpad + grid_w + m)
    H = title_h + grid_h + 124.0
    gx = leftpad
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" '
        f'height="{H:.0f}">',
        f'<rect width="{W:.0f}" height="{H:.0f}" fill="white"/>',
        f'<text id="title" x="{W / 2:.0f}" y="36" font-size="22" '
        f'text-anchor="middle" font-family="serif" font-weight="bold" '
        f'fill="#111">{a} × {b} = {a * b}</text>',
    ]
    for rr in range(a):
        for cc in range(b):
            cx = gx + cc * sp + sp / 2
            cy = title_h + rr * sp + sp / 2
            out.append(
                f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" '
                f'fill="#3d6fb4" stroke="#1a3a5c" stroke-width="1"/>')
    out.append(
        f'<text id="rows" x="{gx - 16:.1f}" '
        f'y="{title_h + grid_h / 2 + 5:.1f}" font-size="16" '
        f'text-anchor="end" font-family="serif" fill="#333">'
        f'{a} rows</text>')
    out.append(
        f'<text id="cols" x="{gx + grid_w / 2:.1f}" '
        f'y="{title_h + grid_h + 30:.1f}" font-size="16" '
        f'text-anchor="middle" font-family="serif" fill="#333">'
        f'{b} in each row</text>')
    out.append(
        f'<text x="{W / 2:.1f}" y="{title_h + grid_h + 66:.1f}" '
        f'font-size="19" text-anchor="middle" font-family="serif" '
        f'fill="#111">{a} groups of {b} make {a * b}</text>')
    out.append("</svg>")
    narration = [
        {"speak": f"This array shows {a} times {b}.",
         "highlight": ["title"]},
        {"speak": f"There are {a} rows with {b} dots in each row.",
         "highlight": ["rows"]},
        {"speak": (f"Counting every dot gives {a} times {b} "
                   f"equals {a * b}."), "highlight": ["cols"]},
    ]
    return "".join(out), narration
