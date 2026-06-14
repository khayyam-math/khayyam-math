"""Deterministic renderer for the Taylor series of sin(x) about 0.

A user report on the LLM-SVG figure said: "You didn't highlight the curves
or their legends when mentioning them."  This figure overlays sin(x) and
its degree-1/3/5/7 Taylor polynomials, gives each curve a distinct colour
and a legend entry, and — crucially — returns narration phrases that
HIGHLIGHT each curve together with its legend entry as it is named (every
curve and legend row carries a stable element id the narration references).
The polynomials are exact, so they are computed and arithmetic-checked.
"""
from __future__ import annotations

import html as _html
import math
from typing import Any

_W, _H = 940, 600

# Plot box (pixels) and the math window it maps.
_OX, _OR = 60, 740          # x-pixels: left, right
_OT, _OB = 118, 498         # y-pixels: top, bottom
_XMIN, _XMAX = -6.6, 6.6
_YMIN, _YMAX = -2.6, 2.6


def _text(x: float, y: float, s: str, *, fs: float = 14, anchor: str = "start",
          weight: str = "normal", fill: str = "#1a1d24", el_id: str = "") -> str:
    i = f' id="{el_id}"' if el_id else ""
    return (f'<text{i} x="{x:.1f}" y="{y:.1f}" font-size="{fs}" '
            f'text-anchor="{anchor}" font-weight="{weight}" '
            f'fill="{fill}">{_html.escape(s)}</text>')


def _sx(x: float) -> float:
    return _OX + (x - _XMIN) / (_XMAX - _XMIN) * (_OR - _OX)


def _sy(y: float) -> float:
    return _OB - (y - _YMIN) / (_YMAX - _YMIN) * (_OB - _OT)


def _clipped_path(fn, *, n: int = 480) -> str:
    """Polyline path for y = fn(x) over the window, split into sub-paths so
    nothing is drawn outside the plot band (keeps every point inside the
    viewBox; a diverging polynomial simply exits the frame)."""
    segs: list[list[tuple[float, float]]] = [[]]
    for i in range(n + 1):
        x = _XMIN + (_XMAX - _XMIN) * i / n
        y = fn(x)
        if _YMIN <= y <= _YMAX:
            segs[-1].append((_sx(x), _sy(y)))
        elif segs[-1]:
            segs.append([])
    d_parts = []
    for seg in segs:
        if len(seg) >= 2:
            d_parts.append("M " + " L ".join(f"{px:.1f},{py:.1f}"
                                              for px, py in seg))
    return " ".join(d_parts)


