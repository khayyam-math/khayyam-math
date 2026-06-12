"""Deterministic stats-template tests: normal distribution + confusion matrix.

Lock in routing (and the no-hijack guards), arithmetic correctness, valid
XML, and no overlapping anchors.
"""
from __future__ import annotations

import asyncio
import re
from collections import Counter
from xml.dom import minidom

from studio.templates import normal_distribution as N
from studio.templates import confusion_matrix as C


def _anchor_dups(svg: str):
    a = re.findall(r'<text x="([\-0-9.]+)" y="([\-0-9.]+)"', svg)
    return [k for k, n in Counter(a).items() if n > 1]


# ── normal distribution ──────────────────────────────────────────────
def test_normal_routing():
    assert N.is_normal_distribution_prompt("explain the normal distribution")
    assert N.is_normal_distribution_prompt("bell curve with the empirical rule")
    assert N.is_normal_distribution_prompt("the 68-95-99.7 rule")
    # Must NOT hijack Gaussian elimination.
    assert not N.is_normal_distribution_prompt("solve Ax=b by gaussian elimination")
    assert not N.is_normal_distribution_prompt("what is a matrix")


def test_normal_render():
    svg, narr = N.render_normal_distribution()
    minidom.parseString(svg)
    assert len(narr) == 5
    assert not _anchor_dups(svg)
    # The empirical-rule percentages must be present and correct.
    for pct in ("68.2%", "13.6%", "2.1%", "95.4%", "99.7%"):
        assert pct in svg, pct


# ── confusion matrix ─────────────────────────────────────────────────
def test_confusion_routing():
    assert C.is_confusion_matrix_prompt("draw a confusion matrix")
    assert C.is_confusion_matrix_prompt("explain precision and recall")
    assert C.is_confusion_matrix_prompt("make a contingency table")
    assert not C.is_confusion_matrix_prompt("multiply two matrices")


def test_confusion_metrics_correct():
    svg, narr = C.render_confusion_matrix()
    minidom.parseString(svg)
    assert len(narr) == 5
    assert not _anchor_dups(svg)
    # Cells and metrics must be the computed values.
    for s in ("TP = 80", "FP = 10", "FN = 20", "TN = 90",
              "0.889", "0.800", "0.850", "0.842"):
        assert s in svg, s


# ── end-to-end routing through express ───────────────────────────────
def test_routes_through_express():
    from studio.express import express_figure
    r1 = asyncio.run(express_figure("explain the normal distribution",
                                    base_url="", model="", api_key=""))
    assert r1.get("template") == "normal_distribution"
    r2 = asyncio.run(express_figure("draw a confusion matrix",
                                    base_url="", model="", api_key=""))
    assert r2.get("template") == "confusion_matrix"
    assert r1.get("retries_used") == 0 and r2.get("retries_used") == 0
