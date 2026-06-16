"""Deterministic renderer for the Clique ≤ₚ Vertex-Cover reduction, drawn as
TWO REAL GRAPHS WITH NODES.

A user repeatedly asked to "reduce clique to vertex cover ... on a graph ...
I need nodes! two graphs with nodes!" and kept getting either wall-of-text
explanations or the generic number-free box-and-arrow reduction schematic.
The reduction is concrete and small, so we draw it correct-by-construction:
the input graph G with a clique highlighted on the left, and its complement
graph Ḡ with the corresponding vertex cover highlighted on the right, over
the SAME vertex layout so the correspondence is visible.

Canonical instance (n = 5):
    V = {1,2,3,4,5}
    E(G)  = {12, 13, 23, 34, 45}        clique S = {1,2,3},  k = 3
    E(Ḡ)  = {14, 15, 24, 25, 35}        vertex cover V∖S = {4,5},  n−k = 2

Everything below is asserted in Python before drawing: S is a clique in G,
Ḡ is exactly the complement of G, and V∖S covers every edge of Ḡ.
"""
from __future__ import annotations

import html as _html
import math
from itertools import combinations
from typing import Any

_W, _H = 960, 640


def _text(x: float, y: float, s: str, *, fs: float = 14, anchor: str = "start",
          weight: str = "normal", fill: str = "#1a1d24", el_id: str = "") -> str:
    i = f' id="{el_id}"' if el_id else ""
    return (f'<text{i} x="{x:.1f}" y="{y:.1f}" font-size="{fs}" '
            f'text-anchor="{anchor}" font-weight="{weight}" '
            f'fill="{fill}">{_html.escape(s)}</text>')


def _positions(cx: float, cy: float, r: float, n: int = 5) -> dict[int, tuple]:
    """Pentagon layout: vertex 1 at the top, the rest clockwise."""
    pos = {}
    for i in range(n):
        ang = math.radians(-90 + i * (360.0 / n))
        pos[i + 1] = (cx + r * math.cos(ang), cy + r * math.sin(ang))
    return pos


def _edges_svg(pos, edges, *, stroke: str, width: float = 2.0) -> str:
    out = []
    for u, v in edges:
        (x1, y1), (x2, y2) = pos[u], pos[v]
        out.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" '
                   f'y2="{y2:.1f}" stroke="{stroke}" stroke-width="{width}"/>')
    return "".join(out)


def _nodes_svg(pos, *, highlight: set, hi_fill: str, base_fill: str = "#ffffff",
               r: float = 21) -> str:
    out = []
    for v, (x, y) in sorted(pos.items()):
        fill = hi_fill if v in highlight else base_fill
        tcol = "#ffffff" if v in highlight else "#1a1d24"
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{fill}" '
                   f'stroke="#1a1d24" stroke-width="1.8"/>')
        out.append(_text(x, y + 5, str(v), fs=15, anchor="middle",
                         weight="700", fill=tcol))
    return "".join(out)


