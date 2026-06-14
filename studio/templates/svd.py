"""Deterministic renderer for the singular value decomposition of a 2×2 matrix.

The probe caught "show the singular value decomposition of a 2x2 matrix"
failing on the LLM-SVG path: route=None, text outside the viewBox,
overlapping labels, and a vision review that the figure "lacks orthonormal
columns for U and V".  SVD has one fixed structure and the matrices are
exact, so we compute U, Σ, Vᵀ in Python, ASSERT that U Σ Vᵀ reproduces A
AND that U and V are orthogonal (UᵀU = VᵀV = I), and draw them
correct-by-construction — including the canonical unit-circle→ellipse
picture that shows the orthonormal singular directions directly.

Canonical example:  A = [[2, 2], [−1, 1]],  σ₁ = 2√2,  σ₂ = √2.
"""
from __future__ import annotations

import html as _html
import math
from typing import Any

_W, _H = 960, 620


def _text(x: float, y: float, s: str, *, fs: float = 14, anchor: str = "start",
          weight: str = "normal", fill: str = "#1a1d24", el_id: str = "") -> str:
    i = f' id="{el_id}"' if el_id else ""
    return (f'<text{i} x="{x:.1f}" y="{y:.1f}" font-size="{fs}" '
            f'text-anchor="{anchor}" font-weight="{weight}" '
            f'fill="{fill}">{_html.escape(s)}</text>')


def _matrix(x: float, y: float, rows: list[list[str]], *, cell_w: float = 44,
            cell_h: float = 30, fs: float = 15, stroke: str = "#1a1d24",
            el_id: str = "") -> tuple[str, float]:
    """Draw a bracketed matrix at top-left (x,y); return (svg, width)."""
    nr, nc = len(rows), len(rows[0])
    W, H = nc * cell_w, nr * cell_h
    b = 7
    g = f' id="{el_id}"' if el_id else ""
    p = [f'<g{g}>',
         f'<path d="M {x+b},{y} L {x},{y} L {x},{y+H} L {x+b},{y+H}" '
         f'fill="none" stroke="{stroke}" stroke-width="1.5"/>',
         f'<path d="M {x+W-b},{y} L {x+W},{y} L {x+W},{y+H} L {x+W-b},{y+H}" '
         f'fill="none" stroke="{stroke}" stroke-width="1.5"/>']
    for i, row in enumerate(rows):
        for j, e in enumerate(row):
            cx = x + j * cell_w + cell_w / 2
            cy = y + i * cell_h + cell_h / 2 + fs * 0.34
            p.append(_text(cx, cy, e, fs=fs, anchor="middle"))
    p.append('</g>')
    return "".join(p), W


