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
        # Final phrase must STATE the result (missing_conclusion check).
        {"speak": "Therefore v1 and v2 are adjacent.", "highlight": []},
    ]
    assert _structural_review(svg, narration) == []


def test_highlight_id_missing_flags():
    svg = '<svg><circle id="v1"/></svg>'
    narration = [
        {"speak": "look at v1", "highlight": ["v1"]},
        # Closer that satisfies missing_conclusion so the test only
        # asserts the one issue it's actually checking.
        {"speak": "Therefore v999 = v1.", "highlight": ["v999"]},  # bogus id
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
    # Includes a primary shape so the no_geometric_primitive check
    # doesn't legitimately fire on this overlap-detection test.
    svg = (
        "<svg viewBox='0 0 900 650'>"
        "<circle cx='450' cy='300' r='100'/>"
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


def test_autofit_shrinks_oversized_rect_around_small_content():
    """When the rect is much larger than its children's bbox, shrink
    it down — the previous "leave it alone" behaviour left huge empty
    boxes around small matrices and was the source of a user
    complaint ("the boxes around the matrices are huge")."""
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<g id="m">'
        '<rect x="50" y="50" width="500" height="400" stroke="black"/>'
        '<text x="100" y="100">a</text>'
        '<text x="150" y="100">b</text>'
        '</g></svg>'
    )
    fixed = autofit_group_rects(svg)
    import re
    m = re.search(r'<rect[^>]*width="(\d+)"[^>]*height="(\d+)"', fixed)
    assert m, fixed
    w, h = int(m.group(1)), int(m.group(2))
    # Should be much smaller than 500x400 — the content only spans
    # ~50 px wide and ~16 px tall.
    assert w < 200, f"rect width {w} still too big"
    assert h < 100, f"rect height {h} still too big"


def test_autofit_idempotent_on_tightly_wrapped_rect():
    """A rect already padded around its children stays unchanged."""
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<g id="m">'
        '<rect x="80" y="68" width="100" height="55"/>'
        '<text x="100" y="100">a</text>'
        '<text x="150" y="100">b</text>'
        '</g></svg>'
    )
    fixed = autofit_group_rects(svg)
    # Should be stable: re-running yields the same SVG.
    assert autofit_group_rects(fixed) == fixed


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


# ---------------------------------------------------------------------
# fit_node_boxes_to_labels — shrinks oversized flow/node boxes to their
# own labels so neighbouring boxes stop overlapping.  Gated on the
# presence of a connector arrow so it never touches matrices/tables.
# ---------------------------------------------------------------------

from studio.express import fit_node_boxes_to_labels


def _rects(svg: str):
    import re
    out = []
    for tag in re.findall(r'<rect\b[^>]*?/?>', svg):
        a = dict(re.findall(r'([A-Za-z_-]+)\s*=\s*"([^"]*)"', tag))
        try:
            out.append((float(a["x"]), float(a["y"]),
                        float(a["width"]), float(a["height"])))
        except (KeyError, ValueError):
            pass
    return out


def _overlap(a, b) -> bool:
    ax, ay, aw, ah = a; bx, by, bw, bh = b
    return ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by


def test_fit_node_boxes_separates_oversized_overlapping_boxes():
    """The real 'partition' regression: box1 is 333px wide for the label
    'Set S', so box2 falls inside it.  Sizing each box to its own label
    must make the two boxes disjoint."""
    svg = (
        "<svg viewBox='-24 -24 935 574'>"
        "<g id='partition_problem'>"
        "<rect x='271' y='196' width='333' height='58' fill='#e0e0e0'/>"
        "<text x='275' y='230' font-size='16'>Set S</text>"
        "<rect x='450' y='200' width='150' height='50' fill='#e0e0e0'/>"
        "<text x='475' y='230' font-size='16'>Subsets</text>"
        "<line x1='400' y1='225' x2='450' y2='225' marker-end='url(#arrow)'/>"
        "</g></svg>"
    )
    fixed = fit_node_boxes_to_labels(svg)
    boxes = _rects(fixed)
    assert len(boxes) == 2, fixed
    assert not _overlap(boxes[0], boxes[1]), f"boxes still overlap: {boxes}"
    # The oversized box must have shrunk.
    assert boxes[0][2] < 333, f"box1 width not reduced: {boxes}"
    # Labels are re-centred → text-anchor=middle appears.
    assert 'text-anchor="middle"' in fixed


