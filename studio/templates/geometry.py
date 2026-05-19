"""Deterministic geometry + elementary-figure templates.

Like ``matrix.py``: each function returns ``(svg, narration_script)``.
The LLM picks the template and the numbers; every coordinate is
computed here, so the figure is correct by construction — no LLM
pixel-placement, no vision-audit retries.

``pythagoras``  — right triangle with the a²/b²/c² squares correctly
                  attached to each side (replaces the LLM-SVG path
                  that kept emitting detached, mislabelled squares).
``number_line`` — a single honest figure for elementary arithmetic,
                  so "explain addition" gets a number line instead of
                  a contrived box-and-arrow flowchart.
"""
from __future__ import annotations

import math
from typing import List, Tuple


def _num(v: float) -> str:
    """Compact number: int → '9', float → '4.24'."""
    if isinstance(v, int) or (isinstance(v, float) and float(v).is_integer()):
        return str(int(v))
    return f"{v:.2f}".rstrip("0").rstrip(".")


# --------------------------------------------------------------------
# Pythagorean theorem
# --------------------------------------------------------------------

def pythagoras(
    a: float | int = 3,
    b: float | int = 4,
    *,
    canvas_w: int | None = None,
    canvas_h: int | None = None,
) -> Tuple[str, List[dict]]:
    """Right triangle with a square built outward on each side.

    Legs ``a`` (horizontal) and ``b`` (vertical) meet at the right
    angle; the hypotenuse ``c = √(a²+b²)`` carries the third square.
    The classic visual proof: area(c²) = area(a²) + area(b²).
    """
    a = float(a)
    b = float(b)
    if a <= 0 or b <= 0:
        raise ValueError("triangle legs must be positive")
    c = math.hypot(a, b)
    a2, b2, c2 = a * a, b * b, c * c

    s = 44.0       # px per math unit
    M = 104.0      # margin around the drawing
    TITLE = 64.0   # band at top for the title

    xmin, xmax = -b, a + b
    ymin, ymax = -a, a + b
    W = (xmax - xmin) * s + 2 * M
    # y of the lowest drawn unit, then the formula caption below it,
    # then a generous bottom margin: the headless rasteriser clips
    # the last ~9% of a viewBox, so the caption must sit well clear
    # of the bottom edge.
    draw_bottom = TITLE + M + (ymax - ymin) * s
    formula_y = draw_bottom + 58.0
    H = formula_y + 120.0

    def X(mx: float) -> float:
        return M + (mx - xmin) * s

    def Y(my: float) -> float:
        return TITLE + M + (ymax - my) * s

    def poly_pts(pts: List[Tuple[float, float]]) -> str:
        return " ".join(f"{X(px):.1f},{Y(py):.1f}" for px, py in pts)

    # Vertices: C is the right angle, B on the +x leg, A on the +y leg.
    tri = [(0.0, 0.0), (a, 0.0), (0.0, b)]
    sq_a = [(0.0, 0.0), (a, 0.0), (a, -a), (0.0, -a)]          # on leg a
    sq_b = [(0.0, 0.0), (0.0, b), (-b, b), (-b, 0.0)]          # on leg b
    sq_c = [(a, 0.0), (0.0, b), (b, a + b), (a + b, a)]        # on hyp c

    parts: List[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {W:.0f} {H:.0f}">'
    )
    parts.append(
        f'<text id="title" x="{W / 2:.0f}" y="44" font-size="30" '
        f'text-anchor="middle" font-family="serif" fill="#111">'
        f'The Pythagorean Theorem</text>'
    )

    # Squares first, triangle on top so its edges stay crisp.
    parts.append(
        f'<polygon id="square_b" points="{poly_pts(sq_b)}" '
        f'fill="#f4cccc" stroke="#cc4125" stroke-width="2.5"/>'
    )
    parts.append(
        f'<polygon id="square_a" points="{poly_pts(sq_a)}" '
        f'fill="#d9ead3" stroke="#6aa84f" stroke-width="2.5"/>'
    )
    parts.append(
        f'<polygon id="square_c" points="{poly_pts(sq_c)}" '
        f'fill="#cfe2f3" stroke="#3d6fb4" stroke-width="2.5"/>'
    )
    parts.append(
        f'<polygon id="triangle" points="{poly_pts(tri)}" '
        f'fill="#fff2cc" stroke="#333" stroke-width="3"/>'
    )

    # Right-angle marker in the interior corner at C.
    d = 15.0
    cx, cy = X(0.0), Y(0.0)
    parts.append(
        f'<polyline id="right_angle" points="'
        f'{cx:.1f},{cy - d:.1f} {cx + d:.1f},{cy - d:.1f} '
        f'{cx + d:.1f},{cy:.1f}" fill="none" '
        f'stroke="#333" stroke-width="2"/>'
    )

    # Side labels, nudged into the triangle interior.
    parts.append(
        f'<text id="label_a" x="{X(a / 2):.1f}" y="{Y(0.0) - 16:.1f}" '
        f'font-size="22" text-anchor="middle" font-family="serif" '
        f'fill="#111" font-style="italic">a</text>'
    )
    parts.append(
        f'<text id="label_b" x="{X(0.0) + 16:.1f}" y="{Y(b / 2) + 6:.1f}" '
        f'font-size="22" text-anchor="middle" font-family="serif" '
        f'fill="#111" font-style="italic">b</text>'
    )
    # c label: hypotenuse midpoint nudged toward C.
    mx, my = X(a / 2), Y(b / 2)
    vx, vy = cx - mx, cy - my
    vlen = math.hypot(vx, vy) or 1.0
    lcx, lcy = mx + vx / vlen * 22, my + vy / vlen * 22
    parts.append(
        f'<text id="label_c" x="{lcx:.1f}" y="{lcy:.1f}" '
        f'font-size="22" text-anchor="middle" font-family="serif" '
        f'fill="#111" font-style="italic">c</text>'
    )

    # Area labels centred in each square.
    def area_label(gid: str, cxm: float, cym: float,
                   main: str, sub: str) -> str:
        tx, ty = X(cxm), Y(cym)
        return (
            f'<g id="{gid}">'
            f'<text x="{tx:.1f}" y="{ty - 4:.1f}" font-size="26" '
            f'text-anchor="middle" font-family="serif" fill="#111">'
            f'{main}</text>'
            f'<text x="{tx:.1f}" y="{ty + 22:.1f}" font-size="16" '
            f'text-anchor="middle" font-family="serif" fill="#444">'
            f'{sub}</text></g>'
        )

    parts.append(area_label("area_a", a / 2, -a / 2, "a²",
                            f"= {_num(a2)}"))
    parts.append(area_label("area_b", -b / 2, b / 2, "b²",
                            f"= {_num(b2)}"))
    parts.append(area_label("area_c", (a + b) / 2, (a + b) / 2, "c²",
                            f"= {_num(c2)}"))

    # Formula caption.
    parts.append(
        f'<text id="formula" x="{W / 2:.0f}" y="{formula_y:.0f}" '
        f'font-size="26" text-anchor="middle" font-family="serif" '
        f'fill="#111">a² + b² = c²'
        f'    ({_num(a2)} + {_num(b2)} = {_num(c2)})</text>'
    )
    parts.append("</svg>")
    svg = "".join(parts)

    narration: List[dict] = [
        {"speak": ("The Pythagorean theorem relates the three sides of "
                   "a right triangle. We will see it as a statement "
                   "about areas."),
         "highlight": ["title"]},
        {"speak": (f"Here is a right triangle. The right angle sits in "
                   f"this corner, with legs a and b, and the hypotenuse "
                   f"c opposite the right angle."),
         "highlight": ["triangle", "right_angle"]},
        {"speak": (f"Build a square on leg a. Since a is {_num(a)}, its "
                   f"area is a squared, which is {_num(a2)}."),
         "highlight": ["square_a", "area_a"]},
        {"speak": (f"Build a square on leg b. Since b is {_num(b)}, its "
                   f"area is b squared, which is {_num(b2)}."),
         "highlight": ["square_b", "area_b"]},
        {"speak": (f"Now the square on the hypotenuse c. Its area is c "
                   f"squared, which is {_num(c2)}."),
         "highlight": ["square_c", "area_c"]},
        {"speak": (f"The theorem says the big square equals the other "
                   f"two combined: a squared plus b squared equals c "
                   f"squared. Here, {_num(a2)} plus {_num(b2)} equals "
                   f"{_num(c2)}."),
         "highlight": ["formula"]},
    ]
    return svg, narration


