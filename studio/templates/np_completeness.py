"""Deterministic renderer for NP-completeness proofs.

This is the Phase-4 "renderer-first" pattern applied to the class that
was the worst offender on the LLM-SVG path (garbled side-columns, ~3
vision-review retries, ~90 s).  Every "prove X is NP-complete" proof
shares one fixed structure:

    X is NP-complete
      1. X ∈ NP            (a certificate is verifiable in poly time)
      2. X is NP-hard      (reduce a known NP-complete Y ≤p X)
         Y instance ──f──▶ X instance     (f runs in poly time)
         Y is a yes-instance  ⟺  f(Y) is a yes-instance
      ∴ X is NP-complete.

The renderer draws that structure correct-by-construction from a small
extracted spec, so layout is controlled (no overlap, consistent every
time).  The LLM only fills in the *content* fields, never coordinates.
"""
from __future__ import annotations

import html as _html
import json
import os
from typing import Any, Optional

_W, _H = 900, 640


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


def _text(x: float, y: float, s: str, *, fs: int = 14,
          anchor: str = "start", weight: str = "normal",
          fill: str = "#1a1d24") -> str:
    return (f'<text x="{x:.0f}" y="{y:.0f}" font-size="{fs}" '
            f'text-anchor="{anchor}" font-weight="{weight}" '
            f'fill="{fill}">{_html.escape(s)}</text>')


def _column(x: float, y0: float, lines: list[str], *, fs: int = 14,
            lh: int = 20, anchor: str = "start") -> tuple[str, float]:
    """Emit stacked lines; return (svg, next_y)."""
    parts = [_text(x, y0 + i * lh, ln, fs=fs, anchor=anchor)
             for i, ln in enumerate(lines)]
    return "".join(parts), y0 + len(lines) * lh


def render_np_completeness(spec: dict[str, Any]) -> tuple[str, list[dict]]:
    """Render the proof figure + narration from a spec dict:
        problem_x        — name of the problem being shown NP-complete
        definition_x     — one-sentence definition (decision-problem form)
        np_certificate   — what certificate is checked in poly time
        known_problem_y  — the known NP-complete problem reduced from
        reduction        — one/two sentences on the construction f
        equivalence      — the yes-instance ⟺ statement (optional)
    Missing fields fall back to sensible generic phrasing so the figure is
    always complete.
    """
    x = (spec.get("problem_x") or "the problem").strip()
    defn = (spec.get("definition_x") or
            f"Given an instance, decide whether {x} has a solution.").strip()
    cert = (spec.get("np_certificate") or
            "a proposed solution, checkable in polynomial time").strip()
    y = (spec.get("known_problem_y") or "3-SAT").strip()
    reduction = (spec.get("reduction") or
                 f"Map each instance of {y} to an instance of {x} with a "
                 f"polynomial-time construction f.").strip()
    equiv = (spec.get("equivalence") or
             f"the {y} instance is satisfiable  ⟺  the constructed "
             f"{x} instance is a yes-instance").strip()

    title = f"{x} is NP-Complete"
    P = []  # svg pieces

    # Title
    P.append(_text(_W / 2, 44, title, fs=22, anchor="middle", weight="700"))

    # Part 1 — in NP
    P.append(_text(40, 92, f"1.  {x} is in NP", fs=16, weight="700",
                   fill="#1657b8"))
    body1 = _wrap(f"Definition: {defn}", 88) + \
        _wrap(f"A certificate — {cert} — can be verified in polynomial "
              f"time, so {x} ∈ NP.", 88)
    s, _ = _column(40, 116, body1)
    P.append(s)

    # Part 2 — NP-hard heading
    P.append(_text(40, 210, f"2.  {x} is NP-hard:  reduce {y} ≤p {x}",
                   fs=16, weight="700", fill="#1657b8"))

    # Reduction diagram: two boxes + arrow
    box_y, box_h = 240, 64
    lx, rx, bw = 70, 480, 240
    for bx, label, sub in ((lx, y, "known NP-complete"),
                           (rx, f"{x} instance", "constructed by f")):
        P.append(f'<rect x="{bx}" y="{box_y}" width="{bw}" height="{box_h}" '
                 f'rx="8" fill="#eef2f7" stroke="#1f6fe0"/>')
        P.append(_text(bx + bw / 2, box_y + 28, label, fs=15,
                       anchor="middle", weight="600"))
        P.append(_text(bx + bw / 2, box_y + 48, sub, fs=11,
                       anchor="middle", fill="#5a6470"))
    # arrow between the boxes
    ax0, ax1, ay = lx + bw + 6, rx - 6, box_y + box_h / 2
    P.append(f'<line x1="{ax0}" y1="{ay}" x2="{ax1 - 12}" y2="{ay}" '
             f'stroke="#1a1d24" stroke-width="2" marker-end="url(#npc_arr)"/>')
    P.append(_text((ax0 + ax1) / 2, ay - 10,
                   "poly-time construction f", fs=12, anchor="middle"))
    P.append('<defs><marker id="npc_arr" markerWidth="10" markerHeight="10" '
             'refX="8" refY="3" orient="auto">'
             '<path d="M0,0 L0,6 L9,3 z" fill="#1a1d24"/></marker></defs>')

    # Equivalence line
    eq_lines = _wrap(equiv, 96)
    s, ny = _column(_W / 2, box_y + box_h + 36, eq_lines, anchor="middle")
    P.append(s)

    # Reduction prose
    body2 = _wrap(reduction, 96)
    s, ny = _column(40, ny + 22, body2)
    P.append(s)

    # Conclusion
    P.append(_text(_W / 2, _H - 36,
                   f"In NP and NP-hard  ⇒  {x} is NP-complete.  ∎",
                   fs=15, anchor="middle", weight="600"))

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'viewBox="0 0 {_W} {_H}" width="{_W}" height="{_H}">'
           + "".join(P) + "</svg>")

    narration = [
        {"speak": f"To prove {x} is NP-complete we establish two things: "
                  f"membership in NP, and NP-hardness."},
        {"speak": f"For NP membership, {cert} serves as a certificate that "
                  f"is verified in polynomial time."},
        {"speak": f"For NP-hardness we reduce the known NP-complete problem "
                  f"{y} to {x} by a polynomial-time construction f."},
        {"speak": f"The construction is sound because {equiv}."},
        {"speak": f"Being both in NP and NP-hard, {x} is NP-complete."},
    ]
    return svg, narration


