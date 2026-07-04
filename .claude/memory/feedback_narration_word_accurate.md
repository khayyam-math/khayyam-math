---
name: Voice narration must be word-accurate, not estimated or rate-adjusted
description: User's preference for narration timing — visuals adapt to natural speech, not the other way around.
type: feedback
originSessionId: b750514c-b82c-4fee-9ab5-0f4710523a32
---
Voice narration in Sevim must keep its natural cadence; the visuals
adapt to the speech timing, never the reverse.  Highlight scheduling
must come from real audio durations, not character-count estimates.

**Why:** User explicitly stated — quote: *"this should be word-accurate.
we don't want the narration to be faster or slower."*  Speeding the
audio to fit a pre-computed visual schedule, or estimating word timings
from character counts, both produce drift the listener notices.

**How to apply:** Synthesise each narration phrase to its own WAV;
read the exact duration from the WAV header; build the highlight
schedule from those measured durations (with a small fixed
``phrase_gap_s`` between phrases).  This is what
``sevim/narrate.py:synthesize_script`` does.  If you ever upgrade to
word-level (sub-phrase) granularity, use real alignment from the TTS
engine (piper exposes phoneme alignment) — never an estimator.

Backend choice: piper-tts at ``~/.local/share/sevim/voices/`` is the
chosen local engine (user OK'd it over espeak-ng for quality).
