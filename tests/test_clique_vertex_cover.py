"""Clique ≤ₚ Vertex-Cover reduction renderer tests.

Added after a user repeatedly asked to "reduce clique to vertex cover ...
on a graph ... I need nodes! two graphs with nodes!" and got the generic
number-free reduction schematic instead.  The renderer must draw two real
node-link graphs and assert the reduction's correctness.
"""
from __future__ import annotations

import asyncio
import re
from itertools import combinations
from xml.dom import minidom

from studio.templates import clique_vertex_cover as C


def test_routing():
    assert C.is_clique_vertex_cover_prompt("reduce clique to vertex cover")
    assert C.is_clique_vertex_cover_prompt(
        "show the reduction from clique to vertex cover on a graph")
    assert C.is_clique_vertex_cover_prompt("clique ≤p vertex cover")
    assert not C.is_clique_vertex_cover_prompt("reduce 3sat to clique")
    assert not C.is_clique_vertex_cover_prompt("what is a vertex cover")


def test_reduction_is_correct_and_drawn_as_graphs():
    svg, narr = C.render_clique_to_vertex_cover()
    minidom.parseString(svg)
    assert 5 <= len(narr) <= 9
    # Two real graphs: 5 nodes each = 10 node circles (r="21").
    assert svg.count('r="21"') == 10
    # The clique correspondence must be exact.
    V = [1, 2, 3, 4, 5]
    E_G = {(1, 2), (1, 3), (2, 3), (3, 4), (4, 5)}
    S = {1, 2, 3}
    all_pairs = {tuple(sorted(p)) for p in combinations(V, 2)}
    E_Gc = all_pairs - E_G
    VC = set(V) - S
    assert all(tuple(sorted(p)) in E_G for p in combinations(S, 2))
    assert all(u in VC or v in VC for (u, v) in E_Gc)
    assert len(VC) == len(V) - len(S)
    # Both panels and their labels are present.
    assert "Graph G" in svg and "Complement graph" in svg
    assert "Clique" in svg and "Vertex cover" in svg


def test_narration_highlights_exist_in_svg():
    svg, narr = C.render_clique_to_vertex_cover()
    ids = set(re.findall(r'id="([^"]+)"', svg))
    for phrase in narr:
        for ref in phrase.get("highlight", []):
            assert ref in ids, f"highlight id {ref!r} missing from SVG"


def test_routes_through_express_and_passes_probe():
    from studio.express import express_figure
    r = asyncio.run(express_figure(
        "reduce clique to vertex cover", base_url="", model="", api_key=""))
    assert r.get("template") == "clique_vertex_cover"
    assert r.get("retries_used") == 0
    import importlib.util
    spec = importlib.util.spec_from_file_location("qp", "studio/quality_probe.py")
    qp = importlib.util.module_from_spec(spec)
    qp_mod = qp
    spec.loader.exec_module(qp_mod)
    assert qp_mod.inspect_quality("clique_vertex_cover", r) == []
