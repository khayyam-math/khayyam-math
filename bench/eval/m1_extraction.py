"""M1 — Per-relation extraction accuracy on the 60-clause gold set.

For each clause we run the full SeVim pipeline and check whether at least
one extracted edge has the correct relation type with subject/object
labels matching the ground truth (after the same lemma normalisation
SeVim itself applies).

Outputs:
    bench/eval/results/m1_extraction.json — raw per-case verdicts plus
        per-relation precision / recall / F1.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sevim.pipeline import run_pipeline  # noqa: E402

GOLD = json.loads((Path(__file__).parent / "gold_clauses.json").read_text())
RESULTS = Path(__file__).parent / "results" / "m1_extraction.json"
RELATIONS = sorted({c["relation"] for c in GOLD["cases"]})


def _norm(label: str) -> str:
    """Match SeVim's normalisation closely enough for label equality.

    SeVim canonicalises labels by lowercasing, stripping articles, and
    singularising. We mirror only the parts that matter for our gold set
    (lowercasing + article stripping + simple plural -> singular).
    """
    s = label.lower().strip()
    s = re.sub(r"^(the|a|an)\s+", "", s)
    s = re.sub(r"\s+(the|a|an)\s+", " ", s)
    if s.endswith("s") and not s.endswith("ss") and len(s) > 3:
        s = s[:-1]
    return s


def _label_for(graph, nid: str) -> str:
    for n in graph.nodes:
        if n.id == nid:
            return _norm(n.label)
    return ""


def evaluate_one(case: dict) -> dict:
    """Run sevim on one clause, compare against ground-truth triple."""
    t0 = time.perf_counter()
    res = run_pipeline(case["clause"])
    dt_ms = (time.perf_counter() - t0) * 1000.0

    want_rel = case["relation"]
    want_s = _norm(case["subject"])
    want_o = _norm(case["object"])

    rel_match = False
    full_match = False
    matched_edge = None
    for e in res.graph.edges:
        if e.relation != want_rel:
            continue
        rel_match = True
        s_lbl = _label_for(res.graph, e.from_id)
        o_lbl = _label_for(res.graph, e.to_id)
        s_ok = (want_s in s_lbl) or (s_lbl in want_s)
        o_ok = (want_o in o_lbl) or (o_lbl in want_o)
        if s_ok and o_ok:
            full_match = True
            matched_edge = {
                "from": s_lbl, "to": o_lbl, "relation": e.relation,
            }
            break

    extracted = [
        {"from": _label_for(res.graph, e.from_id),
         "to": _label_for(res.graph, e.to_id),
         "relation": e.relation}
        for e in res.graph.edges
    ]
    return {
        "id": case["id"],
        "clause": case["clause"],
        "want": {"subject": want_s, "object": want_o, "relation": want_rel},
        "extracted_edges": extracted,
        "rel_match": rel_match,
        "full_match": full_match,
        "matched_edge": matched_edge,
        "latency_ms": round(dt_ms, 3),
    }


def aggregate(per_case: list[dict]) -> dict:
    """Compute per-relation precision / recall / F1.

    Treating "did sevim emit an edge with the gold relation between
    matching endpoints" as a true positive is closest to a faithfulness
    metric: it rewards the system for getting the WHOLE triple right,
    not just the relation type.
    """
    out = {}
    for rel in RELATIONS:
        cases_rel = [c for c in per_case if c["want"]["relation"] == rel]
        # TP : full-match cases of this relation.
        tp = sum(1 for c in cases_rel if c["full_match"])
        # FN : gold cases of this relation we missed.
        fn = sum(1 for c in cases_rel if not c["full_match"])
        # FP : cases of OTHER gold relations where sevim emitted an edge of THIS
        #      relation type. (Approximation: counts only cases in our set;
        #      sevim never emits edges with no source clause, so this is
        #      well-defined under the gold-set scope.)
        fp = 0
        for c in per_case:
            if c["want"]["relation"] == rel:
                continue
            for e in c["extracted_edges"]:
                if e["relation"] == rel:
                    fp += 1
                    break
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        out[rel] = {
            "n_gold": len(cases_rel),
            "tp": tp, "fp": fp, "fn": fn,
            "precision": round(prec, 3),
            "recall": round(rec, 3),
            "f1": round(f1, 3),
        }
    macro_p = sum(out[r]["precision"] for r in RELATIONS) / len(RELATIONS)
    macro_r = sum(out[r]["recall"] for r in RELATIONS) / len(RELATIONS)
    macro_f = sum(out[r]["f1"] for r in RELATIONS) / len(RELATIONS)
    out["_macro"] = {
        "precision": round(macro_p, 3),
        "recall": round(macro_r, 3),
        "f1": round(macro_f, 3),
    }
    return out


def main() -> int:
    cases = GOLD["cases"]
    per_case = [evaluate_one(c) for c in cases]
    by_rel = aggregate(per_case)

    summary = {
        "metric": "M1 per-relation extraction accuracy",
        "n_cases": len(cases),
        "n_full_match": sum(1 for c in per_case if c["full_match"]),
        "n_rel_match_only": sum(1 for c in per_case
                                if c["rel_match"] and not c["full_match"]),
        "by_relation": by_rel,
        "per_case": per_case,
        "encoder_mode": "off"
            if os.environ.get("SEVIM_ENCODER", "off") == "off"
            else "on",
    }
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(summary, indent=2))
    print(f"M1: {summary['n_full_match']}/{len(cases)} full triples matched. "
          f"macro F1 = {by_rel['_macro']['f1']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
