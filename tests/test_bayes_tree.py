"""Deterministic Bayes probability-tree renderer tests.

Locks in routing, arithmetic correctness (leaves sum to 1, posterior is
the Bayes quotient), and that no two labels share an anchor — replacing
the LLM-emitted-DOT graphviz figure that produced floating, disconnected
nodes for this prompt class.
"""
from __future__ import annotations

import asyncio
import re
from collections import Counter
from xml.dom import minidom

from studio.templates import bayes_tree as B


def test_routing():
    assert B.is_bayes_tree_prompt("explain Bayes theorem with a tree diagram")
    assert B.is_bayes_tree_prompt("draw a probability tree")
    assert B.is_bayes_tree_prompt("Bayes rule tree diagram")
    # Must not hijack unrelated "tree" prompts or bare "bayesian".
    assert not B.is_bayes_tree_prompt("explain a binary search tree")
    assert not B.is_bayes_tree_prompt("bayesian inference")


def test_valid_xml_and_no_anchor_collisions():
    svg, narr = B.render_bayes_tree()
    minidom.parseString(svg)
    assert len(narr) == 5
    anchors = re.findall(r'<text x="([\-0-9.]+)" y="([\-0-9.]+)"', svg)
    dups = [a for a, n in Counter(anchors).items() if n > 1]
    assert not dups, f"overlapping anchors: {dups}"


def test_arithmetic_is_correct():
    svg, _ = B.render_bayes_tree()
    # Joints shown must be the path products and sum to 1.
    for joint, val in (("P(A∩B) = 0.24", 0.24), ("P(¬A∩B) = 0.06", 0.06),
                       ("P(A∩¬B) = 0.07", 0.07), ("P(¬A∩¬B) = 0.63", 0.63)):
        assert joint in svg, joint
    assert abs(0.24 + 0.06 + 0.07 + 0.63 - 1.0) < 1e-9
    # Posterior = 0.24 / 0.31 ≈ 0.7742, and it exceeds the prior 0.3.
    assert "0.24 / 0.31" in svg
    assert "0.7742" in svg


def test_route_returns_bayes_template():
    from studio.express import express_figure
    r = asyncio.run(express_figure(
        "explain Bayes theorem with a tree diagram",
        base_url="", model="", api_key=""))
    assert r.get("template") == "bayes_tree"
    assert r.get("retries_used") == 0
    assert "<svg" in (r.get("svg") or "")
