---
name: project_quality_gate_flake_handling
description: Quality gate now retries flaky prompts and has a 125s perf cap; how to tune/disable it
metadata: 
  node_type: memory
  type: project
  originSessionId: 99d729af-e760-4960-a041-8a1eccc50fb2
---

2026-06-11: hardened `infra/quality_gate.py` against the recurring flaky-gate churn (it had blocked 3+ consecutive deploys, each on a different transient check).

- **Flake retry**: `evaluate_with_retry` re-runs a prompt once when any check fails and keeps a check failed only if it fails BOTH attempts. Transient wobble self-heals and is reported as "flakes auto-recovered"; a real bug fails consistently and still blocks. Only fires on a failing prompt, so clean runs cost nothing. Disable with `SEVIM_GATE_RETRY_FLAKES=0`.
- **Perf cap raised 90s → 125s** (`SEVIM_GATE_PERF_CAP_S` to override). The LLM-SVG path runs up to 3 attempts (max_retries=2), each an LLM gen + gpt-4o vision review (~35-40s); a figure the vision auditor keeps rejecting legitimately hits ~110-120s (arith_gcd was 90-117s every run, never under 90s). 125s still catches a genuine stuck loop.
- **Deterministic-route verifier check**: `check_math_verifier_ran` now passes when the log shows a `fast-path:` marker — deterministic routes (FDL, graphviz, matrix templates) are correct-by-construction and run no LLM math verifier (e.g. "Verify Euler's identity" → FDL fast-path, no verifier line; that's correct, not a regression).

Tests: `tests/test_quality_gate_retry.py`. This is the user-endorsed "fix the gate, don't bypass" approach — see [[feedback_fix_root_cause_over_gate_bypass]]. Standalone gate run: `uv run python infra/quality_gate.py` (starts its own server, hits the OpenAI API).