def test_fit_node_boxes_idempotent():
    svg = (
        "<svg viewBox='0 0 900 574'>"
        "<g id='flow'>"
        "<rect x='100' y='100' width='300' height='60' fill='#ddd'/>"
        "<text x='110' y='135' font-size='16'>Start</text>"
        "<rect x='200' y='110' width='140' height='40' fill='#ddd'/>"
        "<text x='220' y='135' font-size='16'>End</text>"
        "<line x1='150' y1='130' x2='200' y2='130' marker-end='url(#a)'/>"
        "</g></svg>"
    )
    once = fit_node_boxes_to_labels(svg)
    assert fit_node_boxes_to_labels(once) == once


def test_fit_node_boxes_leaves_matrix_untouched():
    """A matrix group has NO connector arrow, so the node-box fitter must
    not touch it — that stays the job of autofit_group_rects."""
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<g id="matrix_a">'
        '<rect x="100" y="100" width="200" height="200" stroke="black"/>'
        '<text x="150" y="140">a11</text>'
        '<text x="250" y="140">a12</text>'
        '<text x="150" y="240">a21</text>'
        '</g></svg>'
    )
    assert fit_node_boxes_to_labels(svg) == svg


def test_fit_node_boxes_no_arrow_passes_through():
    """Even a box+label group is left alone when there's no arrow — the
    arrow is the flow-diagram signature that gates this pass."""
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<g><rect x="50" y="50" width="400" height="80" fill="#eee"/>'
        '<text x="60" y="95" font-size="16">lonely</text></g></svg>'
    )
    assert fit_node_boxes_to_labels(svg) == svg


# ---------------------------------------------------------------------
# reflow_overlapping_text — greedy 2-D layout pass that nudges
# top-level <text> elements apart when their bounding boxes collide.
# ---------------------------------------------------------------------

from studio.express import reflow_overlapping_text


def test_reflow_shifts_overlapping_formulas_down():
    """A long formula at x=20,y=290 overlapping three short formulas
    on the same y must get the three short ones shifted DOWN until
    they clear."""
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<text x="20" y="290" font-size="24">'
        'det(A) = a11.det(M11) - a12.det(M12) + a13.det(M13)</text>'
        '<text x="300" y="290" font-size="24">det(M11)=a22.a33-a23.a31</text>'
        '<text x="450" y="290" font-size="24">det(M12)=a21.a33-a23.a32</text>'
        '<text x="600" y="290" font-size="24">det(M13)=a21.a32-a22.a33</text>'
        '</svg>'
    )
    out = reflow_overlapping_text(svg)
    import re
    ys = [int(y) for y in re.findall(r'y="(\d+)"', out)]
    # First element stays at 290; the next three must each be strictly
    # below the previous one to avoid mutual overlap.
    assert ys[0] == 290
    for prev, cur in zip(ys, ys[1:]):
        assert cur > prev, f"{ys} not strictly increasing"


def test_reflow_leaves_non_overlapping_alone():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<text x="50" y="100" font-size="20">label A</text>'
        '<text x="50" y="200" font-size="20">label B</text>'
        '<text x="50" y="300" font-size="20">label C</text>'
        '</svg>'
    )
    assert reflow_overlapping_text(svg) == svg


def test_reflow_skips_text_inside_groups():
    """Matrix cells inside a <g> aren't candidates for reflow — the
    autofit_group_rects pass + the bordered grid keep them coherent."""
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<g id="matrix"><rect x="100" y="100" width="200" height="200"/>'
        '<text x="150" y="150" font-size="24">a</text>'
        '<text x="150" y="180" font-size="24">b</text>'
        '</g></svg>'
    )
    # Text inside <g> should pass through untouched.
    out = reflow_overlapping_text(svg)
    assert 'y="150"' in out and 'y="180"' in out


def test_reflow_jumps_to_new_column_when_narrow_text_fits():
    """Repeated overlaps where the text is NARROW enough to fit a
    second column should bump elements into x≈500."""
    items = "".join(
        f'<text x="20" y="600" font-size="20">label {i}</text>'
        for i in range(6)
    )
    svg = f'<svg viewBox="0 0 900 650">{items}</svg>'
    out = reflow_overlapping_text(svg)
    import re
    xs = [int(x) for x in re.findall(r'x="(\d+)"', out)]
    assert any(x >= 400 for x in xs), f"no column jump for narrow items: {xs}"


