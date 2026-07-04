---
name: Show figure fully visible from the start, don't gate visibility on narration playback
description: User feedback: opacity-0 narration-reveal made figures look broken on first paint and broke frozen snapshots; show everything immediately, use highlight rect for narration emphasis.
type: feedback
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
The previous design (commit a6f1302, "viewer: narration-synced fade-in")
made every narration-highlighted SVG element start at `opacity: 0` and
fade in only when its phrase began playing. The user repeatedly
perceived this as a bug: "matrices randomly located," "left-hand side
of the equation has nothing," "new canvas overlays the old one with
corners showing through" — all explained by elements still at
opacity 0 because Play wasn't pressed.

**Why:** users who don't press Play (the default on mobile, where
autoplay is blocked) see a mostly-blank canvas. Worse, the frozen-
snapshot feature (`takeSnapshotForThisTurn` in studio.html) captures
the live SVG via XMLSerializer — if opacity-0 is set inline, the
snapshot is also blank. Two visible features (live canvas + inline
chat snapshots) were both broken by one design choice.

**How to apply:** keep `applyRevealMask` as a no-op so callers don't
crash. Narration emphasis must come from the highlight rect (the
`.sevim-highlight` class stroke/fill swap) or from the audio
walkthrough itself — never from opacity-0-by-default. If a future
feature wants per-element entry animations, gate it behind an opt-in
SVG attribute (e.g. `data-stagger="true"`) rather than the default
narration-reveal map.

Disabled at commit 225a926+ (canvas.html `applyRevealMask` → no-op).
