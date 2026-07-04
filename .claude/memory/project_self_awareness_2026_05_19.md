---
name: 2026-05-19 — system self-awareness features (all 4 shipped)
description: Capability manifest, user "Something's wrong" escape hatch, problem-patterns admin view, and LLM auto-diagnosis — all live.
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
The user wants the app to be "self-aware" — know its own
capabilities, let users get unstuck, and surface collective
problems. Four features, all shipped & live (commit cdecd6a):

1. **Capability manifest** — the chat `SYSTEM_PROMPT` (studio/app.py)
   now lists what the canvas draws well, says to be honest about
   limits, and to treat "it / fix it" as the current canvas.
2. **"Something's wrong" escape hatch** — a button on the canvas
   (studio.html `#fix-btn`); the user describes the problem, it's
   sent as a refinement turn with `flagged=true` → telemetry records
   `intent="flagged"`.
3. **Problem-patterns admin view** — `GET /studio/admin/problems`
   mines telemetry (flagged / errored / high-retry turns, 30 days);
   rendered as a card on the admin page.
4. **Auto-diagnosis** — `POST /studio/admin/diagnose` feeds those
   reports to the LLM, which drafts a diagnosis + suggested fixes.

**Design principle (important — the user agreed):** the system is
self-aware and self-correcting *within bounds + with human review*.
It is NOT autonomously self-modifying — `/admin/diagnose` proposes
TEXT only, never edits code. Do not build autonomous code
self-modification; it is unsafe and the operator must keep control.
