"""Regression tests for CanvasRegistry memory bounding.

Background: the registry was an unbounded ``dict[str, Canvas]`` — every
/studio/chat request inserted a Canvas (each holding a ~100 KB SVG) that
was never evicted, so the process crept upward in memory over a long
uptime (the slow side of the 2026-06-08 OOM).  The registry now evicts
by LRU (oldest ``updated_at``) past a size cap and by a TTL.  Eviction is
safe because ``get()`` rehydrates an evicted canvas from S3 on a miss.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _fresh_registry(max_n: int, ttl_s: float):
    os.environ["SEVIM_REGISTRY_MAX"] = str(max_n)
    os.environ["SEVIM_REGISTRY_TTL_S"] = str(ttl_s)
    sys.modules.pop("service.canvas", None)
    import service.canvas as cv
    return cv.CanvasRegistry(), cv.Canvas


def _put(reg, Canvas, cid: str, updated_at: float) -> None:
    """Insert a canvas with a controlled updated_at directly, bypassing
    open()'s eager eviction so _evict_locked can be tested in isolation."""
    c = Canvas(canvas_id=cid)
    c.updated_at = updated_at
    with reg._lock:
        reg._canvases[cid] = c


def test_size_cap_evicts_oldest() -> None:
    reg, Canvas = _fresh_registry(max_n=8, ttl_s=10_000)
    base = time.time()
    for i in range(20):
        # all recent enough to stay within the TTL, so only the size cap
        # bites; staggered updated_at makes LRU order deterministic
        _put(reg, Canvas, f"c{i:02d}", base + i)
    with reg._lock:
        reg._evict_locked()
    live = {d["canvas_id"] for d in reg.list()}
    assert len(live) <= 8, f"expected <=8 resident, got {len(live)}"
    # The 8 most-recent must survive; the oldest must be gone.
    assert "c19" in live and "c00" not in live, sorted(live)


def test_ttl_evicts_stale() -> None:
    reg, Canvas = _fresh_registry(max_n=1000, ttl_s=100)
    _put(reg, Canvas, "old", time.time() - 500)   # past the 100s TTL
    _put(reg, Canvas, "fresh", time.time())
    with reg._lock:
        reg._evict_locked()
    live = {d["canvas_id"] for d in reg.list()}
    assert "fresh" in live and "old" not in live, sorted(live)


def test_active_canvas_not_evicted() -> None:
    # The most-recently-updated canvas (the one being streamed, whose
    # updated_at is bumped continuously during generation) must never be
    # the eviction victim, even under heavy churn.
    # _max is floored at 8 in the registry; use that as the cap.
    reg, Canvas = _fresh_registry(max_n=8, ttl_s=10_000)
    base = time.time()
    for i in range(30):
        _put(reg, Canvas, f"churn{i}", base + i)   # older
    _put(reg, Canvas, "active", base + 1000)       # newest of all
    with reg._lock:
        reg._evict_locked()
    live = {d["canvas_id"] for d in reg.list()}
    assert "active" in live, sorted(live)
    assert len(live) <= 8


if __name__ == "__main__":
    test_size_cap_evicts_oldest()
    test_ttl_evicts_stale()
    test_active_canvas_not_evicted()
    print("All registry-eviction tests passed.")
