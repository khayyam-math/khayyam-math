"""Graph-shaped figure templates.

Unlike the matrix templates, these have variable topology — number
of nodes and edges depends on the user's prompt.  Layout is computed
deterministically by a small longest-path-layering + barycenter
algorithm (a stripped-down Sugiyama) so different inputs always
yield reproducible-and-non-overlapping placements.

Currently:
  * state_diagram(states, transitions)  — finite-state automata,
    DFA/NFA, control-flow style diagrams.

Each function returns ``(svg, narration)`` in the same shape as the
matrix templates so the router + express path treat them
interchangeably.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ── Public dataclasses (light wrappers over dicts) ────────────────


@dataclass
class State:
    id: str
    label: Optional[str] = None
    initial: bool = False
    accept: bool = False


@dataclass
class Transition:
    source: str
    target: str
    label: str = ""


# ── Layered layout (mini-Sugiyama) ────────────────────────────────


def _assign_layers(states: List[State],
                   transitions: List[Transition]) -> Dict[str, int]:
    """BFS from initial states using FIRST-discovery depth.

    State diagrams have cycles (self-loops, back-edges), so a
    longest-path layering loops forever — a node on a cycle would
    keep getting depth+1 each time around.  We use plain BFS shortest
    distance instead: each state's layer = the SHORTEST path from any
    initial state.  States unreachable from any initial state get
    layer = max-reachable-depth + 1 (placed in their own column).
    """
    ids = [s.id for s in states]
    if not ids:
        return {}
    succ: Dict[str, List[str]] = {sid: [] for sid in ids}
    for t in transitions:
        if t.source in succ and t.target in succ and t.source != t.target:
            succ[t.source].append(t.target)
    starts = [s.id for s in states if s.initial] or [ids[0]]
    layer: Dict[str, int] = {}
    queue = list(dict.fromkeys(starts))  # dedup, preserve order
    for s in queue:
        layer[s] = 0
    head = 0
    while head < len(queue):
        u = queue[head]; head += 1
        for v in succ[u]:
            if v not in layer:
                layer[v] = layer[u] + 1
                queue.append(v)
    # Anything unreachable goes after the farthest reachable layer.
    max_reachable = max(layer.values(), default=0)
    for sid in ids:
        if sid not in layer:
            max_reachable += 1
            layer[sid] = max_reachable
    return layer


def _order_within_layers(layers: Dict[str, int],
                         transitions: List[Transition]) -> Dict[str, int]:
    """Assign each state an integer order within its layer using a
    barycenter heuristic over a few sweeps to reduce edge crossings.
    """
    # Bucket states by layer.
    by_layer: Dict[int, List[str]] = {}
    for sid, ly in layers.items():
        by_layer.setdefault(ly, []).append(sid)
    for ly in by_layer:
        by_layer[ly].sort()  # stable initial order

    # Build adjacency between layers.
    pred: Dict[str, List[str]] = {sid: [] for sid in layers}
    succ: Dict[str, List[str]] = {sid: [] for sid in layers}
    for t in transitions:
        if t.source in layers and t.target in layers:
            succ[t.source].append(t.target)
            pred[t.target].append(t.source)

    max_layer = max(by_layer.keys()) if by_layer else 0
    # 3 sweeps (forward + back + forward) is the classic Sugiyama recipe.
    for sweep in range(3):
        layer_range = (range(1, max_layer + 1) if sweep % 2 == 0
                       else range(max_layer - 1, -1, -1))
        for ly in layer_range:
            row = by_layer[ly]
            neighbours = pred if sweep % 2 == 0 else succ
            adj_layer = ly - 1 if sweep % 2 == 0 else ly + 1
            if adj_layer not in by_layer:
                continue
            adj_order = {sid: i for i, sid in enumerate(by_layer[adj_layer])}

            def barycenter(sid: str) -> float:
                ns = neighbours[sid]
                if not ns:
                    return 0.0
                return sum(adj_order.get(n, 0) for n in ns) / len(ns)

            row.sort(key=barycenter)
            by_layer[ly] = row

    order: Dict[str, int] = {}
    for ly, row in by_layer.items():
        for i, sid in enumerate(row):
            order[sid] = i
    return order


# ── Renderer ──────────────────────────────────────────────────────


def state_diagram(
    states: List[State | dict],
    transitions: List[Transition | dict],
    *,
    canvas_w: int = 900,
    canvas_h: int = 650,
    title: str = "State diagram",
) -> Tuple[str, List[dict]]:
    """Render a finite-state-automaton-style diagram.

    ``states`` and ``transitions`` accept either dataclasses or plain
    dicts — the latter makes the LLM-classifier path simpler.
    """
    # Normalise input.
    norm_states: List[State] = []
    for s in states:
        if isinstance(s, dict):
            norm_states.append(State(
                id=str(s["id"]),
                label=s.get("label"),
                initial=bool(s.get("initial", False)),
                accept=bool(s.get("accept", False)),
            ))
        else:
            norm_states.append(s)
    norm_trans: List[Transition] = []
    for t in transitions:
        if isinstance(t, dict):
            norm_trans.append(Transition(
                source=str(t["source"]),
                target=str(t["target"]),
                label=str(t.get("label", "")),
            ))
        else:
            norm_trans.append(t)
    if not norm_states:
        raise ValueError("state_diagram requires at least one state")

    layers = _assign_layers(norm_states, norm_trans)
    order = _order_within_layers(layers, norm_trans)
    max_layer = max(layers.values()) if layers else 0
    max_per_layer = max(
        sum(1 for sid, ly in layers.items() if ly == k)
        for k in range(max_layer + 1)
    )

    # Compute pixel positions.
    RADIUS = 32
    LAYER_DX = 160          # horizontal gap between layers
    ROW_DY = 110            # vertical gap between rows
    LEFT_MARGIN = 100       # leaves room for "start" arrow
    TOP_MARGIN = 100
    pos: Dict[str, Tuple[float, float]] = {}
    for s in norm_states:
        x = LEFT_MARGIN + layers[s.id] * LAYER_DX
        y = TOP_MARGIN + order[s.id] * ROW_DY + (max_per_layer - 1
                            - max(order[sid] for sid in layers
                                  if layers[sid] == layers[s.id])) * ROW_DY / 2
        # Re-center column vertically within canvas using row count of THIS layer.
        col_rows = [sid for sid, ly in layers.items() if ly == layers[s.id]]
        col_n = len(col_rows)
        total_h = (col_n - 1) * ROW_DY
        y = (canvas_h - total_h) / 2 + order[s.id] * ROW_DY
        pos[s.id] = (x, y)

    parts: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {canvas_w} {canvas_h}">',
        f'<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        f'markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="#222"/></marker></defs>',
        f'<text id="title" x="{canvas_w // 2}" y="50" font-size="26" '
        f'text-anchor="middle" font-family="serif" fill="#111">{title}</text>',
    ]

    # Render edges first so they sit BEHIND the state circles.
    for i, t in enumerate(norm_trans):
        if t.source not in pos or t.target not in pos:
            continue
        sx, sy = pos[t.source]
        tx, ty = pos[t.target]
        eid = f"edge_{i}"
        if t.source == t.target:
            # Self-loop: arc above the state.
            r = RADIUS + 12
            parts.append(
                f'<path id="{eid}" d="M {sx:.0f} {sy - RADIUS:.0f} '
                f'C {sx - r:.0f} {sy - 3 * r:.0f}, '
                f'{sx + r:.0f} {sy - 3 * r:.0f}, '
                f'{sx + 0.5:.0f} {sy - RADIUS:.0f}" '
                f'fill="none" stroke="#222" stroke-width="1.5" '
                f'marker-end="url(#arrow)"/>'
            )
            if t.label:
                parts.append(
                    f'<text id="{eid}_label" x="{sx:.0f}" y="{sy - 2 * r:.0f}" '
                    f'font-size="16" text-anchor="middle" font-family="serif" '
                    f'fill="#111">{t.label}</text>'
                )
        else:
            # Straight line, shortened at both ends so the arrow doesn't
            # vanish under the destination circle.
            dx, dy = tx - sx, ty - sy
            d = (dx * dx + dy * dy) ** 0.5 or 1.0
            ux, uy = dx / d, dy / d
            x1 = sx + ux * RADIUS
            y1 = sy + uy * RADIUS
            x2 = tx - ux * RADIUS
            y2 = ty - uy * RADIUS
            parts.append(
                f'<line id="{eid}" x1="{x1:.0f}" y1="{y1:.0f}" '
                f'x2="{x2:.0f}" y2="{y2:.0f}" stroke="#222" '
                f'stroke-width="1.5" marker-end="url(#arrow)"/>'
            )
            if t.label:
                # Label at midpoint, offset perpendicular so it doesn't
                # sit on the line.
                mx = (x1 + x2) / 2 - uy * 14
                my = (y1 + y2) / 2 + ux * 14
                parts.append(
                    f'<text id="{eid}_label" x="{mx:.0f}" y="{my:.0f}" '
                    f'font-size="15" text-anchor="middle" font-family="serif" '
                    f'fill="#111">{t.label}</text>'
                )

    # Render states.
    for s in norm_states:
        sx, sy = pos[s.id]
        # Initial-state arrow.
        if s.initial:
            parts.append(
                f'<line id="start_arrow_{s.id}" '
                f'x1="{sx - RADIUS - 40:.0f}" y1="{sy:.0f}" '
                f'x2="{sx - RADIUS - 2:.0f}" y2="{sy:.0f}" '
                f'stroke="#222" stroke-width="2" '
                f'marker-end="url(#arrow)"/>'
            )
        # Outer circle (accept = double circle).
        if s.accept:
            parts.append(
                f'<circle id="{s.id}_outer" cx="{sx:.0f}" cy="{sy:.0f}" '
                f'r="{RADIUS + 5}" fill="white" stroke="#222" '
                f'stroke-width="1.5"/>'
            )
        parts.append(
            f'<circle id="{s.id}" cx="{sx:.0f}" cy="{sy:.0f}" '
            f'r="{RADIUS}" fill="white" stroke="#222" stroke-width="2"/>'
        )
        label = s.label if s.label is not None else s.id
        parts.append(
            f'<text id="{s.id}_label" x="{sx:.0f}" y="{sy + 5:.0f}" '
            f'font-size="18" text-anchor="middle" font-family="serif" '
            f'fill="#111">{label}</text>'
        )

    parts.append("</svg>")
    svg = "".join(parts)

    # Narration: walk states in layer order, then summarize transitions.
    narration: List[dict] = [{
        "speak": (f"This state diagram has {len(norm_states)} states and "
                  f"{len(norm_trans)} transitions."),
        "highlight": ["title"],
    }]
    initials = [s for s in norm_states if s.initial]
    if initials:
        narration.append({
            "speak": (f"The initial state is "
                      f"{initials[0].label or initials[0].id}, "
                      f"marked by the incoming arrow on the left."),
            "highlight": [initials[0].id],
        })
    accepts = [s for s in norm_states if s.accept]
    if accepts:
        accept_names = ", ".join(s.label or s.id for s in accepts)
        narration.append({
            "speak": (f"Accept state{'s' if len(accepts) > 1 else ''}: "
                      f"{accept_names} — drawn as double circle"
                      f"{'s' if len(accepts) > 1 else ''}."),
            "highlight": [s.id for s in accepts],
        })
    # Sample a few representative transitions for the walkthrough.
    for t in norm_trans[:3]:
        src = next((s for s in norm_states if s.id == t.source), None)
        dst = next((s for s in norm_states if s.id == t.target), None)
        src_name = (src.label or src.id) if src else t.source
        dst_name = (dst.label or dst.id) if dst else t.target
        narration.append({
            "speak": (f"From {src_name}, reading '{t.label}' transitions "
                      f"to {dst_name}." if t.label else
                      f"There is a transition from {src_name} to {dst_name}."),
            "highlight": [f"edge_{norm_trans.index(t)}"],
        })
    return svg, narration