def test_reflow_stacks_past_bottom_when_too_wide_for_column2():
    """When text is too wide to fit in the second column, the reflow
    accepts a tall figure (canvas viewer has overflow:scroll) rather
    than leaving the items overlapping each other.  This is the
    "8 wide formulas at y=600" failure mode."""
    items = "".join(
        f'<text x="20" y="600" font-size="24">'
        f'long formula number {i} that takes a lot of horizontal space</text>'
        for i in range(5)
    )
    svg = f'<svg viewBox="0 0 900 650">{items}</svg>'
    out = reflow_overlapping_text(svg)
    import re
    ys = [int(y) for y in re.findall(r'y="(\d+)"', out)]
    for prev, cur in zip(ys, ys[1:]):
        assert cur > prev, f"items still overlap: {ys}"


# ---------------------------------------------------------------------
# fix_html_subsup — replace HTML <sup>/<sub> with SVG <tspan>.
# ---------------------------------------------------------------------

from studio.express import fix_html_subsup, reflow_overlapping_groups


def test_fix_html_sup_converts_to_tspan():
    svg = '<svg><text>A<sup>-1</sup> = ...</text></svg>'
    out = fix_html_subsup(svg)
    assert "<sup>" not in out and "</sup>" not in out
    assert 'baseline-shift="super"' in out
    assert ">-1</tspan>" in out


def test_fix_html_sub_converts_to_tspan():
    svg = '<svg><text>x<sub>i</sub> = 0</text></svg>'
    out = fix_html_subsup(svg)
    assert "<sub>" not in out
    assert 'baseline-shift="sub"' in out


def test_fix_html_subsup_idempotent_on_tspan():
    svg = ('<svg><text>x<tspan baseline-shift="sub" font-size="80%">i</tspan>'
           '</text></svg>')
    assert fix_html_subsup(svg) == svg


# ---------------------------------------------------------------------
# reflow_overlapping_groups — slide overlapping <g> bboxes apart.
# ---------------------------------------------------------------------

def test_groups_overlapping_horizontally_get_shifted():
    """matrix_a at x=20-310 overlaps matrix_a_inverse at x=200-396 —
    the second group must be translated right past the first."""
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<g id="a"><rect x="20" y="100" width="290" height="151"/></g>'
        '<g id="b"><rect x="200" y="100" width="196" height="128"/></g>'
        '</svg>'
    )
    out = reflow_overlapping_groups(svg)
    import re
    m = re.search(r'<g\s+id="b"[^>]*transform="translate\((\d+)\s+0\)"', out)
    assert m, f"expected translate transform on group b: {out}"
    dx = int(m.group(1))
    # Original x=200 + dx must be >= 310 (end of group a) + some pad.
    assert 200 + dx >= 310, f"shift {dx} insufficient"


def test_non_overlapping_groups_unchanged():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<g><rect x="20" y="100" width="200" height="100"/></g>'
        '<g><rect x="500" y="100" width="200" height="100"/></g>'
        '</svg>'
    )
    assert reflow_overlapping_groups(svg) == svg


# ---------------------------------------------------------------------
# normalize_matrix_layout — recompute cell positions on a true lattice.
# ---------------------------------------------------------------------

from studio.express import normalize_matrix_layout


def test_matrix_4x4_with_3_cols_plus_stragglers_rebuilt():
    """The screenshot failure: 4×4 matrix where col 4 got stacked
    below cols 1–3 instead of beside them.  Normaliser must put all
    16 cells on a regular 4-column lattice."""
    cells = []
    for i in range(1, 5):
        for j in range(1, 5):
            if j < 4:
                x = 20 + (j-1) * 130
                y = 100 + (i-1) * 40
            else:
                # Col 4 stranded at x=20 below the others (the bug).
                x = 20
                y = 300 + (i-1) * 40
            cells.append(f'<text x="{x}" y="{y}" font-size="20">a_{{{i}{j}}} = 0</text>')
    svg = '<svg viewBox="0 0 900 650">' + ''.join(cells) + '</svg>'
    out = normalize_matrix_layout(svg)
    # All cells with the same i (row) now share a y.
    import re
    rows = {}
    for m in re.finditer(r'<text x="(\d+)" y="(\d+)"[^>]*>a_\{(\d)(\d)\}', out):
        x, y, i, j = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        rows.setdefault(i, []).append((j, x, y))
    for i, items in rows.items():
        ys = {it[2] for it in items}
        assert len(ys) == 1, f"row {i} cells on different ys: {ys}"
        # And columns within a row monotonically increasing x.
        items.sort()
        xs = [it[1] for it in items]
        assert xs == sorted(xs), f"row {i} xs not monotonic: {xs}"