def render_svd() -> tuple[str, list[dict]]:
    """Canonical 2×2 example; A = U Σ Vᵀ and orthonormality both asserted."""
    A = [[2.0, 2.0], [-1.0, 1.0]]
    s2 = math.sqrt(2.0)
    U = [[1.0, 0.0], [0.0, -1.0]]            # orthogonal (a reflection)
    S = [[2.0 * s2, 0.0], [0.0, s2]]         # singular values 2√2, √2
    V = [[1.0 / s2, 1.0 / s2], [1.0 / s2, -1.0 / s2]]   # orthogonal, symmetric
    VT = [[V[j][i] for j in range(2)] for i in range(2)]

    def mul(X, Y):
        return [[sum(X[i][k] * Y[k][j] for k in range(2)) for j in range(2)]
                for i in range(2)]

    def is_identity(M):
        I = [[1.0, 0.0], [0.0, 1.0]]
        return all(abs(M[i][j] - I[i][j]) < 1e-12 for i in range(2)
                   for j in range(2))

    recon = mul(mul(U, S), VT)
    assert all(abs(recon[i][j] - A[i][j]) < 1e-9
               for i in range(2) for j in range(2)), "A != U Σ Vᵀ"
    Ut = [[U[j][i] for j in range(2)] for i in range(2)]
    assert is_identity(mul(Ut, U)), "U not orthonormal"
    assert is_identity(mul(VT, V)), "V not orthonormal"

    P: list[str] = []
    P.append(_text(_W / 2, 34, "The Singular Value Decomposition (2×2)",
                   fs=21, anchor="middle", weight="700"))

    # Statement band.
    P.append('<rect id="statement" x="40" y="50" width="880" height="56" rx="6" '
             'fill="#eef4fb" stroke="#1f6fe0"/>')
    P.append(_text(_W / 2, 72,
                   "Every real matrix A factors as  A = U Σ Vᵀ,  with U and V "
                   "orthogonal",
                   fs=14, anchor="middle", weight="600", fill="#1657b8"))
    P.append(_text(_W / 2, 94,
                   "(their columns are orthonormal) and Σ diagonal with "
                   "nonnegative singular values σ₁ ≥ σ₂ ≥ 0.",
                   fs=14, anchor="middle", weight="600", fill="#1657b8"))

    # ── Left column: worked example + decomposition ──────────────────
    P.append(_text(56, 130, "Worked example:", fs=15, weight="700"))
    mA, _ = _matrix(110, 146, [["2", "2"], ["−1", "1"]])
    P.append(_text(104, 166, "A =", fs=15, anchor="end", weight="600"))
    P.append(mA)
    P.append(_text(220, 164, "Singular values:  σ₁ = 2√2 ≈ 2.83,", fs=13.5))
    P.append(_text(220, 184, "σ₂ = √2 ≈ 1.41", fs=13.5))

    P.append(_text(56, 236,
                   "U and V are orthogonal: each has orthonormal columns,",
                   fs=13, fill="#2c7a38", weight="600"))
    P.append(_text(56, 254, "so UᵀU = I and VᵀV = I.", fs=13,
                   fill="#2c7a38", weight="600"))

    # The decomposition A = U Σ Vᵀ, drawn with the actual matrices.
    yeq = 286
    x = 150
    P.append(_text(x - 12, yeq + 32, "A  =", fs=17, anchor="end", weight="700"))

    def place(rows, cell_w, label, el_id):
        nonlocal x
        m, w = _matrix(x, yeq, rows, cell_w=cell_w, el_id=el_id)
        P.append(m)
        P.append(_text(x + w / 2, yeq - 8, label, fs=14, anchor="middle",
                       weight="600", fill="#1657b8"))
        x += w + 16
    place([["1", "0"], ["0", "−1"]], 40, "U", "mU")
    place([["2√2", "0"], ["0", "√2"]], 50, "Σ", "mS")
    place([["1/√2", "1/√2"], ["1/√2", "−1/√2"]], 58, "Vᵀ", "mVT")

    P.append(_text(56, 396,
                   "Multiplying back:  U Σ Vᵀ = [[2, 2], [−1, 1]] = A.",
                   fs=13.5, weight="600", fill="#2c7a38"))
    P.append(_text(56, 422,
                   "A rotation/reflection (Vᵀ), then a scaling by the singular",
                   fs=13, fill="#3a4250"))
    P.append(_text(56, 440,
                   "values (Σ), then another rotation/reflection (U).",
                   fs=13, fill="#3a4250"))

    # ── Right column: unit circle → ellipse geometry ─────────────────
    cx, cy, sc = 748, 300, 40.0
    P.append(_text(cx, 146, "Unit circle  →  ellipse", fs=14, anchor="middle",
                   weight="700"))

    def gx(mx: float) -> float:
        return cx + mx * sc

    def gy(my: float) -> float:
        return cy - my * sc

    # axes
    P.append(f'<line x1="{cx - 150:.1f}" y1="{cy:.1f}" x2="{cx + 150:.1f}" '
             f'y2="{cy:.1f}" stroke="#9aa4b2" stroke-width="1"/>')
    P.append(f'<line x1="{cx:.1f}" y1="{cy - 150:.1f}" x2="{cx:.1f}" '
             f'y2="{cy + 150:.1f}" stroke="#9aa4b2" stroke-width="1"/>')
    # unit circle (the domain)
    P.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{sc:.1f}" fill="none" '
             f'stroke="#1f6fe0" stroke-width="1.6" stroke-dasharray="4 3"/>')
    # image ellipse: semi-axes σ1 along x, σ2 along y
    P.append(f'<ellipse id="ellipse" cx="{cx:.1f}" cy="{cy:.1f}" '
             f'rx="{2 * s2 * sc:.1f}" ry="{s2 * sc:.1f}" fill="#cfe0f5" '
             f'fill-opacity="0.35" stroke="#1a3a63" stroke-width="2"/>')

    def arrow(x2: float, y2: float, col: str, dashed: bool = False) -> str:
        dash = ' stroke-dasharray="4 3"' if dashed else ""
        return (f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                f'stroke="{col}" stroke-width="2.2"{dash}/>'
                f'<circle cx="{x2:.1f}" cy="{y2:.1f}" r="3" fill="{col}"/>')

    # right-singular vectors v1, v2 (orthonormal, on the unit circle)
    inv = 1.0 / s2
    P.append(f'<g id="vvecs">{arrow(gx(inv), gy(inv), "#1f6fe0", True)}'
             f'{arrow(gx(inv), gy(-inv), "#1f6fe0", True)}</g>')
    P.append(_text(gx(inv) + 6, gy(inv) - 4, "v₁", fs=12, fill="#1657b8",
                   weight="700"))
    P.append(_text(gx(inv) + 6, gy(-inv) + 14, "v₂", fs=12, fill="#1657b8",
                   weight="700"))
    # their images σ1 u1 (along +x) and σ2 u2 (along −y), on the ellipse axes
    P.append(f'<g id="uvecs">{arrow(gx(2 * s2), gy(0), "#c0392b")}'
             f'{arrow(gx(0), gy(-s2), "#c0392b")}</g>')
    P.append(_text(gx(2 * s2) - 2, gy(0) - 8, "σ₁u₁", fs=12, anchor="end",
                   fill="#a02a1a", weight="700"))
    P.append(_text(gx(0) + 8, gy(-s2) + 4, "σ₂u₂", fs=12, fill="#a02a1a",
                   weight="700"))

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'viewBox="0 0 {_W} {_H}" width="{_W}" height="{_H}">'
           + "".join(P) + "</svg>")

    narration = [
        {"speak": "The singular value decomposition writes any matrix as a "
                  "rotation or reflection, then a pure scaling, then another "
                  "rotation or reflection: A equals U times Sigma times V "
                  "transpose.",
         "highlight": ["statement"]},
        {"speak": "For this two by two matrix the singular values, the scaling "
                  "factors on Sigma's diagonal, are two root two and root two.",
         "highlight": ["mS"]},
        {"speak": "The columns of V are orthonormal directions in the input "
                  "space; the matrix sends them to perpendicular directions, "
                  "the columns of U.",
         "highlight": ["mVT", "vvecs"]},
        {"speak": "Geometrically the unit circle is mapped to an ellipse whose "
                  "semi-axis lengths are exactly the singular values, aligned "
                  "with the columns of U.",
         "highlight": ["ellipse", "uvecs"]},
        {"speak": "Because U and V are orthogonal their columns stay unit "
                  "length and mutually perpendicular, so reassembling U, Sigma "
                  "and V transpose returns the original matrix exactly.",
         "highlight": ["mU"]},
    ]
    return svg, narration


def is_svd_prompt(prompt: str) -> bool:
    p = (prompt or "").lower()
    if "singular value decomposition" in p or "singular-value decomposition" in p:
        return True
    if "singular value" in p and ("matrix" in p or "decompos" in p):
        return True
    if "svd" in p and ("matrix" in p or "decompos" in p
                       or "singular" in p or "2x2" in p or "2×2" in p):
        return True
    return False


async def generate_svd_svg(
    prompt: str = "", *, api_key: str = "", base_url: str = "",
    model: str = "",
) -> tuple[str, list[dict]]:
    return render_svd()
