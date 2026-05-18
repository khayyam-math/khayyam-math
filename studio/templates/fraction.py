"""Deterministic fraction template — exact part-of-a-whole figures.

Drawing ``p/q of a whole`` by free-hand SVG (the LLM path) is
unreliable: pie sectors and bar divisions come out with the wrong
counts and wrong angles --- e.g. a ``2/3`` pie rendered as a half, a
``3/5`` pie cut into six sectors. This template computes every
division and every arithmetic result EXACTLY in Python (``q`` equal
parts, ``p`` shaded; arithmetic via :class:`fractions.Fraction`), so
the geometry and the mathematics cannot be wrong.

Public API:
  fraction(parts, model="bar", title="")          representation
  fraction_operation(left, right, op, title="")    exact arithmetic
"""
from __future__ import annotations

import math
from fractions import Fraction
from typing import List, Tuple


def _esc(s: object) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


_SHADE = ("#3d6fb4", "#cc4125", "#6aa84f", "#e69138", "#8e7cc3")
_OP = {"+": "+", "-": "−", "−": "−", "*": "×",
       "x": "×", "×": "×", "/": "÷",
       "÷": "÷", ":": "÷"}


def _one(x: object) -> Tuple[int, int]:
    if isinstance(x, dict):
        return int(x["numerator"]), int(x["denominator"])
    if isinstance(x, (list, tuple)) and len(x) >= 2:
        return int(x[0]), int(x[1])
    raise ValueError("expected a [numerator, denominator] fraction")


def _norm_parts(parts: object) -> List[Tuple[int, int, str]]:
    out: List[Tuple[int, int, str]] = []
    for p in (parts or []):  # type: ignore[union-attr]
        try:
            if isinstance(p, dict):
                n, d = int(p.get("numerator")), int(p.get("denominator"))
                lab = str(p.get("label") or "").strip()
            elif isinstance(p, (list, tuple)) and len(p) >= 2:
                n, d, lab = int(p[0]), int(p[1]), ""
            else:
                continue
        except (TypeError, ValueError):
            continue
        if d < 1 or d > 24 or n < 0 or n > 8 * d:
            continue
        out.append((n, d, lab or f"{n}/{d}"))
    return out


def _render_bars(P: List[Tuple[int, int, str]], title: str,
                 caption: str = "") -> Tuple[str, List[dict]]:
    """Each fraction as a bar split into d equal cells, n shaded.
    Bars share one fixed cell width, so a comparison is honest. An
    improper fraction (n > d) extends past the whole, with the
    whole-unit boundary marked."""
    whole_w = 620.0      # ONE whole — the same width on every bar, so
    bar_h = 84.0         # comparisons and sums are visually honest
    gap = 52.0
    lab_w = 150.0
    m = 40.0
    top = 74.0 if title else 40.0
    cap_h = 60.0 if caption else 0.0
    # an improper fraction (n > d) extends past the whole
    max_ratio = max(max(n, d) / d for n, d, _ in P)
    bar_full = max_ratio * whole_w
    W = m + lab_w + bar_full + m + 96
    H = top + len(P) * (bar_h + gap) + 44 + cap_h
    out: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" '
        f'height="{H:.0f}">',
        f'<rect width="{W:.0f}" height="{H:.0f}" fill="white"/>',
    ]
    if title:
        out.append(
            f'<text id="title" x="{W/2:.0f}" y="44" font-size="25" '
            f'text-anchor="middle" font-family="serif" '
            f'font-weight="bold" fill="#111">{_esc(title)}</text>')
    narration: List[dict] = [{
        "speak": ("Each fraction is shown as a bar split into equal "
                  "parts."),
        "highlight": ["title"] if title else []}]
    y = top
    x0 = m + lab_w
    for i, (n, d, lab) in enumerate(P):
        shade = _SHADE[i % len(_SHADE)]
        cells = max(n, d)
        cw = whole_w / d
        out.append(
            f'<text id="frac_{i}" x="{m + lab_w - 24:.1f}" '
            f'y="{y + bar_h/2 + 10:.1f}" font-size="32" '
            f'text-anchor="end" font-family="serif" '
            f'font-weight="bold" fill="#111">{_esc(lab)}</text>')
        for c in range(cells):
            fill = shade if c < n else "#ffffff"
            out.append(
                f'<rect x="{x0 + c*cw:.2f}" y="{y:.1f}" '
                f'width="{cw:.2f}" height="{bar_h}" fill="{fill}" '
                f'stroke="#222" stroke-width="1.6"/>')
        # whole-unit boundary markers for an improper fraction
        for k in range(d, cells, d):
            bx = x0 + k * cw
            out.append(
                f'<line x1="{bx:.1f}" y1="{y-6:.1f}" x2="{bx:.1f}" '
                f'y2="{y+bar_h+6:.1f}" stroke="#111" stroke-width="4"/>')
        out.append(
            f'<text x="{x0 + cells*cw + 16:.1f}" '
            f'y="{y + bar_h/2 + 6:.1f}" font-size="16" '
            f'font-family="serif" fill="#555">{n} of {d}</text>')
        narration.append({
            "speak": (f"The fraction {lab.replace('/', ' over ')}: the "
                      f"whole is {d} equal parts, and {n} "
                      f"{'is' if n == 1 else 'are'} shaded."),
            "highlight": [f"frac_{i}"]})
        y += bar_h + gap
    if caption:
        out.append(
            f'<text x="{W/2:.0f}" y="{y + 14:.1f}" font-size="26" '
            f'text-anchor="middle" font-family="serif" '
            f'font-weight="bold" fill="#1a3a5c">{_esc(caption)}</text>')
    out.append("</svg>")
    return "".join(out), narration


