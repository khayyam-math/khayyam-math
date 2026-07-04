---
name: project_session_2026_06_08_oom_incident
description: "2026-06-08 site-down incident — OOM root cause, 5 fixes shipped (taskdef rev 212), open leak + small-text items"
metadata: 
  node_type: memory
  type: project
  originSessionId: 7b5611d5-76e1-4124-8388-5dc5cf3a2293
---

2026-06-08: "khayyammath.com is down". Root cause: the single ECS task was
**OOM-killed** (exit 137) after ~26h uptime — a slow memory climb plus a
piper TTS re-synth spike crossed the 2 GB limit; ECS rescheduled, blanking
the site until the replacement went healthy. Caught mid-recovery.

Shipped (taskdef **rev 212**, live, 193/193 quality gate, all on main):
- **OOM hardening** (`infra/sevim_stack.py`): memory 2048→3072, desired_count
  1→2 (redundancy so one OOM never blanks the site), LB cookie stickiness
  (8h) so a turn's requests land on the task building its canvas. Deploy
  via `infra/deploy.sh` per [[feedback_deploy_wrapper]].
- **FDL language/inspector** (`express.py`, `templates/fdl.py`): the FDL
  fast-path emitted German for an English "recursion theorem" prompt AND
  drew an irrelevant y=x² (recursion theorem isn't function-graphable) AND
  skipped the inspector. Fixed: pin detected language, decline abstract
  CS/logic topics, deterministic language guard before returning.
- **Idle/sliding session** (`auth.py`): login cookie now re-issues each
  request with a fresh idle deadline (`SEVIM_AUTH_IDLE_TTL_S`, default 24h)
  + absolute cap (mexp). Annotate the dependency param as plain `Response`
  (NOT `Response | None` — FastAPI rejects the union → startup crash).
- **Chat tool-invocation flake** (`app.py` SYSTEM_PROMPT): imperative math
  commands (solve/compute/evaluate/…) now reliably call sevim_express
  instead of answering in text (was leaving the canvas empty).
- **Math-verifier solve claims** (`templates/math_verifier.py`): it crashed
  on the correct claim `solve(x**2-5x+6,x)==[2,3]` (no `solve` in env, only
  knew scalar a-b → TypeError → false FAIL). Added solve/roots/FiniteSet +
  unordered solution-set comparison; wrong sets still fail. New tests in
  `tests/test_math_verifier_solve.py` (module had none).

Deploy took 5 attempts — each early failure was a safety net working:
verifier caught the FastAPI bug, the gate caught the chat flake then the
verifier false-negative. See [[feedback_fix_root_cause_over_gate_bypass]].

**FOLLOW-UP — all resolved 2026-06-08 (taskdef rev 213, gate 193/193):**
- **Memory root-caused via CloudWatch**, two distinct problems both fixed:
  (1) the OOM *spike* (single min hit ~1.6GB) was the vision reviewer's
  headless-Chrome rasteriser — `_svg_to_png` runs under asyncio.to_thread
  so concurrent requests spawned concurrent Chromes + tall figures made
  unbounded viewports. Fixed: process-wide raster semaphore
  (`SEVIM_MAX_RASTER`=1), width/height/area clamps, `--single-process`.
  (2) the slow ~30MB/day leak = unbounded CanvasRegistry → LRU+TTL eviction
  (`SEVIM_REGISTRY_MAX`=256, `SEVIM_REGISTRY_TTL_S`=2h), safe via S3 rehydrate.
- **"Small text"** fixed deterministically: `enforce_min_font_size` raises
  sub-13px text to a legibility floor (`SEVIM_MIN_FONT_PX`), runs before
  layout passes. Only enlarges, leaves %-sizes alone.
- **solve-prompt verifier flakiness** fixed: `_deterministic_solve_claims`
  injects a SymPy-derived root claim (true by construction) for solve-intent
  prompts, overriding the LLM's unreliable claims (it emitted "3=0"-style
  x-intercept claims that the verifier rejected). 3x3 attempts all verified.
- **Two real quality-gate bugs fixed**: (a) per-prompt log-window race —
  added a settle (`SEVIM_GATE_SETTLE_S`=0.6s) before snapshot so a prompt's
  verifier line isn't sliced into the next prompt's window; (b)
  `check_no_verifier_failures` now reads the LAST verifier line instead of
  grepping the obsolete "all N claim(s) verified" wording.
- Took deploys #5–#8 (each early failure was the gate catching a real
  bug). Commits 68fc6a9 → 79a6e7a.

**2026-06-09 follow-up (taskdef rev 214):**
- **Text overlap fixed at the source** (`render_text_blocks`): it reset y
  to the region's fixed top for EVERY block, so multiple blocks in one
  region (e.g. three "Output P/Q/R" right-column blocks) overlapped into
  mush. Now keeps a per-region y-cursor so same-region blocks stack.
  Test: `tests/test_text_block_stacking.py`. Commit 5f3e7c0.
- **Dropped `--single-process` from the Chrome rasteriser** (commit
  daada6d): it slowed each render and tipped the heaviest 3-retry proof
  prompt to 90.1s (>90s budget). The raster SEMAPHORE (one Chrome at a
  time) + dimension clamps are the real memory bound; one normal render
  is ~0.5-1GB, safe on 3GB. Memory fix intact, latency restored.
