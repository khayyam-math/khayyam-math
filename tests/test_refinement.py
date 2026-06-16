"""Self-aware conversational-refinement tests.

Covers Layer 1 (topic carry-forward so a correction re-reaches the right
deterministic renderer), Layer 2 (per-session repeat-failure circuit
breaker), and Layer 3 (durable refinement-outcome logging).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from studio import refinement as R


# ── Layer 1: dissatisfaction detection + topic carry-forward ────────────

def test_is_dissatisfaction():
    assert R.is_dissatisfaction("no, this is nonsense")
    assert R.is_dissatisfaction("I need nodes! two graphs with nodes!")
    assert R.is_dissatisfaction("this is not a graph")
    assert R.is_dissatisfaction("that's wrong, it should be a tree")
    assert not R.is_dissatisfaction("show me the SVD of a matrix")
    assert not R.is_dissatisfaction("")


def test_carry_topic_folds_prior_subject():
    ctx = [{"prompt": "reduce clique to vertex cover", "svg": "<svg/>"}]
    out = R.carry_topic("I need nodes! two graphs with nodes!", ctx,
                        is_narrow_edit=False)
    assert "clique" in out.lower() and "vertex cover" in out.lower()
    assert "nodes" in out.lower()


def test_carry_topic_makes_deterministic_route_match_again():
    """The crux of the bug: the correction alone does not match the clique
    route, but with the carried topic it does."""
    from studio.templates.clique_vertex_cover import (
        is_clique_vertex_cover_prompt,
    )
    correction = "I need nodes! two graphs with nodes!"
    assert not is_clique_vertex_cover_prompt(correction)
    ctx = [{"prompt": "reduce clique to vertex cover"}]
    carried = R.carry_topic(correction, ctx, is_narrow_edit=False)
    assert is_clique_vertex_cover_prompt(carried)


def test_carry_topic_noops_on_narrow_edit_and_fresh():
    ctx = [{"prompt": "reduce clique to vertex cover"}]
    # narrow edit must edit the existing figure, not re-route
    assert R.carry_topic("make it red", ctx, is_narrow_edit=True) == "make it red"
    # no context => fresh request, unchanged
    assert R.carry_topic("draw a circle", None,
                         is_narrow_edit=False) == "draw a circle"
    # topic already present => not duplicated
    out = R.carry_topic("reduce clique to vertex cover on a graph", ctx,
                        is_narrow_edit=False)
    assert out.lower().count("clique") == 1


# ── Layer 2: repeat-failure circuit breaker ─────────────────────────────

def test_tracker_counts_consecutive_complaints_per_subject():
    t = R.Tracker()
    s = "reduce clique to vertex cover"
    t.record("sess1", s, "llm_svg", complaint=True)
    assert t.consecutive_complaints("sess1", s) == 1
    t.record("sess1", s, "llm_svg", complaint=True)
    assert t.consecutive_complaints("sess1", s) == 2
    assert t.should_escalate("sess1", s)        # default threshold 2
    # a different subject is tracked separately
    assert t.consecutive_complaints("sess1", "taylor series") == 0


def test_tracker_streak_breaks_on_satisfied_turn():
    t = R.Tracker()
    s = "newton's method"
    t.record("sess2", s, "llm_svg", complaint=True)
    t.record("sess2", s, "newton_intro", complaint=False)  # satisfied
    assert t.consecutive_complaints("sess2", s) == 0


def test_escalation_directive_says_change_approach():
    d = R.escalation_directive(2, "llm_svg", deterministic_available=False)
    low = d.lower()
    assert "rejected" in low and "change" in low
    assert "closest" in low  # honesty clause when LLM-SVG kept failing


# ── Layer 3: durable logging ────────────────────────────────────────────

def test_record_and_mine_refinement_outcomes():
    from sevim.telemetry import Telemetry
    with tempfile.TemporaryDirectory() as d:
        tel = Telemetry(db_url=str(Path(d) / "t.db"))
        for _ in range(3):
            tel.record_refinement_outcome(
                session_id="s", subject="reduce clique to vertex cover",
                route="llm_svg", complaint=True)
        tel.record_refinement_outcome(
            session_id="s", subject="something else", route="svd",
            complaint=False)
        rows = tel.recurring_refinement_failures(min_count=3, since_s=86400.0)
        subjects = {r[0] for r in rows}
        assert "reduce clique to vertex cover" in subjects
        assert "something else" not in subjects
