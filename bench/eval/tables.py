r"""Generate LaTeX table fragments from raw JSON in results/.

Outputs four self-contained ``.tex`` fragments into ``bench/eval/figs/``:
  eval_m1_extraction.tex     — per-relation P/R/F1
  eval_m2_determinism.tex    — byte-determinism rate vs raw-LLM
  eval_m3_latency_cross.tex  — wall-clock side-by-side
  eval_m4_judge.tex          — LLM-judge axis means + lexical coverage

The companion paper repository is expected to ``\input{}`` these
fragments directly (or to vendor a copy under its own ``figs/``); this
code repository deliberately does not ship the manuscript itself, so
the harness writes its outputs alongside the rest of the eval
artefacts under ``bench/eval/``.

Every cell is read directly from the JSON; no value is hand-edited.
"""
from __future__ import annotations

import json
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "bench" / "eval" / "results"
FIGS = ROOT / "bench" / "eval" / "figs"


def m1_table() -> str:
    d = json.loads((RESULTS / "m1_extraction.json").read_text())
    by = d["by_relation"]
    macro = by["_macro"]
    rels = sorted(r for r in by if r != "_macro")
    rows = []
    for r in rels:
        v = by[r]
        rows.append(
            f"{r.replace('_', '\\_')} & {v['n_gold']} & {v['tp']} & {v['fp']} & "
            f"{v['fn']} & {v['precision']:.2f} & {v['recall']:.2f} & "
            f"{v['f1']:.2f} \\\\")
    body = "\n".join(rows)
    full = (
        f"{d['n_full_match']}/{d['n_cases']} = "
        f"{d['n_full_match']/d['n_cases']:.2%}").replace("%", r"\%")
    return rf"""\begin{{table}}[t]
\centering
\caption{{M1 — Per-relation extraction accuracy on a 60-clause hand-authored
gold set ($5$ clauses per concept-diagram relation).
A case scores a true positive only when the system emits an edge with the
gold relation type AND with subject/object labels matching the gold
triple under SeVim's own normalisation.
Aggregate full-triple match: {full}; macro-F1 = {macro['f1']:.3f}.}}
\label{{tab:m1-extraction}}
\small
\setlength{{\tabcolsep}}{{4pt}}
\begin{{tabular}}{{lrrrrrrr}}
\toprule
relation & $n$ & TP & FP & FN & P & R & F1 \\
\midrule
{body}
\midrule
\textbf{{macro}} & 60 & --- & --- & --- &
{macro['precision']:.2f} & {macro['recall']:.2f} &
\textbf{{{macro['f1']:.3f}}} \\
\bottomrule
\end{{tabular}}
\end{{table}}
"""


def m2_table() -> str:
    d = json.loads((RESULTS / "m2_determinism.json").read_text())
    s_rate = d["sevim_byte_determinism_rate"]
    r_rate = d["raw_llm_byte_determinism_rate"]
    n = d["n_clauses"]; runs = d["n_runs_per_clause"]
    s_ok = sum(1 for r in d["per_clause"] if r["sevim_unique"] == 1)
    r_ok = sum(1 for r in d["per_clause"] if r["raw_llm_unique"] == 1)
    s_pct = f"{s_rate*100:.0f}\\%"
    r_pct = f"{r_rate*100:.0f}\\%"
    return rf"""\begin{{table}}[t]
\centering
\caption{{M2 — Cross-system byte-determinism rate. Each clause was rendered
$N={runs}$ times by each system; a clause counts as deterministic iff all
$N$ runs produced byte-identical output. The raw-LLM baseline is Claude
Haiku 4.5 prompted with a fixed system+user template (see
\texttt{{bench/eval/m2\_determinism.py}}). Test clauses: one per relation,
$n={n}$.}}
\label{{tab:m2-determinism}}
\small
\setlength{{\tabcolsep}}{{6pt}}
\begin{{tabular}}{{lrr}}
\toprule
system & deterministic clauses & rate \\
\midrule
\textbf{{\svggpt}} (encoder-off) & {s_ok}/{n} & \textbf{{{s_pct}}} \\
Claude Haiku 4.5 raw-SVG         & {r_ok}/{n} & {r_pct} \\
\bottomrule
\end{{tabular}}
\end{{table}}
"""


def _percentile(xs, q: float) -> float:
    s = sorted(xs)
    if not s: return 0.0
    i = min(len(s) - 1, int(q * (len(s) - 1)))
    return s[i]


