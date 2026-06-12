"""Deterministic renderer for a Bayes' theorem probability tree.

"Explain Bayes theorem with a tree diagram" on the LLM-emitted-DOT path
produced an awkward graph (orphan/floating nodes, branch labels that did
not line up with their edges).  A probability tree has one fixed shape,
so we draw it correct-by-construction: a prior split on B, each branch
split on A by the conditionals, joint probabilities at the leaves, and
Bayes' theorem assembled from them.  A small worked example with
arithmetic checked in Python makes the posterior concrete.
"""
from __future__ import annotations

import html as _html
from typing import Any

_W, _H = 940, 640


def _text(x: float, y: float, s: str, *, fs: int = 14,
          anchor: str = "start", weight: str = "normal",
          fill: str = "#1a1d24") -> str:
    return (f'<text x="{x:.0f}" y="{y:.0f}" font-size="{fs}" '
            f'text-anchor="{anchor}" font-weight="{weight}" '
            f'fill="{fill}">{_html.escape(s)}</text>')


def _node(cx: float, cy: float, label: str, *, fill: str,
          stroke: str) -> str:
    return (f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="18" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="2"/>'
            + _text(cx, cy + 5, label, fs=15, anchor="middle", weight="700",
                    fill=stroke))


def _branch(x1: float, y1: float, x2: float, y2: float) -> str:
    # Start/end pulled in to the node radius so the line touches the rim.
    import math
    dx, dy = x2 - x1, y2 - y1
    d = math.hypot(dx, dy) or 1.0
    ux, uy = dx / d, dy / d
    sx, sy = x1 + ux * 18, y1 + uy * 18
    ex, ey = x2 - ux * 18, y2 - uy * 18
    return (f'<line x1="{sx:.0f}" y1="{sy:.0f}" x2="{ex:.0f}" y2="{ey:.0f}" '
            f'stroke="#9aa3af" stroke-width="1.6"/>')


