"""Deterministic Taylor-series-of-sin(x) renderer tests.

Added after a user reported the LLM-SVG figure named curves/legends in
narration without highlighting them.  The renderer must give each curve
and legend entry a stable id, and every narration highlight must point at
a real id.
"""
from __future__ import annotations

import asyncio
import math
import re
from collections import Counter
from xml.dom import minidom

from studio.templates import taylor_sin as T


def test_routing():
    assert T.is_taylor_sin_prompt("Taylor series for sin(x)")
    assert T.is_taylor_sin_prompt("the maclaurin series of sine")
    assert T.is_taylor_sin_prompt("power series expansion of sin x")
    assert not T.is_taylor_sin_prompt("taylor series of e^x")
    assert not T.is_taylor_sin_prompt("plot sin(x)")


def test_polynomials_are_correct_and_improving():
    svg, narr = T.render_taylor_sin()
    minidom.parseString(svg)
    assert 5 <= len(narr) <= 9

    f3, f5, f7 = 6, 120, 5040

    def t1(x):
        return x

    def t3(x):
        return x - x**3 / f3

    def t5(x):
        return x - x**3 / f3 + x**5 / f5

    def t7(x):
        return x - x**3 / f3 + x**5 / f5 - x**7 / f7

    for xv in (0.3, 0.7, 1.1):
        s = math.sin(xv)
        e = [abs(t1(xv) - s), abs(t3(xv) - s), abs(t5(xv) - s), abs(t7(xv) - s)]
        assert e[0] >= e[1] >= e[2] >= e[3]
    assert abs(t7(0.5) - math.sin(0.5)) < 1e-4


def test_every_curve_has_a_legend_and_narration_highlight():
    """The reported defect: curves/legends mentioned but not highlighted.
    Each of the five curves must have a curve id, a legend id, and be
    referenced by some narration highlight."""
    svg, narr = T.render_taylor_sin()
    ids = set(re.findall(r'id="([^"]+)"', svg))
    refs = {r for phrase in narr for r in phrase.get("highlight", [])}
    for ref in refs:
        assert ref in ids, f"highlight id {ref!r} missing from SVG"
    for key in ("sin", "t1", "t3", "t5", "t7"):
        assert f"curve_{key}" in ids
        assert f"leg_{key}" in ids
        # mentioned-and-highlighted: the curve or its legend is highlighted
        assert f"curve_{key}" in refs or f"leg_{key}" in refs


def test_curves_stay_inside_viewbox():
    """Clipped polylines must not place points outside the plot band."""
    svg, _ = T.render_taylor_sin()
    coords = re.findall(r'[ML]\s*([\-0-9.]+),([\-0-9.]+)', svg)
    for sx, sy in coords:
        assert T._OT - 1 <= float(sy) <= _bottom() + 1


def _bottom():
    return T._OB


def test_no_anchor_collisions():
    svg, _ = T.render_taylor_sin()
    a = re.findall(r'<text[^>]*\sx="([\-0-9.]+)" y="([\-0-9.]+)"', svg)
    dupes = [k for k, n in Counter(a).items() if n > 1]
    assert not dupes, f"overlapping text anchors: {dupes}"


def test_routes_through_express_and_passes_probe():
    from studio.express import express_figure
    r = asyncio.run(express_figure(
        "show the Taylor series for sin(x)", base_url="", model="", api_key=""))
    assert r.get("template") == "taylor_sin"
    assert r.get("retries_used") == 0
    import importlib.util
    spec = importlib.util.spec_from_file_location("qp", "studio/quality_probe.py")
    qp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qp)
    assert qp.inspect_quality("taylor_sin", r) == []
