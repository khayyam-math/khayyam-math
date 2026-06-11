"""Phase-3 curation: turn logged gaps into taxonomy growth.

Offline pipeline (run by an admin job, never at request time):
  1. find_gaps      — indexed prompts whose category recognition is weak.
  2. cluster_gaps   — group them by embedding similarity.
  3. propose        — each dense cluster becomes a candidate (new template
                      under an existing category, or a whole new category).
  4. dedup_templates— flag near-duplicate templates across categories.
  5. suggest_migrations — flag templates closer to another category.
  6. promote        — admin-approved candidate goes live (optionally only
                      after passing the quality gate on its golden prompt).

Everything is deterministic and testable; the optional LLM naming and the
quality-gate promotion check are injected so tests run without either.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional


def _vec(json_str: str) -> Optional[list[float]]:
    try:
        v = json.loads(json_str)
        return v if isinstance(v, list) and v else None
    except Exception:  # noqa: BLE001
        return None


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return (s or "topic")[:40]


def find_gaps(tel, taxonomy, tau_cat: float = 0.74) -> list[dict[str, Any]]:
    """Indexed prompts whose best category match is below ``tau_cat``."""
    taxonomy.ensure_loaded()
    gaps: list[dict[str, Any]] = []
    for canvas_id, prompt, emb_json in (
            (r[0], r[1], r[2]) for r in tel.iter_canvas_index(accepted_only=True)):
        v = _vec(emb_json)
        if v is None:
            continue
        rec = taxonomy.recognize(v)
        cos = rec.category_cos if rec else 0.0
        if cos < tau_cat:
            gaps.append({"canvas_id": canvas_id, "prompt": prompt,
                         "vec": v, "best_cat": (rec.category_id if rec else None),
                         "best_cos": cos})
    return gaps


def cluster_gaps(gaps: list[dict[str, Any]],
                 tau: float = 0.85) -> list[dict[str, Any]]:
    """Greedy single-pass clustering by cosine to a running centroid."""
    import numpy as np
    clusters: list[dict[str, Any]] = []
    for g in gaps:
        v = np.asarray(g["vec"], dtype="float64")
        nv = np.linalg.norm(v) or 1.0
        vn = v / nv
        best_i, best_c = -1, tau
        for i, cl in enumerate(clusters):
            c = cl["_centroid"]
            sim = float(vn @ (c / (np.linalg.norm(c) or 1.0)))
            if sim >= best_c:
                best_c, best_i = sim, i
        if best_i >= 0:
            cl = clusters[best_i]
            cl["members"].append(g)
            cl["_sum"] = cl["_sum"] + v
            cl["_centroid"] = cl["_sum"] / len(cl["members"])
        else:
            clusters.append({"members": [g], "_sum": v.copy(),
                             "_centroid": v.copy()})
    # Finalise: pick the most central member as the representative.
    out = []
    for cl in clusters:
        c = cl["_centroid"]
        cn = c / (np.linalg.norm(c) or 1.0)
        best, best_sim = None, -2.0
        for m in cl["members"]:
            mv = np.asarray(m["vec"], dtype="float64")
            sim = float((mv / (np.linalg.norm(mv) or 1.0)) @ cn)
            if sim > best_sim:
                best_sim, best = sim, m
        out.append({"members": cl["members"], "size": len(cl["members"]),
                    "centroid": c.tolist(), "representative": best})
    out.sort(key=lambda x: x["size"], reverse=True)
    return out


def propose(tel, taxonomy, clusters: list[dict[str, Any]], *,
            min_size: int = 3, fits_tau: float = 0.70,
            name_fn: Optional[Callable[[list[str]], dict]] = None) -> list[str]:
    """Write a taxonomy_candidate for each cluster >= ``min_size``.

    ``name_fn`` (optional LLM) maps the cluster's prompts to
    {category_title, template_id, fits_category}.  Without it a heuristic
    is used: attach to the nearest existing category if confident, else a
    new category named from the representative prompt.  Returns the list of
    created candidate_ids.
    """
    import numpy as np
    created: list[str] = []
    for k, cl in enumerate(clusters):
        if cl["size"] < min_size:
            continue
        rep = cl["representative"]
        rep_prompt = rep["prompt"]
        centroid = cl["centroid"]
        # Does this cluster fit an existing category?
        rec = taxonomy.recognize(centroid)
        fits = rec.category_id if (rec and rec.category_cos >= fits_tau) else None

        title = template_id = None
        if name_fn is not None:
            try:
                info = name_fn([m["prompt"] for m in cl["members"][:8]]) or {}
                title = info.get("category_title")
                template_id = info.get("template_id")
                if info.get("fits_category"):
                    fits = info["fits_category"]
            except Exception:  # noqa: BLE001
                pass
        template_id = template_id or _slug(rep_prompt)
        cid = f"cand_{k}_{template_id}"
        if fits:
            kind = "new_template"
            category_id = fits
        else:
            kind = "new_category"
            category_id = _slug(title or rep_prompt)
            title = title or rep_prompt[:60]
        tel.upsert_candidate(
            cid, kind, category_id=category_id, template_id=template_id,
            title=title, golden_prompt=rep_prompt, member_count=cl["size"],
            centroid_json=json.dumps(centroid),
            exemplar_canvas_id=rep["canvas_id"],
            note=f"{cl['size']} similar prompts, no confident template")
        created.append(cid)
    return created


def dedup_templates(tel, tau: float = 0.90) -> list[dict[str, Any]]:
    """Pairs of live templates in DIFFERENT categories whose embeddings are
    near-duplicates — enforces "no similar templates in different
    categories"."""
    import numpy as np
    rows = tel.iter_templates(status="live")
    items = []
    for (tid, cid, _kind, _r, _ex, emb_json, _g, _s) in rows:
        v = _vec(emb_json) if emb_json else None
        if v is None:
            continue
        arr = np.asarray(v, dtype="float64")
        items.append((tid, cid, arr / (np.linalg.norm(arr) or 1.0)))
    flags = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            ti, ci, vi = items[i]
            tj, cj, vj = items[j]
            if ci == cj:
                continue
            sim = float(vi @ vj)
            if sim >= tau:
                flags.append({"template_a": ti, "category_a": ci,
                              "template_b": tj, "category_b": cj,
                              "cosine": round(sim, 4)})
    flags.sort(key=lambda x: x["cosine"], reverse=True)
    return flags


