"""Regression tests for two canvas-viewer UX fixes.

1. Auto-follow: the narration highlight scrolls the panel under attention
   into view, so a multi-step / multi-panel figure does not require manual
   scrolling to keep up.
2. Play-button clip: the body is a flex column with the window scroll
   locked, so the header (and its Play button) can never be pushed behind
   the mobile browser toolbar after panning the figure.

These are static-asset assertions (no browser) — they guard the wiring from
being silently removed by a future refactor, the same way
test_narration_interrupt.py guards the interrupt call sites.
"""
from __future__ import annotations

from pathlib import Path

_HTML = (Path(__file__).resolve().parent.parent
         / "service" / "static" / "canvas.html").read_text(encoding="utf-8")


def test_autoscroll_function_defined_and_called():
    # the helper exists
    assert "function scrollHighlightIntoView(" in _HTML
    # and applyHighlight invokes it so every narration phrase auto-follows
    apply_idx = _HTML.index("function applyHighlight(")
    next_fn = _HTML.index("function scrollHighlightIntoView(")
    body = _HTML[apply_idx:next_fn]
    assert "scrollHighlightIntoView()" in body, \
        "applyHighlight must call scrollHighlightIntoView()"


def test_autoscroll_uses_union_bbox_and_smooth_scroll():
    fn_idx = _HTML.index("function scrollHighlightIntoView(")
    fn = _HTML[fn_idx:fn_idx + 2400]
    assert ".sevim-highlight" in fn            # follows the highlighted set
    assert "getBoundingClientRect" in fn       # measures real on-screen box
    assert "scrollBy" in fn and "smooth" in fn  # smooth, minimal scroll


def test_body_is_flex_column_with_locked_window_scroll():
    # The Play-button-clip fix: body is a flex column that never scrolls the
    # window, so the header stays pinned regardless of header wrapping or the
    # iOS dynamic toolbar.  (Asserted against the whole file; the body rule
    # carries a long explanatory comment.)
    assert "flex-direction: column" in _HTML
    assert "overflow: hidden" in _HTML
    assert "padding-top: env(safe-area-inset-top)" in _HTML
    # the header is a fixed-size, non-shrinking flex row
    assert "flex: 0 0 auto" in _HTML


def test_main_height_no_longer_hardcoded_to_header_offset():
    # The fragile `height: calc(100dvh - 50px/44px)` is gone; <main> sizes
    # itself via flex so a wrapped header can't push it off-screen.
    assert "calc(100dvh - 50px)" not in _HTML
    assert "calc(100dvh - 44px)" not in _HTML