# --- routing -------------------------------------------------------------
_NPC_KEYWORDS = (
    "np-complete", "np complete", "npcomplete", "np-completeness",
    "np completeness", "np-hard", "np hard",
)


def is_np_completeness_prompt(prompt: str) -> bool:
    p = (prompt or "").lower()
    if not any(k in p for k in _NPC_KEYWORDS):
        return False
    # Must be a *prove/show* request, not "what is NP-completeness".
    # (Don't match the incidental "is np" inside "what is np-completeness".)
    return any(v in p for v in ("prove", "show", "demonstrate", "reduce",
                                "reduction", "hardness"))


_EXTRACT_SYSTEM = (
    "You extract the content of an NP-completeness proof.  Return STRICT "
    "JSON with keys: problem_x (the problem shown NP-complete), "
    "definition_x (one-sentence decision-problem definition), "
    "np_certificate (what is checked in poly time), known_problem_y (a "
    "known NP-complete problem to reduce FROM — e.g. 3-SAT, Vertex Cover, "
    "Subset Sum), reduction (1-2 sentences on the construction), "
    "equivalence (the 'yes-instance iff yes-instance' statement).  Be "
    "mathematically correct and concise.  JSON only."
)


async def generate_np_completeness_svg(
    user_prompt: str, *, api_key: str, base_url: str, model: str,
) -> Optional[tuple[str, list[dict]]]:
    """Extract the proof spec via the LLM, then render deterministically.
    Returns None on extraction failure so the caller can fall through."""
    if not api_key:
        return None
    try:
        import httpx
        async with httpx.AsyncClient(timeout=40) as c:
            r = await c.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={"model": model or "gpt-4o-mini",
                      "temperature": 0,
                      "response_format": {"type": "json_object"},
                      "messages": [
                          {"role": "system", "content": _EXTRACT_SYSTEM},
                          {"role": "user", "content": user_prompt}]},
            )
        if r.status_code != 200:
            return None
        spec = json.loads(r.json()["choices"][0]["message"]["content"])
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(spec, dict) or not spec.get("problem_x"):
        return None
    return render_np_completeness(spec)
