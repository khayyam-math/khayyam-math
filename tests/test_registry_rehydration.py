"""REGISTRY rehydration: a canvas persisted to storage by one process
should be reconstructible after a fresh process spins up with an empty
in-memory dict — proving the "ECS task replacement no longer 404s the
user's canvas" guarantee.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def fresh_storage(monkeypatch):
    """Point storage at a temp directory and reset the module singleton.

    Forces FileStorage (no SEVIM_STORAGE_URL=s3://) so the test is
    self-contained.  The storage root is exposed so the test can poke
    at the persisted blob directly.
    """
    tmp = tempfile.mkdtemp(prefix="sevim_storage_")
    monkeypatch.delenv("SEVIM_STORAGE_URL", raising=False)
    monkeypatch.setenv("SEVIM_DATA_DIR", tmp)
    import service.storage as storage_mod
    storage_mod._INSTANCE = None  # type: ignore[attr-defined]
    yield Path(tmp)
    storage_mod._INSTANCE = None  # type: ignore[attr-defined]


def test_canvas_persists_on_set_raw_svg(fresh_storage):
    from service.canvas import Canvas
    c = Canvas(canvas_id="express_test1", math_mode=False)
    c.set_raw_svg(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '<rect id="r1" x="0" y="0" width="50" height="50"/></svg>'
    )
    state_path = fresh_storage / "express_test1" / "state.json"
    assert state_path.exists(), "set_raw_svg should persist state.json"
    payload = json.loads(state_path.read_text())
    assert payload["canvas_id"] == "express_test1"
    assert payload["is_raw_svg"] is True
    assert "r1" in payload["raw_svg_ids"]
    assert "<rect" in payload["svg"]


def test_registry_get_rehydrates_after_memory_wipe(fresh_storage):
    """Simulate the production scenario: process A sets a canvas, then
    process B (fresh REGISTRY) is asked for the same id — should
    rehydrate from the persisted blob instead of raising KeyError."""
    from service.canvas import Canvas, CanvasRegistry

    # Process A: create + persist.
    reg_a = CanvasRegistry()
    c = reg_a.open(canvas_id="express_test2")
    c.set_raw_svg(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        '<rect id="alpha" x="0" y="0" width="50" height="50"/></svg>'
    )
    # Process B: fresh registry (empty in-memory dict, same storage backend).
    reg_b = CanvasRegistry()
    assert "express_test2" not in reg_b._canvases  # pre-condition
    rehydrated = reg_b.get("express_test2")
    assert rehydrated.canvas_id == "express_test2"
    assert rehydrated.is_raw_svg is True
    assert "alpha" in rehydrated.raw_svg_ids
    assert "<rect" in rehydrated.svg


def test_registry_get_missing_canvas_raises(fresh_storage):
    """No state.json → KeyError, same as the in-memory-only behaviour."""
    from service.canvas import CanvasRegistry
    reg = CanvasRegistry()
    with pytest.raises(KeyError):
        reg.get("never_existed_42")


def test_rehydrate_idempotent(fresh_storage):
    """Calling get() twice on a rehydrated canvas returns the SAME
    object — rehydration must populate the in-memory cache."""
    from service.canvas import Canvas, CanvasRegistry
    reg_a = CanvasRegistry()
    c = reg_a.open(canvas_id="express_test3")
    c.set_raw_svg(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 50 50">'
        '<circle id="c1" cx="25" cy="25" r="10"/></svg>'
    )
    reg_b = CanvasRegistry()
    first = reg_b.get("express_test3")
    second = reg_b.get("express_test3")
    assert first is second  # in-memory cached on first rehydrate
