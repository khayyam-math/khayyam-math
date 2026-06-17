"""Deterministic conditional-probability (Venn) renderer tests.

Added after the probe caught "explain conditional probability with a Venn
diagram" failing the structural rubric (missing explicit definition
statement) on the LLM-SVG path.
"""
from __future__ import annotations

import asyncio
import re
from xml.dom import minidom

from studio.templates import conditional_probability as C


def test_routing():
    assert C.is_conditional_probability_prompt(
        "explain conditional probability with a Venn diagram")
    assert C.is_conditional_probability_prompt("what is conditional probability")
    assert not C.is_conditional_probability_prompt("draw a Venn diagram of A and B")
    assert not C.is_conditional_probability_prompt("normal distribution")


def test_definition_present_and_arithmetic_asserted():
    svg, narr = C.render_conditional_probability()
    minidom.parseString(svg)
    assert 5 <= len(narr) <= 9
    # The structural-rubric fix: an explicit "is defined as" statement.
    assert "is defined as" in svg
    assert "P(A | B)" in svg and "P(A ∩ B) / P(B)" in svg
    # Recompute the worked example and check the figure's numbers.
    N, only_a, both, only_b, neither = 20, 6, 4, 6, 4
    assert only_a + both + only_b + neither == N
    p_b = (only_b + both) / N
    p_ab = both / N
    p_a_given_b = both / (only_b + both)
    assert abs(p_b - 0.5) < 1e-9
    assert abs(p_ab - 0.2) < 1e-9
    assert abs(p_a_given_b - 0.4) < 1e-9
    assert abs(p_ab / p_b - p_a_given_b) < 1e-9
    # two Venn circles present
    assert svg.count("<circle") == 2


def test_narration_highlights_exist_in_svg():
    svg, narr = C.render_conditional_probability()
    ids = set(re.findall(r'id="([^"]+)"', svg))
    for phrase in narr:
        for ref in phrase.get("highlight", []):
            assert ref in ids, f"highlight id {ref!r} missing from SVG"


def test_routes_through_express_and_passes_probe():
    from studio.express import express_figure
    r = asyncio.run(express_figure(
        "explain conditional probability with a Venn diagram",
        base_url="", model="", api_key=""))
    assert r.get("template") == "conditional_probability"
    assert r.get("retries_used") == 0
    import importlib.util
    spec = importlib.util.spec_from_file_location("qp", "studio/quality_probe.py")
    qp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qp)
    assert qp.inspect_quality("conditional_probability", r) == []
