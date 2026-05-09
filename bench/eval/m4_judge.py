"""M4 — LLM-as-judge diagram quality (sevim vs raw-LLM SVG).

Reads the raw-LLM SVGs cached by M2, generates the matching SeVim SVG
locally, randomises which is shown as `A` and which as `B`, sends
both to Claude Sonnet 4.6 with `rubric.txt` as the system prompt, and
records each axis score plus the unblinded mapping.

Also computes an OBJECTIVE label-coverage proxy that does not require
re-paying the judge:

  coverage(svg, clause)  =  | content_nouns(clause) ∩ text_in(svg) |
                            ----------------------------------------
                                  | content_nouns(clause) |

Reviewers can verify the trend (sevim wins on coverage and
structural-clarity, raw-LLM ties or wins on render-validity for
extreme-simplicity scenes) without API access.

Output:  bench/eval/results/m4_judge.json
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bench.eval.llm_call import Ledger, call            # noqa: E402
from sevim.pipeline import run_pipeline                  # noqa: E402

GOLD = json.loads((Path(__file__).parent / "gold_clauses.json").read_text())
RUBRIC = (Path(__file__).parent / "rubric.txt").read_text()
M2 = json.loads(
    (Path(__file__).parent / "results" / "m2_determinism.json").read_text())
RESULTS = Path(__file__).parent / "results" / "m4_judge.json"

JUDGE_MODEL = "claude-sonnet-4-6"
RAW_LLM_MODEL_FOR_M4 = "claude-haiku-4-5"  # documented for reproducibility
SAMPLE_IDS = [c["id"] for c in M2["per_clause"]]

# Stop-words for the lexical proxy: tiny English closed-class list.
_STOP = frozenset({
    "the", "a", "an", "of", "to", "for", "and", "or", "but",
    "is", "are", "was", "were", "be", "been", "being",
    "in", "on", "at", "by", "from", "with", "as", "that", "this",
    "it", "its", "into", "than", "then", "if", "so",
})


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[A-Za-z][A-Za-z\-]+", text.lower())
            if w not in _STOP and len(w) > 2}


def _svg_text(svg: str) -> set[str]:
    """Union of word tokens visible inside any <text>...</text> element."""
    out: set[str] = set()
    for body in re.findall(r"<text[^>]*>(.*?)</text>", svg, flags=re.S):
        out |= _content_words(re.sub(r"<[^>]+>", " ", body))
    return out


def label_coverage(svg: str, clause: str) -> float:
    """Fraction of clause content words also present in the SVG's text."""
    want = _content_words(clause)
    have = _svg_text(svg)
    if not want:
        return 0.0
    return round(len(want & have) / len(want), 3)


def _user_payload(clause: str, svg_a: str, svg_b: str) -> str:
    return (
        f"Source sentence: {clause}\n\n"
        f"--- SVG A ---\n{svg_a}\n\n"
        f"--- SVG B ---\n{svg_b}\n"
    )


def _safe_json(raw: str) -> dict:
    """Salvage the JSON object from a possibly-fenced response."""
    raw = raw.strip()
    if raw.startswith("```"):
        nl = raw.find("\n")
        raw = raw[nl + 1:] if nl > 0 else raw
        if raw.endswith("```"):
            raw = raw[:-3]
    s = raw.find("{")
    e = raw.rfind("}") + 1
    return json.loads(raw[s:e])


