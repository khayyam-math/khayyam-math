"""Phase 2 taxonomy + recognition tests (stubbed embedder, no network)."""
from __future__ import annotations

import pytest

from sevim import embeddings as emb
from sevim.telemetry import Telemetry
from studio import taxonomy as tax
from studio import taxonomy_seed


# Toy embedder: bag-of-words over a vocab that separates the seed domains.
_VOCAB = ["matrix", "multiply", "transpose", "determinant", "inverse",
          "system", "triangle", "pythagorean", "venn", "fraction",
          "derivative", "integrate", "newton", "sphere", "plot", "graph",
          "dfa", "sort", "search", "table", "vertex", "cover", "np",
          "complete", "sat", "partition", "reduction", "unit", "circle"]


def _toy_embed(text, *, model=None, timeout_s=15.0):
    t = (text or "").lower()
    return [float(t.count(w)) for w in _VOCAB]


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    monkeypatch.setattr(emb, "embed", _toy_embed)
    monkeypatch.setattr(emb, "available", lambda: True)
    monkeypatch.setattr(tax._emb, "embed", _toy_embed)
    monkeypatch.setattr(tax._emb, "available", lambda: True)
    monkeypatch.setenv("SEVIM_TAXONOMY", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")


def _seeded_tel(tmp_path, monkeypatch):
    tel = Telemetry(db_path=tmp_path / "t.db")
    monkeypatch.setattr("sevim.telemetry.get_telemetry", lambda: tel)
    summary = taxonomy_seed.seed(tel, _toy_embed)
    assert summary["categories"] >= 8
    assert summary["templates"] >= 10
    return tel


def test_seed_populates_tables(tmp_path, monkeypatch):
    tel = _seeded_tel(tmp_path, monkeypatch)
    assert len(tel.iter_categories()) >= 8
    assert len(tel.iter_templates(status="live")) >= 10
    assert len(tel.iter_template_examples()) >= 20


def test_recognises_matrix_category(tmp_path, monkeypatch):
    _seeded_tel(tmp_path, monkeypatch)
    t = tax.Taxonomy()
    rec = t.recognize(_toy_embed("multiply matrix A and B"))
    assert rec is not None
    assert rec.category_id == "linear_algebra"
    assert rec.template_id == "matrix_multiplication"
    assert rec.template_cos > 0.5


def test_recognises_np_completeness_as_exemplar(tmp_path, monkeypatch):
    _seeded_tel(tmp_path, monkeypatch)
    t = tax.Taxonomy()
    rec = t.recognize(_toy_embed("prove that vertex cover is np complete"))
    assert rec is not None
    assert rec.category_id == "complexity_proofs"
    assert rec.kind == "exemplar"
    assert rec.template_id == "np_complete_reduction"


def test_serve_exemplar_returns_stored_figure(tmp_path, monkeypatch):
    tel = _seeded_tel(tmp_path, monkeypatch)
    # Attach a canonical canvas to the exemplar template.
    tel.upsert_session(session_id="s", user_agent="x", ip_hash=None)
    tid = tel.record_turn(session_id="s",
                          user_prompt="prove vertex cover is np complete",
                          canvas_id="exemplar1", prior_canvas_ids=None,
                          n_phrases=1, retries_used=0, review_history=None,
                          duration_s=1.0, cost_usd_estimate=0.0,
                          intent="express", model_id="gpt-4o")
    tel.record_canvas(canvas_id="exemplar1", session_id="s", turn_id=tid,
                      title="VC", svg="<svg id='goldvc'></svg>",
                      narration=[{"speak": "x"}], model_id="gpt-4o")
    tel.upsert_template("np_complete_reduction", "complexity_proofs",
                        "exemplar", exemplar_canvas_id="exemplar1",
                        embedding_json=None)
    # Re-run seed embedding for the template so it has a vector again
    # (upsert above cleared it); simplest: reload taxonomy after re-seed.
    import json
    import numpy as np
    vecs = [_toy_embed(p) for p in
            ["prove vertex cover is NP-complete", "prove 3-SAT is NP-complete",
             "show the partition problem is NP-complete"]]
    centroid = np.asarray(vecs).mean(axis=0).tolist()
    tel.upsert_template("np_complete_reduction", "complexity_proofs",
                        "exemplar", exemplar_canvas_id="exemplar1",
                        embedding_json=json.dumps(centroid))

    t = tax.Taxonomy()
    fig = t.serve("prove that 3-SAT is np complete")
    assert fig is not None
    assert fig.get("svg") == "<svg id='goldvc'></svg>"
    assert fig.get("template_hit") == "np_complete_reduction"


def test_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SEVIM_TAXONOMY", "0")
    _seeded_tel(tmp_path, monkeypatch)
    t = tax.Taxonomy()
    assert t.serve("multiply matrix A and B") is None