# --------------------------------------------------------------------
# Number line (elementary arithmetic — a minimal, honest figure)
# --------------------------------------------------------------------

def _nice_step(rng: float) -> int:
    """An integer tick step that yields roughly 8-16 ticks."""
    raw = max(rng / 12.0, 1e-9)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return max(1, int(round(m * mag)))
    return max(1, int(round(10 * mag)))


def number_line(
    a: float | int = 23,
    b: float | int = 15,
    *,
    operation: str = "+",
    canvas_w: int | None = None,
    canvas_h: int | None = None,
) -> Tuple[str, List[dict]]:
    """One honest figure for elementary addition / subtraction.

    Draws a number line, marks the start ``a``, an arc hop of size
    ``b``, and the result. This is what a simple arithmetic prompt
    should get instead of a box-and-arrow flowchart.
    """
    a = float(a)
    b = float(b)
    sub = operation in ("-", "minus", "subtract", "subtraction")
    op = "-" if sub else "+"
    result = a - b if sub else a + b

    left = min(a, result)
    right = max(a, result)
    span = max(right - left, 1.0)
    pad = max(1.0, round(span * 0.4))
    lo = math.floor(left - pad)
    hi = math.ceil(right + pad)
    rng = hi - lo

    W, H = 920.0, 340.0
    ax0, ax1, axy = 90.0, 830.0, 214.0

    def X(v: float) -> float:
        return ax0 + (v - lo) / rng * (ax1 - ax0)

    parts: List[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {W:.0f} {H:.0f}">'
    )
    parts.append(
        '<defs><marker id="nl_arrow" viewBox="0 0 10 10" refX="8" '
        'refY="5" markerWidth="7" markerHeight="7" orient="auto">'
        '<path d="M0,0 L10,5 L0,10 z" fill="#3d6fb4"/></marker></defs>'
    )
    title = "Subtraction on the Number Line" if sub \
        else "Addition on the Number Line"
    parts.append(
        f'<text id="title" x="{W / 2:.0f}" y="46" font-size="28" '
        f'text-anchor="middle" font-family="serif" fill="#111">'
        f'{title}</text>'
    )

    # Axis with end arrows.
    parts.append(
        f'<line id="axis" x1="{ax0 - 18:.0f}" y1="{axy:.0f}" '
        f'x2="{ax1 + 18:.0f}" y2="{axy:.0f}" stroke="#333" '
        f'stroke-width="2.5" marker-start="url(#nl_arrow)" '
        f'marker-end="url(#nl_arrow)"/>'
    )

    # Ticks.
    step = _nice_step(rng)
    first = lo + ((-lo) % step) if lo % step else lo
    tick_parts: List[str] = []
    v = first
    while v <= hi + 1e-6:
        tx = X(v)
        tick_parts.append(
            f'<line x1="{tx:.1f}" y1="{axy - 7:.0f}" x2="{tx:.1f}" '
            f'y2="{axy + 7:.0f}" stroke="#333" stroke-width="1.5"/>'
        )
        tick_parts.append(
            f'<text x="{tx:.1f}" y="{axy + 28:.0f}" font-size="15" '
            f'text-anchor="middle" font-family="sans-serif" '
            f'fill="#444">{_num(v)}</text>'
        )
        v += step
    parts.append(f'<g id="ticks">{"".join(tick_parts)}</g>')

    # Hop arc from a to result.
    xa, xr = X(a), X(result)
    apex_y = axy - 96.0
    parts.append(
        f'<path id="hop" d="M {xa:.1f} {axy - 9:.1f} '
        f'Q {(xa + xr) / 2:.1f} {apex_y:.1f} {xr:.1f} {axy - 9:.1f}" '
        f'fill="none" stroke="#3d6fb4" stroke-width="3" '
        f'marker-end="url(#nl_arrow)"/>'
    )
    parts.append(
        f'<text id="hop_label" x="{(xa + xr) / 2:.1f}" '
        f'y="{apex_y + 4:.1f}" font-size="22" text-anchor="middle" '
        f'font-family="serif" fill="#3d6fb4">'
        f'{op}{_num(b)}</text>'
    )

    # Start + result markers.
    parts.append(
        f'<circle id="start_dot" cx="{xa:.1f}" cy="{axy:.0f}" r="6" '
        f'fill="#6aa84f"/>'
    )
    parts.append(
        f'<text x="{xa:.1f}" y="{axy + 50:.0f}" font-size="16" '
        f'text-anchor="middle" font-family="sans-serif" '
        f'fill="#6aa84f">start: {_num(a)}</text>'
    )
    parts.append(
        f'<circle id="result_dot" cx="{xr:.1f}" cy="{axy:.0f}" r="6" '
        f'fill="#cc4125"/>'
    )
    parts.append(
        f'<text x="{xr:.1f}" y="{axy + 50:.0f}" font-size="16" '
        f'text-anchor="middle" font-family="sans-serif" '
        f'fill="#cc4125">result: {_num(result)}</text>'
    )

    parts.append(
        f'<text id="caption" x="{W / 2:.0f}" y="{H - 26:.0f}" '
        f'font-size="26" text-anchor="middle" font-family="serif" '
        f'fill="#111">{_num(a)} {op} {_num(b)} = {_num(result)}</text>'
    )
    parts.append("</svg>")
    svg = "".join(parts)

    verb = "subtract" if sub else "add"
    direction = "left" if sub else "right"
    narration: List[dict] = [
        {"speak": (f"Let's {verb} using a number line — the simplest "
                   f"honest picture of what {verb}ing means."),
         "highlight": ["title"]},
        {"speak": (f"Each evenly spaced tick is one whole number. We "
                   f"start at {_num(a)}."),
         "highlight": ["ticks", "start_dot"]},
        {"speak": (f"To {verb} {_num(b)}, we hop {_num(b)} units to the "
                   f"{direction}."),
         "highlight": ["hop", "hop_label"]},
        {"speak": (f"We land on {_num(result)}. So {_num(a)} {op} "
                   f"{_num(b)} equals {_num(result)}."),
         "highlight": ["result_dot", "caption"]},
    ]
    return svg, narration
