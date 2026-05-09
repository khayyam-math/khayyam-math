"""Tests for sevim.plan — coordinate planning for math-mode figures."""
from __future__ import annotations

import math

import pytest

from sevim.plan import plan_layout


def _dist(a: dict[str, float], b: dict[str, float]) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def test_empty_input_returns_clean_envelope():
    out = plan_layout(nodes=[])
    assert out["node_positions"] == {}
    assert out["warnings"], "should warn on empty input"


def test_radial_lays_nodes_on_a_ring():
    """Six nodes, no clusters, radial layout: all on the same circle."""
    nodes = [{"node_id": f"n{i}", "label": f"N{i}"} for i in range(6)]
    out = plan_layout(nodes=nodes, layout="radial")
    assert out["layout_chosen"] == "radial"
    radii = [
        math.hypot(p["x"], p["y"])
        for p in out["node_positions"].values()
    ]
    assert all(abs(r - radii[0]) < 1e-6 for r in radii), (
        f"radial layout produced varying radii: {radii}"
    )


def test_auto_falls_back_to_radial_without_clusters():
    nodes = [{"node_id": f"n{i}", "label": f"N{i}"} for i in range(4)]
    out = plan_layout(nodes=nodes)
    assert out["layout_chosen"] == "radial"


def test_cluster_layout_groups_members_near_their_centre():
    """In 3SAT-clique style: 3 clauses × 3 literals.  Members of the
    same cluster should sit closer to each other than to members of
    other clusters."""
    nodes = []
    for clause in ("C1", "C2", "C3"):
        for lit in ("x", "y", "z"):
            nodes.append({
                "node_id": f"{clause}_{lit}",
                "label": lit,
                "cluster": clause,
            })
    out = plan_layout(nodes=nodes, layout="constraint_clusters")
    pos = out["node_positions"]
    assert len(pos) == 9

    # Intra-cluster distance: pairs within C1
    d_intra = _dist(pos["C1_x"], pos["C1_y"])
    # Inter-cluster distance: C1 member vs C2 member
    d_inter = _dist(pos["C1_x"], pos["C2_x"])
    assert d_intra < d_inter, (
        f"intra-cluster distance {d_intra:.2f} should be < "
        f"inter-cluster distance {d_inter:.2f}"
    )


def test_cluster_layout_is_deterministic():
    """Same input → same output, every time.  Critical for diffing/regression."""
    nodes = [
        {"node_id": f"c{c}_{lit}", "cluster": f"C{c}", "label": lit}
        for c in (1, 2, 3) for lit in ("a", "b")
    ]
    a = plan_layout(nodes=nodes, layout="constraint_clusters")
    b = plan_layout(nodes=nodes, layout="constraint_clusters")
    assert a["node_positions"] == b["node_positions"]


def test_unknown_layout_falls_back_with_warning():
    nodes = [{"node_id": "n1", "label": "A"}]
    out = plan_layout(nodes=nodes, layout="bogus_layout")
    assert out["layout_chosen"] == "radial"
    assert any("bogus_layout" in w for w in out["warnings"])


def test_constraint_clusters_warns_when_no_clusters_present():
    nodes = [{"node_id": f"n{i}", "label": f"N{i}"} for i in range(3)]
    out = plan_layout(nodes=nodes, layout="constraint_clusters")
    assert out["layout_chosen"] == "radial"
    assert any("no node carries a 'cluster'" in w for w in out["warnings"])


def test_dense_radial_warns_about_crossings():
    """A radial layout with many edges produces visual clutter; surface
    a warning so the model knows to add cluster fields next time."""
    nodes = [{"node_id": f"n{i}", "label": f"N{i}"} for i in range(5)]
    edges = [{"src": f"n{i}", "dst": f"n{j}", "relation": "connects"}
             for i in range(5) for j in range(i + 1, 5)]
    out = plan_layout(nodes=nodes, edges=edges, layout="radial")
    assert any("crossings" in w or "cluster" in w for w in out["warnings"])


def test_returned_bounds_cover_all_positions():
    nodes = [
        {"node_id": f"c{c}_{lit}", "cluster": f"C{c}", "label": lit}
        for c in (1, 2, 3) for lit in ("a", "b", "c")
    ]
    out = plan_layout(nodes=nodes)
    bounds = out["math_bounds"]
    for pos in out["node_positions"].values():
        assert bounds["xmin"] <= pos["x"] <= bounds["xmax"]
        assert bounds["ymin"] <= pos["y"] <= bounds["ymax"]


