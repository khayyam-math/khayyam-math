"""Regression tests for the legibility font-size floor.

The express LLM sometimes sizes secondary text at 8-11px, which renders
as squint-text next to 14-20px primary labels (the "small text" flagged
on the recursion-theorem figure). enforce_min_font_size raises sub-floor
absolute sizes to the floor; it NEVER shrinks, and leaves percentage
(sub/superscript) sizes alone.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio.express import enforce_min_font_size  # noqa: E402


def test_raises_small_attr_font() -> None:
    out = enforce_min_font_size('<text font-size="9">x</text>')
    assert 'font-size="13"' in out and 'font-size="9"' not in out


def test_keeps_large_font() -> None:
    out = enforce_min_font_size('<text font-size="20">x</text>')
    assert 'font-size="20"' in out


def test_leaves_percentage_alone() -> None:
    # sub/superscript tspans use relative %; must not be touched
    out = enforce_min_font_size('<tspan font-size="80%">n</tspan>')
    assert 'font-size="80%"' in out


def test_raises_small_style_font() -> None:
    out = enforce_min_font_size('<text style="font-size:10px">x</text>')
    assert 'font-size:13px' in out


def test_keeps_large_style_font() -> None:
    out = enforce_min_font_size('<text style="font-size:18px">x</text>')
    assert 'font-size:18px' in out


def test_custom_floor() -> None:
    out = enforce_min_font_size('<text font-size="10">x</text>', floor=16)
    assert 'font-size="16"' in out


def test_mixed_figure() -> None:
    svg = ('<svg><text font-size="22">Title</text>'
           '<text font-size="9">tiny note</text>'
           '<tspan font-size="80%">sub</tspan></svg>')
    out = enforce_min_font_size(svg)
    assert 'font-size="22"' in out          # untouched
    assert 'font-size="13"' in out          # 9 -> 13
    assert 'font-size="9"' not in out
    assert 'font-size="80%"' in out          # untouched


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("All min-font-size tests passed.")
