"""Graph-homomorphism template — deterministic verifier + clean figure.

A graph homomorphism prompt asks for two graphs G, H and a function
f: V(G) → V(H) that preserves edges: for every edge (u, v) in E(G),
the image (f(u), f(v)) must be in E(H).  This template:

1. The LLM emits both graphs and the proposed mapping as structured
   JSON — never draws the figure freehand.
2. A deterministic O(|E(G)|) check verifies the mapping IS a
   homomorphism.  Any failure (wrong image, missing vertex, edge
   that doesn't preserve) is fed back as a critique and the LLM is
   asked to fix it — up to three attempts.
3. Only when the mapping verifies does anything get rendered.

The rendering avoids the tangled-dashed-arrows pattern that produced
the earlier wrong-looking example.  Instead, each vertex of G is
*coloured* the same as its image in H — so the homomorphism is
readable at a glance, and the mapping table is shown below.

Returns ``(svg, narration)`` or ``None`` (so the caller falls back).
"""
from __future__ import annotations

import json
from typing import Any, Optional


_KEYWORDS = (
    "homomorphism", "graph homomorphism", "homomorphic",
    "morphism between graphs", "edge-preserving map",
    "edge preserving map", "homomorphic image",
    "morphism of graphs",
)


def is_homomorphism_prompt(prompt: str) -> bool:
    p = (prompt or "").lower()
    return any(kw in p for kw in _KEYWORDS)


HOMOM_SPEC_SYSTEM = """\
You map a graph-homomorphism prompt into JSON.  Do NOT compute or
draw — just identify the two graphs and the proposed function
f: V(G) -> V(H).

Return ONLY a JSON object:
{
  "title": "<short figure title>",
  "graph_g": {
    "vertices": ["v1","v2", ...],
    "edges": [["v1","v2"], ...]
  },
  "graph_h": {
    "vertices": ["u1","u2", ...],
    "edges": [["u1","u2"], ...]
  },
  "mapping": {"v1": "u1", "v2": "u2", ...},
  "intro": "<one sentence on what we are showing>",
  "takeaway": "<one sentence on why this is a homomorphism>"
}

Rules:
- Edges are UNORDERED — list each once (don't include both
  ["v1","v2"] and ["v2","v1"]).
- Every vertex of G MUST have a mapping entry.
- The image f(v) for every vertex v must be a vertex of H.
- For "C_n homomorphic to K_2": G is the n-cycle (n even); H is the
  single-edge graph on {u1,u2}; alternate the mapping (v1->u1,
  v2->u2, v3->u1, v4->u2, ...).
- For "K_3 homomorphic to K_3": identity mapping is the obvious one.
- A homomorphism cannot map an edge of G to a single vertex of H
  (no self-loops), unless H itself has that self-loop.

Respond with ONLY the JSON object.
"""


