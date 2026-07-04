---
name: 2026-05-17 — figure-quality overhaul (78-prompt stress test)
description: Stress-tested 78 prompts (geometry, graph theory, Turing machines, formula-rich, dense, regression/SVM/RBF curves, 3D); fixed the vision-audit rasteriser, Graphviz narration, narration highlighting, and XML safety. Deployed + pushed.
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
Two stress runs — 50 mixed prompts then 28 curve/3D prompts — drove a
round of figure-quality fixes. Commits `c2914d7` (geometry templates +
commutative routing) and `0830bf2` (this overhaul) on `khayyam-math`.
Deployed: ECS image `702b1540…`, khayyammath.com healthy.

## What was fixed (now-current architecture)

- **Vision audit rasteriser → headless Chrome.** `_svg_to_png` in
  studio/express.py used cairosvg, which mis-sized `font-size="80%"`
  tspans (every exponent/subscript) and lacked math glyphs — the
  reviewer was auditing a garbled image. Now renders via headless
  Chrome (same engine as the canvas viewer); falls back to cairosvg
  if no Chrome binary. Dockerfile runtime stage adds `chromium` +
  `fonts-dejavu` (full).
- **Graphviz narration.** Graphviz figures (graph theory, automata,
  commutative diagrams) shipped with `narration: []` → zero
  highlighting. New `narrate_graphviz` in graphviz_route.py
  synthesises a phrase walkthrough highlighting real `nodeN`/`edgeN`
  ids.
- **Narration binding.** The LLM-SVG generator nearly always draws
  figures with NO `id` attributes, so narration highlights resolved
  to nothing. `bind_narration_to_svg` injects ids on every `<text>`
  and grounds each phrase to the element it describes by token
  overlap. 196 dead highlight refs → 0 across the test set.
- **XML safety.** `escape_bare_xml_in_svg` entity-escapes stray
  `&`/`<` in content AND attribute values; runs first and last.
  `_assign_text_ids` matches single- AND double-quoted ids (a
  single-quoted id was getting a duplicate `id=` injected → invalid
  XML).

## Known remaining limitations (NOT deterministic bugs)

Dense LLM-SVG figures still overlap (caption-on-diagram, column
collisions); some figures draw oversized/irrelevant elements (e.g. an
SVM figure with class blobs filling the canvas); "3D" prompts render
as stylised 2.5-D side views. These are gpt-4o-mini generation-quality
limits — the Chrome audit can now SEE them and issue fixes on retry,
but they are not fully solved. Consistent with the paper's
"mature tools beat new ML for layout" framing. Do not treat these as
easy unfixed bugs.

## Stress harness

Throwaway harness lived in /tmp/stress and /tmp/stress3 (ephemeral —
gone after reboot). 78 prompts, concurrency 6, automated checks for
XML validity / OOB / LaTeX leak / highlight-id resolution, plus
Chrome rasterisation for visual review. Rebuild from the commit if a
future regression sweep is needed.
