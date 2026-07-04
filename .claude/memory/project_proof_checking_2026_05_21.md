---
name: 2026-05-21 — proof-checking integration (Z3 + Lean core + Lean+Mathlib offline)
description: Three-tier proof verification chain - SymPy → Z3 (SMT) → Lean core (decide) at runtime, plus Lean+Mathlib offline catalog verifier. All shipped to khayyammath.com.
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
**Tier order in studio/templates/math_verifier.py:**
  1. SymPy simplify / trigsimp / value compare.
  2. Z3 SMT (studio/templates/z3_verifier.py) — translates the
     SymPy AST to Z3 expressions, proves identity by asking
     `solver.check(a != b) == unsat`.  Returns concrete counter-models
     on failure (much more actionable than "a-b simplifies to …").
     ~10 MB dep, ms-to-seconds per claim.
  3. Lean 4 core (studio/templates/lean_verifier.py) — generates a
     tiny .lean file with `example : … := by decide`, runs `lean`
     against it, kernel-checks the proof.  Narrow scope: closed Nat
     arithmetic, divisibility, finite-case enumeration.  ~300 MB in
     the Docker image; ~600 ms cold start; fires only as fourth try.
  4. (Offline) Lean + Mathlib catalog verifier
     (studio/catalog_verifier.py) — batch job that reads queued
     claims from the lean_verifications table, formalises via
     studio/templates/lean_translator.py, runs `lake env lean
     Catalog/Probe.lean` under lean_catalog/, records pass/fail.
     Mathlib is NOT in the production container (3 GB; offline only).
     Failures are surfaced at /studio/admin/lean for triage — they
     do NOT pull figures from production (user policy: "tag
     privately for review; keep showing publicly").

**Schema additions** (sevim/telemetry.py):
  • canvases.math_claims_json — claims persisted per canvas.
  • lean_verifications (canvas_id, claim_idx, status …) — one row
    per claim; status ∈ {queued, verified, failed, unsupported,
    timeout}.  Additive migration covers existing prod rows.

**Engine tag on the log line** (studio/express.py):
  "math-correctness verifier: all N claim(s) verified (z3=X, lean=Y)"
  lets the quality gate see the engine mix without log archaeology.

**Lake project setup** (lean_catalog/README.md):
  One-time `lake update && lake build` (~30 min, ~4 GB).  Lean 4
  v4.29.0 / Mathlib4 v4.29.0 pinned.  Run on dev box; offline
  service.  CLI runner exits non-zero if Lake build hasn't been
  done — so cron can detect drift.

**Deploy of 2026-05-21**: gate green, image rebuilt with z3-solver
+ Lean toolchain (~900 MB total, +300 MB).  Total deploy time
238 s.  ECS task def rev 144 live.

**Disable flags**: SEVIM_LEAN_VERIFIER=off skips Lean tier; Z3 is
unconditional (cheap + Python-native).

**Open**: Mathlib install on dev box not yet done — the catalog
verifier is wired and ready but no claims have been verified by it
yet.  Run `cd lean_catalog && lake update && lake build` once when
you want the offline pass to start.

**Other tools considered, not adopted**:
  • CVC5 — similar to Z3, kept Z3 as the canonical SMT for now.
  • Isabelle/Sledgehammer — would add value as a parallel ATP, but
    deferred.
  • WolframAlpha API — paid + rate-limited, no fit for our budget.

**Commits**: da4a900 (Phase A), 85fb687 (Phase B), 4b5a086 (Phase C).
