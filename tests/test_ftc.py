"""Deterministic FTC proof renderer tests.

Routed because the prompt failed the 'proof' completeness rubric (no
deduction steps, no QED) and the vision review (it wanted a graph of f
and a mean-value strip) on the LLM-SVG path.  The deterministic figure
must carry all of those by construction.
"""
from __future__ import annotations

import asyncio
import re
from collections import Counter
from xml.dom import minidom

from studio.templates import ftc as F


def test_routing():
    assert F.is_ftc_prompt("show the proof of the fundamental theorem of calculus")
    assert F.is_ftc_prompt("prove the FTC")
    assert not F.is_ftc_prompt("integrate x^2")
    assert not F.is_ftc_prompt("fundamental group of a torus")


def test_proof_components_present():
    svg, narr = F.render_ftc()
    minidom.parseString(svg)
    assert 5 <= len(narr) <= 9                        # 'proof' archetype
    low = svg.lower()
    # statement of both parts
    assert "part 1" in low and "part 2" in low
    # >= 3 deduction markers
    markers = sum(m in low for m in ("define", "consider", "mean value",
                                     "as h", "therefore", "so the"))
    assert markers >= 3, f"only {markers} deduction markers"
    assert "∎" in svg                                 # QED
    # the geometric strip the vision review asked for
    assert "y = f(t)" in svg and "F(x)" in svg


def test_no_anchor_collisions():
    svg, _ = F.render_ftc()
    a = re.findall(r'<text x="([\-0-9.]+)" y="([\-0-9.]+)"', svg)
    assert not [k for k, n in Counter(a).items() if n > 1]


def test_routes_through_express_and_passes_probe():
    from studio.express import express_figure
    r = asyncio.run(express_figure(
        "show the proof of the fundamental theorem of calculus",
        base_url="", model="", api_key=""))
    assert r.get("template") == "ftc"
    assert r.get("retries_used") == 0
    import importlib.util
    spec = importlib.util.spec_from_file_location("qp", "studio/quality_probe.py")
    qp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qp)
    assert qp.inspect_quality("ftc", r) == []
