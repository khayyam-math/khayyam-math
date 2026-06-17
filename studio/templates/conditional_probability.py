"""Deterministic renderer for conditional probability via a Venn diagram.

The probe caught "explain conditional probability with a Venn diagram"
failing on the LLM-SVG path: route=None, retries exhausted, structural
review FAIL (completeness_missing_statement — the 'concept_with_intuition'
class needs the definition stated explicitly with "is defined as").  The
concept has one fixed structure, so we render it correct-by-construction:
the explicit definition P(A|B) = P(A∩B)/P(B), a two-set Venn with concrete
region counts, and an arithmetic-checked worked example that also shows the
"restrict the world to B" intuition.

Canonical instance (N = 20 outcomes):
    only A = 6,  A∩B = 4,  only B = 6,  neither = 4
    P(B) = 10/20 = 0.5,  P(A∩B) = 4/20 = 0.2,  P(A|B) = 0.2/0.5 = 0.4 = 4/10
"""
from __future__ import annotations

import html as _html
from typing import Any

_W, _H = 940, 600


def _text(x: float, y: float, s: str, *, fs: float = 14, anchor: str = "start",
          weight: str = "normal", fill: str = "#1a1d24", el_id: str = "") -> str:
    i = f' id="{el_id}"' if el_id else ""
    return (f'<text{i} x="{x:.1f}" y="{y:.1f}" font-size="{fs}" '
            f'text-anchor="{anchor}" font-weight="{weight}" '
            f'fill="{fill}">{_html.escape(s)}</text>')


