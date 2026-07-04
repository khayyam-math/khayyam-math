---
name: Canvas must be SLIDABLE on mobile — never force SVG to fit the iframe
description: User asked four times for the canvas iframe to scroll in both directions so they can pan around a figure bigger than the viewport. Forcing SVG to width:100% removes this affordance and is the wrong call.
type: feedback
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
The canvas iframe on mobile MUST be scrollable in both directions so
the user can pan a figure that's bigger than the viewport. The SVG
fits the container width but never shrinks below a readable minimum
(`min-width: 640px` for a 900-px viewBox keeps font-size ≥ ~11 px at
the smallest); `main { overflow: auto }` inside canvas.html handles
the pan affordance for the bit that doesn't fit. The previous
extremes were both wrong: native size (900 px) made the figure
unreadably large in a 375-px iPhone viewport; pure `width:100%`
made text microscopic and silently clipped the right edge.

**Why:** asked four separate times. Each time I "fixed" it by
forcing `#stage svg { width: 100% }` to avoid right-edge clipping —
which makes text tiny on a 375-px iPhone AND removes the scroll
affordance entirely. The trade-off was wrong: small unreadable
figure with no scroll < native-size figure that the user can pan.

**How to apply:**
* Keep `#stage svg { width: auto; height: auto }` on mobile.
* `<main>` must have `overflow: auto !important`,
  `-webkit-overflow-scrolling: touch`, `touch-action: pan-x pan-y`,
  visible scrollbars (`::-webkit-scrollbar` styling with non-zero
  width + colored thumb).
* Do NOT add a CSS media query that overrides SVG width to 100%.
* If a future regression report says "labels clipped on the right,"
  the right fix is to ensure the figure has a visible scroll affordance
  (e.g. a fade gradient at the edge or a "drag to pan" hint), NOT to
  shrink the SVG.

Reverted to slidable canvas at commit after 225a926.

**2026-06-19 update (commit e9896e6):** canvas.html `<body>` is now a flex
COLUMN (`display:flex; flex-direction:column; overflow:hidden;
padding-top:env(safe-area-inset-top)`); header is `flex:0 0 auto`; `<main>`
is `flex:1 1 auto; min-height:0; overflow:auto` (the hardcoded
`height:calc(100dvh - 50px/44px)` is GONE). This fixed the Play button
getting clipped behind the iOS Safari toolbar after panning (window used to
scroll when the header wrapped / the dynamic toolbar resized 100dvh). The
`overflow:hidden` is on the BODY (window), NOT on `<main>` — `<main>` keeps
`overflow:auto` and the slidability above is fully preserved. Also added
`scrollHighlightIntoView()` (called from `applyHighlight`) so narration
auto-scrolls the highlighted panel into view (union bbox, smooth, only when
out of view). Don't remove body `overflow:hidden` (window scroll returns the
Play-button bug) and don't put `overflow:hidden` on `<main>` (kills the pan).
