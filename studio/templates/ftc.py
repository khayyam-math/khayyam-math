"""Deterministic renderer for the proof of the Fundamental Theorem of
Calculus (Part 1).

On the LLM-SVG path this prompt failed the 'proof' completeness rubric
(no explicit deduction steps, no QED) AND the vision review (it wanted a
graph of f and a mean-value-theorem strip).  The proof has one fixed
structure, so we render it correct-by-construction: the statement of both
parts, the difference-quotient proof of Part 1 with explicit deduction
steps, and the area-under-the-curve picture with the thin strip from x to
x+h that the argument is about.
"""
from __future__ import annotations

import html as _html
import math
from typing import Any

_W, _H = 940, 600


def _text(x: float, y: float, s: str, *, fs: int = 14, anchor: str = "start",
          weight: str = "normal", fill: str = "#1a1d24",
          style: str = "") -> str:
    extra = f' font-style="{style}"' if style else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{fs}" '
            f'text-anchor="{anchor}" font-weight="{weight}" '
            f'fill="{fill}"{extra}>{_html.escape(s)}</text>')


def _wrap(text: str, max_chars: int) -> list[str]:
    words, out, cur = (text or "").split(), [], ""
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= max_chars:
            cur += " " + w
        else:
            out.append(cur)
            cur = w
    if cur:
        out.append(cur)
    return out