def _sector(cx: float, cy: float, r: float, a0: float, a1: float) -> str:
    def pt(a: float) -> Tuple[float, float]:
        rad = math.radians(a - 90.0)
        return cx + r * math.cos(rad), cy + r * math.sin(rad)
    x0, y0 = pt(a0)
    x1, y1 = pt(a1)
    large = 1 if (a1 - a0) > 180.0 else 0
    return (f'M {cx:.2f},{cy:.2f} L {x0:.2f},{y0:.2f} '
            f'A {r:.2f},{r:.2f} 0 {large} 1 {x1:.2f},{y1:.2f} Z')


def _render_pies(P: List[Tuple[int, int, str]], title: str
                 ) -> Tuple[str, List[dict]]:
    R = 132.0
    gap = 64.0
    m = 46.0
    top = 78.0 if title else 40.0
    W = 2 * m + len(P) * (2 * R) + (len(P) - 1) * gap
    H = top + 2 * R + 104
    cy = top + R
    out: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" '
        f'height="{H:.0f}">',
        f'<rect width="{W:.0f}" height="{H:.0f}" fill="white"/>',
    ]
    if title:
        out.append(
            f'<text id="title" x="{W/2:.0f}" y="46" font-size="25" '
            f'text-anchor="middle" font-family="serif" '
            f'font-weight="bold" fill="#111">{_esc(title)}</text>')
    narration: List[dict] = [{
        "speak": "Each circle is one whole, split into equal slices.",
        "highlight": ["title"] if title else []}]
    for i, (n, d, lab) in enumerate(P):
        shade = _SHADE[i % len(_SHADE)]
        cx = m + R + i * (2 * R + gap)
        for s in range(d):
            fill = shade if s < n else "#ffffff"
            gid = f' id="pie_{i}"' if s == 0 else ""
            out.append(
                f'<path{gid} d="'
                f'{_sector(cx, cy, R, s*360.0/d, (s+1)*360.0/d)}" '
                f'fill="{fill}" stroke="#222" stroke-width="1.8"/>')
        out.append(
            f'<text x="{cx:.1f}" y="{cy + R + 40:.1f}" font-size="26" '
            f'text-anchor="middle" font-family="serif" '
            f'font-weight="bold" fill="#111">{_esc(lab)}</text>')
        narration.append({
            "speak": (f"The fraction {lab.replace('/', ' over ')}: "
                      f"{d} equal slices, {n} shaded."),
            "highlight": [f"pie_{i}"]})
    out.append("</svg>")
    return "".join(out), narration


