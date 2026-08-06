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


def _embedded_field_colours(svg: str) -> int:
    """Distinct colours in an embedded escape-time PNG, 0 if there is none.

    The escape-time renderers (Mandelbrot / Julia) ship their field as
    one base64 <image> instead of thousands of rectangles — smaller on
    the wire AND higher resolution.  That makes primitive-counting the
    wrong richness measure for them, so measure the picture itself: a
    real escape-time field uses most of its escape-count palette, a
    blank or flat-filled placeholder uses two or three colours.  The
    ceiling is the iteration cap, so the bar is set well under it.
    """
    m = re.search(r'href="data:image/png;base64,([A-Za-z0-9+/=]+)"', svg)
    if not m:
        return 0
    import base64
    import io
    from PIL import Image
    img = Image.open(io.BytesIO(base64.b64decode(m.group(1))))
    return len(img.convert("RGB").getcolors(maxcolors=1 << 20) or [])


def test_all_render_valid_and_rich():
    for kind, fn in Fr._RENDER.items():
        svg, narr = fn()
        minidom.parseString(svg)                 # valid XML
        assert 5 <= len(narr) <= 9, kind
        # a real recursive fractal is geometrically rich: many line
        # segments (one big path) or many rects/cells, not a 2-triangle
        # sketch.  Count both so a single dense <path> still qualifies.
        richness = len(re.findall(r" L |<rect|<path|<circle", svg))
        if richness < 40:
            colours = _embedded_field_colours(svg)
            assert colours >= 40, (
                f"{kind} has neither drawn richness ({richness}) nor a "
                f"detailed embedded field ({colours} colours)")


def test_escape_time_fields_are_high_resolution():
    """Guards the fix for "Not detailed enough" (field report 2026-07-06).

    The old run-length encoding cost ~3 KB per pixel-row, which capped
    the field at 168×130 and made it visibly blocky.  Pin both ends of
    the trade: the field must be large, and the SVG must stay small.
    """
    for kind, fn in (("mandelbrot", Fr.render_mandelbrot),
                     ("julia", Fr.render_julia)):
        svg, _ = fn()
        m = re.search(r'href="data:image/png;base64,([A-Za-z0-9+/=]+)"', svg)
        assert m, f"{kind} did not embed its escape field as a PNG"
        import base64
        import io
        from PIL import Image
        w, h = Image.open(io.BytesIO(base64.b64decode(m.group(1)))).size
        assert w >= 290 and h >= 220, f"{kind} field is only {w}×{h}"
        assert len(svg) < 300_000, f"{kind} SVG is {len(svg)} bytes"


def test_julia_shows_the_iteration_not_just_the_outcome():
    """The report asked to SEE the iteration, not only its result."""
    svg, narr = Fr.render_julia()
    assert "two starting points" in svg.lower()
    # both verdicts present, and they must disagree — one orbit stays
    # bounded, the other escapes, or the panel teaches nothing.
    assert "in the set" in svg and "outside" in svg
    # a zoom, so "the detail never runs out" is shown rather than claimed
    assert "zoom" in svg.lower()
    # the magnitudes are computed, so the bounded orbit must really stay
    # under 2 and the escaping one must really pass it
    mags = [float(x) for x in re.findall(r"\|z₁…z₄\| = ([\d.,\s]+)", svg)[0]
            .replace(" ", "").split(",")]
    assert max(mags) < 2.0, f"the 'bounded' orbit escaped: {mags}"
    esc = [float(x) for x in re.findall(r"\|z₁…z₄\| = ([\d.,\s]+)", svg)[1]
           .replace(" ", "").split(",")]
    assert max(esc) > 2.0, f"the 'escaping' orbit stayed bounded: {esc}"


def test_menger_shows_two_iterations():
    """"More iterations would be even more informative" — field report."""
    svg, narr = Fr.render_menger()
    assert "Level 1" in svg and "Level 2" in svg
    assert "400" in svg                          # 20² cubes at level 2
    # level 2 needs a 9×9 face, so it must draw far more cells than the
    # 3 × 9 = 27 of a level-1-only figure.
    assert svg.count("<path") > 200, "level 2 face is not actually drawn"


def test_carpet_hole_matches_the_sierpinski_rule():
    # level 1: only the centre of the 3×3 grid is removed
    holes1 = {(a, b) for a in range(3) for b in range(3)
              if Fr._carpet_hole(a, b, 1)}
    assert holes1 == {(1, 1)}
    # level 2: the level-1 centre block (9 cells) plus the centre of each
    # of the 8 surviving blocks = 17 removed cells out of 81
    holes2 = {(a, b) for a in range(9) for b in range(9)
              if Fr._carpet_hole(a, b, 2)}
    assert len(holes2) == 9 + 8


def test_captions_stay_inside_the_canvas():
    """No footer may run off the edge (the clipped Koch caption bug)."""
    for kind, fn in Fr._RENDER.items():
        svg, _ = fn()
        for x, fs, body in re.findall(
                r'<text x="([\d.]+)" y="[\d.]+" font-size="([\d.]+)" '
                r'text-anchor="middle"[^>]*>([^<]*)</text>', svg):
            width = len(body) * float(fs) * 0.55
            assert float(x) - width / 2 > -1, f"{kind}: {body[:40]!r} clipped left"
            assert float(x) + width / 2 < Fr._W + 1, (
                f"{kind}: {body[:40]!r} runs past the right edge")


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