async def _llm_emit_spec(
    user_prompt: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    extra_feedback: str = "",
    timeout_s: float = 30.0,
) -> Optional[dict]:
    import httpx
    user = user_prompt
    if extra_feedback:
        user = (user_prompt
                + "\n\nVERIFIER FEEDBACK (your previous attempt failed):\n"
                + extra_feedback
                + "\n\nFix the mapping or graph and try again.")
    payload = {
        "model": model,
        "max_tokens": 900,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": HOMOM_SPEC_SYSTEM},
            {"role": "user", "content": user},
        ],
    }
    headers = {"content-type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                json=payload, headers=headers)
        if r.status_code != 200:
            return None
        spec = json.loads(r.json()["choices"][0]["message"]["content"] or "")
    except Exception:  # noqa: BLE001
        return None
    return spec if isinstance(spec, dict) else None


def _normalise_edges(edges) -> set:
    """Convert a list of [a,b] pairs into a set of frozensets so the
    edge representation is unordered."""
    out: set = set()
    for e in (edges or []):
        if isinstance(e, (list, tuple)) and len(e) == 2:
            a, b = str(e[0]), str(e[1])
            out.add(frozenset((a, b)) if a != b else frozenset({a}))
    return out


def verify_homomorphism(spec: dict) -> tuple[bool, list[str]]:
    """Return ``(ok, problems)`` — deterministic O(|E_G|) check that
    ``f`` is a graph homomorphism G → H."""
    g = spec.get("graph_g") or {}
    h = spec.get("graph_h") or {}
    f = spec.get("mapping") or {}
    if not isinstance(g, dict) or not isinstance(h, dict) or not isinstance(f, dict):
        return False, ["graph_g, graph_h and mapping must all be objects"]
    g_verts = [str(v) for v in (g.get("vertices") or [])]
    h_verts = set(str(v) for v in (h.get("vertices") or []))
    if not g_verts:
        return False, ["graph_g.vertices is empty"]
    if not h_verts:
        return False, ["graph_h.vertices is empty"]
    g_edges = _normalise_edges(g.get("edges"))
    h_edges = _normalise_edges(h.get("edges"))

    problems: list[str] = []
    for v in g_verts:
        if v not in f:
            problems.append(f"vertex {v!r} of G has no image under f")
        elif str(f[v]) not in h_verts:
            problems.append(
                f"f({v}) = {f[v]!r} is not a vertex of H "
                f"(H has {sorted(h_verts)})")
    for e in g_edges:
        verts = tuple(e)
        if len(verts) == 1:
            u = v = verts[0]
        else:
            u, v = verts
        if u not in f or v not in f:
            continue   # already reported
        fu, fv = str(f[u]), str(f[v])
        image = frozenset((fu, fv)) if fu != fv else frozenset({fu})
        if image not in h_edges:
            problems.append(
                f"edge ({u}, {v}) of G maps to ({fu}, {fv}) which is "
                f"NOT an edge of H — f does not preserve this edge")
    return (len(problems) == 0, problems)


_PALETTE = ("#3d6fb4", "#cc4125", "#6aa84f", "#e69138",
            "#8e7cc3", "#45818e", "#a64d79", "#6cb86c",
            "#674ea7", "#bf9000")


def _render(spec: dict) -> Optional[tuple[str, list[dict]]]:
    """Render G and H side-by-side with vertex colours encoding the
    homomorphism.  Mapping table shown below the figure."""
    try:
        from studio.templates.graphviz_route import (
            render_graphviz, _make_svg_responsive,
        )
    except Exception:  # noqa: BLE001
        return None

    g_verts = [str(v) for v in spec.get("graph_g", {}).get("vertices", [])]
    g_edges = [[str(a), str(b)] for a, b in
               (spec.get("graph_g", {}).get("edges") or [])]
    h_verts = [str(v) for v in spec.get("graph_h", {}).get("vertices", [])]
    h_edges = [[str(a), str(b)] for a, b in
               (spec.get("graph_h", {}).get("edges") or [])]
    mapping = {str(k): str(v) for k, v in (spec.get("mapping") or {}).items()}

    colour_for = {h: _PALETTE[i % len(_PALETTE)]
                  for i, h in enumerate(h_verts)}

    def _node(name: str, label: str, fill: str) -> str:
        return (f'  "{name}" [label="{label}", '
                f'shape=circle, style="filled", fillcolor="{fill}", '
                f'fontcolor="white", penwidth=2, color="#222"];')

    dot: list[str] = [
        'graph H {',
        '  rankdir=LR; bgcolor="white";',
        '  splines=true; pad=0.4;',
        '  node [fontname="Georgia", fontsize=18, fixedsize=true, '
        'width=0.6, height=0.6];',
        '  edge [color="#333", penwidth=1.5];',
        '',
        '  subgraph cluster_g {',
        '    label="G";  fontsize=22; fontname="Georgia";',
        '    style=rounded; color="#aaa";',
    ]
    for v in g_verts:
        col = colour_for.get(mapping.get(v, ""), "#888")
        dot.append(_node(f"g_{v}", v, col).replace("  ", "    ", 1))
    for a, b in g_edges:
        dot.append(f'    "g_{a}" -- "g_{b}";')
    dot.append('  }')
    dot.append('')
    dot.append('  subgraph cluster_h {')
    dot.append('    label="H";  fontsize=22; fontname="Georgia";')
    dot.append('    style=rounded; color="#aaa";')
    for v in h_verts:
        col = colour_for.get(v, "#888")
        dot.append(_node(f"h_{v}", v, col).replace("  ", "    ", 1))
    for a, b in h_edges:
        dot.append(f'    "h_{a}" -- "h_{b}";')
    dot.append('  }')
    dot.append('}')

    svg = render_graphviz('\n'.join(dot), engine="dot")
    if not svg:
        return None
    svg = _make_svg_responsive(svg)

    # Append a mapping legend below the figure.
    pairs = ",  ".join(f"{k}→{mapping[k]}" for k in g_verts if k in mapping)
    title = str(spec.get("title") or "Graph homomorphism")
    legend = (
        f'<div style="text-align:center; font-family:Georgia, serif; '
        f'margin-top:4px;">'
        f'<div style="font-weight:bold; color:#1a3a5c; font-size:1.05em;">'
        f'{title}</div>'
        f'<div style="margin-top:6px; color:#555;">mapping&nbsp;'
        f'<span style="font-family:monospace;">f</span>:&nbsp;{pairs}</div>'
        f'</div>')
    # The graphviz SVG is followed by the legend via <foreignObject> —
    # but the canvas viewer renders raw HTML alongside SVG happily
    # since stage.innerHTML = state.svg accepts mixed markup.  Just
    # concatenate and the browser handles both.
    combined = svg + legend

    notes = []
    intro = str(spec.get("intro") or "").strip()
    if intro:
        notes.append({"speak": intro, "highlight": []})
    notes.append({
        "speak": ("This is a graph homomorphism: vertices of G are "
                  "coloured by their image in H, so vertices sharing a "
                  "colour are mapped to the same target. Every edge of "
                  "G connects two different colours, and every pair of "
                  "differently coloured vertices is an edge of H — so "
                  "the mapping preserves edges by construction."),
        "highlight": [],
    })
    notes.append({
        "speak": str(spec.get("takeaway")
                     or "Edge-preservation is what makes this a "
                        "homomorphism."),
        "highlight": [],
    })
    return combined, notes


async def generate_homomorphism_svg(
    user_prompt: str,
    *,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini",
    max_attempts: int = 3,
) -> Optional[tuple[str, list[dict]]]:
    """End-to-end: emit spec → verify → retry on failure → render."""
    feedback = ""
    for _ in range(max_attempts):
        spec = await _llm_emit_spec(
            user_prompt, api_key=api_key, base_url=base_url, model=model,
            extra_feedback=feedback)
        if not spec:
            return None
        ok, problems = verify_homomorphism(spec)
        if ok:
            return _render(spec)
        feedback = "\n".join(f"  - {p}" for p in problems)
    return None