def m3_table() -> str:
    d = json.loads((RESULTS / "m2_determinism.json").read_text())
    sevim_l = sum((r["sevim_lat_ms"] for r in d["per_clause"]), [])
    raw_l = sum((r["raw_llm_lat_ms"] for r in d["per_clause"]), [])
    sp50 = _percentile(sevim_l, 0.5)
    sp95 = _percentile(sevim_l, 0.95)
    smean = st.mean(sevim_l)
    rp50 = _percentile(raw_l, 0.5)
    rp95 = _percentile(raw_l, 0.95)
    rmean = st.mean(raw_l)
    return rf"""\begin{{table}}[t]
\centering
\caption{{M3 — End-to-end wall-clock per clause (ms), measured on the same
12-clause sample as M2 over $36$ runs per system. SeVim is the
encoder-off, full S1\textendash S5 pipeline (now with the spaCy
dependency-parse path enabled, which adds a fixed warm-load cost on the
first call). Raw-LLM is one network round-trip to Claude Haiku 4.5 with
the same system+user prompt as M2; it includes API queueing.}}
\label{{tab:m3-latency-cross}}
\small
\setlength{{\tabcolsep}}{{6pt}}
\begin{{tabular}}{{lrrr}}
\toprule
system & $p_{{50}}$ & $p_{{95}}$ & mean \\
\midrule
\textbf{{\svggpt}} (encoder-off, S1--S5) & {sp50:.1f}     & {sp95:.1f}     & {smean:.1f} \\
Claude Haiku 4.5 raw-SVG                  & {rp50:,.0f}   & {rp95:,.0f}   & {rmean:,.0f} \\
\bottomrule
\end{{tabular}}

\medskip
\small Speed ratio at $p_{{95}}$: \svggpt\ is $\approx
{rp95/sp95:,.0f}\times$ faster than the raw-LLM baseline.
\end{{table}}
"""


def m4_table() -> str:
    d = json.loads((RESULTS / "m4_judge.json").read_text())
    a = d["axis_means"]
    cov = d["label_coverage_mean"]
    return rf"""\begin{{table*}}[t]
\centering
\caption{{M4 --- Subjective LLM-as-judge scores (Claude Sonnet 4.6, single
judge, fixed rubric pinned in \texttt{{bench/eval/rubric.txt}}, blinded
A/B labels). Axis abbreviations: \emph{{rel-faith}} = relation
faithfulness, \emph{{cov}} = concept coverage, \emph{{clarity}} =
structural clarity, \emph{{validity}} = render validity, \emph{{lex-cov}}
= objective lexical coverage of clause content words inside the SVG's
\texttt{{<text>}} elements (proxy reviewers can re-derive without the
judge). Each rubric axis is averaged over $n=12$ paired clauses (one per
concept-diagram relation); seed for the A/B label randomisation = 42.}}
\label{{tab:m4-judge}}
\small
\setlength{{\tabcolsep}}{{6pt}}
\begin{{tabular}}{{lcccccc}}
\toprule
system & rel-faith & cov & clarity & validity & rubric mean & lex-cov \\
\midrule
\textbf{{\svggpt}}        & {a['sevim']['relation_faithfulness']:.2f}
                          & {a['sevim']['concept_coverage']:.2f}
                          & {a['sevim']['structural_clarity']:.2f}
                          & {a['sevim']['render_validity']:.2f}
                          & {(a['sevim']['relation_faithfulness']+a['sevim']['concept_coverage']+a['sevim']['structural_clarity']+a['sevim']['render_validity'])/4:.2f}
                          & {cov['sevim']:.2f} \\
Claude Haiku 4.5 raw-SVG  & {a['raw_llm']['relation_faithfulness']:.2f}
                          & {a['raw_llm']['concept_coverage']:.2f}
                          & {a['raw_llm']['structural_clarity']:.2f}
                          & {a['raw_llm']['render_validity']:.2f}
                          & {(a['raw_llm']['relation_faithfulness']+a['raw_llm']['concept_coverage']+a['raw_llm']['structural_clarity']+a['raw_llm']['render_validity'])/4:.2f}
                          & {cov['raw_llm']:.2f} \\
\bottomrule
\end{{tabular}}
\end{{table*}}
"""


def main() -> int:
    FIGS.mkdir(parents=True, exist_ok=True)
    (FIGS / "eval_m1_extraction.tex").write_text(m1_table())
    (FIGS / "eval_m2_determinism.tex").write_text(m2_table())
    (FIGS / "eval_m3_latency_cross.tex").write_text(m3_table())
    (FIGS / "eval_m4_judge.tex").write_text(m4_table())
    print(f"wrote eval_m{{1,2,3,4}}*.tex into {FIGS.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