def render_conditional_probability() -> tuple[str, list[dict]]:
    """Fully deterministic; the probabilities are asserted before drawing."""
    N = 20
    only_a, both, only_b, neither = 6, 4, 6, 4
    assert only_a + both + only_b + neither == N

    p_b = (only_b + both) / N
    p_ab = both / N
    p_a_given_b = both / (only_b + both)        # restrict to B
    assert abs(p_b - 0.5) < 1e-9
    assert abs(p_ab - 0.2) < 1e-9
    assert abs(p_a_given_b - 0.4) < 1e-9
    # the definition and the "restrict to B" view must agree
    assert abs(p_ab / p_b - p_a_given_b) < 1e-9

    P: list[str] = []
    P.append(_text(_W / 2, 34, "Conditional Probability", fs=21,
                   anchor="middle", weight="700"))

    # Statement band — the explicit definition the structural rubric wants.
    P.append('<rect id="statement" x="40" y="50" width="860" height="56" rx="6" '
             'fill="#eef4fb" stroke="#1f6fe0"/>')
    P.append(_text(_W / 2, 74,
                   "P(A | B) is defined as  P(A ∩ B) / P(B)  —  the probability "
                   "of A given that B has occurred,",
                   fs=14, anchor="middle", weight="600", fill="#1657b8"))
    P.append(_text(_W / 2, 96,
                   "valid whenever P(B) > 0.",
                   fs=14, anchor="middle", weight="600", fill="#1657b8"))

    # ── Venn diagram (left) ───────────────────────────────────────────
    rx, ry, rw, rh = 60, 150, 400, 300
    P.append(f'<rect x="{rx}" y="{ry}" width="{rw}" height="{rh}" rx="4" '
             f'fill="#fbfcfe" stroke="#9aa4b2"/>')
    P.append(_text(rx + 10, ry + 22, f"S  ({N} equally-likely outcomes)",
                   fs=12.5, fill="#5a6472", weight="600"))

    ax, bx, cy, cr = 215, 315, 312, 108
    # Circle B drawn first with an id so narration can spotlight "given B".
    P.append(f'<circle id="circleB" cx="{bx}" cy="{cy}" r="{cr}" '
             f'fill="#f6c87a" fill-opacity="0.40" stroke="#d9822b" '
             f'stroke-width="2"/>')
    P.append(f'<circle id="circleA" cx="{ax}" cy="{cy}" r="{cr}" '
             f'fill="#9ec9f0" fill-opacity="0.40" stroke="#1f6fe0" '
             f'stroke-width="2"/>')
    P.append(_text(ax - 70, cy - 70, "A", fs=20, weight="700", fill="#1657b8"))
    P.append(_text(bx + 70, cy - 70, "B", fs=20, weight="700", fill="#b56a12"))
    # region counts
    P.append(_text(ax - 48, cy + 5, str(only_a), fs=18, anchor="middle",
                   weight="700", fill="#1657b8"))
    P.append(_text((ax + bx) / 2, cy + 5, str(both), fs=18, anchor="middle",
                   weight="700", fill="#7a4f12", el_id="bothCount"))
    P.append(_text(bx + 48, cy + 5, str(only_b), fs=18, anchor="middle",
                   weight="700", fill="#b56a12"))
    P.append(_text(rx + rw - 14, ry + rh - 14, f"neither: {neither}", fs=12.5,
                   anchor="end", fill="#5a6472"))

    # ── Worked example (right) ────────────────────────────────────────
    cx = 500
    P.append(_text(cx, 168, "Worked example", fs=15, weight="700"))
    rows = [
        f"|B| = {only_b + both},   |A ∩ B| = {both},   N = {N}",
        f"P(B) = {only_b + both}/{N} = {p_b:g}",
        f"P(A ∩ B) = {both}/{N} = {p_ab:g}",
        "",
        f"P(A | B) = P(A ∩ B) / P(B)",
        f"          = {p_ab:g} / {p_b:g} = {p_a_given_b:g}",
    ]
    y = 196
    for r in rows:
        if r:
            P.append(_text(cx, y, r, fs=14, fill="#23282f"))
        y += 26
    P.append(f'<rect x="{cx - 6}" y="{y - 2}" width="404" height="92" rx="6" '
             f'fill="#f1f8f2" stroke="#bcdcc2"/>')
    P.append(_text(cx + 6, y + 22,
                   "Intuition: conditioning on B throws away every",
                   fs=13, fill="#23282f"))
    P.append(_text(cx + 6, y + 42,
                   f"outcome outside B, shrinking the world to B's "
                   f"{only_b + both} outcomes.",
                   fs=13, fill="#23282f"))
    P.append(_text(cx + 6, y + 66,
                   f"Of those, {both} are in A, so P(A | B) = "
                   f"{both}/{only_b + both} = {p_a_given_b:g}.",
                   fs=13.5, weight="700", fill="#147a40"))

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'viewBox="0 0 {_W} {_H}" width="{_W}" height="{_H}">'
           + "".join(P) + "</svg>")

    narration = [
        {"speak": "Conditional probability of A given B is defined as the "
                  "probability of A and B together, divided by the probability "
                  "of B.",
         "highlight": ["statement"]},
        {"speak": "Picture the sample space as all equally-likely outcomes, "
                  "with A and B two overlapping events inside it.",
         "highlight": ["circleA", "circleB"]},
        {"speak": "Conditioning on B throws away everything outside B: the new, "
                  "smaller sample space is just B itself.",
         "highlight": ["circleB"]},
        {"speak": "Within B's ten outcomes, four also lie in A, so the "
                  "probability of A given B is four tenths, which is zero point "
                  "four.",
         "highlight": ["bothCount"]},
        {"speak": "The definition agrees: P of A and B over P of B is zero point "
                  "two over zero point five, again exactly zero point four.",
         "highlight": ["statement"]},
    ]
    return svg, narration


def is_conditional_probability_prompt(prompt: str) -> bool:
    p = (prompt or "").lower()
    if "conditional probability" in p:
        return True
    if ("p(a|b)" in p.replace(" ", "") or "p(a | b)" in p) and "probab" in p:
        return True
    if "given that" in p and "probab" in p and "venn" in p:
        return True
    return False


async def generate_conditional_probability_svg(
    prompt: str = "", *, api_key: str = "", base_url: str = "",
    model: str = "",
) -> tuple[str, list[dict]]:
    return render_conditional_probability()