def _render_number_line(n: int, d: int, title: str
                        ) -> Tuple[str, List[dict]]:
    whole = max(1, -(-n // d))           # ceil(n/d)
    m = 70.0
    top = 80.0 if title else 50.0
    span = 1000.0
    unit = span / whole
    y = top + 70.0
    W = span + 2 * m
    H = y + 130.0
    out: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" '
        f'height="{H:.0f}">',
        f'<rect width="{W:.0f}" height="{H:.0f}" fill="white"/>',
    ]
    if title:
        out.append(
            f'<text id="title" x="{W/2:.0f}" y="46" font-size="25" '
            f'text-anchor="middle" font-family="serif" '
            f'font-weight="bold" fill="#111">{_esc(title)}</text>')
    out.append(
        f'<line x1="{m:.1f}" y1="{y:.1f}" x2="{m+span:.1f}" '
        f'y2="{y:.1f}" stroke="#222" stroke-width="3"/>')
    for k in range(whole * d + 1):
        x = m + k * (unit / d)
        major = (k % d == 0)
        th = 20.0 if major else 11.0
        out.append(
            f'<line x1="{x:.1f}" y1="{y-th:.1f}" x2="{x:.1f}" '
            f'y2="{y+th:.1f}" stroke="#222" '
            f'stroke-width="{3 if major else 1.5}"/>')
        if major:
            out.append(
                f'<text x="{x:.1f}" y="{y+46:.1f}" font-size="20" '
                f'text-anchor="middle" font-family="serif" '
                f'fill="#111">{k // d}</text>')
    fx = m + n * (unit / d)
    out.append(
        f'<circle id="mark" cx="{fx:.1f}" cy="{y:.1f}" r="11" '
        f'fill="#cc4125" stroke="#7a2010" stroke-width="2"/>')
    out.append(
        f'<text x="{fx:.1f}" y="{y-32:.1f}" font-size="26" '
        f'text-anchor="middle" font-family="serif" font-weight="bold" '
        f'fill="#cc4125">{n}/{d}</text>')
    out.append("</svg>")
    narration = [
        {"speak": (f"This number line runs from 0 to {whole}, with "
                   f"each unit split into {d} equal steps."),
         "highlight": ["title"] if title else []},
        {"speak": (f"The fraction {n} over {d} is marked {n} steps "
                   f"along from zero."), "highlight": ["mark"]}]
    return "".join(out), narration


def fraction(parts: object, model: str = "bar",
             title: str = "") -> Tuple[str, List[dict]]:
    """Render one or more fractions as exact part-of-a-whole figures.

    model: "bar" (default), "pie", or "numberline".
    """
    P = _norm_parts(parts)
    if not P or len(P) > 5:
        raise ValueError("fraction needs 1 to 5 valid [num, den] parts")
    mode = str(model).lower()
    if mode in ("numberline", "number_line", "number-line"):
        return _render_number_line(P[0][0], P[0][1], title)
    if mode == "pie":
        return _render_pies(P, title)
    return _render_bars(P, title)


def fraction_operation(left: object, right: object, op: str,
                       title: str = "") -> Tuple[str, List[dict]]:
    """Add / subtract / multiply / divide two fractions, EXACTLY.

    The result is computed with :class:`fractions.Fraction`, then the
    two operands and the result are drawn as bars with the worked
    equation as a caption.  The arithmetic is correct by construction.
    """
    an, ad = _one(left)
    bn, bd = _one(right)
    if ad == 0 or bd == 0:
        raise ValueError("zero denominator")
    sym = _OP.get(str(op).strip())
    if sym is None:
        raise ValueError(f"fraction_operation: unknown op {op!r}")
    a, b = Fraction(an, ad), Fraction(bn, bd)
    if sym == "+":
        res = a + b
    elif sym == "−":
        res = a - b
    elif sym == "×":
        res = a * b
    else:
        if b == 0:
            raise ValueError("division by zero")
        res = a / b

    def lab(fr: Fraction) -> str:
        return (str(fr.numerator) if fr.denominator == 1
                else f"{fr.numerator}/{fr.denominator}")

    parts = [(an, ad, f"{an}/{ad}"), (bn, bd, f"{bn}/{bd}"),
             (res.numerator, res.denominator, lab(res))]
    caption = (f"{an}/{ad}   {sym}   {bn}/{bd}   =   {lab(res)}")
    return _render_bars(parts, title or "Fraction Arithmetic",
                        caption=caption)
