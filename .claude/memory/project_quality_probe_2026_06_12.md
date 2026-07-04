---
name: project_quality_probe_2026_06_12
description: "6-hourly cloud quality probe (EventBridge Scheduler) live, hard-stops end of Aug 2026, emails on real problems"
metadata:
  node_type: memory
  type: project
  originSessionId: 99d729af-e760-4960-a041-8a1eccc50fb2
---

2026-06-12: shipped the recurring quality probe the user asked for ("every 6 hours a new challenging problem fed to the system + inspected; notify arash_kermani@yahoo.com on any problem; do NOT run past end of August 2026"). Chosen design: **cloud monitor + email alerts**, fixes applied in-the-loop (NOT autonomous self-fix).

- **`studio/quality_probe.py`** (lives in the `studio` package, NOT `scripts/` — the Docker image only COPYs whole packages, so a `scripts/` file 404s as `python: can't open file`). Invoked `python -m studio.quality_probe`. Rotating pool of 15 hard prompts; runs the SAME prod path `express_figure`; `inspect_quality()` does pure structural checks; emails via SES `send_alert()` ONLY on a problem or a probe crash — a clean run is silent. Two independent hard stops at **2026-08-31**: the script no-ops past its `END_DATE`, AND the schedule's `EndDate`.
- **CDK (`infra/sevim_stack.py`)**: `aws_scheduler.CfnSchedule` `rate(6 hours)`, `EndDate="2026-08-31T23:59:59.000Z"` (MUST have `.SSSZ` millis or Scheduler rejects with InvalidRequest), FlexibleTimeWindow OFF, ECS RunTask FARGATE in private subnets. Dedicated `ProbeTask` FargateTaskDefinition reuses the app image + env_vars + secrets_map; dedicated SG with RDS access (answer-cache/taxonomy reads); `ses:SendEmail` on task role; scheduler role trusts `scheduler.amazonaws.com`, scoped to `ecs:RunTask` + PassRole on the probe roles.
- **Probe accuracy fix (important):** the viewBox bounds check first false-positived on EVERY graphviz figure (tree/DFA/Turing/DAG/Hasse) — graphviz wraps content in `<g transform="translate(4 200)">`, so raw text `y` is a big negative that renders INSIDE once transformed. Fixed: `_texts_outside_viewbox` walks the DOM folding ancestor translate+scale transforms; returns None (no alert) on irreducible rotation/matrix. Verified live: Bayes tree went 5 false-positives → clean. `tests/test_quality_probe.py` (7 tests).

Verified end-to-end on prod: manual `ecs run-task` exit 0, real `express_figure` ran, **alert email reached arash_kermani@yahoo.com** (the first run's "5 text outside viewBox" alert — that one was the false positive, now fixed), corrected run reports `[probe] clean`. Commits c272d99/c7a2621/db4f3da/807cea8 on main (pushed). Note: the deployer IAM user lacks `scheduler:ListSchedules`; confirm the schedule via `cloudformation describe-stack-resources`. Known cosmetic: probe logs a `datetime.utcnow()` DeprecationWarning (harmless, left unfixed to avoid deploy churn). See [[feedback_deploy_wrapper]] and [[project_taxonomy_system]].

**2026-06-12 — public-repo hardening + opt-in + auto-fix (commit 10940af, deployed).** Repo is PUBLIC, so the operator email must NOT be in committed source except as genuine contact info (CITATION/NOTICE/README-contact/security.txt KEEP; config REMOVED). Now all operator-only values come ONLY from the gitignored repo-root `.env`, which `infra/deploy.sh` now sources (`set -a; . ../.env; set +a`) so they reach `cdk synth`:
- `SEVIM_PROBE_ENABLED` (default OFF): the whole probe block in `sevim_stack.py` (task+SG+scheduler-role+CfnSchedule) is wrapped in `if _probe_enabled:`. A clone running deploy.sh gets NO probe.
- `SEVIM_PROBE_ALERT_EMAIL`: read from env; `quality_probe.ALERT_EMAIL` default "" and `send_alert` no-ops (logs) when empty. No hardcoded fallback.
- `SEVIM_ADMIN_EMAILS`: `os.environ.get(...,"")` in CDK (empty => admin 404s for all).
- `SEVIM_PROBE_AUTOFIX` (default OFF): `attempt_autofix()` re-runs `polish_svg` then regenerates once, re-inspects; emails ONLY if the problem PERSISTS. Repairs figure OUTPUT only — never edits code/redeploys (task has no git/deploy creds). Operator .env has all four =1/email; verified live in probe task env.
Scraper UA strings (download_textbooks.sh, generate_reference_corpus.py) had personal email removed (kept `+https://khayyammath.com`).
**Two false positives fixed** (from live 6h alerts): (1) oversized-element check now SKIPS deterministic routes (matplotlib/plotly draw full-canvas bg by design) + explicit white/none backdrops on LLM path — gradient-descent matplotlib prompt verified `[probe] clean` live; (2) Bayes "5 text outside viewBox" was a stale pre-transform-fix run, verified clean. Probe + flags documented in README ("Operational quality probe") and in the paper (khayyam-math-paper main.tex deployment section, commit ae1a27f, 42pp). 494 tests pass. See [[feedback_no_internal_leakage]] and [[project_reduction_overlap_fix_2026_06_12]].
