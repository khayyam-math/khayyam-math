"""Answer cache (Phase 1 of the template-taxonomy plan).

When a prompt is near-identical to one we have already answered well, we
retrieve the prior accepted figure instead of regenerating it from
scratch.  This gives **consistency** (the same question gets the same
answer) and **speed/cost** (a hit skips the gpt-4o generation + vision
review entirely), which matters most exactly when usage grows and many
users ask the same textbook questions.

The index is a thin in-memory layer over the ``canvas_index`` telemetry
table (canvas_id, prompt, embedding).  On a hit we fetch the stored
figure (svg/narration/title) from the ``canvases`` table by id.

Disabled by default: gated behind ``SEVIM_ANSWER_CACHE=1`` so it can be
switched on only once the similarity threshold is tuned against the real
corpus.  When embeddings are unavailable (no API key) it self-disables.

Design choices:
  * Mis-retrieval (serving the wrong cached answer) is worse than drawing
    fresh, so the threshold is high and lookups fail closed: any doubt →
    return None → normal generation.
  * Pure-cosine over a numpy matrix; the catalog is small (thousands of
    rows), so a full scan is well under a millisecond and we avoid the
    operational cost of pgvector.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Optional

from sevim import embeddings as _emb


def enabled() -> bool:
    return (os.environ.get("SEVIM_ANSWER_CACHE", "0") == "1"
            and _emb.available())


def _tau() -> float:
    try:
        return float(os.environ.get("SEVIM_ANSWER_CACHE_TAU", "0.93"))
    except ValueError:
        return 0.93


class AnswerCache:
    """In-memory nearest-neighbour index over accepted canvases."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ids: list[str] = []
        self._prompts: list[str] = []
        self._matrix = None          # numpy array (N, D), L2-normalised rows
        self._loaded = False

    # -- index construction ------------------------------------------------
    def _vectors_from(self, rows: list[tuple]):
        import numpy as np
        ids: list[str] = []
        prompts: list[str] = []
        vecs: list[list[float]] = []
        for r in rows:
            cid, prompt, emb_json = r[0], r[1], r[2]
            try:
                v = json.loads(emb_json)
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(v, list) or not v:
                continue
            ids.append(cid)
            prompts.append(prompt)
            vecs.append(v)
        if not vecs:
            return ids, prompts, None
        m = np.asarray(vecs, dtype="float32")
        norms = np.linalg.norm(m, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return ids, prompts, m / norms

    def load(self) -> None:
        """(Re)build the index from the telemetry canvas_index table."""
        from sevim.telemetry import get_telemetry
        tel = get_telemetry()
        rows = tel.iter_canvas_index(accepted_only=True) if tel else []
        ids, prompts, matrix = self._vectors_from(rows)
        with self._lock:
            self._ids, self._prompts, self._matrix = ids, prompts, matrix
            self._loaded = True

    def ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # -- query -------------------------------------------------------------
    def nearest(self, query_vec: list[float]) -> Optional[tuple[str, str, float]]:
        """Return (canvas_id, prompt, cosine) of the closest indexed
        canvas, or None if the index is empty."""
        self.ensure_loaded()
        import numpy as np
        with self._lock:
            matrix = self._matrix
            ids = list(self._ids)
            prompts = list(self._prompts)
        if matrix is None or not ids:
            return None
        q = np.asarray(query_vec, dtype="float32")
        n = float(np.linalg.norm(q))
        if n == 0:
            return None
        q = q / n
        sims = matrix @ q                      # cosine, rows are normalised
        i = int(np.argmax(sims))
        return ids[i], prompts[i], float(sims[i])

    def lookup_figure(self, prompt: str) -> Optional[dict[str, Any]]:
        """Embed ``prompt``, find the nearest accepted canvas, and if it
        clears the similarity threshold return its stored figure.  Returns
        None on any miss / error (fail closed)."""
        if not enabled():
            return None
        qv = _emb.embed(prompt)
        if qv is None:
            return None
        hit = self.nearest(qv)
        if hit is None:
            return None
        cid, matched_prompt, cos = hit
        if cos < _tau():
            return None
        fig = self._fetch_figure(cid)
        if fig is None:
            return None
        fig.update({"cache_hit": True, "cache_cosine": round(cos, 4),
                    "cache_canvas_id": cid,
                    "cache_matched_prompt": matched_prompt})
        return fig

    @staticmethod
    def _fetch_figure(canvas_id: str) -> Optional[dict[str, Any]]:
        from sevim.telemetry import get_telemetry
        tel = get_telemetry()
        if tel is None:
            return None
        try:
            rows = tel.query(
                "SELECT svg, narration_json, title FROM canvases "
                "WHERE canvas_id = ?", (canvas_id,))
        except Exception:  # noqa: BLE001
            return None
        if not rows:
            return None
        svg, narration_json, title = rows[0]
        if not svg:
            return None
        try:
            narration = json.loads(narration_json) if narration_json else []
        except Exception:  # noqa: BLE001
            narration = []
        return {"svg": svg, "narration": narration, "title": title or ""}

    # -- index maintenance -------------------------------------------------
    def add(self, canvas_id: str, prompt: str, *, accepted: bool,
            category_id: str | None = None) -> None:
        """Embed ``prompt`` (cached) and add/update its index row.  Called
        best-effort after a turn completes; never raises."""
        if not _emb.available():
            return
        qv = _emb.embed(prompt)
        if qv is None:
            return
        from sevim.telemetry import get_telemetry
        tel = get_telemetry()
        if tel is None:
            return
        tel.index_canvas(canvas_id, prompt, json.dumps(qv),
                         os.environ.get("SEVIM_EMBED_MODEL",
                                        "text-embedding-3-small"),
                         accepted=accepted, category_id=category_id)
        if accepted:
            # Hot-add to the live matrix so the very next identical prompt
            # hits without a reload.
            self._hot_add(canvas_id, prompt, qv)

    def _hot_add(self, canvas_id: str, prompt: str, vec: list[float]) -> None:
        import numpy as np
        with self._lock:
            if not self._loaded:
                return
            v = np.asarray(vec, dtype="float32")
            n = float(np.linalg.norm(v))
            if n == 0:
                return
            v = (v / n).reshape(1, -1)
            self._ids.append(canvas_id)
            self._prompts.append(prompt)
            self._matrix = (v if self._matrix is None
                            else np.vstack([self._matrix, v]))


# Module-level singleton.
_CACHE = AnswerCache()


def get_cache() -> AnswerCache:
    return _CACHE
