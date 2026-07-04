---
name: Desmos-quality figure phases — all 3 shipped
description: The "GPT + Desmos quality" figure overhaul — Plotly graphing, SymPy symbolic math, and interactive figure embed are all live.
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
Goal: figures that communicate like a mathematician friend — Desmos
*quality*, our own implementation (NOT integrating Desmos itself).
Triggered by screenshots showing garbled LLM-drawn Hessians and a
missing 3-D surface.

**Why:** the LLM was both doing the math and drawing it, failing at
both. Fix = LLM emits a spec; real engines compute + render.

**Shipped & live (2026-05-19):**
- Phase 1 — `studio/templates/plotly_render.py`: Plotly renders
  surface3d / contour / plot2d (proper 3-D, far better than
  matplotlib mplot3d). PNG via Kaleido, wrapped in SVG.
  `generate_matplotlib_svg` tries Plotly first for those kinds.
  Kaleido uses the container's `/usr/bin/chromium` via choreographer.
- Phase 2 — `studio/templates/symbolic_route.py`: derivatives,
  Hessians, gradients, integrals, limits computed EXACTLY by SymPy;
  matplotlib mathtext typesets them. New express route before the
  matplotlib/LLM-SVG paths. `SEVIM_SYMBOLIC_ROUTE` toggle.
- deps added to pyproject.toml AND uv.lock (the Docker build runs
  `uv sync --frozen` — pyproject changes alone don't reach the
  container).

- Phase 3 — interactive figure embed (also 2026-05-19, commit
  030e3c1). `plotly_render.py` embeds the figure spec (base64) in an
  SVG `<metadata id="plotly-spec">`; `canvas.html` detects it,
  lazy-loads Plotly.js from the CDN, and swaps the static PNG for a
  live pan/zoom/rotate widget. Safe by design: zero changes to the
  canvas data model / state endpoint / SSE (the spec rides inside
  `state.svg`); the static SVG stays hidden in the DOM so the chat
  snapshot still works and any failure falls back to the static
  image. CSP already permits jsdelivr.

**All three phases shipped.** Figures (3-D, symbolic, plots) are now
mathematician-grade, correct, and interactive.
