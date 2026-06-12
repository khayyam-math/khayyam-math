"""Deterministic Euclid infinitude-of-primes proof renderer tests.

This route exists because the prompt failed the 'proof' completeness
rubric on the LLM-SVG path (no statement, no deduction steps, no QED).
The deterministic figure must carry all three, plus a correct example.
"""
from __future__ import annotations

import asyncio
import re
from collections import Counter
from xml.dom import minidom

from studio.templates import infinitude_primes as I


def test_routing():
    assert I.is_infinitude_primes_prompt("prove that there are infinitely many primes")
    assert I.is_infinitude_primes_prompt("the infinitude of primes")
    assert I.is_infinitude_primes_prompt("show primes never run out")
    # Must not hijack other proofs or non-proof prime prompts.
    assert not I.is_infinitude_primes_prompt("prove the pythagorean theorem")
    assert not I.is_infinitude_primes_prompt("list the first 10 primes")
    # Bare "euclid's theorem" without 'prime' is ambiguous -> declines.
    assert not I.is_infinitude_primes_prompt("euclid's theorem")


def test_proof_components_present():
    svg, narr = I.render_infinitude_primes()
    minidom.parseString(svg)
    # 'proof' archetype expects 5-9 narration phrases.
    assert 5 <= len(narr) <= 9
    low = svg.lower()
    assert "statement" in low                       # explicit statement
    # at least three deduction markers
    markers = sum(m in low for m in ("assume", "let ", "note that", "therefore"))
    assert markers >= 3, f"only {markers} deduction markers"
    assert "∎" in svg                               # QED


def test_example_arithmetic_correct():
    svg, _ = I.render_infinitude_primes()
    assert 2 * 3 * 5 + 1 == 31
    assert 2 * 3 * 5 * 7 * 11 * 13 + 1 == 30031 == 59 * 509
    assert "31" in svg and "30031" in svg and "59 × 509" in svg


def test_no_anchor_collisions():
    svg, _ = I.render_infinitude_primes()
    a = re.findall(r'<text x="([\-0-9.]+)" y="([\-0-9.]+)"', svg)
    assert not [k for k, n in Counter(a).items() if n > 1]


def test_routes_through_express_and_passes_probe():
    from studio.express import express_figure
    r = asyncio.run(express_figure("prove that there are infinitely many primes",
                                   base_url="", model="", api_key=""))
    assert r.get("template") == "infinitude_primes"
    assert r.get("retries_used") == 0
    # The probe must now find it clean (it previously flagged retries exhausted).
    import importlib.util
    spec = importlib.util.spec_from_file_location("qp", "studio/quality_probe.py")
    qp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qp)
    assert qp.inspect_quality("prove that there are infinitely many primes", r) == []
