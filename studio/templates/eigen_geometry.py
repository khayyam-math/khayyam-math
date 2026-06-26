"""Deterministic geometric explanation of eigenvalues & eigenvectors.

The probe caught "explain eigenvalues and eigenvectors geometrically": the
LLM-SVG figure was visually fine but the NARRATION made a mathematically
WRONG claim — that eigenvectors "do not rotate".  That is only true for a
positive eigenvalue; a negative eigenvalue REVERSES the eigenvector (a 180°
flip).  The precise statement is that an eigenvector stays on its own line
through the origin, scaled by the eigenvalue (and reversed when the
eigenvalue is negative), while a generic direction is rotated off its line.

The concept has one fixed geometry, so we render it correct-by-construction
from a concrete matrix and ASSERT A·v = λ·v before drawing, with narration
that states the relationship precisely.

Canonical example: A = [[2, 1], [1, 2]]
    λ₁ = 3, eigenvector along (1, 1);  λ₂ = 1, eigenvector along (1, −1)
    A generic vector (e.g. (2, 0)) maps to (4, 2) — rotated off its line.
"""
from __future__ import annotations

import html as _html
import math
from typing import Any

_W, _H = 940, 560

# Plane geometry (left panel).
_OX, _OY = 250.0, 300.0      # origin in pixels
_SC = 30.0                   # pixels per unit


def _text(x: float, y: float, s: str, *, fs: float = 14, anchor: str = "start",
          weight: str = "normal", fill: str = "#1a1d24", el_id: str = "") -> str:
    i = f' id="{el_id}"' if el_id else ""
    return (f'<text{i} x="{x:.1f}" y="{y:.1f}" font-size="{fs}" '
            f'text-anchor="{anchor}" font-weight="{weight}" '
            f'fill="{fill}">{_html.escape(s)}</text>')


def _gx(x: float) -> float:
    return _OX + x * _SC


def _gy(y: float) -> float:
    return _OY - y * _SC


def _arrow(x: float, y: float, col: str, *, w: float = 2.4, dash: bool = False,
           el_id: str = "") -> str:
    """Arrow from the origin to math point (x, y)."""
    px, py = _gx(x), _gy(y)
    ang = math.atan2(_OY - py, px - _OX)
    a = 9.0
    a1 = (px - a * math.cos(ang - 0.4), py + a * math.sin(ang - 0.4))
    a2 = (px - a * math.cos(ang + 0.4), py + a * math.sin(ang + 0.4))
    d = ' stroke-dasharray="6 4"' if dash else ""
    i = f' id="{el_id}"' if el_id else ""
    return (f'<g{i}><line x1="{_OX:.1f}" y1="{_OY:.1f}" x2="{px:.1f}" '
            f'y2="{py:.1f}" stroke="{col}" stroke-width="{w}"{d}/>'
            f'<path d="M {px:.1f},{py:.1f} L {a1[0]:.1f},{a1[1]:.1f} '
            f'L {a2[0]:.1f},{a2[1]:.1f} Z" fill="{col}"/></g>')


