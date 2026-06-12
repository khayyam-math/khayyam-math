"""Deterministic renderer for Euclid's proof that there are infinitely
many primes.

On the LLM-SVG path this prompt produced a thin illustration (often a
vague picture of reciprocal divergence) with no statement, no deduction
steps, and no QED, so it failed the 'proof' completeness rubric on every
retry. Euclid's proof has one fixed structure, so we render it
correct-by-construction: the statement, a proof by contradiction with
explicit deduction steps, the key construction N = p1*...*pn + 1, the
conclusion, and a worked example (including the subtle composite case).
"""
from __future__ import annotations

import html as _html
from typing import Any

_W, _H = 900, 580


def _text(x: float, y: float, s: str, *, fs: int = 14, anchor: str = "start",
          weight: str = "normal", fill: str = "#1a1d24") -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{fs}" '
            f'text-anchor="{anchor}" font-weight="{weight}" '
            f'fill="{fill}">{_html.escape(s)}</text>')


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


def render_infinitude_primes() -> tuple[str, list[dict]]:
    """Fully deterministic; no LLM call.  The worked example is checked:
    2*3*5 + 1 = 31 (prime) and 2*3*5*7*11*13 + 1 = 30031 = 59*509."""
    assert 2 * 3 * 5 + 1 == 31
    assert 2 * 3 * 5 * 7 * 11 * 13 + 1 == 30031 == 59 * 509

    P: list[str] = []
    P.append(_text(_W / 2, 42, "Euclid's Theorem: There Are Infinitely Many Primes",
                   fs=21, anchor="middle", weight="700"))

    # Statement band.
    P.append(f'<rect x="50" y="62" width="{_W - 100}" height="40" rx="6" '
             f'fill="#eef4fb" stroke="#1f6fe0"/>')
    P.append(_text(_W / 2, 87,
                   "Statement: no finite list of primes can contain them all "
                   "— the primes never run out.",
                   fs=14, anchor="middle", weight="600", fill="#1657b8"))

    P.append(_text(56, 138, "Proof (by contradiction):", fs=16, weight="700"))

    steps = [
        "Assume, for contradiction, that there are only finitely many "
        "primes, and list every one of them: p₁, p₂, …, pₙ.",
        "Let N = p₁ · p₂ · ⋯ · pₙ + 1, that is, one more than "
        "the product of all the primes on the list.",
        "Note that dividing N by any prime pᵢ on the list leaves "
        "remainder 1, so no pᵢ divides N.",
        "Therefore N is either prime itself, or it has a prime factor; and "
        "in either case that prime cannot be any of p₁ … pₙ.",
        "We have produced a prime outside the supposedly complete list — a "
        "contradiction.",
    ]
    # Worked example lives in a box in the otherwise-empty right column,
    # so it never collides with the proof or the clip-prone bottom edge.
    ex_x, ex_w = 596, 256
    P.append(f'<rect x="{ex_x}" y="160" width="{ex_w}" height="246" rx="8" '
             f'fill="#f6f8fa" stroke="#c7ced8"/>')
    P.append(_text(ex_x + 16, 186, "Worked example", fs=14, weight="700"))
    P.append(_text(ex_x + 16, 212, "Primes 2, 3, 5:", fs=12.5, weight="600",
                   fill="#1657b8"))
    P.append(_text(ex_x + 16, 232, "N = 2·3·5 + 1 = 31", fs=12.5))
    for k, ln in enumerate(_wrap("31 is prime — itself a prime not on the "
                                 "list.", 32)):
        P.append(_text(ex_x + 16, 252 + k * 18, ln, fs=11.5, fill="#3a4250"))
    P.append(f'<line x1="{ex_x + 14}" y1="296" x2="{ex_x + ex_w - 14}" '
             f'y2="296" stroke="#dfe4ea" stroke-width="1"/>')
    P.append(_text(ex_x + 16, 320, "Primes 2, 3, 5, 7, 11, 13:", fs=12.5,
                   weight="600", fill="#b03a3a"))
    P.append(_text(ex_x + 16, 340, "N = 30031 = 59 × 509", fs=12.5))
    for k, ln in enumerate(_wrap("composite — yet 59 and 509 are new primes "
                                 "not on the list.", 32)):
        P.append(_text(ex_x + 16, 360 + k * 18, ln, fs=11.5, fill="#3a4250"))

    y = 168
    lh = 21
    for i, s in enumerate(steps, 1):
        P.append(f'<circle cx="68" cy="{y - 4}" r="11" fill="#1f6fe0"/>')
        P.append(_text(68, y, str(i), fs=12, anchor="middle", weight="700",
                       fill="#ffffff"))
        lines = _wrap(s, 68)
        for j, ln in enumerate(lines):
            P.append(_text(90, y + j * lh, ln, fs=13.5))
        # highlight the construction line (step 2)
        if i == 2:
            P.append(_text(90, y + len(lines) * lh - 1,
                           "N ≡ 1 (mod pᵢ) for every i", fs=12,
                           fill="#b03a3a", weight="600"))
            y += lh
        y += len(lines) * lh + 14

    # Conclusion.
    P.append(_text(56, y + 6,
                   "∴ There are infinitely many primes.  ∎",
                   fs=16, weight="700", fill="#2c7a38"))

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'viewBox="0 0 {_W} {_H}" width="{_W}" height="{_H}">'
           + "".join(P) + "</svg>")

    narration = [
        {"speak": "Euclid's theorem states that the primes never run out: no "
                  "finite list can contain all of them."},
        {"speak": "The proof is by contradiction. Assume there were only "
                  "finitely many primes, and write down every single one of "
                  "them as p one through p n."},
        {"speak": "Now construct a new number N by multiplying all of those "
                  "primes together and adding one."},
        {"speak": "Dividing N by any prime on the list always leaves a "
                  "remainder of one, so none of the listed primes divides N "
                  "evenly."},
        {"speak": "That means N is either prime itself, or it is built from a "
                  "prime factor that was not on our list. Either way a new "
                  "prime exists."},
        {"speak": "This contradicts the assumption that the list was complete. "
                  "Hence there must be infinitely many primes."},
        {"speak": "The example shows the subtle point: the product plus one is "
                  "not always prime, but when it is composite its factors are "
                  "still primes absent from the original list."},
    ]
    return svg, narration


def is_infinitude_primes_prompt(prompt: str) -> bool:
    p = (prompt or "").lower()
    if "prime" not in p:
        return False
    if "infinit" in p:                      # infinitely / infinitude / infinite
        return True
    if "euclid" in p and ("theorem" in p or "proof" in p):
        return True
    if "prime" in p and ("never end" in p or "never run out" in p):
        return True
    return False


async def generate_infinitude_primes_svg(
    prompt: str = "", *, api_key: str = "", base_url: str = "",
    model: str = "",
) -> tuple[str, list[dict]]:
    return render_infinitude_primes()
