---
name: Narrate the idea, never describe what is visually obvious
description: The user does NOT want the system to say "we see A is connected to B" or "on the left there are three circles" — the eye already does object recognition. Narration / chat replies must lead with the conclusion / reasoning, and use the highlight array to POINT at components rather than telling the user the component exists.
type: feedback
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
The user's exact framing: *"the component should be mentioned and
highlighted, but we don't need to hear that the component is there,
because we can see it; this is not math knowledge, but human object
recognition ability."*

**Forbidden opener patterns** (enforced by
`infra/quality_gate.py:check_no_boilerplate_opener`):
  • `\bwe (?:can )?see\b`
  • `\bhere (?:we (?:can )?see|is|are)\b`
  • `\bon the (?:left|right|top|bottom)\b`
  • `\bthe (?:figure|diagram|image) shows\b`
  • `\bin (?:this |the )?(?:figure|diagram)\b`
  • `\bnote that .{0,30}\bis connected to\b`
  • `\brecall that\b`
  • `\bin mathematics, a\b`
  • `\bfirst,? let'?s\b`
  • `\bas (?:we|you) can see\b`

**Why:** the user explicitly objected ("the system explains correct
but useless redundant things instead of talking about the core
idea") and added the clarification above.  The eye sees the
diagram; the audio must add what the eye cannot extract — the
conclusion, the reasoning, the WHY.

**How to apply:**
  1. In `_EXPRESS_SYSTEM` (`studio/express.py`): the
     "NEVER DESCRIBE WHAT IS VISUALLY OBVIOUS" section with three
     ❌→✓ rewrites + the "if you would start with [boilerplate],
     STOP and restart with the math idea" rule.  Plus: don't
     re-define a term the user's prompt already used.
  2. In `SYSTEM_PROMPT` (`studio/app.py`, chat wrapper): same rule,
     plus tighten to 1-3 sentences with the first sentence stating
     the specific insight.
  3. When asked to point at a component, use the `highlight` array;
     the SPOKEN phrase is for the IDEA about the component, not the
     fact that it exists.

**Caught by the quality gate** as `narration avoids boilerplate
opener` — fails the deploy if a generated narration opens with any
flagged pattern.  Catches regressions even without a re-read of
the system prompts.

**Commits**: `f8e040b` (rule + detector); deploy 2026-05-21,
ECS task def rev 145.
