"""Unit tests for studio.neural_layout schema + parser + exporter."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from studio.neural_layout import schema, svg_to_graph, exporter
from studio.neural_layout.schema import (
    EdgeFeatures, NodeFeatures, SceneGraph, TrainingPair,
    classify_math_bucket,
)


# ── schema round-trip ──────────────────────────────────────────────


def test_node_round_trip():
    n = NodeFeatures(
        id="cell_0_0", type="rect",
        bbox=(10.0, 20.0, 50.0, 40.0),
        text="", font_size=14, stroke_width=1.5,
        parent_id="matrix_a", top_level_group_id="matrix_a",
        is_narration_anchor=True,
    )
    n2 = NodeFeatures.from_dict(n.to_dict())
    assert n == n2


def test_training_pair_round_trip():
    g = SceneGraph(
        nodes=[NodeFeatures(id="a", type="rect", bbox=(0, 0, 10, 10))],
        edges=[],
        viewbox=(0, 0, 900, 600),
        canvas_w=900, canvas_h=600,
    )
    pair = TrainingPair(
        pair_id="abc123",
        prompt="show pythagoras",
        source=g, target=g,
        viewport_kind="desktop",
        math_bucket="geometry",
        metadata={"foo": "bar"},
    )
    j = pair.to_json()
    pair2 = TrainingPair.from_dict(json.loads(j))
    assert pair2.prompt == "show pythagoras"
    assert pair2.math_bucket == "geometry"
    assert pair2.viewport_kind == "desktop"
    assert len(pair2.source.nodes) == 1
    assert pair2.source.nodes[0].id == "a"
    assert pair2.schema_version == schema.SCHEMA_VERSION


def test_schema_version_mismatch_raises():
    with pytest.raises(ValueError):
        TrainingPair.from_dict({
            "schema_version": 99,
            "pair_id": "x", "prompt": "", "viewport_kind": "desktop",
            "math_bucket": "other",
            "source": {"nodes": [], "edges": [], "viewbox": [0, 0, 1, 1],
                       "canvas_w": 1, "canvas_h": 1},
            "target": {"nodes": [], "edges": [], "viewbox": [0, 0, 1, 1],
                       "canvas_w": 1, "canvas_h": 1},
        })


# ── math bucket classification ─────────────────────────────────────


@pytest.mark.parametrize("prompt, expected", [
    ("multiplicate two 3x3 matrices", "linear_algebra"),
    ("show the Riemann sum approximation of x^2 from 0 to 1", "calculus"),
    ("inscribed angle theorem on a circle", "geometry"),
    ("Venn diagram for A union B intersect C", "set_theory_logic"),
    ("prove SAT is NP-complete via reduction", "complexity"),
    ("pigeonhole principle with 5 pigeons in 4 holes", "combinatorics"),
    ("explain bayes rule with a tree diagram", "probability"),
    ("derivative of sin(x) as slope of tangent", "calculus"),
    ("prove sqrt(2) is irrational by contradiction", "proof"),
])
def test_math_bucket_classifier(prompt, expected):
    assert classify_math_bucket(prompt) == expected


# ── SVG parser ─────────────────────────────────────────────────────


SIMPLE_SVG = """<svg xmlns="http://www.w3.org/2000/svg"
       width="900" height="600" viewBox="0 0 900 600">
  <rect id="bg" x="10" y="10" width="100" height="50"/>
  <text id="title" x="20" y="30" font-size="20">Hello</text>
  <g id="matrix_a" transform="translate(200,100)">
    <rect id="cell_0_0" x="0" y="0" width="40" height="40"/>
    <rect id="cell_0_1" x="40" y="0" width="40" height="40"/>
  </g>
