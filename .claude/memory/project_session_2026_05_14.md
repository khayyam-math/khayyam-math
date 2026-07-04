---
name: 2026-05-14 — template family + show-steps + open canvas-refresh bug
description: Full overnight session: mobile UX fixes, REGISTRY rehydration, CP-SAT layout planner, template library (matrix family + state_diagram), show-intermediate-steps universalisation. Open challenge: shapes on canvas — new figure sometimes layered on old.
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
**Open challenge at session compact**: canvas refresh.  User report just
before compact: "the canvas didn't get refreshed and the new figure
was added to the old one."  No screenshot grabbed; recent logs show
recent prompts went through both the template fast-path (matrix
inverse) and the LLM-SVG path (SAT, 3SAT-to-Clique).  Hypotheses:

* iframe didn't reload between turns when canvas_id changed (possible
  if Safari cached the canvas iframe content)
* `sevim_partial_svg` postMessage from a NEW turn's streaming hit the
  OLD canvas iframe before iframe.src was reassigned to the new
  /canvas/<new_id>/view, causing the new partial SVG to layer over
  the old one
* The frozen-snapshot feature inserts an `<img>` into the prior
  primer; if that snapshot was somehow placed on the live canvas
  pane, it would look layered

To diagnose next session: grab a screenshot from the user; look at
the iframe.src change timeline in CloudWatch.

## Live on khayyammath.com after this session

* **Mobile UX** — sticky-top canvas, slidable iframe (touch pan, visible
  scrollbar, min-width:640 so figures stay readable), per-turn frozen-
  snapshot in chat bubbles (data:image/svg+xml inline), tight viewBox
  on templates (auto-computed canvas_h to avoid empty whitespace),
  removed #stage border so empty area doesn't draw a useless square.
* **Canvas durability** — `_persist_state()` writes <cid>/state.json to
  S3 on every set_raw_svg + narrate; `CanvasRegistry.get()` rehydrates
  on miss.  Survives ECS task replacement.  Server returns friendly
  HTML 404 page for stale canvas URLs.
* **Templates (studio/templates/)** — matrix_multiplication,
  matrix_transpose, matrix_determinant, matrix_inverse,
  system_of_equations, state_diagram.  Each renders deterministic SVG
  + rich narration script.  Math computed in Python (no LLM
  arithmetic).  All show intermediate steps as REAL canvas elements:
    * matrix_inverse — three matrices A → adj(A) → A^(-1), with
      cof / "/det" labels, 4 step caption lines
    * matrix_multiplication — A · B = C plus "general rule" formula
      and "worked example c[1,1] = …" lines
    * system_of_equations — TWO rows: [A][x]=[b] up top, then
      x = [A^(-1)] * [b] = [solution] below + per-unknown values
* **Template router** — gpt-4o-mini classifier at the top of
  express_figure; matched prompts skip the 30-90 s LLM-SVG loop and
  render in <1 s.  Router system prompt is aggressive (rule 1: "prefer
  matching over rejecting"; rule 2: "invent concrete entries when the
  prompt is loose").  SEVIM_TEMPLATE_ROUTER=off disables.
* **LLM-SVG path** — `_EXPRESS_SYSTEM` gained a strict "SHOW DON'T
  TELL" rule: every narration phrase must highlight a VISIBLE SVG
  element.  Vision-review reviewer told "NEVER FAIL on narration
  highlights" (was firing false-positive verdicts on
  highlight-not-visible-in-static-PNG complaints).
* **CP-SAT layout planner** (studio/layout_planner.py) — text labels
  AND <g> matrix groups placed via OR-tools CP-SAT.  Protected ids
  (narration-highlighted text) stay at anchor.  Wrapper rects
  expanded after group movement.
* **Visibility fix** — applyRevealMask in canvas.html is a no-op;
  narration emphasis via highlight rect only (not opacity).
* **Caption-retry skip** — express loop accepts a figure if the ONLY
  remaining structural issue is `caption_overlaps_diagram` and
  vision review passed.  Saved ~60 s per slow turn.

## Latest commits (verify with git log)

  91d07f / ...     fresh canvas on refresh + aggressive router
  5638d82 / ...    mobile SVG-fit; later REVERTED to slidable
  d608cc3          sticky-top canvas + freeze-as-image
  225a926          pin narration-highlighted ids + env toggle
  b3xy5bbhm        wrapper-rect expand
  dd1657b          matrix template library (matrix_multiplication etc.)
  ...
  b77so4ene (deploy ~04:00 May 14) — final system_of_equations show-steps

## Task list state

  #50  in_progress — Stage 7 weight tuning (DEFERRED, lower priority now)
  #52  pending     — Device-aware figure generation (viewport-conditioned)
  #55  in_progress — Graph family (state_diagram spike done; turing,
                     dag, tree still pending)
  #56  in_progress — Show-steps universalisation (matrix done; state_diagram
                     could show step-by-step language acceptance; LLM path
                     has the new system-prompt rule)

## Open items requested by user (not yet started)

* The "canvas didn't refresh, new figure added on top" bug → MUST be
  first thing investigated next session.  Get a screenshot from user.
* Device-aware figure generation (task #52) — viewport-conditioned
  prompts so mobile gets stacked layouts.

## Resume hint

When you come back: read this file + check `git log --oneline -20` to
see what landed; then ask the user for a screenshot of the
"figure-added-on-top-of-old" issue.  Don't propose new features until
that visual bug is resolved.