def render_clique_to_vertex_cover() -> tuple[str, list[dict]]:
    """Fully deterministic; the reduction's correctness is asserted."""
    V = [1, 2, 3, 4, 5]
    n = len(V)
    E_G = {(1, 2), (1, 3), (2, 3), (3, 4), (4, 5)}
    S = {1, 2, 3}          # clique in G
    k = len(S)

    all_pairs = {tuple(sorted(p)) for p in combinations(V, 2)}
    E_Gc = {p for p in all_pairs if p not in E_G}    # complement
    VC = set(V) - S        # the vertex cover of the complement

    # ── asserted correctness ──────────────────────────────────────────
    assert all(tuple(sorted(p)) in E_G for p in combinations(S, 2)), \
        "S is not a clique in G"
    assert E_Gc == all_pairs - E_G, "Ḡ is not the complement of G"
    assert all(u in VC or v in VC for (u, v) in E_Gc), \
        "V∖S does not cover every edge of Ḡ"
    assert len(VC) == n - k, "|vertex cover| != n − k"

    posL = _positions(248, 330, 118)
    posR = _positions(704, 330, 118)

    P: list[str] = []
    P.append(_text(_W / 2, 36, "Reducing Clique to Vertex Cover", fs=21,
                   anchor="middle", weight="700"))
    P.append(_text(_W / 2, 60,
                   "A clique of size k in G  ⇔  a vertex cover of size n − k "
                   "in the complement graph Ḡ.",
                   fs=14, anchor="middle", weight="600", fill="#1657b8",
                   el_id="statement"))

    # ── Left: graph G with the clique ─────────────────────────────────
    P.append(_text(248, 110, "Graph G", fs=16, anchor="middle", weight="700"))
    P.append(f'<g id="graphG">{_edges_svg(posL, E_G - set(map(lambda p: tuple(sorted(p)), combinations(S, 2))), stroke="#9aa4b2")}'
             f'<g id="cliqueEdges">{_edges_svg(posL, {tuple(sorted(p)) for p in combinations(S, 2)}, stroke="#1f9d55", width=3.4)}</g>'
             f'<g id="cliqueG">{_nodes_svg(posL, highlight=S, hi_fill="#1f9d55")}</g></g>')
    P.append(_text(248, 488, "Clique  S = {1, 2, 3}   (k = 3)", fs=14,
                   anchor="middle", weight="700", fill="#147a40"))
    P.append(_text(248, 510,
                   "every pair in S is an edge of G", fs=12.5,
                   anchor="middle", fill="#3a4250"))

    # ── Right: complement graph Ḡ with the vertex cover ───────────────
    P.append(_text(704, 110, "Complement graph  Ḡ", fs=16, anchor="middle",
                   weight="700"))
    P.append(f'<g id="graphGc">{_edges_svg(posR, E_Gc, stroke="#9aa4b2")}'
             f'<g id="vcGc">{_nodes_svg(posR, highlight=VC, hi_fill="#e08a1e")}</g></g>')
    P.append(_text(704, 488, "Vertex cover  V∖S = {4, 5}   (n − k = 2)",
                   fs=14, anchor="middle", weight="700", fill="#b56a12"))
    P.append(_text(704, 510,
                   "these two vertices touch every edge of Ḡ", fs=12.5,
                   anchor="middle", fill="#3a4250"))

    # ── Bottom: why it works ──────────────────────────────────────────
    P.append('<rect x="60" y="540" width="840" height="74" rx="6" '
             'fill="#f4f7fb" stroke="#c9d4e2"/>')
    P.append(_text(_W / 2, 564,
                   "Ḡ has an edge exactly where G has none. If S is a clique "
                   "of G then no edge of Ḡ joins two vertices of S,",
                   fs=13, anchor="middle", fill="#23282f"))
    P.append(_text(_W / 2, 584,
                   "so every edge of Ḡ has an endpoint outside S — the n − k "
                   "vertices V∖S form a vertex cover, and the steps reverse.",
                   fs=13, anchor="middle", fill="#23282f"))
    P.append(_text(_W / 2, 606,
                   "The complement is built in polynomial time, so this is a "
                   "valid reduction: Clique ≤ₚ Vertex Cover.",
                   fs=13, anchor="middle", weight="600", fill="#147a40"))

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" '
           f'viewBox="0 0 {_W} {_H}" width="{_W}" height="{_H}">'
           + "".join(P) + "</svg>")

    narration = [
        {"speak": "This reduction turns a question about cliques in one graph "
                  "into a question about vertex covers in its complement.",
         "highlight": ["statement"]},
        {"speak": "In graph G the three vertices one, two and three are all "
                  "pairwise connected, so they form a clique of size three.",
         "highlight": ["cliqueG"]},
        {"speak": "The complement graph keeps the same vertices but flips every "
                  "pair: edges of G become non-edges here, and non-edges become "
                  "edges.",
         "highlight": ["graphGc"]},
        {"speak": "Because every pair inside the clique was an edge of G, none "
                  "of those pairs is an edge of the complement, so the clique "
                  "becomes an independent set there.",
         "highlight": ["graphGc"]},
        {"speak": "The two leftover vertices, four and five, therefore touch "
                  "every edge of the complement: they are a vertex cover of "
                  "size n minus k, which is two.",
         "highlight": ["vcGc"]},
        {"speak": "So a clique of size k in G exists exactly when the "
                  "complement has a vertex cover of size n minus k, which is "
                  "what the reduction needed.",
         "highlight": ["statement"]},
    ]
    return svg, narration


def is_clique_vertex_cover_prompt(prompt: str) -> bool:
    p = (prompt or "").lower()
    if "clique" not in p or "vertex cover" not in p:
        return False
    return any(v in p for v in (
        "reduc", "reduce", "to vertex cover", "≤p", "<=p", "complement"))


async def generate_clique_vertex_cover_svg(
    prompt: str = "", *, api_key: str = "", base_url: str = "",
    model: str = "",
) -> tuple[str, list[dict]]:
    return render_clique_to_vertex_cover()
