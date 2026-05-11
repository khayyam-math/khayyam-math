"""Tests for studio.express._structural_review.

Locks in the deterministic checks that catch failures the vision
reviewer can't reliably see from a PNG: narration highlights pointing
at non-existent SVG ids, and graph figures missing vertex labels.
"""
from __future__ import annotations

from studio.express import _structural_review


# ---------------------------------------------------------------------
# Highlight-id integrity
# ---------------------------------------------------------------------

def test_highlight_ids_present_pass():
    svg = '<svg><circle id="v1"/><circle id="v2"/></svg>'
    narration = [
        {"speak": "look at v1", "highlight": ["v1"]},
        {"speak": "look at v2", "highlight": ["v2"]},
        {"speak": "neither in particular", "highlight": []},
    ]
    assert _structural_review(svg, narration) == []


def test_highlight_id_missing_flags():
    svg = '<svg><circle id="v1"/></svg>'
    narration = [
        {"speak": "look at v1", "highlight": ["v1"]},
        {"speak": "look at v999", "highlight": ["v999"]},  # bogus id
    ]
    issues = _structural_review(svg, narration)
    assert len(issues) == 1
    assert "narration_highlight_id_missing" in issues[0]
    assert "v999" in issues[0]
    # ...but v1 (which exists) must not be flagged
    assert "phrase[0] -> 'v1'" not in issues[0]


def test_highlight_id_accepts_string_or_list():
    """Some models emit a single string instead of a one-element list."""
    svg = '<svg><circle id="v1"/></svg>'
    narration = [{"speak": "x", "highlight": "v1"}]
    assert _structural_review(svg, narration) == []


def test_highlight_empty_list_is_fine():
    svg = '<svg><circle id="v1"/></svg>'
    narration = [{"speak": "intro", "highlight": []}]
    assert _structural_review(svg, narration) == []


def test_highlight_single_quoted_ids_recognised():
    """SVG id attributes may use single quotes."""
    svg = "<svg><circle id='v1'/></svg>"
    narration = [{"speak": "x", "highlight": ["v1"]}]
    assert _structural_review(svg, narration) == []


# ---------------------------------------------------------------------
# Vertex-label heuristic
# ---------------------------------------------------------------------

def test_graph_with_labels_passes():
    svg = (
        '<svg>'
        '<circle id="v1"/><text>1</text>'
        '<circle id="v2"/><text>2</text>'
        '<circle id="v3"/><text>3</text>'
        '<circle id="v4"/><text>4</text>'
        '<circle id="v5"/><text>5</text>'
        '</svg>'
    )
    assert _structural_review(svg, []) == []


def test_graph_missing_labels_flagged():
    """Five vertices, only two text labels — the symptom from the
    user's report."""
    svg = (
        '<svg>'
        '<circle id="v1"/><text>1</text>'
        '<circle id="v2"/><text>2</text>'
        '<circle id="v3"/>'
        '<circle id="v4"/>'
        '<circle id="v5"/>'
        '</svg>'
    )
    issues = _structural_review(svg, [])
    assert len(issues) == 1
    assert "vertex_labels_missing" in issues[0]
    assert "5" in issues[0] and "2" in issues[0]


def test_small_graph_does_not_trip_heuristic():
    """Under the 4-vertex threshold the heuristic stays silent;
    a triangle-with-no-labels is a valid figure for many topics."""
    svg = '<svg><circle id="a"/><circle id="b"/><circle id="c"/></svg>'
    assert _structural_review(svg, []) == []


def test_decorative_circles_dont_count():
    """Circles without an id are decorative (chart background,
    dashed outline, etc.) and don't trigger the heuristic."""
    svg = '<svg>' + '<circle r="2"/>' * 10 + '</svg>'
    assert _structural_review(svg, []) == []


# ---------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------

def test_empty_inputs():
    assert _structural_review("", []) == []
    assert _structural_review("", None) == []
    assert _structural_review("<svg/>", []) == []
