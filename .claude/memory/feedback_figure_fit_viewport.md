---
name: feedback_figure_fit_viewport
description: "Deterministic figures must fit the viewport — keep them compact (aspect that fits when width-fit), don't spread content to the edges"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 99d729af-e760-4960-a041-8a1eccc50fb2
---

2026-06-19: user reported the 3SAT→Vertex-Cover figure "out of the page" — the right gadget ran off the right edge and the clause triangles fell below the fold. It was authored at 1020×700; the canvas viewer fits a figure to the pane WIDTH, so a tall figure (700) became ~900px tall and overflowed the visible height. Fixed by compacting to 960×520.

**Why:** the viewer width-fits the SVG (`#stage svg width:100%` on tablet/mobile; native size on desktop with `<main>` scroll). A figure whose viewBox is too TALL relative to its width overflows the viewport vertically once width-fit; content spread to the edges gets clipped.

**How to apply when authoring a deterministic renderer (`studio/templates/*.py`):**
- Keep the viewBox compact. Rule of thumb: width ≈ 940–980, height ≤ ~560 for a single-screen figure (aspect ≥ ~1.7:1). At a ~1340px pane, height-when-width-fit = H × 1340/W — keep that under ~750px.
- Leave margins: no node/label within ~40px of any edge.
- Don't waste vertical space — compress big empty middle bands (e.g. long connecting edges) rather than spreading panels far apart.
- Multi-panel figures still scroll/pan ([[feedback_canvas_must_be_slidable]]) and narration auto-scrolls the active panel into view (scrollHighlightIntoView, commit e9896e6), but the FIRST paint should fit so the user isn't lost. Existing compact renderers (svd 960×620, conditional_probability 940×600, sphere_area 940×600) are good references; sat_vertex_cover is now 960×520.
