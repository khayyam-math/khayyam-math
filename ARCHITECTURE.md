# Architecture

A high-level map of how Khayyam Math turns a one-line prompt into a
voice-narrated SVG figure. Read this before opening a non-trivial PR.

## Request lifecycle

```
                       ┌─────────────────────────────────────────────┐
                       │              user prompt                    │
                       │   ("draw a DFA for L = (a|b)* ending ab")   │
                       └────────────────────┬────────────────────────┘
                                            │
                                            ▼
                            ┌───────────────────────────────┐
                            │  studio/app.py  POST /chat    │
                            │  • magic-link auth            │
                            │  • rate limiter + cost guard  │
                            │  • content filter             │
                            └────────────────┬──────────────┘
                                             │ async tool-call
                                             ▼
                       ┌──────────────────────────────────────────┐
                       │   studio/express.py: express_figure()    │
                       │                                          │
   ┌───────────────────┤   1. Graphviz fast-path classifier       │
   │ DFA/Turing/DAG/   │      `is_graphviz_prompt(prompt)` ?      │
   │ tree/Hasse/Cayley │      → LLM emits DOT, `dot -Tsvg`         │
   ▼                   │      → studio/templates/graphviz_route   │
graphviz SVG ----------┤                                          │
                       │   2. Template router                     │
   ┌───────────────────┤      gpt-4o-mini classifier picks one of:│
   │ matrix mul / inv  │      matrix_multiplication, matrix_      │
   │ determinant /     │      transpose, matrix_determinant,      │
   │ transpose / Ax=b  │      matrix_inverse, system_of_equations,│
   ▼                   │      state_diagram. Python renders.      │
template SVG ----------┤      → studio/templates/                 │
                       │                                          │
                       │   3. LLM-SVG path (fallback)             │
                       │      gpt-4o-mini → JSON {svg,narration}  │
                       │      ↓                                   │
                       │      LaTeX scrubber                      │
                       │      ↓                                   │
                       │      autofit_group_rects                 │
                       │      ↓                                   │
                       │      reflow_overlapping_text/groups      │
                       │      ↓                                   │
                       │      wrap_overlong_text                  │
                       │      ↓                                   │
                       │      CP-SAT layout planner               │
                       │      (studio/layout_planner.py)          │
                       │      ↓                                   │
                       │      vision audit (gpt-4o on rendered    │
                       │      PNG; up to 3 retries on FAIL)       │
                       │      ↓                                   │
                       └────────────────┬─────────────────────────┘
                                        │
                                        ▼
                            ┌───────────────────────────────┐
                            │  service/canvas.py:           │
                            │  Canvas.set_raw_svg()         │
                            │  • parses node/edge counts    │
                            │  • persists to S3             │
                            │  • bumps revision             │
                            └────────────────┬──────────────┘
                                             │ SSE stream
                                             ▼
                            ┌───────────────────────────────┐
                            │  /canvas/<cid>/view (iframe)  │
                            │  service/static/canvas.html   │
                            │  • injects SVG into #stage    │
                            │  • plays piper narration WAV  │
                            │  • highlights elements in sync│
                            │    with phrase timings        │
                            └───────────────────────────────┘
```

## Subsystems

### 1. The express loop (`studio/express.py`)

The single entry point for figure generation. ~5 K LOC. Owns:

- **Prompt routing** between the three paths (Graphviz, template,
  LLM-SVG)
- **Retry policy** when the vision auditor fails
- **SVG post-processing pipeline** (LaTeX scrub → autofit → reflow →
  wrap → plan)
- **Narration manifest** generation (phrase → highlight-id mapping)
- **Conversational context** plumbing: when the user follow-ups
  with a refinement (`_looks_like_followup` in `studio/app.py`),
  the prior canvas is attached so the LLM can extend it instead of
  starting from scratch

### 2. The CP-SAT layout planner (`studio/layout_planner.py`)

A constraint solver (Google OR-Tools CP-SAT) that:

- Reads candidate positions for every text + group in the SVG
- Solves for an assignment that **minimises overlap** subject to
  hard constraints (in-bounds, group-cohesion, narration-anchor
  pinning)
- Returns a re-laid-out SVG with no overlapping text and nothing
  off-canvas

The planner is the **production workhorse** for label placement.
The neural layout work (see §6) was an exploration of whether
neural alternatives can match it; they don't, yet.

### 3. The template family (`studio/templates/`)

Pure-Python functions that render specific math-operation families
deterministically (no LLM). Currently:

- `matrix.py` — multiplication, transpose, determinant, inverse,
  Ax=b. Each shows worked examples step-by-step.