</svg>"""


def test_parser_node_count():
    res = svg_to_graph.parse_svg(SIMPLE_SVG)
    ids = {n.id for n in res.graph.nodes}
    assert "bg" in ids
    assert "title" in ids
    assert "matrix_a" in ids
    assert "cell_0_0" in ids
    assert "cell_0_1" in ids


def test_parser_canvas_dims():
    res = svg_to_graph.parse_svg(SIMPLE_SVG)
    assert res.graph.canvas_w == 900
    assert res.graph.canvas_h == 600
    assert res.graph.viewbox == (0.0, 0.0, 900.0, 600.0)


def test_parser_transform_applied():
    """cell_0_0 is at local (0,0) inside matrix_a which is
    translated to (200,100). The parsed bbox should land at (200,100)
    in canvas coordinates."""
    res = svg_to_graph.parse_svg(SIMPLE_SVG)
    cell = next(n for n in res.graph.nodes if n.id == "cell_0_0")
    x, y, w, h = cell.bbox
    assert x == pytest.approx(200, abs=0.5)
    assert y == pytest.approx(100, abs=0.5)
    assert w == pytest.approx(40, abs=0.5)
    assert h == pytest.approx(40, abs=0.5)


def test_parser_narration_anchor_flag():
    res = svg_to_graph.parse_svg(SIMPLE_SVG)
    cell = next(n for n in res.graph.nodes if n.id == "cell_0_0")
    matrix = next(n for n in res.graph.nodes if n.id == "matrix_a")
    assert cell.is_narration_anchor is True
    assert matrix.is_narration_anchor is True


def test_parser_parent_edges():
    res = svg_to_graph.parse_svg(SIMPLE_SVG)
    edge_relations = {(e.src_id, e.dst_id, e.relation)
                      for e in res.graph.edges}
    assert ("matrix_a", "cell_0_0", "parent_of") in edge_relations
    assert ("matrix_a", "cell_0_1", "parent_of") in edge_relations


def test_parser_group_bbox_is_union_of_children():
    """matrix_a wraps two 40×40 cells side by side, so its bbox
    should be 80×40 (after the translate(200,100) shifts it)."""
    res = svg_to_graph.parse_svg(SIMPLE_SVG)
    g = next(n for n in res.graph.nodes if n.id == "matrix_a")
    x, y, w, h = g.bbox
    assert x == pytest.approx(200, abs=0.5)
    assert y == pytest.approx(100, abs=0.5)
    assert w == pytest.approx(80, abs=0.5)
    assert h == pytest.approx(40, abs=0.5)


def test_parser_malformed_svg_no_raise():
    res = svg_to_graph.parse_svg("<svg><not closed>")
    assert any("parse_error" in w for w in res.warnings)
    assert res.graph.nodes == []


# ── exporter from teacher corpus ───────────────────────────────────


_CORPUS = Path(
    "/home/ara/Documents/Programming/sevim_plugin/"
    "data/distill/teacher_v6_mini.jsonl"
)


@pytest.mark.skipif(not _CORPUS.exists(), reason="corpus file missing")
def test_exporter_from_teacher_corpus_extracts_corrected_pairs():
    """Read the first mode=corrected row from the existing corpus
    and verify the exporter produces a valid TrainingPair."""
    target_row = None
    with _CORPUS.open() as fh:
        for line in fh:
            row = json.loads(line)
            if (row.get("meta") or {}).get("mode") == "corrected":
                target_row = row
                break
    assert target_row is not None, "no corrected rows in corpus"
    pairs = exporter.pairs_from_teacher_corpus_row(target_row)
    assert len(pairs) == 1
    p = pairs[0]
    assert p.prompt
    assert len(p.source.nodes) > 0
    assert len(p.target.nodes) > 0
    assert p.viewport_kind in schema.VIEWPORT_KINDS
    assert p.math_bucket in schema.MATH_BUCKETS
    assert "teacher_v6_mini" in (p.metadata or {}).get("source", "")
    # round-trip the produced pair
    p2 = TrainingPair.from_dict(json.loads(p.to_json()))
    assert p2.pair_id == p.pair_id


@pytest.mark.skipif(not _CORPUS.exists(), reason="corpus file missing")
def test_exporter_skips_clean_rows():
    """clean-mode rows have no bad_svg → exporter must return []."""
    with _CORPUS.open() as fh:
        for line in fh:
            row = json.loads(line)
            if (row.get("meta") or {}).get("mode") == "clean":
                assert exporter.pairs_from_teacher_corpus_row(row) == []
                return


# ── exporter from express_result ───────────────────────────────────


def test_exporter_from_express_result_emits_repair_pairs():
    fake_result = {
        "svg": SIMPLE_SVG,
        "narration": [],
        "title": "",
        "retries_used": 2,
        "review_history": [],
        "repairs": [
            {
                "attempt_index": 0,
                "bad_svg": SIMPLE_SVG.replace("Hello", "Hello1"),
                "bad_narration": [],
                "critique": "fix something",
                "good_svg": SIMPLE_SVG.replace("Hello", "Hello2"),
                "good_narration": [],
            },
            {
                "attempt_index": 1,
                "bad_svg": SIMPLE_SVG.replace("Hello", "Hello2"),
                "bad_narration": [],
                "critique": "fix again",
                "good_svg": SIMPLE_SVG,
                "good_narration": [],
            },
        ],
    }
    pairs = exporter.pairs_from_express_result(
        "show the unit circle", fake_result,
    )
    # 2 repairs + 1 long-distance = 3
    assert len(pairs) == 3
    sources = [p.metadata.get("source") for p in pairs]
    assert "express_live" in sources
    assert "express_live_long" in sources
    for p in pairs:
        assert p.prompt == "show the unit circle"
        assert p.viewport_kind == "desktop"
