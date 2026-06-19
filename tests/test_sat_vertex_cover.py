"""3SAT ≤ₚ Vertex-Cover gadget-graph renderer tests."""
from __future__ import annotations

import asyncio
import re
from xml.dom import minidom

from studio.templates import sat_vertex_cover as S


def test_routing():
    assert S.is_sat_vertex_cover_prompt("reduce 3SAT to vertex cover")
    assert S.is_sat_vertex_cover_prompt(
        "show the reduction from 3-sat to vertex cover on a graph")
    assert not S.is_sat_vertex_cover_prompt("reduce clique to vertex cover")
    assert not S.is_sat_vertex_cover_prompt("what is 3sat")


def test_graph_and_cover_are_correct():
    svg, narr = S.render_sat_to_vertex_cover()
    minidom.parseString(svg)
    assert 5 <= len(narr) <= 9
    # 14 vertices (8 variable-gadget + 6 clause-triangle) drawn as r=19 nodes
    assert svg.count('r="19"') == 14
    # Rebuild the construction and re-verify the cover here too.
    clauses = [[(1, False), (2, True), (3, False)],
               [(1, True), (2, False), (4, False)]]
    n, m = 4, 2
    edges = []
    for v in range(1, n + 1):
        edges.append((("v", v, False), ("v", v, True)))
    for ci, cl in enumerate(clauses):
        cv = [("c", ci, p) for p in range(3)]
        edges += [(cv[0], cv[1]), (cv[0], cv[2]), (cv[1], cv[2])]
        for p, (var, neg) in enumerate(cl):
            edges.append((("c", ci, p), ("v", var, neg)))
    assign = {1: True, 2: True, 3: True, 4: True}
    cover = {("v", v, not assign[v]) for v in range(1, n + 1)}
    leave = {0: 0, 1: 1}
    for ci in range(m):
        for p in range(3):
            if p != leave[ci]:
                cover.add(("c", ci, p))
    assert len(cover) == n + 2 * m == 8
    assert all(a in cover or b in cover for a, b in edges)
    assert "k = n + 2m" in svg


def test_narration_highlights_exist_in_svg():
    svg, narr = S.render_sat_to_vertex_cover()
    ids = set(re.findall(r'id="([^"]+)"', svg))
    for phrase in narr:
        for ref in phrase.get("highlight", []):
            assert ref in ids, f"highlight id {ref!r} missing from SVG"


def test_routes_through_express_and_passes_probe():
    from studio.express import express_figure
    r = asyncio.run(express_figure(
        "reduce 3SAT to vertex cover", base_url="", model="", api_key=""))
    assert r.get("template") == "sat_vertex_cover"
    assert r.get("retries_used") == 0
    import importlib.util
    spec = importlib.util.spec_from_file_location("qp", "studio/quality_probe.py")
    qp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qp)
    assert qp.inspect_quality("sat_vertex_cover", r) == []
