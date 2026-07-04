---
name: project_eval_rerun_deferred
description: "Full paper eval re-run is deferred until more experiments accumulate; paper caveats cover the gap"
metadata:
  node_type: memory
  type: project
  originSessionId: 99d729af-e760-4960-a041-8a1eccc50fb2
---

2026-06-12: decided to DEFER re-running the paper's full empirical evaluation until more experiments are done, then refresh everything in one batch (more efficient than piecemeal).

Full re-run estimate (current GPT-4o pricing, ~$0.05-0.10/generation): **~$80-150 API + ~half a day wall-clock**, dominated by the **Lean-graded sweep** (~1000 questions at difficulty 6/7/9/10, each a full GPT-4o production generation + Lean grading ≈ $70-120). Judge eval (~25 prompts × 3 configs; the two Qwen configs run LOCAL on the 5090, ~$0 API) ≈ $2-5. Vision-audit/scorer re-judge ≈ $5-15. Biggest cost uncertainty = retry rate on the hard diff-9/10 prompts. Cheap directional option (40-60 prompt judge pass only, skip the sweep) ≈ $3-5 / ~20 min. Re-training the LoRA is overnight on the owned 5090 (~$1-2 electricity), and per the paper those Qwen+adapter numbers are non-load-bearing baselines.

Why deferral is safe: the paper already carries `\subsection{What this evaluation does not yet show}` (sec:eval-limits) + explicit "post-dating the benchmark run" notes in sec:latency-bench, and the 6 new deterministic renderer-first routes only move the numbers in the FAVORABLE direction (more deterministic share, fewer LLM-SVG failures, lower latency), so the published figures (judge totals 22.9/17.1/13.8 for GPT-4o/Qwen-base/Qwen+LoRA) are conservative, not inflated. The paper also avoids a brittle hard "% deterministic" headline (says "a sizeable share"). See [[project_reduction_overlap_fix_2026_06_12]] and [[feedback_abstract_magnitudes_not_counts]].