def test_unicode_subscript_cells_normalised():
    """The Unicode form a₁₁, a₂₂, … is recognised the same way as
    a_11, a_{1,1}, etc."""
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<text x="40" y="100" font-size="20">a₁₁ = 4</text>'
        '<text x="40" y="140" font-size="20">a₂₁ = 3</text>'
        '<text x="200" y="100" font-size="20">a₁₂ = 2</text>'
        '<text x="200" y="140" font-size="20">a₂₂ = 1</text>'
        '</svg>'
    )
    out = normalize_matrix_layout(svg)
    # Final layout: row 1 (i=1) at one y; row 2 (i=2) at another.
    import re
    cells = re.findall(r'<text x="(\d+)" y="(\d+)"[^>]*>(a[₀-₉]+)', out)
    assert len(cells) == 4
    y_by_row = {}
    for x, y, content in cells:
        # extract i
        sub = content[1:]
        UNI_SUB = "₀₁₂₃₄₅₆₇₈₉"
        i = UNI_SUB.index(sub[0])
        y_by_row.setdefault(i, set()).add(y)
    for i, ys in y_by_row.items():
        assert len(ys) == 1, f"row {i} ys: {ys}"


def test_incomplete_matrix_left_alone():
    """If only 3 cells of a supposed 2×2 are present, don't try to
    re-layout — the model might be doing something intentional."""
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<text x="100" y="100" font-size="20">a_{11} = 1</text>'
        '<text x="200" y="100" font-size="20">a_{12} = 2</text>'
        '<text x="100" y="150" font-size="20">a_{21} = 3</text>'
        '</svg>'
    )
    # Should pass through unchanged.
    assert normalize_matrix_layout(svg) == svg


def test_toplevel_text_avoids_group_internal_text():
    """The bug surfaced as: <text> inside a <g> sat at y=610,
    a TOP-LEVEL <text> at y=620 overlapped it.  The group-internal
    text must stay put (matrix cells shouldn't move) but the
    top-level text must shift to clear it."""
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<g id="det">'
        '<text x="20" y="610" font-size="20">det(A) = 1(4*0 - 2*6) - 2(3*0 - 2*7) + 3(3*6 - 4*7)</text>'
        '</g>'
        '<text x="20" y="620" font-size="16">The inverse of matrix A is given by ...</text>'
        '</svg>'
    )
    out = reflow_overlapping_text(svg)
    import re
    # Group-internal text unchanged.
    assert '<text x="20" y="610"' in out
    # Top-level "The inverse" text either shifted DOWN past the
    # group (y >= 625) OR sideways into a second column (x >= 400).
    # Either resolution is acceptable — just don't overlap.
    m = re.search(
        r'<text x="(\d+)" y="(\d+)"[^>]*>The inverse',
        out,
    )
    assert m, f"top-level inverse text not found in {out}"
    x, y = int(m.group(1)), int(m.group(2))
    assert y >= 625 or x >= 400, (
        f"top-level text at ({x},{y}) still overlaps group text at (20,610)"
    )


# ---------------------------------------------------------------------
# clamp_text_to_viewbox — pull negative-y / negative-x text back inside
# the canvas before any reflow pass runs.
# ---------------------------------------------------------------------

from studio.express import clamp_text_to_viewbox


def test_clamp_pulls_negative_y_inside():
    """Model occasionally places section headers at y=-36 hoping for
    clipping; clamp must pull them back to TOP_MARGIN inside vb."""
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<text x="20" y="-36">Clause Gadgets:</text>'
        '<text x="20" y="-36">Variable Gadgets:</text>'
        '</svg>'
    )
    out = clamp_text_to_viewbox(svg)
    import re
    ys = [int(y) for y in re.findall(r'y="(\-?\d+)"', out)]
    assert all(y >= 20 for y in ys), f"clamp left negative ys: {ys}"


def test_clamp_idempotent_on_inside_text():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<text x="50" y="100">already inside</text>'
        '</svg>'
    )
    assert clamp_text_to_viewbox(svg) == svg


def test_clamp_then_reflow_separates_stacked_headers():
    """Three headers at the same negative y must end up at three
    distinct positive ys after clamp + reflow."""
    from studio.express import reflow_overlapping_text
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<text x="20" y="-36" font-size="20">A:</text>'
        '<text x="20" y="-36" font-size="20">B:</text>'
        '<text x="20" y="-36" font-size="20">C:</text>'
        '</svg>'
    )
    out = reflow_overlapping_text(clamp_text_to_viewbox(svg))
    import re
    ys = [int(y) for y in re.findall(r'y="(\d+)"', out)]
    assert len(ys) == 3 and len(set(ys)) == 3, f"ys not distinct: {ys}"
    for prev, cur in zip(ys, ys[1:]):
        assert cur > prev, f"ys not monotonic: {ys}"


