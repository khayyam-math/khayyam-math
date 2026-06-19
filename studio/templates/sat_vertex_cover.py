"""Deterministic renderer for the 3SAT ≤ₚ Vertex-Cover reduction, drawn as
the actual gadget GRAPH with vertices.

A user asked "reduce 3SAT to vertex cover" and "show all steps of reduction
on the vertices of a graph" and got the generic number-free reduction
schematic (and wall-of-text narration) instead of the real gadget graph.
The construction is concrete and small, so we draw it correct-by-
construction: a variable-gadget edge per variable, a clause-gadget triangle
per clause, the literal-to-gadget connecting edges, and a size-k vertex
cover (k = n + 2m) derived from a satisfying assignment — with the cover's
correctness (it covers every edge) asserted in Python before drawing.

Canonical instance:
    F = (x₁ ∨ ¬x₂ ∨ x₃) ∧ (¬x₁ ∨ x₂ ∨ x₄)        n = 4 vars, m = 2 clauses
    k = n + 2m = 4 + 4 = 8
    satisfying assignment x₁=x₂=x₃=x₄ = true
"""
from __future__ import annotations

import html as _html
from typing import Any

_W, _H = 1020, 700

_SUB = "₀₁₂₃₄₅₆₇₈₉"


def _lit(var: int, neg: bool) -> str:
    return ("¬x" if neg else "x") + _SUB[var]


def _text(x: float, y: float, s: str, *, fs: float = 14, anchor: str = "start",
          weight: str = "normal", fill: str = "#1a1d24", el_id: str = "") -> str:
    i = f' id="{el_id}"' if el_id else ""
    return (f'<text{i} x="{x:.1f}" y="{y:.1f}" font-size="{fs}" '
            f'text-anchor="{anchor}" font-weight="{weight}" '
            f'fill="{fill}">{_html.escape(s)}</text>')


