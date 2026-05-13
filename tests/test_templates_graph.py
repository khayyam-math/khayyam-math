"""Tests for the graph-shaped templates."""
from __future__ import annotations

import re
import pytest

from studio.templates.graph import state_diagram


def _ids(svg: str) -> set[str]:
    return set(re.findall(r'id="([^"]+)"', svg))


def test_dfa_two_state_renders():
    svg, narr = state_diagram(
        [{"id": "q0", "initial": True},
         {"id": "q1", "accept": True}],
        [{"source": "q0", "target": "q0", "label": "0"},
         {"source": "q0", "target": "q1", "label": "1"},
         {"source": "q1", "target": "q0", "label": "0"},
         {"source": "q1", "target": "q1", "label": "1"}],
    )
    ids = _ids(svg)
    # Every state has a circle id.
    for sid in ("q0", "q1"):
        assert sid in ids, f"state {sid} missing from SVG"
    # Accept state has a double-circle outer.
    assert "q1_outer" in ids
    # Initial state arrow.
    assert "start_arrow_q0" in ids
    # Edge ids (one per transition).
    assert "edge_0" in ids and "edge_1" in ids and "edge_2" in ids and "edge_3" in ids
    # Narration walks the states + samples transitions.
    assert len(narr) >= 3


def test_state_diagram_handles_cycles_without_hanging():
    """Earlier longest-path layering blew up on cycles (24 GB RAM on a
    2-state DFA with self-loops).  Shortest-path BFS layering must
    terminate quickly even on graphs with many cycles."""
    states = [{"id": f"q{i}"} for i in range(5)]
    states[0]["initial"] = True
    states[-1]["accept"] = True
    # Fully connected cycle: every state to every other.
    transitions = [
        {"source": f"q{i}", "target": f"q{j}", "label": f"{i}{j}"}
        for i in range(5) for j in range(5)
    ]
    import time
    t0 = time.time()
    svg, narr = state_diagram(states, transitions)
    elapsed = time.time() - t0
    assert elapsed < 1.0, f"layout took {elapsed:.2f}s — should be sub-second"
    assert "<svg" in svg and "</svg>" in svg
    assert all(f"q{i}" in _ids(svg) for i in range(5))


def test_no_initial_state_uses_first():
    svg, _ = state_diagram(
        [{"id": "a"}, {"id": "b"}],
        [{"source": "a", "target": "b", "label": "x"}],
    )
    assert "<svg" in svg


def test_empty_states_raises():
    with pytest.raises(ValueError):
        state_diagram([], [])


def test_accept_state_double_circle():
    svg, _ = state_diagram(
        [{"id": "q0", "initial": True}, {"id": "qf", "accept": True}],
        [{"source": "q0", "target": "qf", "label": "x"}],
    )
    # An accept state has BOTH an outer (larger) and inner circle.
    ids = _ids(svg)
    assert "qf_outer" in ids
    assert "qf" in ids


def test_self_loop_renders_as_path():
    svg, _ = state_diagram(
        [{"id": "q0", "initial": True}],
        [{"source": "q0", "target": "q0", "label": "loop"}],
    )
    # Self-loops use a <path> (curved arc above the state).
    assert '<path id="edge_0"' in svg


def test_narration_highlight_ids_resolve():
    svg, narration = state_diagram(
        [{"id": "q0", "initial": True}, {"id": "q1", "accept": True}],
        [{"source": "q0", "target": "q1", "label": "a"}],
    )
    ids = _ids(svg)
    for phrase in narration:
        for hid in (phrase.get("highlight") or []):
            assert hid in ids, f"narration highlight {hid!r} not in SVG"