# ---------------------------------------------------------------------
# named_quantity_not_shown — narration mentions a labelled measurement
# (height h, base b₁, radius r, angle θ, ...) that the SVG never draws.
# ---------------------------------------------------------------------

def test_named_quantity_shown_passes_when_label_present():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<polygon points="100,400 400,400 350,200 150,200" id="trap"/>'
        '<line x1="250" y1="200" x2="250" y2="400" stroke-dasharray="4 4"/>'
        '<text x="260" y="310">h</text>'
        '<text x="250" y="420">b</text>'
        '</svg>'
    )
    narration = [
        {"speak": "Here is a trapezoid.", "highlight": []},
        {"speak": "The height h is 4 units.", "highlight": []},
    ]
    issues = _structural_review(svg, narration)
    assert not any("named_quantity_not_shown" in i for i in issues), issues


def test_named_quantity_not_shown_flags_height_without_label():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<polygon points="100,400 400,400 350,200 150,200"/>'
        '<text x="250" y="420">b</text>'
        '</svg>'
    )
    narration = [
        {"speak": "The trapezoid has bases b₁ and b₂.", "highlight": []},
        {"speak": "The height h equals 4.", "highlight": []},
    ]
    issues = _structural_review(svg, narration)
    flagged = [i for i in issues if "named_quantity_not_shown" in i]
    assert len(flagged) == 1
    assert "'height h'" in flagged[0]


def test_named_quantity_flags_multiple_missing():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<circle cx="200" cy="200" r="80"/>'
        '</svg>'
    )
    narration = [
        {"speak": "The radius r is 80 units.", "highlight": []},
        {"speak": "The diameter d is 160 units.", "highlight": []},
    ]
    issues = _structural_review(svg, narration)
    flagged = [i for i in issues if "named_quantity_not_shown" in i]
    assert len(flagged) == 1
    assert "'radius r'" in flagged[0]
    assert "'diameter d'" in flagged[0]


def test_named_quantity_passes_when_label_has_value():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<polygon points="100,400 400,400 350,200 150,200"/>'
        '<text x="260" y="310">h = 4</text>'
        '</svg>'
    )
    narration = [{"speak": "The height h is 4.", "highlight": []}]
    issues = _structural_review(svg, narration)
    assert not any("named_quantity_not_shown" in i for i in issues)


def test_named_quantity_passes_when_unicode_subscript_label():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<polygon points="100,400 400,400 350,200 150,200"/>'
        '<text x="250" y="420">b₁</text>'
        '<text x="250" y="190">b₂</text>'
        '<text x="260" y="310">h</text>'
        '</svg>'
    )
    narration = [
        {"speak": "The base b₁ is the longer parallel side.", "highlight": []},
        {"speak": "The base b₂ is the shorter parallel side.", "highlight": []},
        {"speak": "The height h equals 4.", "highlight": []},
    ]
    issues = _structural_review(svg, narration)
    assert not any("named_quantity_not_shown" in i for i in issues), issues


def test_named_quantity_passes_when_quantity_word_used_as_label():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<polygon points="100,400 400,400 350,200 150,200"/>'
        '<text x="260" y="310">height</text>'
        '</svg>'
    )
    narration = [{"speak": "The height h is 4.", "highlight": []}]
    issues = _structural_review(svg, narration)
    assert not any("named_quantity_not_shown" in i for i in issues)


def test_named_quantity_ignores_quantity_without_letter():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<polygon points="100,400 400,400 350,200 150,200"/>'
        '</svg>'
    )
    narration = [
        {"speak": "Now we compute the height of the trapezoid.", "highlight": []},
    ]
    issues = _structural_review(svg, narration)
    assert not any("named_quantity_not_shown" in i for i in issues)


def test_named_quantity_ignores_base_case_idiom():
    svg = '<svg viewBox="0 0 900 650"><circle/></svg>'
    narration = [
        {"speak": "In the base case the recursion terminates.", "highlight": []},
    ]
    issues = _structural_review(svg, narration)
    assert not any("named_quantity_not_shown" in i for i in issues)


def test_named_quantity_handles_greek_letter():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<line x1="100" y1="500" x2="400" y2="500"/>'
        '<line x1="100" y1="500" x2="350" y2="200"/>'
        '</svg>'
    )
    narration = [{"speak": "The angle θ is 30 degrees.", "highlight": []}]
    issues = _structural_review(svg, narration)
    flagged = [i for i in issues if "named_quantity_not_shown" in i]
    assert len(flagged) == 1
    assert "θ" in flagged[0]