def render_sat_to_vertex_cover() -> tuple[str, list[dict]]:
    """Fully deterministic; the reduction + vertex cover are asserted."""
    n_vars, m_clauses = 4, 2
    # clauses as lists of (var, negated)
    clauses = [
        [(1, False), (2, True), (3, False)],    # (x1 ∨ ¬x2 ∨ x3)
        [(1, True), (2, False), (4, False)],    # (¬x1 ∨ x2 ∨ x4)
    ]
    k = n_vars + 2 * m_clauses

    # ── vertex ids ────────────────────────────────────────────────────
    # variable-gadget vertices: ("v", var, negated)
    # clause-gadget vertices:   ("c", clause_idx, pos)
    edges: list[tuple] = []
    for v in range(1, n_vars + 1):
        edges.append((("v", v, False), ("v", v, True)))          # var edge
    for ci, cl in enumerate(clauses):
        cvs = [("c", ci, p) for p in range(3)]
        edges += [(cvs[0], cvs[1]), (cvs[0], cvs[2]), (cvs[1], cvs[2])]  # triangle
        for p, (var, neg) in enumerate(cl):
            edges.append((("c", ci, p), ("v", var, neg)))        # connecting

    # ── a satisfying assignment → vertex cover of size k ─────────────
    assign = {1: True, 2: True, 3: True, 4: True}
    cover: set = set()
    # variable gadgets: take the TRUE literal of each variable
    for v in range(1, n_vars + 1):
        cover.add(("v", v, not assign[v]))   # assign True -> positive vertex (neg=False)
    # clause triangles: leave out ONE satisfied literal (covered via its
    # variable gadget), take the other two.
    leave_out = {0: 0, 1: 1}                  # C1 drop x1, C2 drop x2 (both true)
    for ci, cl in enumerate(clauses):
        for p in range(3):
            if p != leave_out[ci]:
                cover.add(("c", ci, p))

    # ── assert the reduction is correct ──────────────────────────────
    assert len(cover) == k, f"cover size {len(cover)} != k {k}"
    for a, b in edges:
        assert a in cover or b in cover, f"edge {a}-{b} not covered"
    # the dropped clause literal must be true under the assignment
    for ci, p in leave_out.items():
        var, neg = clauses[ci][p]
        assert assign[var] != neg, "dropped literal is not satisfied"

    # ── positions ────────────────────────────────────────────────────
    pos: dict = {}
    gx = {1: 170, 2: 400, 3: 630, 4: 860}
    for v in range(1, n_vars + 1):
        pos[("v", v, False)] = (gx[v] - 30, 132)
        pos[("v", v, True)] = (gx[v] + 30, 132)
    tri = {0: (300, 470), 1: (720, 470)}
    for ci, (tx, ty) in tri.items():
        pos[("c", ci, 0)] = (tx, ty)            # apex
        pos[("c", ci, 1)] = (tx - 64, ty + 110)
        pos[("c", ci, 2)] = (tx + 64, ty + 110)

    def label(vid) -> str:
        if vid[0] == "v":
            return _lit(vid[1], vid[2])
        var, neg = clauses[vid[1]][vid[2]]
        return _lit(var, neg)

    P: list[str] = []
    P.append(_text(_W / 2, 30, "Reducing 3SAT to Vertex Cover", fs=21,
                   anchor="middle", weight="700"))
    P.append(_text(_W / 2, 56,
                   "F = (x₁ ∨ ¬x₂ ∨ x₃) ∧ (¬x₁ ∨ x₂ ∨ x₄)        "
                   "k = n + 2m = 4 + 4 = 8", fs=14, anchor="middle",
                   weight="600", fill="#1657b8", el_id="statement"))

    # connecting edges (dotted, behind), then gadget edges, then nodes.
    conn = []
    gad = []
    for a, b in edges:
        (x1, y1), (x2, y2) = pos[a], pos[b]
        is_conn = (a[0] == "c" and b[0] == "v") or (a[0] == "v" and b[0] == "c")
        line = (f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" '
                f'y2="{y2:.1f}" ')
        if is_conn:
            conn.append(line + 'stroke="#b8c0cc" stroke-width="1.3" '
                        'stroke-dasharray="4 3"/>')
        else:
            gad.append(line + 'stroke="#5a6472" stroke-width="2"/>')
    P.append('<g id="connect">' + "".join(conn) + '</g>')
    P.append('<g id="gadgets">' + "".join(gad) + '</g>')

    # group labels
    P.append(_text(_W / 2, 100, "Variable gadgets (one edge per variable)",
                   fs=12.5, anchor="middle", weight="600", fill="#5a6472"))
    P.append(_text(300, 445, "clause C₁", fs=12, anchor="middle",
                   weight="600", fill="#5a6472"))
    P.append(_text(720, 445, "clause C₂", fs=12, anchor="middle",
                   weight="600", fill="#5a6472"))

    node_svg = []
    for vid, (x, y) in pos.items():
        incover = vid in cover
        fill = "#1f9d55" if incover else "#ffffff"
        tcol = "#ffffff" if incover else "#1a1d24"
        node_svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="19" '
                        f'fill="{fill}" stroke="#1a1d24" stroke-width="1.6"/>')
        node_svg.append(_text(x, y + 4, label(vid), fs=11.5, anchor="middle",
                              weight="700", fill=tcol))
    P.append('<g id="cover">' + "".join(node_svg) + '</g>')

    # ── recipe / legend band ─────────────────────────────────────────
    P.append('<rect x="40" y="612" width="940" height="76" rx="6" '
             'fill="#f4f7fb" stroke="#c9d4e2"/>')
    P.append(_text(56, 636,
                   "Construction: each variable xᵢ → one edge (xᵢ, ¬xᵢ); each "
                   "clause → a triangle on its 3 literals; connect every "
                   "clause-literal to its matching variable vertex.",
                   fs=12.5, fill="#23282f"))
    P.append(_text(56, 658,
                   "F is satisfiable  ⇔  the graph has a vertex cover of size "
                   "k = n + 2m = 8.  Green = the cover for x₁=x₂=x₃=x₄=true: "
                   "the true literal in each gadget,",
                   fs=12.5, fill="#23282f"))
    P.append(_text(56, 678,
                   "and the two non-satisfying vertices of each clause "
                   "triangle.  Those 8 vertices touch every one of the "
                   f"{len(edges)} edges.",
                   fs=12.5, fill="#147a40", weight="600"))

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'viewBox="0 0 {_W} {_H}" width="{_W}" height="{_H}">'
           + "".join(P) + "</svg>")

    narration = [
        {"speak": "This reduction turns a 3SAT formula into a graph whose "
                  "vertex covers correspond exactly to satisfying "
                  "assignments.",
         "highlight": ["statement"]},
        {"speak": "Each variable becomes a gadget: two vertices, the variable "
                  "and its negation, joined by an edge. Covering that edge "
                  "forces a true-or-false choice.",
         "highlight": ["gadgets"]},
        {"speak": "Each clause becomes a triangle on its three literals. A "
                  "triangle needs at least two of its vertices in any cover, "
                  "so exactly one literal per clause is left out.",
         "highlight": ["gadgets"]},
        {"speak": "Dashed edges tie every clause-literal to the matching "
                  "variable vertex, so the choices in the gadgets and the "
                  "triangles must agree.",
         "highlight": ["connect"]},
        {"speak": "Setting k to n plus two m — here eight — the formula is "
                  "satisfiable exactly when a vertex cover of that size "
                  "exists. The green vertices are one such cover.",
         "highlight": ["cover"]},
    ]
    return svg, narration


def is_sat_vertex_cover_prompt(prompt: str) -> bool:
    p = (prompt or "").lower()
    if "vertex cover" not in p:
        return False
    has_sat = ("3sat" in p or "3-sat" in p or "3 sat" in p
               or "sat " in f" {p} " or "satisfiability" in p)
    if not has_sat:
        return False
    return any(v in p for v in ("reduc", "reduce", "≤p", "<=p", "to vertex"))


async def generate_sat_vertex_cover_svg(
    prompt: str = "", *, api_key: str = "", base_url: str = "",
    model: str = "",
) -> tuple[str, list[dict]]:
    return render_sat_to_vertex_cover()