- `graph.py` — state-diagram template (BFS-layered Sugiyama).
- `graphviz_route.py` — LLM-emits-DOT classifier and renderer for
  every other graph-shaped figure.

The router (`router.py`) is a gpt-4o-mini classifier that maps a
prompt to a template name + extracts structured arguments
(matrices, transition tables) as JSON.

### 4. The canvas runtime (`service/canvas.py` + `canvas.html`)

The Canvas object on the server holds:

- The current SVG, its revision, and its narration manifest
- A durable JSON blob persisted to S3 on every mutation
- The piper-generated narration WAV and a phrase-→-highlight-id map

The browser `canvas.html` viewer:

- Receives SVG + narration via SSE
- Plays the WAV; the active phrase changes `highlight` overlays in
  sync with WAV-measured phrase timings (NOT estimated — see
  `feedback_narration_word_accurate`)
- Handles iOS visibility quirks (tab switch → SVG cleared by Safari;
  we recover via localStorage + canvas-id lookup)

### 5. The neural-layout subsystem (`studio/neural_layout/`)

Research module. Three trained models live here:

- **GNN delta-predictor** (`models/gnn_baseline.py`, 6.3 M params).
  Predicts per-node bbox corrections. **Result: -3 % to -8 % vs
  no-op baseline**, i.e. one-shot delta prediction is structurally
  under-determined.
- **LayoutDM diffusion denoiser** (`models/layoutdm.py`, 6.9 M params).
  Discrete diffusion over quantised positions. Learns the in-bounds
  constraint cleanly (0 OOB per regenerated graph) but does not
  improve overlap-pair-count vs the CP-SAT planner alone.
- **Layout-quality scorer** (`models/quality_scorer.py`, 1.8 M params).
  Graph-conditioned binary classifier predicting gpt-4o pass/fail.
  **71 % pairwise win-rate on real (broken, fixed) labels.**
  Used as a re-ranker over CP-SAT candidates → measured **+2 pp
  gpt-4o pass-rate** improvement on a 150-pair benchmark.

Full plan in [`studio/neural_layout/PLAN.md`](studio/neural_layout/PLAN.md).
Training data (~22 K pairs) and checkpoints (~230 MB) live on disk;
gitignored. Regenerate via the data-factory scripts in `scripts/`.

## What plugs where (file-level map)

| Capability | File |
|---|---|
| Studio chat endpoint | `studio/app.py` |
| Tool-routing follow-up classifier | `studio/app.py` (`_looks_like_followup`) |
| Express loop | `studio/express.py` |
| LaTeX scrubber | `studio/express.py` (`strip_latex_in_svg_text`) |
| Template router | `studio/templates/router.py` |
| Matrix templates | `studio/templates/matrix.py` |
| Graphviz route | `studio/templates/graphviz_route.py` |
| State-diagram template | `studio/templates/graph.py` |
| CP-SAT layout planner | `studio/layout_planner.py` |
| Canvas object | `service/canvas.py` |
| Canvas viewer (browser) | `service/static/canvas.html` |
| Landing page | `service/static/landing.html` |
| Studio (chat panel) | `studio/static/studio.html` |
| Auth (magic link) | `studio/auth.py` |
| Telemetry | `studio/sessions.py`, `sevim/telemetry.py` |
| AWS deploy (CDK) | `infra/` |
| Tests | `tests/` |
| Demo screenshots | `service/static/screenshots/` |

## What's NOT here (deliberate non-goals)

- **No model training in production.** All fine-tuning happens on a
  local GPU (5090 in the dev setup); only inference runs in AWS.
- **No new SVG generation paradigm.** We don't try to invent a
  "better SVG-from-LLM" approach — we route to the right deterministic
  tool when possible.
- **No knowledge graph back-end.** Earlier work (Sevim v0.1-v0.2) had
  one; we collapsed to the single `sevim_express` tool in v0.3+
  because graph extraction was the failure mode, not the value.

## Deploy / ops

```
Local dev:   .venv + uvicorn at 127.0.0.1:8765
Container:   Dockerfile (Python 3.12 slim + graphviz + cairo + piper voice)
Cloud:       AWS ECS Fargate, us-east-1, ALB + ACM + Route53
Telemetry:   Postgres (RDS) for sessions / cost / events
Canvas data: S3 (rehydration on task replacement)
Auth:        Magic link via SES; HttpOnly signed cookie
Deploy:      `cd infra && ./deploy.sh` (wraps `cdk deploy`)
```

See `infra/deploy.sh` for the exact env var contract; do NOT run
`cdk deploy` directly — the wrapper preserves HTTPS / ACM / Route53
config that bare CDK would otherwise drop.
