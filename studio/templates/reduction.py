"""Deterministic renderer for complexity reductions.

A "reduce X to Y" figure has a fixed shape: a source instance, a
polynomial-time construction, a target instance, and a yes-iff-yes
equivalence.  On the LLM-SVG path this class produced colliding /
duplicated labels and, worse, WRONG arithmetic (an example whose two
"equal" halves did not actually sum to the same value).  Rendering it
correct-by-construction fixes both at once: coordinates are computed so
nothing overlaps, and the example is verified in Python before it is
drawn.

Two tiers:
  • Subset Sum ≤p Partition — the canonical pair — is rendered FULLY
    deterministically (no LLM) from a hard-coded, arithmetic-checked
    example.
  • Any other recognised "reduce A to B" prompt gets a deterministic
    schematic (source ──f──▶ target + a generic equivalence line) with
    NO invented numbers, so it is always clean even when we don't have a
    concrete construction for that pair.
"""
from __future__ import annotations

import html as _html
import re
from typing import Any, Optional

_W, _H = 900, 560


def _text(x: float, y: float, s: str, *, fs: int = 14,
          anchor: str = "start", weight: str = "normal",
          fill: str = "#1a1d24") -> str:
    return (f'<text x="{x:.0f}" y="{y:.0f}" font-size="{fs}" '
            f'text-anchor="{anchor}" font-weight="{weight}" '
            f'fill="{fill}">{_html.escape(s)}</text>')


def _wrap(text: str, max_chars: int) -> list[str]:
    words = (text or "").split()
    out: list[str] = []
    cur = ""
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


_ARROW_DEF = (
    '<defs><marker id="red_arr" markerWidth="10" markerHeight="10" '
    'refX="8" refY="3" orient="auto">'
    '<path d="M0,0 L0,6 L9,3 z" fill="#1a1d24"/></marker></defs>'
)