def render_bayes_tree() -> tuple[str, list[dict]]:
    """Fully deterministic, no LLM.  Canonical worked example; all
    arithmetic asserted before drawing."""
    # Prior + conditionals (the only free numbers; everything else derived).
    pB = 0.3
    pA_B = 0.8           # P(A | B)
    pA_nB = 0.1          # P(A | ¬B)
    pnB = round(1 - pB, 10)
    pnA_B = round(1 - pA_B, 10)
    pnA_nB = round(1 - pA_nB, 10)
    # Joint leaves = path products.
    jAB = round(pB * pA_B, 10)        # P(A ∩ B)
    jnAB = round(pB * pnA_B, 10)      # P(¬A ∩ B)
    jAnB = round(pnB * pA_nB, 10)     # P(A ∩ ¬B)
    jnAnB = round(pnB * pnA_nB, 10)   # P(¬A ∩ ¬B)
    pA = round(jAB + jAnB, 10)        # total prob of A
    posterior = round(jAB / pA, 4)    # P(B | A) by Bayes
    assert abs((jAB + jnAB + jAnB + jnAnB) - 1.0) < 1e-9, "leaves must sum to 1"

    P: list[str] = []
    P.append(_text(_W / 2, 40, "Bayes' Theorem via a Probability Tree",
                   fs=22, anchor="middle", weight="700"))

    # Coordinates.  The tree is kept in the upper ~70% so the Bayes
    # formula below it sits clear of the bottom edge (headless-Chrome
    # screenshots clip the last ~10% of the canvas).
    root = (70, 255)
    nB = (300, 150)
    nnB = (300, 360)
    cBA = (560, 95)
    cBnA = (560, 205)
    cnBA = (560, 315)
    cnBnA = (560, 425)

    # Branches.
    for a, b in ((root, nB), (root, nnB), (nB, cBA), (nB, cBnA),
                 (nnB, cnBA), (nnB, cnBnA)):
        P.append(_branch(a[0], a[1], b[0], b[1]))

    # Branch probability labels, sitting just off the midpoint.
    def _blabel(a, b, s, dy=-6):
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        P.append(_text(mx, my + dy, s, fs=12, anchor="middle", fill="#3a4250"))
    _blabel(root, nB, f"P(B) = {pB:g}")
    _blabel(root, nnB, f"P(¬B) = {pnB:g}")
    _blabel(nB, cBA, f"P(A|B) = {pA_B:g}")
    _blabel(nB, cBnA, f"P(¬A|B) = {pnA_B:g}")
    _blabel(nnB, cnBA, f"P(A|¬B) = {pA_nB:g}")
    _blabel(nnB, cnBnA, f"P(¬A|¬B) = {pnA_nB:g}")

    # Nodes.
    P.append(_node(*root, "Ω", fill="#eef2f7", stroke="#5a6470"))
    P.append(_node(*nB, "B", fill="#e7f0fb", stroke="#1f6fe0"))
    P.append(_node(*nnB, "¬B", fill="#fdeee7", stroke="#d9822b"))
    for c, lab, st in ((cBA, "A", "#1f6fe0"), (cBnA, "¬A", "#1f6fe0"),
                       (cnBA, "A", "#d9822b"), (cnBnA, "¬A", "#d9822b")):
        P.append(_node(c[0], c[1], lab, fill="#ffffff", stroke=st))

    # Leaf joint probabilities, to the right of each leaf node.
    for c, s, hl in ((cBA, f"P(A∩B) = {jAB:g}", True),
                     (cBnA, f"P(¬A∩B) = {jnAB:g}", False),
                     (cnBA, f"P(A∩¬B) = {jAnB:g}", True),
                     (cnBnA, f"P(¬A∩¬B) = {jnAnB:g}", False)):
        P.append(_text(c[0] + 28, c[1] + 5, s, fs=13,
                       weight="700" if hl else "normal",
                       fill="#1a1d24" if hl else "#5a6470"))
    P.append(_text(cBA[0] + 28, cBA[1] - 16,
                   "← the two A-leaves", fs=11, fill="#9aa3af"))

    # Bayes assembly + worked result, in the lower band (~80-85% of the
    # canvas) with a separator rule, well clear of the clip-prone edge.
    P.append(f'<line x1="60" y1="490" x2="{_W - 60}" y2="490" '
             f'stroke="#e2e6ec" stroke-width="1"/>')
    P.append(_text(_W / 2, 520,
                   "P(B|A) = P(A∩B) / P(A) = P(A|B)P(B) / "
                   "[ P(A|B)P(B) + P(A|¬B)P(¬B) ]",
                   fs=14, anchor="middle", weight="600"))
    P.append(_text(_W / 2, 548,
                   f"= {jAB:g} / ({jAB:g} + {jAnB:g}) = {jAB:g} / {pA:g} "
                   f"≈ {posterior:g}   (the prior P(B) = {pB:g} rises to "
                   f"{posterior:g} once A is observed)",
                   fs=13, anchor="middle", fill="#2c7a38"))

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'viewBox="0 0 {_W} {_H}" width="{_W}" height="{_H}">'
           + "".join(P) + "</svg>")

    narration = [
        {"speak": "Bayes' theorem reverses a conditional probability. We are "
                  "given how likely A is when B holds, and we want the reverse: "
                  "how likely B is once we have observed A."},
        {"speak": "The tree organises every joint outcome. The first split is "
                  "the prior on B; each branch then splits on A using the "
                  "conditional probabilities."},
        {"speak": "Multiplying the probabilities along a path gives the joint "
                  "probability at that leaf. The four leaves partition the "
                  "sample space, so they sum to one."},
        {"speak": "For the posterior probability of B given A, take the leaf "
                  "where both B and A hold and divide it by the total "
                  "probability of A, which adds the two leaves where A occurs."},
        {"speak": f"Here the posterior is {jAB:g} over {pA:g}, about "
                  f"{posterior:g}. It exceeds the prior of {pB:g} because "
                  "observing A is strong evidence for B."},
    ]
    return svg, narration


# ── routing ──────────────────────────────────────────────────────────
def is_bayes_tree_prompt(prompt: str) -> bool:
    p = (prompt or "").lower()
    if "probability tree" in p:
        return True
    if ("bayes" in p or "bayesian" in p) and (
            "tree" in p or "diagram" in p):
        return True
    return False


async def generate_bayes_tree_svg(
    prompt: str = "", *, api_key: str = "", base_url: str = "",
    model: str = "",
) -> tuple[str, list[dict]]:
    """Deterministic — no LLM call.  Signature mirrors the other routes."""
    return render_bayes_tree()
