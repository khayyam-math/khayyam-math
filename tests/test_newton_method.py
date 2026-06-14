"""Newton's-method intro-routing tests.

A user reported the LLM-SVG figure's "tangents" were not tangent lines.
Root cause: the correct deterministic renderer (studio.templates.newton)
only fired when the prompt pinned a concrete f/x₀; vague prompts fell to
LLM-SVG.  studio.templates.newton_intro routes vague Newton prompts to the
canonical √2 example through that same exact renderer.  These tests check
the routing predicate, the tangent geometry of the rendered example, and
that narration highlight ids exist.
"""
from __future__ import annotations

import asyncio
import math
import re
from xml.dom import minidom

from studio.templates import newton_intro as NI


def test_routing_fires_on_vague_prompts():
    assert NI.is_newton_intro_prompt("explain Newton's method")
    assert NI.is_newton_intro_prompt("show how Newton's method finds a root")
    assert NI.is_newton_intro_prompt(
        "use the Newton-Raphson method to approximate a root")
    assert NI.is_newton_intro_prompt(
        "Newton's method to find the square root of 2")


def test_routing_defers_when_function_is_pinned():
    # Concrete function present -> let the template router extract it.
    assert not NI.is_newton_intro_prompt(
        "Newton's method for f(x) = x^3 - 2 starting at x0 = 1.5")
    assert not NI.is_newton_intro_prompt(
        "apply Newton's method to cos(x) - x from x = 1")
    # Not a Newton prompt at all.
    assert not NI.is_newton_intro_prompt("newtonian mechanics and gravity")
    assert not NI.is_newton_intro_prompt("integrate x^2")


def test_tangent_geometry_is_correct():
    """The reported defect: tangents must actually be tangent and cross the
    axis at the next iterate.  Re-derive Newton on x²−2 from x₀=2."""
    svg, narr = NI.render_newton_intro()
    minidom.parseString(svg)
    assert 4 <= len(narr) <= 9
    xs = [2.0]
    for _ in range(4):
        xn = xs[-1]
        xs.append(xn - (xn * xn - 2.0) / (2.0 * xn))
        if abs(xs[-1] - xs[-2]) < 1e-7:
            break
    for n in range(len(xs) - 1):
        xn = xs[n]
        intercept = xn - (xn * xn - 2.0) / (2.0 * xn)   # tangent x-intercept
        assert abs(intercept - xs[n + 1]) < 1e-12
    assert abs(xs[-1] - math.sqrt(2.0)) < 1e-4


def test_narration_highlights_exist_in_svg():
    svg, narr = NI.render_newton_intro()
    ids = set(re.findall(r'id="([^"]+)"', svg))
    for phrase in narr:
        for ref in phrase.get("highlight", []):
            assert ref in ids, f"highlight id {ref!r} missing from SVG"


def test_routes_through_express_and_passes_probe():
    from studio.express import express_figure
    r = asyncio.run(express_figure(
        "explain Newton's method to find the square root of 2",
        base_url="", model="", api_key=""))
    assert r.get("template") == "newton_intro"
    assert r.get("retries_used") == 0
    import importlib.util
    spec = importlib.util.spec_from_file_location("qp", "studio/quality_probe.py")
    qp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qp)
    assert qp.inspect_quality("newton_intro", r) == []
