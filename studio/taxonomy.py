"""Category → template taxonomy + embedding recognition (Phase 2).

Implements the user's "recognize the category, then find the template"
flow over the ``categories`` / ``templates`` / ``template_examples``
tables.  Recognition is two-level: nearest category centroid, then the
nearest template within that category.

Two template kinds:
  * ``renderer``  — a parameterized deterministic renderer (the existing
    route/template functions). Recognition is advisory here; the existing
    express cascade already routes renderers well, so Phase 2 only LOGS
    these and leaves routing to the cascade.
  * ``exemplar``  — a curated known-good figure stored as a canvas. On a
    confident match we retrieve and serve it (extending the answer cache
    from "this exact prior answer" to "the category's canonical answer").

Gated behind ``SEVIM_TAXONOMY=1``; high-precision, fails closed.
"""
from __future__ import annotations

import dataclasses
import json
import os
import threading
from typing import Any, Optional

from sevim import embeddings as _emb


def enabled() -> bool:
    return os.environ.get("SEVIM_TAXONOMY", "0") == "1" and _emb.available()


def _tau_cat() -> float:
    try:
        return float(os.environ.get("SEVIM_TAXONOMY_TAU_CAT", "0.74"))
    except ValueError:
        return 0.74


def _tau_tpl() -> float:
    try:
        return float(os.environ.get("SEVIM_TAXONOMY_TAU_TPL", "0.82"))
    except ValueError:
        return 0.82


@dataclasses.dataclass
class Recognition:
    category_id: Optional[str]
    category_cos: float
    template_id: Optional[str]
    template_cos: float
    kind: Optional[str]
    renderer_name: Optional[str]
    exemplar_canvas_id: Optional[str]


class Taxonomy:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loaded = False
        self._cat_ids: list[str] = []
        self._cat_matrix = None
        self._tpl_meta: list[dict[str, Any]] = []
        self._tpl_matrix = None

    # -- load --------------------------------------------------------------
    def load(self) -> None:
        import numpy as np
        from sevim.telemetry import get_telemetry
        tel = get_telemetry()
        cats = tel.iter_categories() if tel else []
        tpls = tel.iter_templates(status="live") if tel else []

        cat_ids, cat_vecs = [], []
        for category_id, _parent, _title, centroid in cats:
            if not centroid:
                continue
            try:
                v = json.loads(centroid)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(v, list) and v:
                cat_ids.append(category_id)
                cat_vecs.append(v)

        meta, tpl_vecs = [], []
        for (tid, cid, kind, rname, ex_cid, emb_json, _golden,
             _status) in tpls:
            if not emb_json:
                continue
            try:
                v = json.loads(emb_json)
            except Exception:  # noqa: BLE001
                continue
            if not (isinstance(v, list) and v):
                continue
            meta.append({"template_id": tid, "category_id": cid,
                         "kind": kind, "renderer_name": rname,
                         "exemplar_canvas_id": ex_cid})
            tpl_vecs.append(v)

        def _norm(vecs):
            if not vecs:
                return None
            m = np.asarray(vecs, dtype="float32")
            n = np.linalg.norm(m, axis=1, keepdims=True)
            n[n == 0] = 1.0
            return m / n

        with self._lock:
            self._cat_ids = cat_ids
            self._cat_matrix = _norm(cat_vecs)
            self._tpl_meta = meta
            self._tpl_matrix = _norm(tpl_vecs)
            self._loaded = True

    def ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # -- recognize ---------------------------------------------------------
    def recognize(self, prompt_vec: list[float]) -> Optional[Recognition]:
        self.ensure_loaded()
        import numpy as np
        with self._lock:
            cat_ids = list(self._cat_ids)
            cat_m = self._cat_matrix
            meta = list(self._tpl_meta)
            tpl_m = self._tpl_matrix
        if tpl_m is None or not meta:
            return None
        q = np.asarray(prompt_vec, dtype="float32")
        n = float(np.linalg.norm(q))
        if n == 0:
            return None
        q = q / n

        cat_id, cat_cos = None, 0.0
        if cat_m is not None and cat_ids:
            cs = cat_m @ q
            ci = int(np.argmax(cs))
            cat_id, cat_cos = cat_ids[ci], float(cs[ci])

        # Template search: prefer templates inside the recognized category;
        # fall back to a global search when the category has no templates.
        idxs = [i for i, mt in enumerate(meta)
                if cat_id is not None and mt["category_id"] == cat_id]
        if not idxs:
            idxs = list(range(len(meta)))
        sub = tpl_m[idxs]
        ts = sub @ q
        j = int(np.argmax(ts))
        gi = idxs[j]
        mt = meta[gi]
        return Recognition(
            category_id=cat_id, category_cos=cat_cos,
            template_id=mt["template_id"], template_cos=float(ts[j]),
            kind=mt["kind"], renderer_name=mt["renderer_name"],
            exemplar_canvas_id=mt["exemplar_canvas_id"],
        )

    # -- serve (exemplar path) --------------------------------------------
    def serve(self, prompt: str) -> Optional[dict[str, Any]]:
        """If the prompt confidently matches an EXEMPLAR template, return
        that template's stored figure.  Renderer matches return a routing
        hint dict (handled by the caller) rather than a figure."""
        if not enabled():
            return None
        qv = _emb.embed(prompt)
        if qv is None:
            return None
        rec = self.recognize(qv)
        if rec is None:
            return None
        if rec.category_cos < _tau_cat() or rec.template_cos < _tau_tpl():
            return {"recognized_category": rec.category_id,
                    "category_cos": round(rec.category_cos, 4),
                    "below_threshold": True}
        if rec.kind == "exemplar" and rec.exemplar_canvas_id:
            from studio.answer_cache import AnswerCache
            fig = AnswerCache._fetch_figure(rec.exemplar_canvas_id)
            if fig is not None:
                fig.update({"template_hit": rec.template_id,
                            "recognized_category": rec.category_id,
                            "template_cos": round(rec.template_cos, 4)})
                return fig
        return {"recognized_category": rec.category_id,
                "recognized_template": rec.template_id,
                "kind": rec.kind, "renderer_name": rec.renderer_name,
                "category_cos": round(rec.category_cos, 4),
                "template_cos": round(rec.template_cos, 4)}


_TAX = Taxonomy()


def get_taxonomy() -> Taxonomy:
    return _TAX