def test_named_quantity_passes_for_greek_when_labelled():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<path d="M 150 480 A 50 50 0 0 0 130 440"/>'
        '<text x="135" y="475">θ</text>'
        '</svg>'
    )
    narration = [{"speak": "The angle θ is 30 degrees.", "highlight": []}]
    issues = _structural_review(svg, narration)
    assert not any("named_quantity_not_shown" in i for i in issues), issues


# ---------------------------------------------------------------------
# micro_figure — primary shape rendered at near-invisible scale because
# the model used user-prompt numbers as viewBox coordinates.
# ---------------------------------------------------------------------

def test_micro_figure_flags_tiny_circle():
    # Model literally used 'r = 5' from the user prompt as the SVG
    # radius, producing a 5-pixel-radius circle inside a 900x650 vb.
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<circle id="circle" cx="450" cy="300" r="5"/>'
        '<text x="20" y="50">Circle with r = 5</text>'
        '</svg>'
    )
    issues = _structural_review(svg, [])
    assert any("micro_figure" in i for i in issues), issues


def test_micro_figure_passes_normal_radius():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<circle id="circle" cx="450" cy="300" r="180"/>'
        '<text x="20" y="50">Circle with r = 5</text>'
        '</svg>'
    )
    issues = _structural_review(svg, [])
    assert not any("micro_figure" in i for i in issues), issues


def test_micro_figure_passes_in_small_viewbox():
    # Small viewBoxes are test fixtures; don't false-flag them.
    svg = (
        '<svg>'
        '<circle id="A" cx="100" cy="100" r="20"/>'
        '<circle id="B" cx="200" cy="100" r="20"/>'
        '</svg>'
    )
    issues = _structural_review(svg, [])
    assert not any("micro_figure" in i for i in issues), issues


def test_micro_figure_flags_tiny_polygon():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<polygon id="trap" points="450,300 460,300 458,295 452,295"/>'
        '<text x="20" y="50">Tiny trapezoid</text>'
        '</svg>'
    )
    issues = _structural_review(svg, [])
    assert any("micro_figure" in i for i in issues), issues


# ---------------------------------------------------------------------
# no_geometric_primitive — SVG contains only <text>, no shape at all.
# ---------------------------------------------------------------------

def test_no_geometric_primitive_flags_text_only():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<text x="20" y="100">Area of a circle: A = πr²</text>'
        '<text x="20" y="150">Given r = 5</text>'
        '<text x="20" y="200">A = π · 25</text>'
        '<text x="20" y="250">A ≈ 78.54</text>'
        '</svg>'
    )
    issues = _structural_review(svg, [])
    assert any("no_geometric_primitive" in i for i in issues), issues


def test_no_geometric_primitive_passes_with_circle():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<circle cx="450" cy="300" r="180"/>'
        '<text x="20" y="100">Area of a circle: A = πr²</text>'
        '<text x="20" y="150">Given r = 5</text>'
        '<text x="20" y="200">A = π · 25</text>'
        '<text x="20" y="250">A ≈ 78.54</text>'
        '</svg>'
    )
    issues = _structural_review(svg, [])
    assert not any("no_geometric_primitive" in i for i in issues), issues


# --- 7. Topic-keyword required primitive ---

def test_circle_topic_flags_when_no_big_circle():
    # Unit circle prompt + narration but only axes drawn (no circle).
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<line x1="450" y1="50" x2="450" y2="600" stroke="black"/>'
        '<line x1="50" y1="325" x2="850" y2="325" stroke="black"/>'
        '<circle cx="600" cy="200" r="6"/>'  # tiny dot, not the circle
        '<text x="700" y="200">P</text>'
        '</svg>'
    )
    narr = [{"speak": "On the unit circle the coordinates are (cos θ, sin θ).", "highlight": []}]
    issues = _structural_review(
        svg, narr,
        user_prompt="Show sin θ and cos θ on the unit circle for θ = 60.",
    )
    assert any("circle_topic_no_big_circle" in i for i in issues), issues


def test_circle_topic_passes_when_big_circle_present():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<circle cx="450" cy="325" r="200" fill="none" stroke="black"/>'
        '<line x1="450" y1="325" x2="550" y2="151" stroke="blue"/>'
        '<text x="560" y="151">P</text>'
        '</svg>'
    )
    narr = [{"speak": "On the unit circle we have the point P.", "highlight": []}]
    issues = _structural_review(
        svg, narr,
        user_prompt="Show sin θ and cos θ on the unit circle for θ = 60.",
    )
    assert not any("missing_required_primitive" in i for i in issues), issues


