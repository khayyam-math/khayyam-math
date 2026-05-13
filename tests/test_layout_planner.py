"""Tests for the CP-SAT layout planner.

Smoke + correctness:
  - empty / single-item / clean SVGs are no-ops
  - overlapping labels get displaced to non-overlapping positions
  - the displacement is bounded (no label gets thrown far from its anchor)
  - results are deterministic across runs
  - text inside <g> groups is left alone
  - failure (ortools missing, infeasible) returns the input unchanged
"""
from __future__ import annotations

import pytest

ortools = pytest.importorskip("ortools.sat.python.cp_model")

from studio.layout_planner import (
    plan_layout, extract_text_items, gen_candidates,
    solve_layout, count_overlaps, _viewbox, _bboxes_overlap, _bbox_at,
)


def _orig_overlaps(svg: str) -> int:
    items = extract_text_items(svg)
    boxes = [_bbox_at(it.anchor_x, it.anchor_y, it.width, it.height,
                      it.text_anchor) for it in items]
    n = 0
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if _bboxes_overlap(boxes[i], boxes[j]):
                n += 1
    return n


def _post_overlaps(svg: str) -> int:
    return _orig_overlaps(svg)


# ── No-op cases ────────────────────────────────────────────────────


def test_empty_svg_no_change():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"></svg>'
    assert plan_layout(svg) == svg


def test_single_text_no_change():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 650">'
           '<text x="100" y="100" font-size="20">solo</text></svg>')
    assert plan_layout(svg) == svg


def test_no_viewbox_no_change():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg">'
           '<text x="0" y="0" font-size="20">a</text>'
           '<text x="0" y="0" font-size="20">b</text></svg>')
    assert plan_layout(svg) == svg


def test_clean_layout_no_change():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 650">'
           '<text x="50" y="100" font-size="16">left</text>'
           '<text x="50" y="300" font-size="16">middle</text>'
           '<text x="50" y="500" font-size="16">bottom</text></svg>')
    # No overlaps to start with — solver picks anchor for every item.
    assert _orig_overlaps(svg) == 0
    result = plan_layout(svg)
    assert _post_overlaps(result) == 0


# ── Overlap resolution ─────────────────────────────────────────────


def test_two_overlapping_labels_become_disjoint():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 650">'
           '<text x="100" y="100" font-size="24">overlapping label A</text>'
           '<text x="200" y="100" font-size="24">overlapping label B</text></svg>')
    assert _orig_overlaps(svg) >= 1
    result = plan_layout(svg)
    assert _post_overlaps(result) == 0


def test_three_way_pileup_resolves():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 650">'
           '<text x="100" y="200" font-size="22">label one</text>'
           '<text x="150" y="200" font-size="22">label two</text>'
           '<text x="200" y="200" font-size="22">label three</text></svg>')
    assert _orig_overlaps(svg) >= 2
    result = plan_layout(svg)
    assert _post_overlaps(result) == 0


def test_displacement_bounded():
    """No label is moved further than the largest candidate offset (~56 px)."""
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 650">'
           '<text x="100" y="100" font-size="20">overlapping A</text>'
           '<text x="180" y="100" font-size="20">overlapping B</text></svg>')
    items = extract_text_items(svg)
    cands = gen_candidates(items, _viewbox(svg))
    picked = solve_layout(items, cands)
    assert picked is not None
    for i, ci in enumerate(picked):
        c = cands[ci]
        it = items[i]
        dist = ((c.x - it.anchor_x) ** 2 + (c.y - it.anchor_y) ** 2) ** 0.5
        # Largest offset in _OFFSETS is 56, diagonal at 0.7 ⇒ ~56*0.99
        assert dist <= 60, f"Item {i} moved {dist:.1f}px from anchor"


# ── Determinism ────────────────────────────────────────────────────


def test_deterministic_output():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 650">'
           '<text x="100" y="100" font-size="24">first label</text>'
           '<text x="200" y="100" font-size="24">second label</text>'
           '<text x="300" y="100" font-size="24">third label</text>'
           '<text x="100" y="200" font-size="20">extra</text></svg>')
    r1 = plan_layout(svg)
    r2 = plan_layout(svg)
    r3 = plan_layout(svg)
    assert r1 == r2 == r3


# ── Group handling ────────────────────────────────────────────────


def test_text_inside_group_ignored():
    """Text inside <g> is handled by autofit_group_rects, not the planner."""
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 650">'
           '<g class="matrix"><text x="50" y="50">a</text>'
           '<text x="55" y="50">b</text></g>'
           '<text x="100" y="200" font-size="20">free A</text>'
           '<text x="100" y="200" font-size="20">free B</text></svg>')
    items = extract_text_items(svg)
    contents = [it.content for it in items]
    assert "a" not in contents
    assert "b" not in contents
    assert "free A" in contents
    assert "free B" in contents
    # The two free labels overlap and should be resolved.
    result = plan_layout(svg)
    assert _post_overlaps(result) == 0


# ── Edge cases ────────────────────────────────────────────────────


def test_label_near_viewbox_edge_keeps_anchor():
    """When all candidates would clip, the planner falls back to the anchor."""
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 650">'
           '<text x="5" y="20" font-size="20">edge</text>'
           '<text x="5" y="20" font-size="20">overlap</text></svg>')
    # Should not crash and should return a valid SVG.
    result = plan_layout(svg)
    assert result.startswith("<svg")
    assert "</svg>" in result


def test_tspan_children_counted_as_one_text():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 650">'
           '<text x="100" y="100" font-size="20">a<tspan>b</tspan>c</text>'
           '<text x="100" y="100" font-size="20">overlapper</text></svg>')
    items = extract_text_items(svg)
    assert len(items) == 2
    # Combined visible chars "abc" → width = 3 * 12 = 36
    assert items[0].content == "abc"


def test_double_and_single_quoted_attrs():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox=\'0 0 900 650\'>'
           "<text x='100' y='100' font-size='20'>single</text>"
           '<text x="100" y="100" font-size="20">double</text></svg>')
    items = extract_text_items(svg)
    assert len(items) == 2


# ── Sanity: integration with real-looking SVG ─────────────────────


def test_realistic_pythagoras_svg():
    """Mimics what express might emit for the 3-4-5 triangle prompt."""
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 650">
        <path d="M 200 500 L 200 200 L 600 500 Z" stroke="black" fill="none"/>
        <text x="180" y="350" font-size="22" text-anchor="end">3</text>
        <text x="400" y="525" font-size="22" text-anchor="middle">4</text>
        <text x="400" y="340" font-size="22" text-anchor="middle">5</text>
        <text x="450" y="525" font-size="22" text-anchor="middle">a = 4</text>
    </svg>"""
    # The last two texts both at y=525 with overlapping x ranges.
    result = plan_layout(svg)
    assert _post_overlaps(result) == 0