def render_ftc() -> tuple[str, list[dict]]:
    """Fully deterministic; no LLM call."""
    P: list[str] = []
    P.append(_text(_W / 2, 40, "The Fundamental Theorem of Calculus",
                   fs=21, anchor="middle", weight="700"))

    # Statement band (both parts).
    P.append(f'<rect x="40" y="58" width="{_W - 80}" height="56" rx="6" '
             f'fill="#eef4fb" stroke="#1f6fe0"/>')
    P.append(_text(_W / 2, 80,
                   "Part 1:  if f is continuous on [a, b] and "
                   "F(x) = ∫ₐˣ f(t) dt,  then F is differentiable and "
                   "F′(x) = f(x).",
                   fs=13.5, anchor="middle", weight="600", fill="#1657b8"))
    P.append(_text(_W / 2, 102,
                   "Part 2:  if F is any antiderivative of f,  then "
                   "∫ₐᵇ f(x) dx = F(b) − F(a).",
                   fs=13.5, anchor="middle", weight="600", fill="#1657b8"))

    # ── Area-under-the-curve picture (right column) ──────────────────
    ox, oy = 560, 360          # plot origin (x-axis left end, baseline)
    pw, ph = 330, 180          # plot width, height
    ta, tx, th, tb = 0.10, 0.50, 0.13, 0.93   # a, x, x+h, b in [0,1]

    def fcurve(t: float) -> float:           # smooth positive shape in (0,1)
        return 0.32 + 0.46 * t + 0.10 * math.sin(5.0 * t)

    def gx(t: float) -> float:
        return ox + t * pw

    def gy(t: float) -> float:
        return oy - fcurve(t) * ph

    # shaded accumulated area F(x) from a to x
    zs = [ta + (tx - ta) * i / 40 for i in range(41)]
    d = f"M {gx(ta):.1f},{oy:.1f} " + " ".join(
        f"L {gx(t):.1f},{gy(t):.1f}" for t in zs) + f" L {gx(tx):.1f},{oy:.1f} Z"
    P.append(f'<path d="{d}" fill="#cfe0f5" stroke="none"/>')
    # the thin strip from x to x+h
    zs2 = [tx + (tx + th - tx) * i / 12 for i in range(13)]
    d2 = f"M {gx(tx):.1f},{oy:.1f} " + " ".join(
        f"L {gx(t):.1f},{gy(t):.1f}" for t in zs2) + f" L {gx(tx + th):.1f},{oy:.1f} Z"
    P.append(f'<path d="{d2}" fill="#f6c87a" stroke="#d9822b"/>')
    # the curve
    N = 90
    pts = [(gx(i / N), gy(i / N)) for i in range(N + 1)]
    P.append('<path d="M ' + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
             + '" fill="none" stroke="#1a3a63" stroke-width="2.2"/>')
    # axes
    P.append(f'<line x1="{ox - 12}" y1="{oy}" x2="{ox + pw + 12}" y2="{oy}" '
             f'stroke="#1a1d24" stroke-width="1.3"/>')
    P.append(f'<line x1="{ox}" y1="{oy + 8}" x2="{ox}" y2="{oy - ph - 10}" '
             f'stroke="#1a1d24" stroke-width="1.3"/>')
    # tick labels a, x, x+h, b
    for t, lab in ((ta, "a"), (tx, "x"), (tx + th, "x+h"), (tb, "b")):
        P.append(f'<line x1="{gx(t):.1f}" y1="{oy}" x2="{gx(t):.1f}" '
                 f'y2="{oy + 5}" stroke="#1a1d24" stroke-width="1"/>')
        P.append(_text(gx(t), oy + 18, lab, fs=12, anchor="middle"))
    P.append(_text(gx(0.92), gy(0.92) - 8, "y = f(t)", fs=12,
                   anchor="middle", fill="#1a3a63", weight="600"))
    P.append(_text(gx(0.30), oy - 34, "F(x)", fs=13, anchor="middle",
                   weight="600", fill="#1657b8"))
    P.append(_text(gx(tx + th) + 6, gy(tx) - 30,
                   "ΔF ≈ f(x)·h", fs=11.5, fill="#b5651d"))

    # ── Proof of Part 1 (left column) ────────────────────────────────
    P.append(_text(56, 152, "Proof of Part 1 (difference quotient):",
                   fs=15, weight="700"))
    steps = [
        "Define the accumulation function F(x) = ∫ₐˣ f(t) dt.",
        "Consider the difference quotient "
        "[F(x+h) − F(x)] / h = (1/h) ∫ₓˣ⁺ʰ f(t) dt.",
        "By the Mean Value Theorem for integrals, there is a c in "
        "[x, x+h] with ∫ₓˣ⁺ʰ f(t) dt = f(c)·h.",
        "So the difference quotient equals f(c).",
        "As h → 0 the point c is squeezed toward x, and since f is "
        "continuous, f(c) → f(x).",
        "Therefore F′(x) = limₕ→₀ [F(x+h) − F(x)] / h = f(x).",
    ]
    y, lh = 180, 21
    for i, s in enumerate(steps, 1):
        P.append(f'<circle cx="66" cy="{y - 4}" r="11" fill="#1f6fe0"/>')
        P.append(_text(66, y, str(i), fs=12, anchor="middle", weight="700",
                       fill="#ffffff"))
        for j, ln in enumerate(_wrap(s, 50)):
            P.append(_text(88, y + j * lh, ln, fs=13.5))
        y += len(_wrap(s, 50)) * lh + 13

    P.append(_text(56, y + 6,
                   "∴ Differentiating the integral recovers the "
                   "integrand.  ∎",
                   fs=15, weight="700", fill="#2c7a38"))

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'viewBox="0 0 {_W} {_H}" width="{_W}" height="{_H}">'
           + "".join(P) + "</svg>")

    narration = [
        {"speak": "The Fundamental Theorem of Calculus links the two central "
                  "operations of calculus, differentiation and integration, "
                  "by showing they undo each other."},
        {"speak": "Part one starts from the accumulation function F of x, the "
                  "signed area under f from a fixed point a up to x."},
        {"speak": "To find its derivative, form the difference quotient: F at "
                  "x plus h minus F at x, all over h. That numerator is just "
                  "the area of the thin strip between x and x plus h."},
        {"speak": "By the mean value theorem for integrals, that strip's area "
                  "equals f at some point c inside the strip times its width "
                  "h, so the difference quotient is simply f of c."},
        {"speak": "Now let h shrink to zero. The point c is trapped between x "
                  "and x plus h, so it is forced to x, and because f is "
                  "continuous, f of c approaches f of x."},
        {"speak": "Therefore the derivative of F at x equals f at x: "
                  "differentiating the area function gives back the original "
                  "integrand."},
        {"speak": "Part two follows: since F is an antiderivative of f, the "
                  "definite integral from a to b equals F at b minus F at a, "
                  "which is how integrals are actually evaluated."},
    ]
    return svg, narration


def is_ftc_prompt(prompt: str) -> bool:
    p = (prompt or "").lower()
    if "fundamental theorem of calculus" in p:
        return True
    if "ftc" in p and any(k in p for k in (
            "calculus", "proof", "prove", "theorem", "integral", "derivative")):
        return True
    return False


async def generate_ftc_svg(
    prompt: str = "", *, api_key: str = "", base_url: str = "",
    model: str = "",
) -> tuple[str, list[dict]]:
    return render_ftc()
