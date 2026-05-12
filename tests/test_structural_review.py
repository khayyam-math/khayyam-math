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
