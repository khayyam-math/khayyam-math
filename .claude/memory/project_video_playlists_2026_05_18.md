---
name: 2026-05-18 — YouTube video pipeline + deterministic-template strategy
description: Screen-free video pipeline producing narrated math playlists; fraction + trig templates added; strategy = every figure type becomes deterministic.
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
**Goal:** YouTube playlists, one per education level (elementary →
PhD), each ~12 narrated videos. User's earlier demo video had no
voice.

**Video pipeline** (`/tmp/make_videos.py`, `/tmp/make_trig.py`): per
subtopic — deterministic figure SVG → compose a 1920×1080 frame
(title + embedded figure + footer) → OpenAI `tts-1-hd` narration →
ffmpeg muxes the audio into the mp4. Voice is part of the file, not
screen-captured — that is why the old video was silent (screen
recorders miss browser TTS).

**Done:** Fractions playlist (12) and Trigonometry playlist (12), in
`/tmp/khayyam_videos/{fractions,trigonometry}/`. Remaining levels:
Bachelor (Differential Equations), Master (Theory of Computation),
PhD (Functional Analysis).

**KEY STRATEGY (user-directed):** every figure type must be a
DETERMINISTIC template, committed to the codebase, reused forever.
The Fractions pilot exposed LLM-drawn fractions that were
mathematically WRONG (2/3 pie drawn as 1/2). Fix = build the
template. New templates this session: `fraction` (+`fraction_
operation`, exact via `fractions.Fraction`), `trig` (`unit_circle`,
`triangle`). Library is now ~16 deterministic families, all routed.

**How to apply:** never ship an LLM-drawn figure for a topic that can
be a template — build the template instead. Video figures call the
templates directly (bypassing the LLM path) so every frame is
correct by construction. Suggested next: log which prompts still hit
the LLM-SVG path so the "make-deterministic" backlog is data-driven.