def render_eigen_geometry() -> tuple[str, list[dict]]:
    """Fully deterministic; A·v = λ·v is asserted before drawing."""
    A = [[2.0, 1.0], [1.0, 2.0]]
    eigs = [(3.0, (1.0, 1.0)), (1.0, (1.0, -1.0))]    # (λ, eigenvector dir)

    def mv(m, v):
        return (m[0][0] * v[0] + m[0][1] * v[1],
                m[1][0] * v[0] + m[1][1] * v[1])

    for lam, v in eigs:
        Av = mv(A, v)
        assert abs(Av[0] - lam * v[0]) < 1e-9 and abs(Av[1] - lam * v[1]) < 1e-9, \
            "A v != lambda v"
    # generic (non-eigen) vector must actually leave its own line
    u = (2.0, 0.0)
    Au = mv(A, u)                                       # (4, 2)
    assert abs(Au[1] * u[0] - Au[0] * u[1]) > 1e-6, "u happens to be an eigenvector"

    P: list[str] = []
    P.append(_text(_W / 2, 32, "Eigenvalues & Eigenvectors, Geometrically",
                   fs=20, anchor="middle", weight="700"))

    # Precise statement band (the correctness fix lives here + in narration).
    P.append('<rect id="statement" x="36" y="48" width="868" height="52" rx="6" '
             'fill="#eef4fb" stroke="#1f6fe0"/>')
    P.append(_text(_W / 2, 70,
                   "A·v = λv:  an eigenvector v is a direction the matrix maps "
                   "to a scalar multiple of itself —",
                   fs=13.5, anchor="middle", weight="600", fill="#1657b8"))
    P.append(_text(_W / 2, 90,
                   "it stays on its own line through the origin, scaled by λ "
                   "(and reversed when λ < 0).",
                   fs=13.5, anchor="middle", weight="600", fill="#1657b8"))

    # ── Left: the plane ───────────────────────────────────────────────
    # axes
    P.append(f'<line x1="{_OX - 200:.1f}" y1="{_OY:.1f}" x2="{_OX + 200:.1f}" '
             f'y2="{_OY:.1f}" stroke="#c2cad6" stroke-width="1"/>')
    P.append(f'<line x1="{_OX:.1f}" y1="{_OY - 175:.1f}" x2="{_OX:.1f}" '
             f'y2="{_OY + 130:.1f}" stroke="#c2cad6" stroke-width="1"/>')

    # invariant eigen-lines (faint, full-length through origin)
    for lam, (vx, vy) in eigs:
        n = math.hypot(vx, vy)
        ex, ey = vx / n, vy / n
        L = 4.6
        P.append(f'<line x1="{_gx(-L*ex):.1f}" y1="{_gy(-L*ey):.1f}" '
                 f'x2="{_gx(L*ex):.1f}" y2="{_gy(L*ey):.1f}" '
                 f'stroke="#9bbbe8" stroke-width="1" stroke-dasharray="3 4"/>')

    # eigenvector v1 and its image 3·v1 (same line, stretched)
    P.append(_arrow(1, 1, "#1f6fe0", el_id="v1"))
    P.append(_arrow(3, 3, "#0b3e8f", w=2.8, el_id="Av1"))
    P.append(_text(_gx(1) - 6, _gy(1) - 6, "v₁", fs=13, anchor="end",
                   weight="700", fill="#1657b8"))
    P.append(_text(_gx(3) + 8, _gy(3), "A v₁ = 3v₁", fs=12.5, weight="700",
                   fill="#0b3e8f"))

    # eigenvector v2 and its image (λ=1, unchanged)
    P.append(_arrow(1.6, -1.6, "#2c7a38", el_id="v2"))
    P.append(_text(_gx(1.6) + 8, _gy(-1.6) + 6, "v₂ :  A v₂ = 1·v₂",
                   fs=12.5, weight="700", fill="#2c7a38"))

    # generic vector u and its image Au (rotated off its line)
    P.append(_arrow(2, 0, "#b23b3b", el_id="u"))
    P.append(_arrow(4, 2, "#b23b3b", w=2.2, dash=True, el_id="Au"))
    P.append(_text(_gx(2) + 4, _gy(0) + 18, "u", fs=13, weight="700",
                   fill="#b23b3b"))
    P.append(_text(_gx(4) + 6, _gy(2), "A u  (rotated)", fs=12.5, weight="700",
                   fill="#b23b3b"))

    # ── Right: matrix + eigen-data ────────────────────────────────────
    bx = 560
    P.append(_text(bx, 150, "Worked example", fs=15, weight="700"))
    P.append(_text(bx, 178, "A = [[2, 1], [1, 2]]  (symmetric)", fs=14))
    P.append(_text(bx, 210, "Eigenvalues:  λ₁ = 3,  λ₂ = 1", fs=14,
                   weight="600", fill="#1657b8"))
    P.append(_text(bx, 234, "Eigenvectors:", fs=14, weight="600"))
    P.append(_text(bx + 14, 258, "• along (1, 1):  A(1,1) = (3,3) = 3·(1,1)",
                   fs=13, fill="#0b3e8f"))
    P.append(_text(bx + 14, 280, "• along (1, −1): A(1,−1) = (1,−1) = 1·(1,−1)",
                   fs=13, fill="#2c7a38"))
    P.append(_text(bx, 312, "A non-eigen direction rotates:", fs=14,
                   weight="600", fill="#b23b3b"))
    P.append(_text(bx + 14, 334, "u = (2, 0)  →  A u = (4, 2)", fs=13,
                   fill="#b23b3b"))
    P.append(_text(bx + 14, 354, "(2,0) and (4,2) are NOT parallel, so u left "
                   "its line.", fs=12.5, fill="#3a4250"))

    # takeaway band
    P.append('<rect x="36" y="486" width="868" height="58" rx="6" '
             'fill="#f1f8f2" stroke="#bcdcc2"/>')
    P.append(_text(_W / 2, 508,
                   "λ is the stretch factor along its eigen-direction: |λ| > 1 "
                   "stretches, |λ| < 1 compresses, λ < 0 flips the arrow.",
                   fs=13, anchor="middle", fill="#23282f"))
    P.append(_text(_W / 2, 530,
                   "Eigen-directions are the axes along which the matrix acts "
                   "as a pure scaling — the skeleton of the transformation.",
                   fs=13, anchor="middle", weight="600", fill="#147a40"))

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'viewBox="0 0 {_W} {_H}" width="{_W}" height="{_H}">'
           + "".join(P) + "</svg>")

    narration = [
        {"speak": "An eigenvector is a direction that the matrix maps to a "
                  "scalar multiple of itself: it stays on its own line through "
                  "the origin, only stretched or compressed by the eigenvalue, "
                  "and reversed if the eigenvalue is negative.",
         "highlight": ["statement"]},
        {"speak": "Take the first eigen-direction, along one-one. The matrix "
                  "sends it to three times itself: same line, stretched by the "
                  "eigenvalue three.",
         "highlight": ["v1", "Av1"]},
        {"speak": "The second eigen-direction, along one minus one, has "
                  "eigenvalue one, so the matrix leaves it exactly where it is.",
         "highlight": ["v2"]},
        {"speak": "A direction that is not an eigenvector, like the horizontal "
                  "vector u, gets turned off its own line — that rotation is "
                  "precisely what eigenvectors avoid.",
         "highlight": ["u", "Au"]},
        {"speak": "So the eigenvalues are the stretch factors and the "
                  "eigenvectors are the special axes along which the matrix acts "
                  "as pure scaling, with no rotation.",
         "highlight": ["statement"]},
    ]
    return svg, narration


def is_eigen_geometry_prompt(prompt: str) -> bool:
    p = (prompt or "").lower()
    has_eigen = ("eigenvector" in p or "eigenvalue" in p
                 or "eigen vector" in p or "eigen value" in p)
    if not has_eigen:
        return False
    # geometric / conceptual intent — NOT a "compute the eigenvalues of [[..]]"
    # request (which wants a worked numeric answer), and NOT the spectral
    # theorem / decomposition route.
    if "spectral" in p:
        return False
    geom = any(k in p for k in (
        "geometr", "visual", "intuition", "intuitive", "meaning", "mean",
        "what is", "what are", "explain", "understand", "picture",
        "direction"))
    return geom


async def generate_eigen_geometry_svg(
    prompt: str = "", *, api_key: str = "", base_url: str = "",
    model: str = "",
) -> tuple[str, list[dict]]:
    return render_eigen_geometry()
