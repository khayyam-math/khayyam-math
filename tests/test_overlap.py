"""Caption layout no-overlap regression tests.

These tests construct a VisualGraph directly and run geometric_layout to
verify that captions never overlap each other and never extend past the
canvas pad zone.  They replace the older run_pipeline-based scene tests
which depended on the NLP pipeline that was stripped in v0.3.
"""
from __future__ import annotations

import pytest

from sevim.overlap import detect_overlaps


def test_many_captions_never_overlap_each_other_or_figure():
    """Locks in the 2-D occupancy guarantee for caption placement.

    Reproduces the 3SAT-Hampath failure mode: the LLM dumps many
    captions onto a dense math figure, the requested margins fill up,
    and the prior 1-D cursor implementation clamped later captions on
    top of earlier ones.  The new implementation walks rows × columns
    and reroutes / shrinks rather than ever overlapping a blocked rect.
    """
    from sevim.ir import VisualGraph, VisualShape
    from sevim.s4_geo_layout import geometric_layout

    points = [
        VisualShape(
            nid=f"p{i}", primitive="point", label=f"P{i}",
            width=12.0, height=12.0,
            font_size=14.0, stroke_width=1.0, fill_index=0,
            meta={"x": float(x), "y": float(y), "kind": "point"},
        )
        for i, (x, y) in enumerate([(0, 0), (10, 0), (5, 5), (5, -5),
                                    (3, 2), (7, -2), (2, -4), (8, 3)])
    ]
    anchors = ["above", "below", "left", "right", "auto"]
    captions = []
    for i in range(18):
        captions.append(VisualShape(
            nid=f"cap_{i}", primitive="caption", label=f"caption number {i}",
            width=160.0 + (i % 5) * 25.0,
            height=30.0 + (i % 3) * 6.0,
            font_size=12.0, stroke_width=0.0, fill_index=0,
            meta={
                "x": float((i % 4) * 3), "y": float((i // 4) * 2 - 3),
                "anchor": anchors[i % len(anchors)],
                "text": f"caption number {i}",
            },
        ))

    vg = VisualGraph(shapes=points + captions, connectors=[], containers=[])
    pg = geometric_layout(vg, canvas_w=900, canvas_h=620)

    cap_ids = {c.nid for c in captions}
    findings = [
        f for f in detect_overlaps(pg)
        if f["kind"] == "shape_overlap"
        and (f["a"] in cap_ids or f["b"] in cap_ids)
    ]
    assert findings == [], (
        f"caption placement produced {len(findings)} overlap(s); "
        f"first 3: {findings[:3]}"
    )


def test_caption_wrap_overflow_does_not_silently_overlap():
    """Long-text captions with small declared height get the rendered
    height bumped up by s5_render's ``h = max(s.height, needed_h)``.
    Layout must use the *rendered* height when reserving slots, otherwise
    multiple captions stacked in the same margin overflow into each
    other's space.  Reproduces the 3SAT→clique v2 canvas failure where
    captions declared as 32 px got rendered at ~62 px and stacked into
    the canvas pad zone.
    """
    from sevim.ir import VisualGraph, VisualShape
    from sevim.s4_geo_layout import geometric_layout

    points = [
        VisualShape(
            nid=f"p{i}", primitive="point", label=f"P{i}",
            width=12.0, height=12.0,
            font_size=14.0, stroke_width=1.0, fill_index=0,
            meta={"x": float(x), "y": float(y), "kind": "point"},
        )
        for i, (x, y) in enumerate([(-3, 1), (3, 1), (0, -3), (0, 3)])
    ]
    long_text = "this caption has enough text to wrap onto multiple lines on its own"
    captions = [
        VisualShape(
            nid=f"cap_{i}", primitive="caption", label=long_text,
            width=200.0, height=32.0,
            font_size=12.0, stroke_width=0.0, fill_index=0,
            meta={"x": 0.0, "y": 0.0, "anchor": "auto", "text": long_text},
        )
        for i in range(8)
    ]

    vg = VisualGraph(shapes=points + captions, connectors=[], containers=[])
    pg = geometric_layout(vg, canvas_w=900, canvas_h=680)

    cap_ids = {c.nid for c in captions}
    findings = [
        f for f in detect_overlaps(pg)
        if f["kind"] == "shape_overlap"
        and (f["a"] in cap_ids or f["b"] in cap_ids)
    ]
    assert findings == [], (
        f"wrap-overflow caused {len(findings)} overlap(s); "
        f"first 3: {findings[:3]}"
    )

    pad = 30.0
    for ps in pg.shapes:
        if ps.shape.primitive != "caption":
            continue
        from sevim.s4_geo_layout import _caption_rendered_height
        rendered_h = _caption_rendered_height(ps.shape)
        assert ps.y + rendered_h <= 680 - pad + 0.5, (
            f"caption {ps.shape.nid} ends at y={ps.y + rendered_h:.1f}, "
            f"past canvas_h - pad = {680 - pad}"
        )
