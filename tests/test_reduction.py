"""Deterministic reduction-renderer tests.

Locks in (a) correct routing, (b) arithmetic correctness of the canonical
Subset Sum -> Partition example (the screenshot bug shipped halves that
summed to 9 and 11), and (c) that no two labels share an anchor — the
overlap class that sent this prompt to the LLM in the first place.
"""
from __future__ import annotations

import asyncio
import re
from collections import Counter
from xml.dom import minidom

from studio.templates import reduction as R


def _anchors(svg: str):
    return re.findall(r'<text x="([\-0-9.]+)" y="([\-0-9.]+)"', svg)


def test_routing_accepts_reductions_rejects_fractions():
    assert R.is_reduction_prompt("show the reduction from subset sum to partition")
    assert R.is_reduction_prompt("reduce subset sum to partition")
    assert R.is_reduction_prompt("prove vertex cover reduces to clique")
    assert R.is_reduction_prompt("reduce 3-SAT to vertex cover")
    # "reduce a fraction" is NOT a complexity reduction (no named problem)
    assert not R.is_reduction_prompt("reduce the fraction 6/8")
    assert not R.is_reduction_prompt("reduce this matrix to row echelon form")


def test_subset_sum_partition_arithmetic_is_correct():
    svg, narr = R.render_subset_sum_to_partition()
    minidom.parseString(svg)  # valid XML
    assert len(narr) == 5
    # The two halves must be SHOWN as equal (the original bug: 9 vs 11).
    sums = re.findall(r"sum = (\d+)", svg)
    halves = [s for s in sums]
    assert halves[:2] == [halves[0], halves[0]], f"halves differ: {sums}"
    # And the equal-half value is total/2 = 12 for the canonical example.
    assert "each half = 12" in svg
    assert "S1 = {2, 3, 7}" in svg and "S2 = {4, 8}" in svg


def test_no_two_labels_share_an_anchor():
    svg, _ = R.render_subset_sum_to_partition()
    dups = [a for a, n in Counter(_anchors(svg)).items() if n > 1]
    assert not dups, f"overlapping text anchors: {dups}"


def test_generic_pair_is_parsed_and_rendered():
    assert R._parse_pair("prove vertex cover reduces to clique") == (
        "vertex cover", "clique")
    assert R._parse_pair("reduce 3-SAT to vertex cover") == (
        "3-sat", "vertex cover")
    svg, narr = asyncio.run(R.generate_reduction_svg("reduce 3-SAT to vertex cover"))
    minidom.parseString(svg)
    assert "Vertex Cover" in svg and "3-Sat" in svg
    # no invented numbers in the generic schematic
    assert "sum =" not in svg


def test_generate_returns_none_for_unparseable():
    assert asyncio.run(R.generate_reduction_svg("explain np-completeness")) is None
