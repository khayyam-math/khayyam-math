"""Regression test: multiple text blocks in the SAME region must stack,
not overlap.

Bug (2026-06-09): the recursion-theorem figure emitted three separate
right-column blocks ("Output P/Q/R: …") and render_text_blocks restarted
each at the region's fixed y, so all three overlapped into unreadable
mush.  render_text_blocks now keeps a per-region y-cursor.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio.express import render_text_blocks, TEXT_REGIONS  # noqa: E402


def _ys(svg: str) -> list[float]:
    return [float(m) for m in re.findall(r'<text x="[\d.]+" y="([\d.]+)"', svg)]


def test_same_region_blocks_do_not_overlap() -> None:
    blocks = [
        {"region": "right-column", "lines": ["Output P:", "Self-desc + result."]},
        {"region": "right-column", "lines": ["Output Q:", "Self-desc + result."]},
        {"region": "right-column", "lines": ["Output R:", "Self-desc + result."]},
    ]
    ys = _ys(render_text_blocks(blocks))
    assert len(ys) == 6
    assert len(set(ys)) == len(ys), f"duplicate y (overlap): {ys}"
    assert ys == sorted(ys), f"not monotonic: {ys}"
    lh = TEXT_REGIONS["right-column"]["line_height"]
    gaps = [b - a for a, b in zip(ys, ys[1:])]
    assert min(gaps) >= lh - 0.01, f"line gap below line_height: {gaps}"


def test_single_block_still_stacks() -> None:
    ys = _ys(render_text_blocks(
        [{"region": "left-column", "lines": ["A", "B", "C"]}]))
    assert ys == sorted(ys) and len(set(ys)) == 3


def test_distinct_regions_independent() -> None:
    blocks = [
        {"region": "left-column", "lines": ["L1", "L2"]},
        {"region": "right-column", "lines": ["R1", "R2"]},
    ]
    svg = render_text_blocks(blocks)
    # both regions start at their own configured y
    assert TEXT_REGIONS["left-column"]["y"] in _ys(svg)
    assert TEXT_REGIONS["right-column"]["y"] in _ys(svg)


if __name__ == "__main__":
    test_same_region_blocks_do_not_overlap()
    test_single_block_still_stacks()
    test_distinct_regions_independent()
    print("All text-block stacking tests passed.")
