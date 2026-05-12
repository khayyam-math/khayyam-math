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


# ---------------------------------------------------------------------
# Bottom overflow with unused right column — the figure spills past
# the bottom band while the right half is empty.
# ---------------------------------------------------------------------

def test_bottom_overflow_with_unused_right_flagged():
    # Everything in left column, last item at y=625 in a 650-tall vb,
    # right half (x > 495) has no text at all.
    svg = (
        "<svg viewBox='0 0 900 650'>"
        "<text x='50' y='100'>step 1</text>"
        "<text x='50' y='200'>step 2</text>"
        "<text x='50' y='400'>step 3</text>"
        "<text x='50' y='625'>step 4 falls off the bottom</text>"
        "</svg>"
    )
    issues = _structural_review(svg, [])
    assert any("bottom_overflow_with_unused_right" in i for i in issues), issues


def test_two_column_layout_does_not_overflow():
    # Same content split across two columns, no element below y=620.
    svg = (
        "<svg viewBox='0 0 900 650'>"
        "<text x='50' y='100'>step 1</text>"
        "<text x='50' y='200'>step 2</text>"
        "<text x='500' y='100'>step 3</text>"
        "<text x='500' y='200'>step 4</text>"
        "</svg>"
    )
    assert _structural_review(svg, []) == []


# ---------------------------------------------------------------------
# autofit_group_rects — the deterministic layout pass that resizes the
# outer <rect> of each <g> to wrap its children.
# ---------------------------------------------------------------------

from studio.express import autofit_group_rects


def test_autofit_grows_undersized_matrix_rect():
    """A 3×3 matrix drawn with a 200×200 rect but cells out at (350, 340)
    should get its rect expanded so it actually contains every cell."""
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<g id="matrix_a">'
        '<rect x="100" y="100" width="200" height="200" stroke="black"/>'
        '<text x="150" y="140">a11</text>'
        '<text x="250" y="140">a12</text>'
        '<text x="350" y="140">a13</text>'
        '<text x="150" y="240">a21</text>'
        '<text x="150" y="340">a31</text>'
        '<text x="350" y="340">a33</text>'
        '</g></svg>'
    )
    fixed = autofit_group_rects(svg)
    # The rect must now extend at least to the rightmost text + width.
    import re
    m = re.search(r'<rect[^>]*x="(\d+)"[^>]*y="(\d+)"[^>]*width="(\d+)"[^>]*height="(\d+)"', fixed)
    assert m, fixed
    rx, ry, rw, rh = map(int, m.groups())
    # Rightmost label is "a33" at x=350; rightmost char is around x=370+.
    assert rx + rw >= 370, f"rect right edge {rx+rw} should cover x=350+"
    # Bottommost label is at y=340 (baseline); rect bottom should reach
    # at least y=340 (and ideally a bit past for descenders).
    assert ry + rh >= 340


def test_autofit_leaves_correctly_sized_rect_alone():
    """A group whose rect already contains its children is untouched."""
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<g id="ok">'
        '<rect x="50" y="50" width="500" height="500"/>'
        '<text x="100" y="100">small label</text>'
        '</g></svg>'
    )
    fixed = autofit_group_rects(svg)
    # Idempotent — same SVG out.
    assert fixed == svg


def test_autofit_handles_single_quoted_attrs():
    """gpt-4o-mini emits single-quoted attributes; the layout pass must
    still detect overflow."""
    svg = (
        "<svg viewBox='0 0 900 650'>"
        "<g id='matrix_a'>"
        "<rect x='100' y='100' width='200' height='200'/>"
        "<text x='350' y='340'>a33</text>"
        "</g></svg>"
    )
    fixed = autofit_group_rects(svg)
    assert 'width="200"' not in fixed, "rect should have been resized"


def test_autofit_no_rect_in_group_passes_through():
    """A <g> with no outer rect (only labels) is left alone — there's
    nothing to resize."""
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<g><text x="100" y="100">just a label</text></g></svg>'
    )
    assert autofit_group_rects(svg) == svg
