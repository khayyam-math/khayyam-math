"""Quality-probe inspection tests.

The probe's structural checks must NOT false-positive on the
transform-wrapped SVG that graphviz (and our deterministic routes) emit:
a top-level ``<g transform="translate(...)">`` makes raw text x/y look
out-of-bounds while the rendered glyph sits comfortably inside the
viewBox.  These tests lock in the transform-aware bounds check and the
genuine-defect detections so a refactor can't silently re-break the
"inbox only carries real problems" guarantee.
"""
from __future__ import annotations

from studio import quality_probe as qp


# A faithful slice of real graphviz output: viewBox origin is negative and
# the content lives inside a translate(4 200) group, so every text y is a
# large negative number that is nonetheless inside once transformed.
GRAPHVIZ_SVG = (
    '<svg viewBox="-20.44 -14.16 401.88 232.32" xmlns="http://www.w3.org/2000/svg">'
    '<g id="graph0" class="graph" transform="scale(1 1) rotate(0) translate(4 200)">'
    '<text text-anchor="middle" x="151.5" y="-174.3">P(A)</text>'
    '<text text-anchor="middle" x="35.5" y="-87.3">P(B|A)</text>'
    '<text text-anchor="middle" x="69.5" y="-14.3">leaf</text>'
    '</g></svg>'
)


def test_graphviz_transform_not_flagged():
    # Raw y values (-174, -87, -14) are all below the viewBox's vy (-14.16),
    # but translate(4 200) lifts them inside.  No false positive.
    assert qp.inspect_quality("bayes tree", {"svg": GRAPHVIZ_SVG}) == []


def test_genuine_overflow_flagged():
    bad = ('<svg viewBox="0 0 100 100"><g transform="translate(0 0)">'
           '<text x="500" y="500">off</text>'
           '<text x="10" y="10">ok</text></g></svg>')
    issues = qp.inspect_quality("p", {"svg": bad})
    assert any("outside the viewBox" in i for i in issues)


def test_rotation_is_inconclusive_not_flagged():
    # A real rotation can't be reduced to translate+scale, so the bounds
    # check declines rather than guesses — no alert.
    rot = ('<svg viewBox="0 0 100 100">'
           '<g transform="rotate(45) translate(10 10)">'
           '<text x="-200" y="-200">x</text></g></svg>')
    assert all("outside the viewBox" not in i
               for i in qp.inspect_quality("p", {"svg": rot}))


def test_oversized_and_leak_and_error_flagged():
    bad = ('<svg viewBox="0 0 100 100">'
           '<rect width="99" height="99"/>'
           '<text x="5" y="5">made by gpt-4o</text></svg>')
    issues = qp.inspect_quality("p", {"svg": bad})
    assert any("nearly fills" in i for i in issues)
    assert any("leaked internal" in i for i in issues)
    assert qp.inspect_quality("p", {"error": "boom"}) == [
        "generation error: boom", "empty or missing SVG"]


def test_clean_simple_svg_passes():
    ok = ('<svg viewBox="0 0 100 100">'
          '<rect width="10" height="10"/><text x="5" y="5">hi</text></svg>')
    assert qp.inspect_quality("p", {"svg": ok}) == []


def test_retries_exhausted_flagged():
    res = {"svg": '<svg viewBox="0 0 100 100"><text x="5" y="5">ok</text></svg>',
           "retries_used": 2, "review_history": ["still failing"]}
    issues = qp.inspect_quality("p", res)
    assert any("retries exhausted" in i for i in issues)


def test_overlapping_labels_flagged():
    bad = _wrap = (
        '<svg viewBox="0 0 900 560">'
        '<text x="600" y="300" font-size="15" text-anchor="middle">S2 = {1, 2, 2, 6}</text>'
        '<text x="605" y="305" font-size="15" text-anchor="middle">S2 = {1, 2, 2, 6}</text>'
        '</svg>')
    assert qp._overlapping_text_pairs(bad) == 1
    assert any("overlapping text" in i for i in qp.inspect_quality("p", {"svg": bad}))


def test_graphviz_transform_not_flagged_for_overlap():
    # The translate-wrapped graphviz figure must not look like overlap.
    assert qp._overlapping_text_pairs(GRAPHVIZ_SVG) == 0


def test_separated_labels_not_flagged():
    ok = ('<svg viewBox="0 0 900 560">'
          '<text x="40" y="100" font-size="14">first label here</text>'
          '<text x="40" y="200" font-size="14">second label here</text></svg>')
    assert qp._overlapping_text_pairs(ok) == 0


def test_overlap_rotation_inconclusive():
    rot = ('<svg viewBox="0 0 100 100"><g transform="rotate(30)">'
           '<text x="10" y="10" font-size="14">overlap one</text>'
           '<text x="11" y="11" font-size="14">overlap one</text></g></svg>')
    assert qp._overlapping_text_pairs(rot) is None


def test_parse_transform_folds_scale_then_translate():
    # scale(2) translate(10 5): a child point (0,0) -> (20,10).
    sx, sy, tx, ty = qp._parse_transform("scale(2) translate(10 5)")
    assert (sx, sy, tx, ty) == (2.0, 2.0, 20.0, 10.0)
    # matrix() is unresolvable -> None
    assert qp._parse_transform("matrix(1 0 0 1 2 3)") is None
