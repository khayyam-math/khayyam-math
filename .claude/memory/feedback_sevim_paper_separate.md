---
name: sevim-paper is a SEPARATE project — do not edit as part of Khayyam Math work
description: The CGF/EUROGRAPHICS SeVim paper at arashkermaniprojects/sevim-paper is a different submission; the only valid paper repo for Khayyam Math is arashkermaniprojects/khayyam-math-paper (the JAIR draft).
type: feedback
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
The user pushed back when I edited `arashkermaniprojects/sevim-paper`
during a Khayyam Math session — that repo is a sibling CGF/
EUROGRAPHICS submission about the deterministic SeVim system, not
about Khayyam Math.

**Why:** the JAIR Khayyam-Math paper *cites* the CGF SeVim paper as
prior work, but they are independent submissions to independent
venues with independent author lists (SeVim is multi-author at this
date; Khayyam Math is sole-author). Cross-editing the two creates
inconsistent provenance and risks reviewer-side confusion.

**How to apply:** when working on Khayyam Math, only touch the JAIR
paper at `/home/ara/Documents/Programming/sevim_plugin/paper/
sevim_studio_jair/` (remote `arashkermaniprojects/khayyam-math-paper`).
Do not open, edit, or push to:

  - `/home/ara/Documents/Programming/sevim-paper/`
    (remote `arashkermaniprojects/sevim-paper`)
  - `/home/ara/Documents/Programming/sevim_math/paper/`
  - `/home/ara/Documents/Programming/agentic_systems/2D-interactive-paper-SVGGPT/`

These repos exist on disk but belong to other projects. If a change
genuinely belongs in one of them, raise it with the user first.
