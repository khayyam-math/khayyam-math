"""Deterministic renderer for the normal distribution + empirical rule.

A bell curve with the 68-95-99.7 bands shaded is a common stats figure
the LLM draws unreliably (lopsided curves, wrong percentages, axis ticks
that don't line up).  The Gaussian is pure math, so we compute the curve
and the band areas exactly and shade them correct-by-construction.
"""
from __future__ import annotations

import html as _html
import math
from typing import Any

_W, _H = 900, 520


def _text(x: float, y: float, s: str, *, fs: int = 14, anchor: str = "start",
          weight: str = "normal", fill: str = "#1a1d24") -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{fs}" '
            f'text-anchor="{anchor}" font-weight="{weight}" '
            f'fill="{fill}">{_html.escape(s)}</text>')


def render_normal_distribution() -> tuple[str, list[dict]]:
    """Standard-normal bell curve with the 68-95-99.7 empirical-rule bands.
    Fully deterministic; the shaded areas ARE the textbook percentages."""
    zmin, zmax = -3.7, 3.7
    x0, x1 = 80, 820          # plot x-extent (px)
    y_base = 380              # x-axis baseline (px)
    peak = 250                # curve height at the mean (px)

    def gx(z: float) -> float:
        return x0 + (z - zmin) / (zmax - zmin) * (x1 - x0)

    def gy(z: float) -> float:
        return y_base - peak * math.exp(-z * z / 2)

    P: list[str] = []
    P.append(_text(_W / 2, 40, "The Normal Distribution: the 68-95-99.7 Rule",
                   fs=21, anchor="middle", weight="700"))

    # Shaded bands (back to front: widest/lightest first).
    def band(a: float, b: float, fill: str) -> str:
        zs = [a + (b - a) * i / 64 for i in range(65)]
        d = f"M {gx(a):.1f},{y_base:.1f} "
        d += " ".join(f"L {gx(z):.1f},{gy(z):.1f}" for z in zs)
        d += f" L {gx(b):.1f},{y_base:.1f} Z"
        return f'<path d="{d}" fill="{fill}" stroke="none"/>'

    P.append(band(-3, 3, "#dfe9f6"))
    P.append(band(-2, 2, "#a9c6e8"))
    P.append(band(-1, 1, "#4a82c4"))

    # The curve itself, on top of the fills.
    N = 260
    pts = [(gx(zmin + (zmax - zmin) * i / N), gy(zmin + (zmax - zmin) * i / N))
           for i in range(N + 1)]
    curve = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    P.append(f'<path d="{curve}" fill="none" stroke="#1a3a63" '
             f'stroke-width="2.5"/>')

    # x-axis + sigma gridlines and tick labels.
    P.append(f'<line x1="{x0 - 10}" y1="{y_base}" x2="{x1 + 10}" y2="{y_base}" '
             f'stroke="#1a1d24" stroke-width="1.5"/>')
    ticks = [(-3, "μ−3σ"), (-2, "μ−2σ"), (-1, "μ−σ"), (0, "μ"),
             (1, "μ+σ"), (2, "μ+2σ"), (3, "μ+3σ")]
    for z, lab in ticks:
        P.append(f'<line x1="{gx(z):.1f}" y1="{gy(z):.1f}" '
                 f'x2="{gx(z):.1f}" y2="{y_base}" stroke="#7a8595" '
                 f'stroke-width="1" stroke-dasharray="3 3"/>')
        P.append(f'<line x1="{gx(z):.1f}" y1="{y_base}" x2="{gx(z):.1f}" '
                 f'y2="{y_base + 6}" stroke="#1a1d24" stroke-width="1.2"/>')
        P.append(_text(gx(z), y_base + 22, lab, fs=13, anchor="middle",
                       weight="700" if z == 0 else "normal"))

    # Band percentage labels (textbook empirical-rule breakdown).
    P.append(_text(gx(0), 300, "68.2%", fs=16, anchor="middle", weight="700",
                   fill="#ffffff"))
    for z in (-1.5, 1.5):
        P.append(_text(gx(z), 345, "13.6%", fs=12, anchor="middle",
                       weight="600", fill="#1a3a63"))
    for z in (-2.5, 2.5):
        P.append(_text(gx(z), 300, "2.1%", fs=11, anchor="middle",
                       fill="#3a4250"))
        P.append(f'<line x1="{gx(z):.1f}" y1="307" x2="{gx(z):.1f}" '
                 f'y2="{gy(z) - 2:.1f}" stroke="#9aa3af" stroke-width="0.8"/>')

    # Cumulative summary, kept clear of the bottom edge.
    P.append(_text(_W / 2, 430,
                   "Within 1σ: 68.2%   ·   within 2σ: 95.4%   ·   "
                   "within 3σ: 99.7%",
                   fs=14, anchor="middle", weight="600", fill="#1a3a63"))

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'viewBox="0 0 {_W} {_H}" width="{_W}" height="{_H}">'
           + "".join(P) + "</svg>")

    narration = [
        {"speak": "The normal distribution is the symmetric bell-shaped curve "
                  "that describes how values cluster around a mean. Its two "
                  "parameters are the mean, which sets the centre, and the "
                  "standard deviation, which sets the spread."},
        {"speak": "The empirical rule reads the area under the curve as "
                  "probability. The dark central band, within one standard "
                  "deviation of the mean, holds about 68 percent of all "
                  "values."},
        {"speak": "Extending to two standard deviations captures about 95 "
                  "percent, and three standard deviations captures about 99.7 "
                  "percent. Almost nothing lies beyond three sigma."},
        {"speak": "Because the curve is symmetric, each side contributes half "
                  "of every band, so 34.1 percent sits between the mean and "
                  "one sigma on each side."},
        {"speak": "These fixed proportions are why the standard deviation is "
                  "such a useful ruler: knowing the mean and sigma tells you "
                  "where almost every observation must fall."},
    ]
    return svg, narration


def is_normal_distribution_prompt(prompt: str) -> bool:
    p = (prompt or "").lower()
    if "elimination" in p:          # never hijack "Gaussian elimination"
        return False
    return any(k in p for k in (
        "normal distribution", "bell curve", "empirical rule",
        "68-95-99.7", "68 95 99", "standard normal",
        "gaussian distribution", "normal curve"))


async def generate_normal_distribution_svg(
    prompt: str = "", *, api_key: str = "", base_url: str = "",
    model: str = "",
) -> tuple[str, list[dict]]:
    return render_normal_distribution()
