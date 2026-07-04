---
name: project_session_2026_06_11_latency_quality
description: 2026-06-11 — fixed ~150s-turn latency + systemic side-column text garble; QA stress-test method
metadata: 
  node_type: memory
  type: project
  originSessionId: 99d729af-e760-4960-a041-8a1eccc50fb2
---

2026-06-11 (after the email/logo/gate work). Triggered by a live "prove vertex cover is NP-complete" turn taking ~149s and a complaint about figure quality as usage grew.

**Latency (commit 6905b05)** — CloudWatch breakdown: ~58s narration + ~90s figure.
- `sevim/narrate.py`: OpenAI TTS per-phrase timeout 45s→12s (`SEVIM_TTS_TIMEOUT_S`); one hung phrase used to stall the whole concurrent batch 45s. `_synthesize_phrase` now retries OpenAI once before piper, so a single transient timeout no longer forces the all-piper re-synth that discards every good OpenAI phrase.
- `studio/express.py`: vision-review retry loop early-stops when a retry doesn't improve the best (lowest) `_attempt_score`. vertex-cover scored 8/8/8 across 3 ~30s rounds and shipped the best regardless. `SEVIM_EXPRESS_EARLY_STOP=0` to disable; first retry always runs.

**Figure legibility (commit 59468d7)** — the dominant systemic bug on proof/concept figures was the garbled side-column text. `render_text_blocks` stacked logical lines at lh=20, then `wrap_overlong_text` inserted wrapped continuations at y+18 without pushing the next line down → collisions (y=200,218,220,238,240...). Fixed by word-wrapping to the column width INSIDE `render_text_blocks`, so every visual line stacks cleanly. Pairs with the earlier `fit_node_boxes_to_labels` fix.

**QA method**: ran 6 hard prompts through `express_figure` locally (vision review on), rasterised with cairosvg, read the PNGs. NOTE: cairosvg artifacts to ignore — Graphviz route renders blank, some figures get giant black blobs, `√`/`≤` show as `□`. These are rasteriser-only; the browser is fine. Real geometry bugs (overlapping `<text>` y-coords) DO show and are trustworthy. Deterministic/plot routes (matplotlib eigen, box partition) were clean; template-less proofs still have residual empty-matrix-box + raw-SVG overlap (irreducible LLM variance).

Admin `/studio/admin/users-summary` is NOT broken: `require_admin` returns 404 to non-admins by design (hides the URL); a signed-in admin gets 200 + live data. See [[project_quality_gate_flake_handling]] and [[feedback_narrate_idea_not_picture]].