# ── Tier 1: the canonical Subset Sum ≤p Partition figure ─────────────
def render_subset_sum_to_partition() -> tuple[str, list[dict]]:
    """Fully deterministic, no LLM.  Uses a fixed example chosen so the
    added element is distinct from every original value (no confusing
    repeats) and both halves verifiably sum to the same total."""
    # Canonical instance — all arithmetic checked below, never by an LLM.
    S = [2, 3, 4, 8]
    T = 5
    sigma = sum(S)            # 17
    a = abs(sigma - 2 * T)    # |17 - 10| = 7  (the one added element)
    Sp = S + [a]              # {2,3,4,8,7}
    total = sum(Sp)           # 24
    half = total // 2         # 12
    # The subset of S that hits the target, plus the added element, is one
    # half; the rest of S is the other half.
    hit = [2, 3]              # subset of S summing to T = 5
    s1 = sorted(hit + [a])    # {2,3,7}  -> 12
    s2 = sorted(x for x in S if x not in hit)  # {4,8} -> 12
    assert sum(hit) == T and sum(s1) == half and sum(s2) == half \
        and total % 2 == 0, "reduction example is inconsistent"

    def _set(xs: list[int]) -> str:
        return "{" + ", ".join(str(v) for v in xs) + "}"

    P: list[str] = [_ARROW_DEF]
    # Title + one-line intent
    P.append(_text(_W / 2, 40, "Reduction:  Subset Sum  ≤p  Partition",
                   fs=22, anchor="middle", weight="700"))
    P.append(_text(_W / 2, 70,
                   "Transform a Subset Sum instance into a Partition instance.",
                   fs=14, anchor="middle", fill="#5a6470"))

    # Left column — the source instance
    P.append(_text(40, 150, "Subset Sum instance", fs=15, weight="700",
                   fill="#1657b8"))
    P.append(_text(40, 176, f"S = {_set(S)}", fs=14))
    P.append(_text(40, 198, f"Target  T = {T}", fs=14))
    P.append(_text(40, 220, f"total(S) = {sigma}", fs=13, fill="#5a6470"))

    # Centre — the construction
    ax, bx, by, bw, bh = 350, 560, 132, 150, 66
    for x0, top, bot, stroke, bg in (
            (ax, "Subset Sum", "(S, T)", "#1f6fe0", "#eef2f7"),
            (bx, "Partition", "(S′)", "#d9822b", "#fdf3e6")):
        P.append(f'<rect x="{x0}" y="{by}" width="{bw}" height="{bh}" rx="8" '
                 f'fill="{bg}" stroke="{stroke}"/>')
        P.append(_text(x0 + bw / 2, by + 28, top, fs=15, anchor="middle",
                       weight="600"))
        P.append(_text(x0 + bw / 2, by + 50, bot, fs=13, anchor="middle",
                       fill="#5a6470"))
    mx0, mx1, my = ax + bw + 4, bx - 4, by + bh / 2
    P.append(f'<line x1="{mx0}" y1="{my}" x2="{mx1 - 12}" y2="{my}" '
             f'stroke="#1a1d24" stroke-width="2" marker-end="url(#red_arr)"/>')
    P.append(_text((mx0 + mx1) / 2, my - 14,
                   f"add a = |{sigma}− 2·{T}| = {a}", fs=12,
                   anchor="middle"))

    # Right column — the target instance
    P.append(_text(_W - 40, 150, "Partition instance", fs=15, weight="700",
                   anchor="end", fill="#d9822b"))
    P.append(_text(_W - 40, 176, f"S′ = {_set(sorted(Sp))}", fs=14,
                   anchor="end"))
    P.append(_text(_W - 40, 198, f"total = {total}", fs=14, anchor="end"))
    P.append(_text(_W - 40, 220, f"each half = {half}", fs=14, anchor="end"))

    # Equivalence headline
    P.append(_text(_W / 2, 300,
                   "A subset of S sums to T   ⟺   S′ splits into "
                   "two equal halves",
                   fs=15, anchor="middle", weight="600"))

    # The two halves as coloured bars, labels stacked cleanly BELOW each bar
    bar_y, bar_w, bar_h = 330, 240, 46
    gx, rx = 180, 480
    P.append(f'<rect x="{gx}" y="{bar_y}" width="{bar_w}" height="{bar_h}" '
             f'rx="6" fill="#d6efd8" stroke="#3a9d4a"/>')
    P.append(f'<rect x="{rx}" y="{bar_y}" width="{bar_w}" height="{bar_h}" '
             f'rx="6" fill="#f7d9d9" stroke="#cc4b4b"/>')
    P.append(_text(gx + bar_w / 2, bar_y + 28,
                   f"S1 = {_set(s1)}", fs=15, anchor="middle",
                   weight="600", fill="#2c7a38"))
    P.append(_text(rx + bar_w / 2, bar_y + 28,
                   f"S2 = {_set(s2)}", fs=15, anchor="middle",
                   weight="600", fill="#b03a3a"))
    P.append(_text(gx + bar_w / 2, bar_y + bar_h + 24,
                   f"sum = {half}  (includes the added {a})", fs=12,
                   anchor="middle", fill="#2c7a38"))
    P.append(_text(rx + bar_w / 2, bar_y + bar_h + 24,
                   f"sum = {half}", fs=12, anchor="middle", fill="#b03a3a"))

    # Conclusion
    P.append(_text(_W / 2, _H - 36,
                   "The construction runs in polynomial time, so "
                   "Subset Sum ≤p Partition.  ∎",
                   fs=15, anchor="middle", weight="600"))

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'viewBox="0 0 {_W} {_H}" width="{_W}" height="{_H}">'
           + "".join(P) + "</svg>")

    narration = [
        {"speak": "Partition asks whether a set of numbers can be split into "
                  "two groups of equal sum. It is the balanced special case "
                  "of Subset Sum, and this reduction turns any Subset Sum "
                  "question into a Partition one."},
        {"speak": "Begin with a Subset Sum instance: the set S and a target T. "
                  "Let sigma be the total of all elements in S."},
        {"speak": "Add exactly one new element whose value is the distance "
                  "between sigma and twice the target. This forces the total "
                  "to become even and each equal half to equal sigma minus "
                  "the target."},
        {"speak": "Now the enlarged set splits into two equal halves precisely "
                  "when the original set had a subset reaching the target, "
                  "because that subset together with the new element forms one "
                  "half."},
        {"speak": "The new element is computed with a single subtraction, so "
                  "the whole construction is polynomial. Subset Sum therefore "
                  "reduces to Partition."},
    ]
    return svg, narration


