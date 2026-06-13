"""Deterministic fractal renderer tests.

Added after a user reported the LLM-SVG fractal figures were not visual
enough.  The deterministic renderers compute the actual recursion, so the
figure is the real fractal at depth, with valid XML and the recursion's
key facts present.
"""
from __future__ import annotations

import asyncio
import re
from xml.dom import minidom

from studio.templates import fractals as Fr


def test_routing():
    assert Fr.which_fractal("Sierpinski carpet") == "sierpinski_carpet"
    assert Fr.which_fractal("Koch's Snowflake") == "koch"
    assert Fr.which_fractal("Menger sponge fractal") == "menger"
    assert Fr.which_fractal("sierpinski triangle") == "sierpinski_triangle"
    assert Fr.which_fractal("koch curve") == "koch"
    # the expanded set the user is likely to try
    assert Fr.which_fractal("Mandelbrot set") == "mandelbrot"
    assert Fr.which_fractal("Julia set") == "julia"
    assert Fr.which_fractal("Barnsley fern") == "barnsley"
    assert Fr.which_fractal("draw a fractal fern") == "barnsley"
    assert Fr.which_fractal("Cantor set") == "cantor"
    assert Fr.which_fractal("dragon curve") == "dragon"
    assert Fr.which_fractal("Pythagoras tree") == "pythagoras"
    assert Fr.which_fractal("integrate x^2") is None
    assert not Fr.is_fractal_prompt("what is a fraction")


def test_all_render_valid_and_rich():
    for kind, fn in Fr._RENDER.items():
        svg, narr = fn()
        minidom.parseString(svg)                 # valid XML
        assert 5 <= len(narr) <= 9, kind
        # a real recursive fractal is geometrically rich: many line
        # segments (one big path) or many rects/cells, not a 2-triangle
        # sketch.  Count both so a single dense <path> still qualifies.
        richness = len(re.findall(r" L |<rect|<path|<circle", svg))
        assert richness >= 40, f"{kind} only has richness {richness}"


def test_key_facts_present():
    koch, _ = Fr.render_koch()
    assert "1.26" in koch                        # Koch dimension
    carp, _ = Fr.render_sierpinski_carpet()
    assert "1.89" in carp                        # carpet dimension
    meng, _ = Fr.render_menger()
    assert "2.727" in meng and "Sierpinski carpet" in meng


def test_routes_through_express():
    from studio.express import express_figure
    for prompt in ("Sierpinski carpet", "Koch's Snowflake",
                   "Menger sponge fractal"):
        r = asyncio.run(express_figure(prompt, base_url="", model="",
                                       api_key=""))
        assert r.get("template") == "fractal", prompt
        assert r.get("retries_used") == 0