def test_function_plot_flags_when_no_curve_path():
    # Integral prompt but only empty axes.
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<line x1="100" y1="600" x2="800" y2="600" stroke="black"/>'
        '<line x1="100" y1="50" x2="100" y2="600" stroke="black"/>'
        '<text x="820" y="610">x</text>'
        '<text x="80" y="40">y</text>'
        '</svg>'
    )
    issues = _structural_review(
        svg, [],
        user_prompt="Compute the integral of f(x) = 2x from x=1 to x=4 as the area under the curve.",
    )
    assert any("function_plot_no_curve" in i for i in issues), issues


def test_function_plot_passes_when_curve_present():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<line x1="100" y1="600" x2="800" y2="600" stroke="black"/>'
        '<line x1="100" y1="50" x2="100" y2="600" stroke="black"/>'
        '<path d="M 100 600 L 200 580 L 300 540 L 400 480 L 500 400 L 600 300 L 700 180 L 800 50" stroke="orange" fill="none"/>'
        '</svg>'
    )
    issues = _structural_review(
        svg, [],
        user_prompt="Compute the integral of f(x) = 2x from x=1 to x=4.",
    )
    assert not any("function_plot_no_curve" in i for i in issues), issues


def test_tangent_missing_flags_when_curve_present_but_no_extra_line():
    # Curve exists (parabola) and axes are short -> < 3 lines total
    # plus tangent topic -> flag.
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<line x1="450" y1="50" x2="450" y2="600" stroke="black"/>'
        '<line x1="50" y1="500" x2="850" y2="500" stroke="black"/>'
        '<path d="M 100 600 Q 450 -100 800 600" stroke="orange" fill="none"/>'
        '<circle cx="500" cy="425" r="6" fill="blue"/>'
        '<text x="520" y="425">(2,4)</text>'
        '</svg>'
    )
    narr = [{"speak": "The derivative gives the slope of the tangent line at x = 2.", "highlight": []}]
    issues = _structural_review(
        svg, narr,
        user_prompt="Show the derivative of f(x) = x² at x = 2 as the slope of the tangent line.",
    )
    assert any("tangent_missing" in i for i in issues), issues


def test_set_topic_flags_when_no_two_ovals():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<text x="200" y="200">1</text>'
        '<text x="200" y="250">2</text>'
        '<text x="200" y="300">3</text>'
        '<text x="400" y="200">4</text>'
        '<text x="600" y="200">5</text>'
        '<circle cx="100" cy="100" r="6"/>'  # small dot, not a set oval
        '</svg>'
    )
    issues = _structural_review(
        svg, [],
        user_prompt="Illustrate set difference A \\ B for two overlapping sets with example elements.",
    )
    assert any("set_topic_no_venn" in i for i in issues), issues


def test_set_topic_passes_with_two_ellipses():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<ellipse cx="380" cy="325" rx="180" ry="130" fill="#fcc" stroke="red"/>'
        '<ellipse cx="540" cy="325" rx="180" ry="130" fill="#ccf" stroke="blue"/>'
        '</svg>'
    )
    issues = _structural_review(
        svg, [],
        user_prompt="Illustrate set difference A \\ B for two overlapping sets.",
    )
    assert not any("set_topic_no_venn" in i for i in issues), issues


def test_graph_topic_flags_when_no_edges():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<text x="450" y="325">3SAT to Vertex Cover</text>'
        '<text x="450" y="360">construct the gadgets here</text>'
        '</svg>'
    )
    issues = _structural_review(
        svg, [],
        user_prompt="Reduce 3SAT to vertex cover: show clause gadgets, variable gadgets, and the chosen cover on the constructed graph.",
    )
    assert any("graph_topic_no_edges" in i for i in issues), issues


def test_algorithm_trace_flags_when_no_digit_steps():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<text x="100" y="100">Euclidean Algorithm for gcd(252, 105)</text>'
        '<line x1="100" y1="200" x2="400" y2="200" stroke="black"/>'
        '<line x1="100" y1="250" x2="400" y2="250" stroke="black"/>'
        '<line x1="100" y1="300" x2="400" y2="300" stroke="black"/>'
        '</svg>'
    )
    issues = _structural_review(
        svg, [],
        user_prompt="Compute gcd(252, 105) using the Euclidean algorithm.",
    )
    assert any("algorithm_trace_no_steps" in i for i in issues), issues