def suggest_migrations(tel, taxonomy) -> list[dict[str, Any]]:
    """Templates whose embedding is closer to a DIFFERENT category's
    centroid than to their own."""
    import numpy as np
    cats = {}
    for cid, _p, _t, centroid in tel.iter_categories():
        v = _vec(centroid) if centroid else None
        if v is not None:
            arr = np.asarray(v, dtype="float64")
            cats[cid] = arr / (np.linalg.norm(arr) or 1.0)
    out = []
    for (tid, cid, _k, _r, _ex, emb_json, _g, _s) in tel.iter_templates("live"):
        v = _vec(emb_json) if emb_json else None
        if v is None or cid not in cats:
            continue
        arr = np.asarray(v, dtype="float64")
        vn = arr / (np.linalg.norm(arr) or 1.0)
        own = float(vn @ cats[cid])
        best_cid, best = cid, own
        for ocid, ocent in cats.items():
            sim = float(vn @ ocent)
            if sim > best:
                best, best_cid = sim, ocid
        if best_cid != cid and best - own > 0.02:
            out.append({"template_id": tid, "from": cid, "to": best_cid,
                        "own_cos": round(own, 4), "to_cos": round(best, 4)})
    return out


def promote(tel, candidate_id: str, *,
            gate_fn: Optional[Callable[[str], bool]] = None) -> dict[str, Any]:
    """Approve a candidate into the live taxonomy.  If ``gate_fn`` is given
    it must return True for the golden prompt before promotion (the quality
    gate)."""
    row = tel.get_candidate(candidate_id)
    if not row:
        return {"ok": False, "reason": "not_found"}
    (_cid, kind, category_id, template_id, title, golden_prompt,
     _mc, centroid, exemplar_canvas_id, _note, status) = row
    if status != "proposed":
        return {"ok": False, "reason": f"status={status}"}
    if gate_fn is not None:
        try:
            if not gate_fn(golden_prompt or ""):
                return {"ok": False, "reason": "quality_gate_failed"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": f"gate_error:{exc}"}
    if kind == "new_category":
        tel.upsert_category(category_id, title or category_id,
                            centroid_json=centroid)
    # An exemplar template pointing at the cluster's best canvas.
    tel.upsert_template(template_id, category_id, "exemplar",
                        exemplar_canvas_id=exemplar_canvas_id,
                        embedding_json=centroid, golden_prompt=golden_prompt,
                        status="live")
    tel.set_candidate_status(candidate_id, "approved")
    return {"ok": True, "category_id": category_id,
            "template_id": template_id, "kind": kind}
