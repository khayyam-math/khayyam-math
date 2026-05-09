"""M2 — Cross-system byte-determinism rate.

For each clause in the M2 sample (one per concept-diagram relation, 12
total) we run THREE invocations of two systems:

  (a) SeVim, encoder-off  — local, deterministic by construction.
  (b) Raw-LLM SVG (Claude Haiku 4.5) prompted to emit a deterministic SVG
      from the natural-language clause.

For each system and each clause we compute the SHA-256 of every output
SVG and report:

  * `byte_determinism_rate` — fraction of clauses for which all three
    runs produced byte-identical SVG.
  * `unique_outputs_per_clause` — distribution of distinct SVGs per clause.
  * Per-call wall-clock for M3.

Output:  bench/eval/results/m2_determinism.json
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bench.eval.llm_call import Ledger, call            # noqa: E402
from sevim.pipeline import run_pipeline                  # noqa: E402

GOLD = json.loads((Path(__file__).parent / "gold_clauses.json").read_text())
RESULTS = Path(__file__).parent / "results" / "m2_determinism.json"

# One canonical clause per relation — keeps API spend bounded while
# spanning the full ontology.  Picked from causes_01, sequence_01, ... .
SAMPLE_IDS = [f"{rel}_01" for rel in [
    "causes", "sequence", "part_of", "contains", "attribute_of",
    "similar_to", "opposes", "instance_of", "used_for", "requires",
    "reduces_to", "measures",
]]
N_RUNS = 3
RAW_LLM_MODEL = "claude-haiku-4-5"

_SYSTEM_PROMPT = (
    "You generate Scalable Vector Graphics. Always reply with one and "
    "only one self-contained <svg>...</svg> document — no prose, no "
    "markdown fences, no explanatory text. Aim for compact, valid SVG. "
    "Be deterministic: identical requests must yield identical output."
)
_USER_TEMPLATE = (
    "Render the following sentence as a small SVG diagram (200x100 "
    "viewBox is fine). The diagram must depict the two referenced "
    "concepts as labelled boxes connected by a line or arrow that "
    "encodes the verb's relation. Sentence: {clause}"
)


def _sha(svg: str) -> str:
    return hashlib.sha256(svg.encode()).hexdigest()


def _strip_fences(s: str) -> str:
    """Remove markdown code fences if the model added them anyway."""
    s = s.strip()
    if s.startswith("```"):
        # Drop the opening fence line and any trailing fence.
        first_nl = s.find("\n")
        s = s[first_nl + 1:] if first_nl > 0 else s
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


def run_sevim(clause: str) -> tuple[str, float]:
    t0 = time.perf_counter()
    res = run_pipeline(clause)
    return res.svg, (time.perf_counter() - t0) * 1000.0


def run_raw_llm(clause: str, ledger: Ledger) -> tuple[str, float, dict]:
    env = call(
        model=RAW_LLM_MODEL,
        system=_SYSTEM_PROMPT,
        user=_USER_TEMPLATE.format(clause=clause),
        label=f"m2_raw_llm:{clause[:40]}",
        ledger=ledger,
    )
    svg = _strip_fences(env["result"])
    return svg, env.get("_wall_clock_s", 0.0) * 1000.0, env.get("usage", {})


def main() -> int:
    ledger = Ledger.load()
    cases = {c["id"]: c for c in GOLD["cases"]}
    out = {
        "metric": "M2 cross-system byte-determinism",
        "n_clauses": len(SAMPLE_IDS),
        "n_runs_per_clause": N_RUNS,
        "raw_llm_model": RAW_LLM_MODEL,
        "system_prompt": _SYSTEM_PROMPT,
        "user_template": _USER_TEMPLATE,
        "per_clause": [],
    }

    for sid in SAMPLE_IDS:
        c = cases[sid]
        clause = c["clause"]
        sevim_hashes, sevim_lats = [], []
        raw_hashes, raw_lats, raw_svgs = [], [], []
        for r in range(N_RUNS):
            sv, lat = run_sevim(clause)
            sevim_hashes.append(_sha(sv))
            sevim_lats.append(round(lat, 2))
        for r in range(N_RUNS):
            sv, lat, _u = run_raw_llm(clause, ledger)
            raw_hashes.append(_sha(sv))
            raw_lats.append(round(lat, 2))
            raw_svgs.append(sv)
            ledger.save()                      # checkpoint after every API call
        sevim_uniq = len(set(sevim_hashes))
        raw_uniq = len(set(raw_hashes))
        out["per_clause"].append({
            "id": sid,
            "clause": clause,
            "sevim_hashes": sevim_hashes,
            "sevim_unique": sevim_uniq,
            "sevim_lat_ms": sevim_lats,
            "raw_llm_hashes": raw_hashes,
            "raw_llm_unique": raw_uniq,
            "raw_llm_lat_ms": raw_lats,
            "raw_llm_svg_lengths": [len(s) for s in raw_svgs],
        })
        print(f"  {sid:18s}  sevim uniq={sevim_uniq}/{N_RUNS}  "
              f"raw uniq={raw_uniq}/{N_RUNS}  "
              f"spent=${ledger.spent_usd:.4f}")

    sevim_det = sum(1 for r in out["per_clause"] if r["sevim_unique"] == 1)
    raw_det = sum(1 for r in out["per_clause"] if r["raw_llm_unique"] == 1)
    out["sevim_byte_determinism_rate"] = sevim_det / len(SAMPLE_IDS)
    out["raw_llm_byte_determinism_rate"] = raw_det / len(SAMPLE_IDS)
    out["sevim_total_runs"] = N_RUNS * len(SAMPLE_IDS)
    out["raw_llm_total_runs"] = N_RUNS * len(SAMPLE_IDS)
    out["api_cost_usd"] = round(ledger.spent_usd, 6)

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(out, indent=2))
    print(f"\nM2 done. sevim det={out['sevim_byte_determinism_rate']:.0%} "
          f"raw det={out['raw_llm_byte_determinism_rate']:.0%}  "
          f"spent=${ledger.spent_usd:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