def test_algorithm_trace_passes_with_step_rows():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<text x="100" y="100">252 = 2·105 + 42</text>'
        '<text x="100" y="150">105 = 2·42 + 21</text>'
        '<text x="100" y="200">42 = 2·21 + 0</text>'
        '<text x="100" y="250">gcd = 21</text>'
        '</svg>'
    )
    issues = _structural_review(
        svg, [],
        user_prompt="Compute gcd(252, 105) using the Euclidean algorithm.",
    )
    assert not any("algorithm_trace_no_steps" in i for i in issues), issues


# --- looks_like_refinement classifier ---

def test_refinement_cues_classify_as_refinement():
    from studio.express import looks_like_refinement
    refinements = [
        "Add a label for the hypotenuse.",
        "Highlight C2 in red.",
        "Change the colour of the parabola to blue.",
        "Continue with the next step.",
        "Now explain step 3 in more detail.",
        "Remove the dashed line at the top.",
        "Move the formula to the top of the figure.",
        "Fix the typo on the label.",
        "Make this figure larger.",
        "Relabel b₁ as b_top.",
    ]
    for p in refinements:
        assert looks_like_refinement(p), f"expected refinement: {p!r}"


def test_new_topic_classify_as_not_refinement():
    from studio.express import looks_like_refinement
    new_topics = [
        "Compute the integral of f(x) = 2x from x = 1 to x = 4 as the area under the curve.",
        "Show the derivative of f(x) = x² at x = 2 as the slope of the tangent line.",
        "Multiply the 2×2 matrices A = [[1,2],[3,4]] and B = [[5,6],[7,8]] step by step.",
        "Show sin θ and cos θ on the unit circle for θ = 60 degrees.",
        "Apply the Pythagorean theorem to a right triangle with legs a = 3 and b = 4.",
        "Compute the volume of a cone with radius r = 3 and height h = 7.",
        "Compute gcd(252, 105) using the Euclidean algorithm.",
        "Enumerate the vertex cover of a 5-cycle graph C₅.",
    ]
    for p in new_topics:
        assert not looks_like_refinement(p), f"expected NEW topic: {p!r}"


# --- text-text overlap critic ---

def test_text_text_overlap_flags_two_long_labels_at_same_y():
    # Two long captions at the same y at x=20 and x=200 — the first is
    # 60 chars (~ 60*16*0.6 = 576 px wide), so it runs UNDER the second
    # at x=200.
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<text x="20" y="200" font-size="16">'
        'The first long formula that runs all the way across the canvas'
        '</text>'
        '<text x="200" y="200" font-size="16">'
        'The second formula sits inside the first'
        '</text>'
        '</svg>'
    )
    issues = _structural_review(svg, [], user_prompt="")
    assert any("text_text_overlap" in i for i in issues), issues


def test_text_text_overlap_passes_well_spaced_rows():
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<text x="20" y="100" font-size="16">First label</text>'
        '<text x="20" y="200" font-size="16">Second label</text>'
        '<text x="20" y="300" font-size="16">Third label</text>'
        '</svg>'
    )
    issues = _structural_review(svg, [], user_prompt="")
    assert not any("text_text_overlap" in i for i in issues), issues


def test_caption_overlaps_diagram_now_fires_on_edge_grazing():
    # 25% overlap should now trigger (was 50%).  Caption sitting on
    # the top edge of a rect with ~30% of its area inside.
    svg = (
        '<svg viewBox="0 0 900 650">'
        '<rect id="trap" x="200" y="200" width="500" height="200" fill="#cef"/>'
        '<text x="250" y="195" font-size="18">b_2 = 10 long</text>'
        '</svg>'
    )
    issues = _structural_review(svg, [], user_prompt="")
    # Position the text so its bbox dips ~5 px into the rect, ~30% area
    # inside.  The text baseline at y=195 with fs=18 means bbox y∈[195-18,
    # 195+0.2*18] = [177, 198.6]; rect starts at y=200 so overlap is 0.
    # Recompute: text bbox uses (ty - fs, h=fs*1.2) → y∈[177, 198.6].
    # That misses the rect.  Use ty=205 instead.
    svg2 = (
        '<svg viewBox="0 0 900 650">'
        '<rect id="trap" x="200" y="200" width="500" height="200" fill="#cef"/>'
        '<text x="250" y="215" font-size="18">b_2 = 10 long</text>'
        '</svg>'
    )
    issues2 = _structural_review(svg2, [], user_prompt="")
    assert any("caption_overlaps_diagram" in i for i in issues2), issues2
