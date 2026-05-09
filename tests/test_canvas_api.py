"""Tests for the MCP-facing Canvas API contract.

Covers two fixes that were silently producing nonsense canvases:
1. Repeated labels with distinct ``node_id`` must produce distinct nodes
   (without ``node_id``, the slugged label collapses them).
2. Caption ``anchor`` outside the closed vocabulary must be rejected so
   the LLM gets immediate feedback instead of misplaced captions.
"""
from __future__ import annotations

import pytest

from service.canvas import Canvas, VALID_ANCHORS


def _make_canvas() -> Canvas:
    return Canvas(canvas_id="test", math_mode=True, width=900, height=620)


def test_repeated_labels_collide_without_node_id():
    """Documents the prior failure mode: nine nodes labelled "x₁/x₂/x₃"
    across three clauses, no explicit ``node_id``, all collapse to three.
    """
    c = _make_canvas()
    for clause in range(3):
        for lit in ("x", "y", "z"):
            c.add_node(label=lit, kind="point",
                       meta_extras={"x": float(clause), "y": float(lit_idx := ord(lit))})
    assert len(c.graph.nodes) == 3, (
        f"expected slug-collision to keep 3 nodes, got {len(c.graph.nodes)}"
    )


def test_explicit_node_id_keeps_repeated_labels_distinct():
    """The fix: pass ``node_id`` per node and they all stay distinct."""
    c = _make_canvas()
    for clause in range(3):
        for lit_name in ("x", "y", "z"):
            c.add_node(
                label=lit_name, kind="point",
                meta_extras={"x": float(clause), "y": float(ord(lit_name))},
                node_id=f"c{clause}_{lit_name}",
            )
    assert len(c.graph.nodes) == 9, (
        f"expected 9 distinct nodes via node_id, got {len(c.graph.nodes)}"
    )
    # And the ids should match the requested ones (with the n_ prefix).
    ids = {n.id for n in c.graph.nodes}
    expected = {f"n_c{clause}_{lit}" for clause in range(3) for lit in ("x", "y", "z")}
    assert ids == expected


def test_node_id_is_sanitised_to_slug_alphabet():
    """Arbitrary characters in node_id must be normalised so downstream
    id-string handling (CSS attributes, edge id concatenation) stays
    well-formed.  Punctuation and spaces become underscores."""
    c = _make_canvas()
    c.add_node(label="alpha", node_id="My ID! 1.0")
    assert c.graph.nodes[0].id == "n_my_id_1_0"


def test_node_id_already_prefixed_is_kept():
    """If the caller already wrote ``n_foo``, don't double-prefix."""
    c = _make_canvas()
    c.add_node(label="alpha", node_id="n_foo")
    assert c.graph.nodes[0].id == "n_foo"


def test_empty_node_id_after_sanitisation_raises():
    """A node_id that strips to nothing (e.g. all punctuation) must fail
    loud rather than silently fall back to label-slugging."""
    c = _make_canvas()
    with pytest.raises(ValueError, match="empty"):
        c.add_node(label="alpha", node_id="!!!")


def test_valid_anchors_set_matches_layout_expectations():
    """Lock the closed vocabulary so the anchor validator and the layout
    placer can never drift apart."""
    expected = {"auto", "above", "below", "left", "right", "overlay",
                "top", "bottom"}
    assert set(VALID_ANCHORS) == expected


def test_validate_anchor_accepts_canonical_values():
    from mcp_server.server import _validate_anchor
    for a in ("auto", "above", "below", "left", "right", "overlay",
              "top", "bottom"):
        _validate_anchor(a)  # no-raise


def test_validate_anchor_rejects_llm_hallucinations():
    """The two cases observed in real test sessions: ``middle`` and
    ``center``.  Both must raise so the model self-corrects."""
    from mcp_server.server import _validate_anchor
    for bad in ("middle", "center", "top-left", "bottomright", ""):
        with pytest.raises(ValueError, match="closed vocabulary"):
            _validate_anchor(bad)


def test_validate_anchor_is_case_insensitive():
    """Pragmatic: ``Above`` should not be a hallucination."""
    from mcp_server.server import _validate_anchor
    _validate_anchor("ABOVE")
    _validate_anchor("Below")
