"""Phase 1 answer-cache tests.

Exercise the embedding cache, cosine, the canvas_index telemetry table,
and the AnswerCache retrieval logic — all with a STUBBED embedder so no
network/API is touched. A deterministic toy embedder maps each prompt to
a unit vector so we control similarities exactly.
"""
from __future__ import annotations

import json
import os

import pytest

from sevim import embeddings as emb
from sevim.telemetry import Telemetry
from studio import answer_cache as ac


# --- toy embedder: bag-of-words over a tiny fixed vocab -------------------
_VOCAB = ["vertex", "cover", "np", "complete", "spectral", "theorem",
          "matrix", "prove", "explain", "derivative"]


def _toy_embed(text, *, model=None, timeout_s=15.0):
    t = (text or "").lower()
    return [float(t.count(w)) for w in _VOCAB]


@pytest.fixture(autouse=True)
def _patch_embed(monkeypatch):
    monkeypatch.setattr(emb, "embed", _toy_embed)
    monkeypatch.setattr(emb, "available", lambda: True)
    monkeypatch.setattr(ac._emb, "embed", _toy_embed)
    monkeypatch.setattr(ac._emb, "available", lambda: True)
    monkeypatch.setenv("SEVIM_ANSWER_CACHE", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")


def test_cosine_basics():
    assert emb.cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert emb.cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert emb.cosine([], [1]) == 0.0
    assert emb.cosine([0, 0], [1, 1]) == 0.0


def _mk_telemetry(tmp_path):
    return Telemetry(db_path=tmp_path / "t.db")


def _seed_canvas(tel, canvas_id, prompt, svg):
    tel.upsert_session(session_id="s", user_agent="x", ip_hash=None)
    tid = tel.record_turn(session_id="s", user_prompt=prompt,
                          canvas_id=canvas_id, prior_canvas_ids=None,
                          n_phrases=1, retries_used=0, review_history=None,
                          duration_s=1.0, cost_usd_estimate=0.0,
                          intent="express", model_id="gpt-4o")
    tel.record_canvas(canvas_id=canvas_id, session_id="s", turn_id=tid,
                      title="T", svg=svg, narration=[{"speak": "hi"}],
                      model_id="gpt-4o")
    return tid


def test_index_and_iter_roundtrip(tmp_path):
    tel = _mk_telemetry(tmp_path)
    tel.index_canvas("c1", "prove vertex cover np complete",
                     json.dumps([1.0, 1.0, 1.0, 1.0, 0, 0, 0, 1.0, 0, 0]),
                     "toy", accepted=True)
    rows = tel.iter_canvas_index(accepted_only=True)
    assert len(rows) == 1 and rows[0][0] == "c1"
    # not-accepted rows are excluded when accepted_only
    tel.index_canvas("c2", "explain spectral theorem",
                     json.dumps([0, 0, 0, 0, 1.0, 1.0, 0, 0, 1.0, 0]),
                     "toy", accepted=False)
    assert len(tel.iter_canvas_index(accepted_only=True)) == 1
    assert len(tel.iter_canvas_index(accepted_only=False)) == 2


def test_near_identical_prompt_hits(tmp_path, monkeypatch):
    tel = _mk_telemetry(tmp_path)
    monkeypatch.setattr("sevim.telemetry.get_telemetry", lambda: tel)
    _seed_canvas(tel, "c1", "prove vertex cover is np complete",
                 "<svg id='vc'></svg>")
    cache = ac.AnswerCache()
    cache.add("c1", "prove vertex cover is np complete", accepted=True)

    # An identical-meaning prompt clears the threshold and returns the SVG.
    hit = cache.lookup_figure("prove vertex cover np complete")
    assert hit is not None, "near-identical prompt should hit"
    assert "vc" in hit["svg"]
    assert hit["cache_cosine"] >= 0.9


def test_unrelated_prompt_misses(tmp_path, monkeypatch):
    tel = _mk_telemetry(tmp_path)
    monkeypatch.setattr("sevim.telemetry.get_telemetry", lambda: tel)
    _seed_canvas(tel, "c1", "prove vertex cover is np complete",
                 "<svg id='vc'></svg>")
    cache = ac.AnswerCache()
    cache.add("c1", "prove vertex cover is np complete", accepted=True)

    # A completely different topic must NOT retrieve the cached figure.
    assert cache.lookup_figure("explain the derivative") is None


def test_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SEVIM_ANSWER_CACHE", "0")
    tel = _mk_telemetry(tmp_path)
    monkeypatch.setattr("sevim.telemetry.get_telemetry", lambda: tel)
    _seed_canvas(tel, "c1", "prove vertex cover is np complete",
                 "<svg id='vc'></svg>")
    cache = ac.AnswerCache()
    cache.add("c1", "prove vertex cover is np complete", accepted=True)
    # Even an exact-match prompt returns None when the cache is off.
    assert cache.lookup_figure("prove vertex cover is np complete") is None


def test_served_figure_is_polished(tmp_path, monkeypatch):
    """On a hit, the stored SVG is re-run through the deterministic polish
    passes before being served (so an old cached figure gets recent fixes)."""
    tel = _mk_telemetry(tmp_path)
    monkeypatch.setattr("sevim.telemetry.get_telemetry", lambda: tel)
    _seed_canvas(tel, "c1", "prove vertex cover is np complete",
                 "<svg id='raw'></svg>")
    cache = ac.AnswerCache()
    cache.add("c1", "prove vertex cover is np complete", accepted=True)
    # Stub polish_svg so we can prove it was applied to the served SVG.
    import studio.express as _ex
    monkeypatch.setattr(_ex, "polish_svg",
                        lambda s: s.replace("raw", "polished"))
    hit = cache.lookup_figure("prove vertex cover np complete")
    assert hit is not None
    assert "polished" in hit["svg"], "served SVG should be polished"


def test_repeated_prompt_keeps_newest(tmp_path, monkeypatch):
    """Versioning: re-answering the same question keeps only the newest
    accepted figure as the indexed answer."""
    tel = _mk_telemetry(tmp_path)
    monkeypatch.setattr("sevim.telemetry.get_telemetry", lambda: tel)
    _seed_canvas(tel, "old", "explain the spectral theorem",
                 "<svg id='old'></svg>")
    _seed_canvas(tel, "new", "explain the spectral theorem",
                 "<svg id='new'></svg>")
    cache = ac.AnswerCache()
    cache.add("old", "explain the spectral theorem", accepted=True)
    cache.add("new", "explain the spectral theorem", accepted=True)
    rows = tel.iter_canvas_index(accepted_only=True)
    ids = {r[0] for r in rows}
    assert ids == {"new"}, f"only the newest should remain, got {ids}"
