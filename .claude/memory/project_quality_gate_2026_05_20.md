---
name: 2026-05-20 — pre-deploy quality gate enforcing 50 automatable criteria
description: infra/quality_gate.py is now wired into deploy.sh; every production deploy is blocked unless a 50-check battery passes locally. Bypass only with SEVIM_SKIP_QUALITY_GATE=1.
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
**What it does.** `infra/quality_gate.py` spins up the local
service on `127.0.0.1:8044`, fires a curated battery of prompts
through `/studio/chat` (homomorphism, critical-points, Pythagoras,
3D surface, Graphviz DAG, Euler identity, …) and asserts ~17
per-prompt criteria + 7 global criteria — 50 checks total in FAST
mode, ~120 in full mode.

**Where it lives.** `infra/quality_gate.py` (the gate itself);
`infra/deploy.sh` runs it before `npx aws-cdk deploy`.  A failing
gate exits 2 and the deploy is blocked.

**Bypass (emergency hotfix only):**
`SEVIM_SKIP_QUALITY_GATE=1 ./deploy.sh`.

**Speed knob:** `SEVIM_QUALITY_GATE_FAST=1` keeps only 3
high-signal prompts (~60s); default is the full 8-prompt battery
(~2-3 min).

**What it catches.**  Floating edges, text outside viewBox (with
group-transform stack so Graphviz isn't a false positive),
oversized arrowheads, 3D plots without `aspectmode=cube`, font
below 8 px, missing narration phrases, wrong route taken
(deterministic-route regression), math-verifier failures, TTFB
> 8 s, total > 30 s, `/docs` exposed, `Server: uvicorn` header
leak, missing HSTS, model/provider names appearing in client SVG
or studio.html, `opacity=0` reveal mask, missing
`{status:ok}`-only `/health` body.

**Why:**  the user requirement on 2026-05-20: "make sure every
production goes through these tests".  The gate is the
mechanical enforcement of the ~50 automatable criteria in
`quality_criteria.xlsx`; gaps (accessibility, anti-fabrication,
seeded random) are tracked there as unchecked rows.

**How to apply:**
- Run before every deploy automatically — already wired.
- When adding a new feature, ALSO add a corresponding criterion
  to the gate so regressions are detected.
- When a check produces a false positive (real example: Graphviz
  group-transform), tighten the check to account for the case —
  don't just relax the assertion.
- The gate uses the SAME source tree the Dockerfile will package,
  so local-pass implies prod-pass (modulo container Python
  differences — accept this risk for now).

**Commit:** `9f7c45f` on `origin/main`.
