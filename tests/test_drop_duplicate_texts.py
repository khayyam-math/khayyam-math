"""Tests for drop_duplicate_texts — the catch-all that removes a label the
LLM stamped twice at the same spot (the 'S2 = {1,2,2,6}' printed-twice
defect).  Must drop the redundant copy but never a legitimate repeat, a
short tick label, or a genuinely different overlapping label.
"""
from __future__ import annotations

from studio.express import drop_duplicate_texts as D


def _wrap(*texts: str) -> str:
    return '<svg viewBox="0 0 900 560">' + "".join(texts) + "</svg>"


def test_drops_overlapping_identical_label():
    svg = _wrap(
        '<text x="600" y="900" font-size="15" text-anchor="middle">S2 = {1, 2, 2, 6}</text>',
        '<text x="610" y="912" font-size="15" text-anchor="middle">S2 = {1, 2, 2, 6}</text>',
    )
    out = D(svg)
    assert out.count("S2 = {1, 2, 2, 6}") == 1


def test_idempotent():
    svg = _wrap(
        '<text x="600" y="900" font-size="15" text-anchor="middle">S2 = {1, 2, 2, 6}</text>',
        '<text x="610" y="912" font-size="15" text-anchor="middle">S2 = {1, 2, 2, 6}</text>',
    )
    once = D(svg)
    assert D(once) == once


def test_keeps_far_apart_repeat():
    svg = _wrap(
        '<text x="100" y="100" font-size="14">subset sums to T</text>',
        '<text x="100" y="500" font-size="14">subset sums to T</text>',
    )
    assert D(svg).count("subset sums to T") == 2


def test_never_drops_short_labels():
    svg = _wrap('<text x="10" y="10">0</text>', '<text x="11" y="11">0</text>')
    assert D(svg).count(">0<") == 2


def test_keeps_different_overlapping_labels():
    # Different content -> reflow_overlapping_text nudges these; dedup keeps both.
    svg = _wrap(
        '<text x="10" y="10" font-size="14">hello world</text>',
        '<text x="12" y="11" font-size="14">goodbye world</text>',
    )
    assert D(svg).count("<text") == 2


def test_clean_svg_unchanged():
    svg = _wrap('<text x="10" y="10" font-size="14">unique label one</text>',
                '<text x="10" y="60" font-size="14">unique label two</text>')
    assert D(svg) == svg
