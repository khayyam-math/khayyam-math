---
name: Captions and labels must not cover figures
description: User's preference for visualisations — captions, labels, and explanatory text should never overlay the figure they describe.
type: feedback
originSessionId: b750514c-b82c-4fee-9ab5-0f4710523a32
---
Captions, labels, and explanatory text on a Sevim canvas must NOT cover
the figure they describe.  Push them to canvas margins with leader lines
back to the anchor point.

**Why:** User explicitly flagged this when reviewing a Hamiltonian-path
diagram where caption boxes were obscuring the v1..v6 vertices and the
"in"/"out" labels — quote: *"we should be careful not to cover the
shapes with text"*.  The fundamental issue is that caption boxes are
typically wider than gaps between figure points, so any in-figure
placement scheme will sometimes overlap.

**How to apply:** When designing any new visual element that adds text
near a referent (captions, annotations, equation tags, axis labels):
default to margin placement with a leader line.  If a referent is
genuinely tiny and the LLM wants in-figure placement, expose an
``anchor="overlay"`` opt-in escape hatch — never make overlay the
default.