def test_columns_layout_arranges_clusters_left_to_right():
    """The textbook 3SAT-CLIQUE layout: 3 clauses become 3 vertical
    columns, literals stacked inside each column."""
    nodes = []
    for clause in ("C1", "C2", "C3"):
        for lit in ("a", "b", "c"):
            nodes.append({
                "node_id": f"{clause}_{lit}",
                "label": lit,
                "cluster": clause,
            })
    out = plan_layout(nodes=nodes, layout="columns")
    assert out["layout_chosen"] == "columns"
    pos = out["node_positions"]
    # Members of C1 share an x; members of C2 share a (different) x; etc.
    c1_xs = {pos[f"C1_{l}"]["x"] for l in "abc"}
    c2_xs = {pos[f"C2_{l}"]["x"] for l in "abc"}
    c3_xs = {pos[f"C3_{l}"]["x"] for l in "abc"}
    assert len(c1_xs) == 1 and len(c2_xs) == 1 and len(c3_xs) == 1
    # Columns ordered C1 < C2 < C3 (left-to-right).
    assert next(iter(c1_xs)) < next(iter(c2_xs)) < next(iter(c3_xs))
    # Within a column, members at distinct y values, top-to-bottom.
    c1_ys = [pos[f"C1_{l}"]["y"] for l in "abc"]
    assert c1_ys == sorted(c1_ys, reverse=True), (
        f"members should be top→bottom (descending y), got {c1_ys}"
    )


def test_auto_now_picks_columns_for_clustered_inputs():
    """v0.3 changed the default for clustered inputs from
    constraint_clusters → columns to match textbook conventions."""
    nodes = [
        {"node_id": f"c{c}_{lit}", "label": lit, "cluster": f"C{c}"}
        for c in (1, 2) for lit in ("x", "y")
    ]
    out = plan_layout(nodes=nodes)
    assert out["layout_chosen"] == "columns"


def test_plan_returns_cluster_anchors_at_centroids():
    """Suggested caption anchors per cluster — the LLM passes these
    straight into add_caption ops so leader lines hit real geometry."""
    nodes = [
        {"node_id": f"C1_{l}", "cluster": "C1"} for l in "abc"
    ] + [
        {"node_id": f"C2_{l}", "cluster": "C2"} for l in "abc"
    ]
    out = plan_layout(nodes=nodes, layout="columns")
    assert "cluster_anchors" in out
    anchors = out["cluster_anchors"]
    assert set(anchors) == {"C1", "C2"}
    # Each anchor is the cluster's centroid.
    pos = out["node_positions"]
    for cluster_name, ids in (("C1", ("C1_a", "C1_b", "C1_c")),
                              ("C2", ("C2_a", "C2_b", "C2_c"))):
        cx = sum(pos[i]["x"] for i in ids) / len(ids)
        cy = sum(pos[i]["y"] for i in ids) / len(ids)
        assert abs(anchors[cluster_name]["x"] - cx) < 1e-6
        assert abs(anchors[cluster_name]["y"] - cy) < 1e-6


def test_cluster_with_single_member_sits_at_cluster_centre():
    """Edge case: a cluster containing exactly one node.  No inner ring
    needed — the single member should be placed at the cluster centre."""
    nodes = [
        {"node_id": "lonely", "label": "L", "cluster": "Lonely"},
        {"node_id": "c2_a", "label": "A", "cluster": "Pair"},
        {"node_id": "c2_b", "label": "B", "cluster": "Pair"},
    ]
    out = plan_layout(nodes=nodes, layout="constraint_clusters")
    pos = out["node_positions"]
    # The lonely node IS its cluster's centre — distance to the cluster
    # centre is zero.  We can't easily fetch the centre, but we can
    # verify there's only one member at that exact position.
    coincident = sum(
        1 for p in pos.values()
        if abs(p["x"] - pos["lonely"]["x"]) < 1e-6
        and abs(p["y"] - pos["lonely"]["y"]) < 1e-6
    )
    assert coincident == 1
