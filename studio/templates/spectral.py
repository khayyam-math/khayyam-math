"""Deterministic renderer for the spectral theorem (symmetric eigendecomposition).

The LLM-SVG path drew a spectral-theorem figure with a TRUNCATED / wrong
Q^T matrix (the probe caught "Q^T should be [[1/√2,1/√2],[1/√2,-1/√2]]").
The theorem and its worked example have one fixed structure and the
matrices are exact, so we compute Q, Λ, Q^T in Python, ASSERT that
Q Λ Q^T reproduces A, and draw them correct-by-construction.
"""
from __future__ import annotations

import html as _html
import math
from typing import Any

_W, _H = 940, 600


def _text(x: float, y: float, s: str, *, fs: int = 14, anchor: str = "start",
          weight: str = "normal", fill: str = "#1a1d24") -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{fs}" '
            f'text-anchor="{anchor}" font-weight="{weight}" '
            f'fill="{fill}">{_html.escape(s)}</text>')


def _matrix(x: float, y: float, rows: list[list[str]], *, cell_w: float = 42,
            cell_h: float = 30, fs: int = 15, stroke: str = "#1a1d24") -> tuple[str, float]:
    """Draw a bracketed matrix at top-left (x,y); return (svg, width)."""
    nr, nc = len(rows), len(rows[0])
    W, H = nc * cell_w, nr * cell_h
    b = 7
    p = [f'<path d="M {x+b},{y} L {x},{y} L {x},{y+H} L {x+b},{y+H}" '
         f'fill="none" stroke="{stroke}" stroke-width="1.5"/>',
         f'<path d="M {x+W-b},{y} L {x+W},{y} L {x+W},{y+H} L {x+W-b},{y+H}" '
         f'fill="none" stroke="{stroke}" stroke-width="1.5"/>']
    for i, row in enumerate(rows):
        for j, e in enumerate(row):
            cx = x + j * cell_w + cell_w / 2
            cy = y + i * cell_h + cell_h / 2 + fs * 0.34
            p.append(_text(cx, cy, e, fs=fs, anchor="middle"))
    return "".join(p), W


def render_spectral_theorem() -> tuple[str, list[dict]]:
    """Canonical 2x2 example A = [[2,1],[1,2]]; all matrices asserted."""
    A = [[2, 1], [1, 2]]
    s = 1.0 / math.sqrt(2.0)
    Q = [[s, s], [s, -s]]            # columns = orthonormal eigenvectors
    Lam = [[3.0, 0.0], [0.0, 1.0]]   # eigenvalues 3, 1
    QT = [[Q[j][i] for j in range(2)] for i in range(2)]

    def mul(X, Y):
        return [[sum(X[i][k] * Y[k][j] for k in range(2)) for j in range(2)]
                for i in range(2)]
    recon = mul(mul(Q, Lam), QT)
    assert all(abs(recon[i][j] - A[i][j]) < 1e-9
               for i in range(2) for j in range(2)), "A != Q Lam Q^T"

    P: list[str] = []
    P.append(_text(_W / 2, 40, "The Spectral Theorem", fs=21,
                   anchor="middle", weight="700"))

    # Statement band.
    P.append(f'<rect x="40" y="58" width="{_W - 80}" height="56" rx="6" '
             f'fill="#eef4fb" stroke="#1f6fe0"/>')
    P.append(_text(_W / 2, 80,
                   "Every real symmetric matrix A can be written "
                   "A = Q Λ Qᵀ,  with Q orthogonal",
                   fs=14, anchor="middle", weight="600", fill="#1657b8"))
    P.append(_text(_W / 2, 102,
                   "(its columns are orthonormal eigenvectors of A) and Λ "
                   "diagonal (the eigenvalues).",
                   fs=14, anchor="middle", weight="600", fill="#1657b8"))

    # Worked example header + eigen-data.
    P.append(_text(56, 152, "Worked example:", fs=15, weight="700"))
    mA, _ = _matrix(224, 138, [["2", "1"], ["1", "2"]])
    P.append(_text(218, 158, "A =", fs=15, anchor="end", weight="600"))
    P.append(mA)
    P.append(_text(348, 150,
                   "Eigenvalues:  λ₁ = 3,  λ₂ = 1", fs=14))
    P.append(_text(348, 174,
                   "Eigenvectors (orthonormal):", fs=14))
    P.append(_text(348, 196,
                   "q₁ = (1, 1)/√2  for λ₁,   q₂ = (1, −1)/√2  for λ₂",
                   fs=13.5, fill="#3a4250"))

    # The decomposition A = Q Λ Qᵀ, drawn with the actual matrices.
    yeq = 270
    x = 232
    P.append(_text(x - 12, yeq + 32, "A  =", fs=17, anchor="end", weight="700"))
    qrows = [["1/√2", "1/√2"], ["1/√2", "−1/√2"]]
    lrows = [["3", "0"], ["0", "1"]]

    def place(rows, cell_w, label):
        nonlocal x
        m, w = _matrix(x, yeq, rows, cell_w=cell_w)
        P.append(m)
        P.append(_text(x + w / 2, yeq - 8, label, fs=14, anchor="middle",
                       weight="600", fill="#1657b8"))
        x += w + 14
    place(qrows, 58, "Q")
    place(lrows, 40, "Λ")
    place(qrows, 58, "Qᵀ")

    P.append(_text(_W / 2, 380,
                   "Multiplying back gives Q Λ Qᵀ = [[2, 1], [1, 2]] = A.  "
                   "(Here Q is symmetric, so Qᵀ = Q.)",
                   fs=13.5, anchor="middle", weight="600", fill="#2c7a38"))
    P.append(_text(_W / 2, 412,
                   "Geometrically: a symmetric matrix stretches space along "
                   "orthogonal eigen-directions, by factors λᵢ — no rotation "
                   "of the axes, only scaling.",
                   fs=13, anchor="middle", fill="#3a4250"))

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'viewBox="0 0 {_W} {_H}" width="{_W}" height="{_H}">'
           + "".join(P) + "</svg>")

    narration = [
        {"speak": "The spectral theorem says every real symmetric matrix can "
                  "be diagonalised by an orthogonal change of basis: A equals "
                  "Q times Lambda times Q transpose."},
        {"speak": "Q is orthogonal, meaning its columns are orthonormal "
                  "eigenvectors of A, and Lambda is diagonal, holding the "
                  "eigenvalues."},
        {"speak": "Take the symmetric matrix with twos on the diagonal and "
                  "ones off it. Solving the characteristic equation gives "
                  "eigenvalues three and one."},
        {"speak": "Their eigenvectors, one one and one minus one, are "
                  "orthogonal; normalised by root two they become the columns "
                  "of Q."},
        {"speak": "Reassembling Q, Lambda and Q transpose multiplies back to "
                  "exactly the original matrix, confirming the decomposition."},
        {"speak": "Geometrically the matrix simply stretches space along those "
                  "two perpendicular eigen-directions by three and by one, "
                  "with no twisting of the axes."},
    ]
    return svg, narration


def is_spectral_prompt(prompt: str) -> bool:
    p = (prompt or "").lower()
    if "spectral theorem" in p or "spectral decomposition" in p:
        return True
    if "eigendecomposition" in p and ("symmetric" in p or "spectral" in p):
        return True
    return False


async def generate_spectral_svg(
    prompt: str = "", *, api_key: str = "", base_url: str = "",
    model: str = "",
) -> tuple[str, list[dict]]:
    return render_spectral_theorem()