def render_taylor_sin() -> tuple[str, list[dict]]:
    """Fully deterministic; no LLM call.  Polynomials are arithmetic-checked."""
    f3, f5, f7 = math.factorial(3), math.factorial(5), math.factorial(7)
    assert (f3, f5, f7) == (6, 120, 5040)

    def t1(x):
        return x

    def t3(x):
        return x - x**3 / f3

    def t5(x):
        return x - x**3 / f3 + x**5 / f5

    def t7(x):
        return x - x**3 / f3 + x**5 / f5 - x**7 / f7

    # Sanity-check the partial sums against sin near 0 (higher degree ⇒
    # closer); these are the curves the figure shows.
    for xv in (0.4, 0.8):
        s = math.sin(xv)
        errs = [abs(t1(xv) - s), abs(t3(xv) - s),
                abs(t5(xv) - s), abs(t7(xv) - s)]
        assert errs[0] >= errs[1] >= errs[2] >= errs[3], "approx not improving"
    assert abs(t7(0.5) - math.sin(0.5)) < 1e-4

    P: list[str] = []
    P.append(_text(_W / 2, 36, "Taylor Polynomials of sin x about x = 0",
                   fs=20, anchor="middle", weight="700"))

    # Formula band.
    P.append('<rect id="formula" x="40" y="52" width="860" height="44" rx="6" '
             'fill="#eef4fb" stroke="#1f6fe0"/>')
    P.append(_text(_W / 2, 80,
                   "sin x  =  x − x³/3!  +  x⁵/5!  −  x⁷/7!  +  ⋯",
                   fs=15, anchor="middle", weight="600", fill="#1657b8"))

    # Axes.
    y0 = _sy(0.0)
    x0 = _sx(0.0)
    P.append(f'<line x1="{_OX:.1f}" y1="{y0:.1f}" x2="{_OR:.1f}" y2="{y0:.1f}" '
             f'stroke="#1a1d24" stroke-width="1.2"/>')
    P.append(f'<line x1="{x0:.1f}" y1="{_OT:.1f}" x2="{x0:.1f}" y2="{_OB:.1f}" '
             f'stroke="#1a1d24" stroke-width="1.2"/>')
    for xv, lab in ((-2 * math.pi, "−2π"), (-math.pi, "−π"),
                    (math.pi, "π"), (2 * math.pi, "2π")):
        P.append(f'<line x1="{_sx(xv):.1f}" y1="{y0:.1f}" x2="{_sx(xv):.1f}" '
                 f'y2="{y0 + 5:.1f}" stroke="#1a1d24" stroke-width="1"/>')
        P.append(_text(_sx(xv), y0 + 18, lab, fs=11, anchor="middle",
                       fill="#5a6472"))
    for yv in (-1.0, 1.0):
        P.append(f'<line x1="{x0 - 4:.1f}" y1="{_sy(yv):.1f}" x2="{x0 + 4:.1f}" '
                 f'y2="{_sy(yv):.1f}" stroke="#1a1d24" stroke-width="1"/>')
        P.append(_text(x0 - 8, _sy(yv) + 4, f"{yv:.0f}", fs=11, anchor="end",
                       fill="#5a6472"))

    # Curves: sin reference (thick black) + the four partial sums.
    curves = [
        ("curve_sin", math.sin, "#1a1d24", 3.0),
        ("curve_t1", t1, "#d9822b", 2.0),
        ("curve_t3", t3, "#2c7a38", 2.0),
        ("curve_t5", t5, "#1f6fe0", 2.0),
        ("curve_t7", t7, "#9b1d8f", 2.0),
    ]
    for cid, fn, col, w in curves:
        P.append(f'<path id="{cid}" d="{_clipped_path(fn)}" fill="none" '
                 f'stroke="{col}" stroke-width="{w}"/>')

    # Legend (top-right inside the plot, on a white card so it stays legible).
    lx, ly = 568, 130
    P.append(f'<rect x="{lx - 14}" y="{ly - 20}" width="178" height="128" '
             f'rx="6" fill="#ffffff" stroke="#c9d4e2" opacity="0.95"/>')
    legend = [
        ("leg_sin", "#1a1d24", "sin x"),
        ("leg_t1", "#d9822b", "T₁ = x"),
        ("leg_t3", "#2c7a38", "T₃ = x − x³/6"),
        ("leg_t5", "#1f6fe0", "T₅ = T₃ + x⁵/120"),
        ("leg_t7", "#9b1d8f", "T₇ = T₅ − x⁷/5040"),
    ]
    for i, (lid, col, lab) in enumerate(legend):
        yy = ly + i * 22
        P.append(f'<line x1="{lx:.1f}" y1="{yy - 4:.1f}" x2="{lx + 26:.1f}" '
                 f'y2="{yy - 4:.1f}" stroke="{col}" stroke-width="3"/>')
        P.append(_text(lx + 34, yy, lab, fs=12, el_id=lid, fill="#23282f",
                       weight="600"))

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'viewBox="0 0 {_W} {_H}" width="{_W}" height="{_H}">'
           + "".join(P) + "</svg>")

    narration = [
        {"speak": "A Taylor series rebuilds a function near a point from its "
                  "derivatives, each added power term correcting the previous "
                  "approximation.",
         "highlight": ["formula"]},
        {"speak": "Sine is the curve being approximated; every polynomial here "
                  "tries to match it, starting at the origin.",
         "highlight": ["curve_sin", "leg_sin"]},
        {"speak": "The first-degree polynomial is just x, the tangent line at "
                  "zero, so it follows sine only very close to the origin.",
         "highlight": ["curve_t1", "leg_t1"]},
        {"speak": "Subtracting x cubed over six gives the cubic, which now "
                  "curves the right way and tracks the first rise and fall.",
         "highlight": ["curve_t3", "leg_t3"]},
        {"speak": "Adding x to the fifth over one hundred twenty, the "
                  "fifth-degree polynomial hugs a full hump on each side of "
                  "the origin.",
         "highlight": ["curve_t5", "leg_t5"]},
        {"speak": "With the seventh-degree term the polynomial matches sine "
                  "across nearly the whole window before finally peeling away.",
         "highlight": ["curve_t7", "leg_t7"]},
        {"speak": "Each extra term widens the range of agreement, so the full "
                  "infinite series equals sine for every real number.",
         "highlight": ["formula"]},
    ]
    return svg, narration


def is_taylor_sin_prompt(prompt: str) -> bool:
    p = (prompt or "").lower()
    has_series = ("taylor" in p or "maclaurin" in p
                  or "power series" in p or "series expansion" in p)
    has_sin = "sin(" in p or "sin x" in p or "sine" in p or "\\sin" in p \
        or " sin " in f" {p} "
    return has_series and has_sin


async def generate_taylor_sin_svg(
    prompt: str = "", *, api_key: str = "", base_url: str = "",
    model: str = "",
) -> tuple[str, list[dict]]:
    return render_taylor_sin()
