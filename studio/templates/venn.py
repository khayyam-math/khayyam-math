"""Deterministic Venn-diagram template.

Two or three overlapping circles with every region positioned by hand,
so the circles always overlap correctly and region labels never drift
onto the wrong lens — the failure mode of the LLM-SVG path.

  venn_diagram(labels, regions=None, title="") -> (svg, narration)

``labels``   list of 2 or 3 set names.
``regions``  optional dict mapping region keys to short text:
             2-set: "a", "b", "ab"
             3-set: "a", "b", "c", "ab", "ac", "bc", "abc"
"""
from __future__ import annotations

from typing import List, Optional, Tuple


def _esc(s: object) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _circle(cx, cy, r, fill, stroke):
    return (f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" '
            f'fill="{fill}" fill-opacity="0.38" stroke="{stroke}" '
            f'stroke-width="2.4"/>')


def _label(x, y, text, size=15, weight="normal", fill="#222"):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" '
            f'text-anchor="middle" font-family="serif" '
            f'font-weight="{weight}" fill="{fill}">{_esc(text)}</text>')


def venn_diagram(labels: List[object],
                 regions: Optional[dict] = None,
                 title: str = "") -> Tuple[str, List[dict]]:
    labels = [str(x) for x in (labels or [])]
    if len(labels) not in (2, 3):
        raise ValueError("venn_diagram needs 2 or 3 set labels")
    regions = {str(k).lower(): str(v) for k, v in (regions or {}).items()}
    out: List[str] = []
    narration: List[dict] = []

    if len(labels) == 2:
        W, H = 760.0, 470.0
        cy = 250.0
        r = 150.0
        ax_, bx = 300.0, 460.0
        out.append(_circle(ax_, cy, r, "#3d6fb4", "#1a3a5c"))
        out.append(_circle(bx, cy, r, "#cc4125", "#7a2010"))
        out.append(_label(ax_ - r + 30, cy - r + 6, labels[0],
                          17, "bold", "#1a3a5c"))
        out.append(_label(bx + r - 30, cy - r + 6, labels[1],
                          17, "bold", "#7a2010"))
        spots = {"a": (ax_ - 64, cy), "b": (bx + 64, cy),
                 "ab": ((ax_ + bx) / 2, cy)}
        names = {"a": f"{labels[0]} only", "b": f"{labels[1]} only",
                 "ab": "both"}
    else:
        W, H = 760.0, 720.0
        cx = W / 2
        cyc = 320.0
        r = 158.0
        d = 96.0
        A = (cx, cyc - d)
        B = (cx - d * 0.866, cyc + d * 0.5)
        C = (cx + d * 0.866, cyc + d * 0.5)
        out.append(_circle(*A, r, "#3d6fb4", "#1a3a5c"))
        out.append(_circle(*B, r, "#cc4125", "#7a2010"))
        out.append(_circle(*C, r, "#6aa84f", "#2f5d22"))
        out.append(_label(A[0], A[1] - r - 14, labels[0],
                          18, "bold", "#1a3a5c"))
        out.append(_label(B[0] - r * 0.62, B[1] + r + 24, labels[1],
                          18, "bold", "#7a2010"))
        out.append(_label(C[0] + r * 0.62, C[1] + r + 24, labels[2],
                          18, "bold", "#2f5d22"))
        spots = {
            "a": (cx, cyc - d - 64),
            "b": (cx - d * 0.866 - 70, cyc + d * 0.5 + 70),
            "c": (cx + d * 0.866 + 70, cyc + d * 0.5 + 70),
            "ab": (cx - 66, cyc - 14),
            "ac": (cx + 66, cyc - 14),
            "bc": (cx, cyc + d + 30),
            "abc": (cx, cyc + 28),
        }
        names = {
            "a": f"{labels[0]} only", "b": f"{labels[1]} only",
            "c": f"{labels[2]} only",
            "ab": f"{labels[0]} ∩ {labels[1]}",
            "ac": f"{labels[0]} ∩ {labels[2]}",
            "bc": f"{labels[1]} ∩ {labels[2]}",
            "abc": "all three",
        }

    for key, (x, y) in spots.items():
        txt = regions.get(key) or ""
        if txt:
            out.append(_label(x, y, txt, 14, "normal", "#111"))
        else:
            out.append(_label(x, y, names[key], 12, "normal", "#555"))

    ttl = title or (
        f"Venn Diagram: {', '.join(labels)}")
    th = 46.0
    body = "".join(out)
    full = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {W:.0f} {H + th:.0f}" width="{W:.0f}" '
        f'height="{H + th:.0f}">',
        f'<rect width="{W:.0f}" height="{H + th:.0f}" fill="white"/>',
        f'<text id="title" x="{W / 2:.0f}" y="32" font-size="21" '
        f'text-anchor="middle" font-family="serif" font-weight="bold" '
        f'fill="#111">{_esc(ttl)}</text>',
        f'<g transform="translate(0 {th:.0f})">{body}</g>',
        "</svg>",
    ]
    narration = [{
        "speak": (f"This Venn diagram shows the sets "
                  f"{', '.join(labels)} and how they overlap."),
        "highlight": ["title"]},
        {"speak": ("Each circle is one set; where circles overlap, "
                   "elements belong to every set in that overlap."),
         "highlight": []}]
    if regions:
        narration.append({
            "speak": ("The labels mark what belongs in each distinct "
                      "region of the diagram."),
            "highlight": []})
    return "".join(full), narration