def main() -> int:
    rng = random.Random(42)
    ledger = Ledger.load()
    cases = {c["id"]: c for c in GOLD["cases"]}
    out = {
        "metric": "M4 LLM-as-judge diagram quality",
        "judge_model": JUDGE_MODEL,
        "judge_temperature": "default (claude -p does not expose temperature; "
                             "rubric is identical across runs)",
        "rubric_sha256": hashlib.sha256(RUBRIC.encode()).hexdigest(),
        "raw_llm_model": RAW_LLM_MODEL_FOR_M4,
        "n_clauses": len(SAMPLE_IDS),
        "per_clause": [],
    }
    # Index raw-LLM SVGs from M2 by clause id.
    # M2 only stored hashes + lengths to keep its own JSON small;
    # we re-render the raw-LLM SVG once per clause here for the judge.
    # That's one fresh API call per clause.
    raw_svg_for: dict[str, str] = {}
    for sid in SAMPLE_IDS:
        clause = cases[sid]["clause"]
        env = call(
            model=RAW_LLM_MODEL_FOR_M4,
            system=("You generate Scalable Vector Graphics. Always reply "
                    "with one and only one self-contained <svg>...</svg> "
                    "document — no prose, no markdown fences."),
            user=("Render the following sentence as a small SVG diagram "
                  "(200x100 viewBox is fine). The diagram must depict "
                  "the two referenced concepts as labelled boxes "
                  "connected by a line or arrow that encodes the verb's "
                  "relation. Sentence: " + clause),
            label=f"m4_raw_llm:{sid}",
            ledger=ledger,
        )
        raw = env["result"].strip()
        if raw.startswith("```"):
            nl = raw.find("\n"); raw = raw[nl + 1:] if nl > 0 else raw
            if raw.endswith("```"): raw = raw[:-3]
        raw_svg_for[sid] = raw.strip()
        ledger.save()

    for sid in SAMPLE_IDS:
        clause = cases[sid]["clause"]
        sevim_svg = run_pipeline(clause).svg
        raw_svg = raw_svg_for[sid]
        sys_a, sys_b = "sevim", "raw_llm"
        svg_a, svg_b = sevim_svg, raw_svg
        if rng.random() < 0.5:
            sys_a, sys_b = sys_b, sys_a
            svg_a, svg_b = svg_b, svg_a

        env = call(
            model=JUDGE_MODEL,
            system=RUBRIC,
            user=_user_payload(clause, svg_a, svg_b),
            label=f"m4_judge:{sid}",
            ledger=ledger,
            timeout_s=90,
        )
        ledger.save()
        try:
            verdict = _safe_json(env["result"])
        except Exception as exc:
            print(f"  WARN {sid}: judge JSON parse failed: {exc}")
            verdict = {"_raw": env["result"][:400], "_parse_error": str(exc)}

        cov_sevim = label_coverage(sevim_svg, clause)
        cov_raw = label_coverage(raw_svg, clause)

        out["per_clause"].append({
            "id": sid,
            "clause": clause,
            "blind_mapping": {"A": sys_a, "B": sys_b},
            "judge": verdict,
            "label_coverage": {"sevim": cov_sevim, "raw_llm": cov_raw},
            "lengths": {"sevim_svg": len(sevim_svg),
                        "raw_llm_svg": len(raw_svg)},
        })
        print(f"  {sid:18s}  cov sevim={cov_sevim:.2f} raw={cov_raw:.2f}  "
              f"spent=${ledger.spent_usd:.4f}")

    # Aggregate axis means.
    axes = ["relation_faithfulness", "concept_coverage",
            "structural_clarity", "render_validity"]
    sums = {sys: {ax: 0.0 for ax in axes} for sys in ("sevim", "raw_llm")}
    counts = {sys: 0 for sys in ("sevim", "raw_llm")}
    for r in out["per_clause"]:
        for letter, sys in r["blind_mapping"].items():
            v = r["judge"].get(letter)
            if not isinstance(v, dict):
                continue
            ok = True
            for ax in axes:
                x = v.get(ax)
                if not isinstance(x, (int, float)):
                    ok = False; break
            if not ok:
                continue
            for ax in axes:
                sums[sys][ax] += float(v[ax])
            counts[sys] += 1
    out["axis_means"] = {sys: {ax: round(sums[sys][ax] / counts[sys], 3)
                               if counts[sys] else None
                               for ax in axes}
                          for sys in ("sevim", "raw_llm")}
    out["axis_means"]["_n_judged_per_system"] = counts
    out["label_coverage_mean"] = {
        "sevim": round(
            sum(r["label_coverage"]["sevim"] for r in out["per_clause"])
            / len(out["per_clause"]), 3),
        "raw_llm": round(
            sum(r["label_coverage"]["raw_llm"] for r in out["per_clause"])
            / len(out["per_clause"]), 3),
    }
    out["api_cost_usd_total"] = round(ledger.spent_usd, 6)

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(out, indent=2))
    print(f"\nM4 done. axis means: {out['axis_means']}")
    print(f"Total spend (M2+M4): ${ledger.spent_usd:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
