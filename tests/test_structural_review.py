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


# ---------------------------------------------------------------------
# Label-inside-wrong-vertex: catches the failure where a vertex label
# letter ends up sitting inside a different vertex's circle.
# ---------------------------------------------------------------------

def test_label_in_correct_vertex_passes():
    svg = (
        '<svg>'
        '<circle id="A" cx="100" cy="100" r="20"/>'
        '<circle id="B" cx="200" cy="100" r="20"/>'
        '<text x="100" y="100">A</text>'
        '<text x="200" y="100">B</text>'
        '</svg>'
    )
    assert _structural_review(svg, []) == []


def test_label_inside_wrong_vertex_flagged():
    # Vertex A is at (100,100), B at (200,100); but the "A" label is
    # placed at (200,100) — inside B's circle.
    svg = (
        '<svg>'
        '<circle id="A" cx="100" cy="100" r="20"/>'
        '<circle id="B" cx="200" cy="100" r="20"/>'
        '<text x="200" y="100">A</text>'
        '<text x="100" y="100">B</text>'
        '</svg>'
    )
    issues = _structural_review(svg, [])
    assert any("label_inside_wrong_vertex" in i for i in issues), issues


def test_long_caption_inside_vertex_not_flagged():
    # A multi-word caption that happens to land near a vertex is NOT
    # treated as a mis-placed letter — only short (<= 5 char) tokens
    # are eligible.
    svg = (
        '<svg>'
        '<circle id="A" cx="100" cy="100" r="20"/>'
        '<text x="100" y="100">starting node</text>'
        '</svg>'
    )
    assert _structural_review(svg, []) == []


# ---------------------------------------------------------------------
# Out-of-bounds: text starting past the viewBox edges.
# ---------------------------------------------------------------------

def test_in_bounds_text_passes():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<text x="450" y="320" text-anchor="middle">centered caption</text>'
        '</svg>'
    )
    assert _structural_review(svg, []) == []


def test_text_past_right_edge_flagged():
    # 900-wide viewBox; text starts at x=920 (off-canvas).
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<text x="920" y="100">offscreen formula</text>'
        '</svg>'
    )
    issues = _structural_review(svg, [])
    assert any("out_of_bounds" in i for i in issues), issues


def test_text_below_bottom_flagged():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<text x="100" y="800">below the canvas</text>'
        '</svg>'
    )
    issues = _structural_review(svg, [])
    assert any("out_of_bounds" in i for i in issues), issues


# ---------------------------------------------------------------------
# Caption-overlaps-diagram: caption text inside a diagram rect/circle.
# ---------------------------------------------------------------------

def test_caption_in_margin_passes():
    # Caption is at the top margin; diagram rect lives well below.
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<text x="20" y="30">SAT to 3SAT reduction</text>'
        '<rect id="diagram" x="100" y="200" width="700" height="300"'
        ' fill="none" stroke="black"/>'
        '</svg>'
    )
    assert _structural_review(svg, []) == []


def test_caption_overlapping_diagram_box_flagged():
    # The caption text sits dead-centre inside a labelled diagram rect.
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<rect id="step_3_box" x="100" y="200" width="500" height="200"'
        ' fill="lightblue" stroke="black"/>'
        '<text x="160" y="300">Each clause is now exactly three literals</text>'
        '</svg>'
    )
    issues = _structural_review(svg, [])
    assert any("caption_overlaps_diagram" in i for i in issues), issues


# ---------------------------------------------------------------------
# Single-quoted SVG attributes — gpt-4o-mini emits single quotes in
# practice, the regex had a long-standing bug where every check
# silently no-op'd against such SVGs.
# ---------------------------------------------------------------------

def test_single_quoted_out_of_bounds_caught():
    """gpt-4o-mini emits SVG with single quotes; the structural critic
    must still catch out-of-bounds in that form."""
    svg = (
        "<svg viewBox='0 0 900 650'>"
        "<text x='100' y='450' id='formula'>"
        "det(A) = a_{11}(a_{22}a_{33} - a_{23}a_{32}) - "
        "a_{12}(a_{21}a_{33} - a_{23}a_{31}) + "
        "a_{13}(a_{21}a_{32} - a_{22}a_{31})</text></svg>"
    )
    issues = _structural_review(svg, [])
    assert any("out_of_bounds" in i for i in issues), issues


# ---------------------------------------------------------------------
# LaTeX source masquerading as math — <text>a_{ij}</text> renders as
# literal "a_{ij}" not as a subscripted symbol.
# ---------------------------------------------------------------------

def test_latex_subscript_in_text_flagged():
    svg = '<svg viewBox="0 0 900 650"><text x="40" y="40">a_{11} + a_{22}</text></svg>'
    issues = _structural_review(svg, [])
    assert any("latex_source_in_text" in i for i in issues), issues


def test_latex_command_in_text_flagged():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<text x="40" y="40">\\sum_{i=1}^{n} a_i \\theta</text>'
        '</svg>'
    )
    issues = _structural_review(svg, [])
    assert any("latex_source_in_text" in i for i in issues), issues


def test_unicode_math_passes():
    """Σθ as actual Unicode glyphs should pass; subscripts via tspan
    should pass."""
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<text x="40" y="40">Σ θ = 1, a'
        '<tspan baseline-shift="sub" font-size="80%">ij</tspan>'
        ' = 0</text></svg>'
    )
    assert _structural_review(svg, []) == []