# ── Tier 2: generic two-problem reduction schematic ──────────────────
def render_generic_reduction(src: str, dst: str) -> tuple[str, list[dict]]:
    """A clean, number-free schematic for a recognised "reduce A to B"
    pair we don't have a concrete construction for."""
    src = src.strip().title()
    dst = dst.strip().title()
    P: list[str] = [_ARROW_DEF]
    P.append(_text(_W / 2, 48, f"Reduction:  {src}  ≤p  {dst}",
                   fs=22, anchor="middle", weight="700"))
    P.append(_text(_W / 2, 82,
                   f"Every {src} instance maps to a {dst} instance in "
                   f"polynomial time.", fs=14, anchor="middle", fill="#5a6470"))

    bw, bh, by = 200, 80, 200
    ax, bx = 110, _W - 110 - bw
    for x0, top, sub, stroke, bg in (
            (ax, f"{src} instance", "input", "#1f6fe0", "#eef2f7"),
            (bx, f"{dst} instance", "f(input)", "#d9822b", "#fdf3e6")):
        P.append(f'<rect x="{x0}" y="{by}" width="{bw}" height="{bh}" rx="10" '
                 f'fill="{bg}" stroke="{stroke}"/>')
        P.append(_text(x0 + bw / 2, by + 38, top, fs=15, anchor="middle",
                       weight="600"))
        P.append(_text(x0 + bw / 2, by + 60, sub, fs=12, anchor="middle",
                       fill="#5a6470"))
    mx0, mx1, my = ax + bw + 6, bx - 6, by + bh / 2
    P.append(f'<line x1="{mx0}" y1="{my}" x2="{mx1 - 12}" y2="{my}" '
             f'stroke="#1a1d24" stroke-width="2" marker-end="url(#red_arr)"/>')
    P.append(_text((mx0 + mx1) / 2, my - 14, "poly-time construction f",
                   fs=13, anchor="middle"))

    eq = (f"the {src} instance is a yes-instance   ⟺   "
          f"f(instance) is a yes-instance for {dst}")
    for i, ln in enumerate(_wrap(eq, 88)):
        P.append(_text(_W / 2, 340 + i * 24, ln, fs=15, anchor="middle",
                       weight="600"))
    P.append(_text(_W / 2, _H - 40,
                   f"A poly-time, answer-preserving map shows {src} "
                   f"≤p {dst}.  ∎",
                   fs=14, anchor="middle", fill="#5a6470"))

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'viewBox="0 0 {_W} {_H}" width="{_W}" height="{_H}">'
           + "".join(P) + "</svg>")
    narration = [
        {"speak": f"To reduce {src} to {dst} we give a polynomial-time map "
                  f"that turns any {src} instance into a {dst} instance."},
        {"speak": "The map must preserve the answer: yes-instances go to "
                  "yes-instances and no-instances go to no-instances."},
        {"speak": f"Because the construction is polynomial and answer "
                  f"preserving, a solver for {dst} would solve {src}, so "
                  f"{src} is no harder than {dst}."},
    ]
    return svg, narration


# ── routing ──────────────────────────────────────────────────────────
_RED_VERBS = ("reduce", "reduction", "reduces to", "≤p", "<=p",
              "≤p")
_PROBLEMS = ("subset sum", "partition", "3-sat", "3sat", "sat",
             "vertex cover", "clique", "independent set", "hamiltonian",
             "hamilton", "tsp", "traveling salesman", "travelling salesman",
             "graph coloring", "graph colouring", "knapsack", "set cover",
             "dominating set", "max cut")


def is_reduction_prompt(prompt: str) -> bool:
    """A complexity reduction, not 'reduce a fraction': needs a reduction
    verb AND at least one named complexity problem."""
    p = (prompt or "").lower()
    if not any(v in p for v in _RED_VERBS):
        return False
    return any(prob in p for prob in _PROBLEMS)


def _is_subset_sum_partition(prompt: str) -> bool:
    p = (prompt or "").lower()
    return "subset sum" in p and "partition" in p


def _parse_pair(prompt: str) -> Optional[tuple[str, str]]:
    """Pull (source, target) out of 'reduce A to B' / 'reduction from A to
    B' / 'A reduces to B'.  Returns None if we can't identify two problems."""
    p = (prompt or "").lower()
    pats = (
        r"reduc\w*\s+(?:from\s+)?(.+?)\s+to\s+(.+?)[.?!]?$",
        r"(.+?)\s+reduces?\s+to\s+(.+?)[.?!]?$",
        r"(.+?)\s*≤p\s*(.+?)[.?!]?$",
        r"(.+?)\s*<=p\s*(.+?)[.?!]?$",
    )
    for pat in pats:
        m = re.search(pat, p)
        if not m:
            continue
        a, b = m.group(1).strip(), m.group(2).strip()
        # Keep only the trailing problem-ish phrase from each side.
        a = _clean_problem(a)
        b = _clean_problem(b)
        if a and b:
            return a, b
    return None


def _clean_problem(s: str) -> str:
    """Trim filler so 'show that the subset sum problem' -> 'subset sum'."""
    s = re.sub(r"^(show|prove|demonstrate|that|the|a|an|how|we can|you can|"
               r"problem of)\s+", "", s).strip()
    s = re.sub(r"\s+(problem|instance|is)\b.*$", "", s).strip()
    # Prefer an explicit known-problem name if present.
    for prob in _PROBLEMS:
        if prob in s:
            return prob
    return s


async def generate_reduction_svg(
    prompt: str, *, api_key: str = "", base_url: str = "", model: str = "",
) -> Optional[tuple[str, list[dict]]]:
    """Deterministic reduction figure.  No LLM call: the canonical pair is
    hard-coded and any other pair is drawn as a number-free schematic.
    Returns None when we can't identify a clean reduction (caller falls
    through to the rest of the cascade)."""
    if _is_subset_sum_partition(prompt):
        return render_subset_sum_to_partition()
    pair = _parse_pair(prompt)
    if pair:
        return render_generic_reduction(*pair)
    return None
