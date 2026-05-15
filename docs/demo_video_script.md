# Khayyam Math — demo video script (60-90 seconds)

A single take if you can; cuts only between prompts if you can't.

## Goal

In 60–90 seconds, prove to a reviewer who has never seen Khayyam
Math that the product (1) takes plain English, (2) renders a real
math figure, (3) narrates it in sync with visual highlighting.
Three prompts, three different routing paths, three different
math topics.

## Setup before you record

1. **Browser**: open Chrome / Safari in a clean window at
   `https://khayyammath.com`. Sign in BEFORE you start recording —
   the magic-link flow isn't part of the story.
2. **Viewport**: desktop, full-screen, 1280×800 minimum. Hide
   bookmarks bar (Cmd+Shift+B on Chrome) so the chrome looks clean.
3. **Audio**: system audio capture ON (so the narration WAV plays
   through into the recording). No microphone needed for this version.
4. **Mouse**: optional but nice — enable a mouse-highlighting
   plugin (Chrome has free ones) so cursor moves are visible.
5. **Test the prompts**: run each prompt once before recording so
   the canvas IDs are fresh and you know the timing.

## Recording tools (pick one)

- **OBS Studio** (free, cross-platform) — capture: Display + system
  audio. 1080p 30 fps. Save as MP4 (H.264 + AAC).
- **macOS Screen Recorder**: Cmd+Shift+5 → "Record Entire Screen".
  Make sure system audio capture is on (toggle in the on-screen
  menu) — by default it captures mic only.
- **Loom desktop app**: easiest, but adds a watermark on free tier.

Hit record, wait 1 s of silence, then go.

## The 60-second script

```
TIME    ACTION                                ON-SCREEN / AUDIO

0:00    Title card (optional, simple text):   "Khayyam Math — math figures, generated live"
        Hold 2 s, fade in to the studio.

0:02    Cursor moves to the prompt box.       /studio is loaded, blank canvas

0:03    TYPE PROMPT #1:                       (typing)
        "draw a DFA for the language
         L = (a|b)* ending in ab"
        Press the send-arrow button.

0:08    Canvas pane shows "Emerging…",        Status bar: "Emerging…"
        then ~3 s later, the DFA appears.     4 nodes, 6+ edges, clean Graphviz layout.

0:13    Click ▶ Play narration.               Audio: "This is a DFA that accepts strings
                                              ending in ab…" (~6 s of narration with
                                              q0/q1/q2 highlighted in sync)

0:20    BEFORE narration ends, type           (typing while audio still playing — the
        PROMPT #2 in the box:                  product handles this gracefully; the new
        "matrix inverse of [[4,7],[2,6]]"      prompt pauses the old narration)
        Press send.

0:23    Canvas instantly shows the matrix     Template fast-path — no Emerging delay.
        family figure: A → adj(A) → A^(-1)    A=[[4,7],[2,6]], adj(A)=[[6,-7],[-2,4]],
        all three matrices side by side.       A^(-1) = adj(A)/10

0:28    Click ▶ Play narration.               Audio: "To invert this matrix, first
                                              compute the determinant…" (~10 s,
                                              highlighting each matrix in turn)

0:40    TYPE PROMPT #3:                       (typing)
        "show the Pythagorean theorem
         with a 3-4-5 triangle and squares
         on each side"
        Press send.

0:44    Triangle + 3 colored squares          Right-triangle 3-4-5 visible.
        appear. Areas 9, 16, 25.              "Area = 25" on hypotenuse-square (red).

0:50    Click ▶ Play narration.               Audio: "Here is a 3-4-5 right triangle.
                                              Square the legs: 3 squared is 9…"

0:58    Fade to outro card:                   "Try it free at khayyammath.com"
        Text: "khayyammath.com"               Hold 2 s.

1:00    End.
```

## What you're DEMONSTRATING in 60 seconds

| Beat | Capability shown |
|---|---|
| Prompt 1 → DFA | Graph-shaped figure path (Graphviz), clean state machine |
| Prompt 2 → matrix | Template fast-path, deterministic worked example |
| Prompt 3 → triangle | LLM-SVG path, geometric figure with coloured proof |
| All three narrations | Synced audio + visual highlighting |
| Typing while playing | Polished interaction model (mid-narration prompts work) |
| `khayyammath.com` outro | Memorable CTA |

You're showing a math tutor that **a reviewer cannot dismiss as
"another wrapper around ChatGPT"**. The audio + visual + multi-tool
routing is the differentiation.

## If you only have 30 seconds

Cut prompt #2 (the matrix). Keep #1 (DFA) and #3 (Pythagoras).
Two figures, two narrations, ~28 seconds.

## If you want to go to 90 seconds

Add a 4th prompt at the end showing a follow-up refinement:
*"highlight q2 in red"* — demonstrates the refinement-context
flow. The DFA from earlier should still be on screen (or pinned).

## After recording

1. Trim dead space at start/end with QuickTime / Premiere / DaVinci
   Resolve / `ffmpeg`
2. Optional: a 2 s title card at the start + a 2 s outro card with
   the URL. Minimal — black background, single line of text.
3. **Export** as 1080p MP4 (H.264 + AAC). Target file size <50 MB.
4. **Upload to YouTube** as **Unlisted**:
   - Title: `Khayyam Math — voice-narrated math figures, generated live`
   - Description: one paragraph + `https://khayyammath.com` link
   - Set visibility to **Unlisted**
   - Disable comments (cleaner for reviewers)
5. **Copy the link** (the `youtu.be/...` short form is fine)
6. **Paste the link** into all three application drafts (see the
   `Demo video` field added to each .md).

## What NOT to do

- ❌ Add background music. The narration IS the soundtrack; music
   competes with it.
- ❌ Use a typing-animation tool. Real typing keeps the demo honest.
- ❌ Stage every prompt to succeed silently. If something has a
   2-second emerge delay, leave it in — it's an honest measurement.
- ❌ Voiceover yourself over the demo. The product's narration is
   the asset; don't drown it out.
- ❌ Add captions / subtitles. The text on the canvas already speaks
   for itself.

The product's own narration is the differentiator. Trust it.
