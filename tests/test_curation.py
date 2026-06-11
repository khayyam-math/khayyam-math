"""Phase-3 curation tests (stubbed embedder, no network)."""
from __future__ import annotations

import pytest

from sevim import embeddings as emb
from sevim.telemetry import Telemetry
from studio import curation
from studio import taxonomy as tax
from studio import taxonomy_seed


_VOCAB = ["matrix", "multiply", "vertex", "cover", "np", "complete", "sat",
          "partition", "reduction", "knapsack", "dynamic", "programming",
          "pump", "lemma", "regular", "language", "automaton"]


def _toy(text, *, model=None, timeout_s=15.0):
    t = (text or "").lower()
    return [float(t.count(w)) for w in _VOCAB]


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    monkeypatch.setattr(emb, "embed", _toy)
    monkeypatch.setattr(emb, "available", lambda: True)
    monkeypatch.setattr(tax._emb, "embed", _toy)
    monkeypatch.setattr(tax._emb, "available", lambda: True)
    monkeypatch.setenv("SEVIM_TAXONOMY", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")


def _tel(tmp_path, monkeypatch):
    tel = Telemetry(db_path=tmp_path / "t.db")
    monkeypatch.setattr("sevim.telemetry.get_telemetry", lambda: tel)
    return tel


def _seed_minimal(tel):
    # One real category so recognition has something, but NOT the topics
    # we'll feed as gaps (knapsack / pumping lemma).
    import json
    import numpy as np
    vecs = [_toy(p) for p in ["multiply matrix A and B", "matrix product"]]
    cen = np.asarray(vecs).mean(axis=0).tolist()
    tel.upsert_template("matrix_multiplication", "linear_algebra", "renderer",
                        renderer_name="matrix_multiplication",
                        embedding_json=json.dumps(cen))
    tel.upsert_category("linear_algebra", "Linear algebra",
                        centroid_json=json.dumps(cen))


def _index_prompt(tel, cid, prompt):
    import json
    tel.index_canvas(cid, prompt, json.dumps(_toy(prompt)), "toy",
                     accepted=True)
    # also store a canvas so promote/exemplar has a target
    tel.upsert_session(session_id="s", user_agent="x", ip_hash=None)
    tid = tel.record_turn(session_id="s", user_prompt=prompt, canvas_id=cid,
                          prior_canvas_ids=None, n_phrases=1, retries_used=0,
                          review_history=None, duration_s=1.0,
                          cost_usd_estimate=0.0, intent="express",
                          model_id="gpt-4o")
    tel.record_canvas(canvas_id=cid, session_id="s", turn_id=tid, title="T",
                      svg=f"<svg id='{cid}'></svg>", narration=[{"speak": "x"}],
                      model_id="gpt-4o")


def test_gaps_cluster_propose_promote(tmp_path, monkeypatch):
    tel = _tel(tmp_path, monkeypatch)
    _seed_minimal(tel)
    # Feed a cluster of NP-completeness prompts (no template for them yet).
    for i, p in enumerate([
            "prove vertex cover is np complete",
            "prove vertex cover np complete reduction",
            "show vertex cover np complete",
            "prove 3-sat reduces to vertex cover np complete"]):
        _index_prompt(tel, f"vc{i}", p)
    # And a smaller, separate cluster that shouldn't pass min_size.
    _index_prompt(tel, "pl0", "pumping lemma regular language")

    t = tax.Taxonomy(); t.load()
    gaps = curation.find_gaps(tel, t, tau_cat=0.74)
    assert len(gaps) >= 4, gaps
    clusters = curation.cluster_gaps(gaps, tau=0.8)
    big = [c for c in clusters if c["size"] >= 3]
    assert big, f"expected a dense NP cluster: {[c['size'] for c in clusters]}"

    created = curation.propose(tel, t, clusters, min_size=3)
    assert created, "a candidate should be proposed for the dense cluster"
    cands = tel.iter_candidates(status="proposed")
    assert len(cands) == len(created)

    # Promote the first candidate → it becomes a live exemplar template.
    res = curation.promote(tel, created[0])
    assert res["ok"], res
    live = tel.iter_templates(status="live")
    assert any(t_[0] == res["template_id"] for t_ in live)
    # Candidate is now approved, not proposed.
    assert tel.get_candidate(created[0])[-1] == "approved"


def test_promote_blocked_by_failing_gate(tmp_path, monkeypatch):
    tel = _tel(tmp_path, monkeypatch)
    _seed_minimal(tel)
    # Same in-vocab tokens (knapsack/dynamic/programming) so they cluster.
    for i, p in enumerate(["knapsack problem dynamic programming",
                           "solve knapsack with dynamic programming",
                           "knapsack dynamic programming approach"]):
        _index_prompt(tel, f"kn{i}", p)
    t = tax.Taxonomy(); t.load()
    created = curation.propose(tel, t,
                               curation.cluster_gaps(curation.find_gaps(tel, t)),
                               min_size=3)
    assert created
    res = curation.promote(tel, created[0], gate_fn=lambda prompt: False)
    assert not res["ok"] and res["reason"] == "quality_gate_failed"
    # Still proposed (not promoted).
    assert tel.get_candidate(created[0])[-1] == "proposed"


def test_dedup_flags_cross_category_duplicates(tmp_path, monkeypatch):
    import json
    tel = _tel(tmp_path, monkeypatch)
    v = json.dumps(_toy("vertex cover np complete"))
    tel.upsert_category("cat_a", "A", centroid_json=v)
    tel.upsert_category("cat_b", "B", centroid_json=v)
    tel.upsert_template("t_a", "cat_a", "exemplar", embedding_json=v)
    tel.upsert_template("t_b", "cat_b", "exemplar", embedding_json=v)
    flags = curation.dedup_templates(tel, tau=0.9)
    assert any({f["template_a"], f["template_b"]} == {"t_a", "t_b"}
               for f in flags), flags
