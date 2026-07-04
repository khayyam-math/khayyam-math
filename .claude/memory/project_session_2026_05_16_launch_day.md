---
name: 2026-05-16 — Khayyam Math launch-day deliverables done, awaiting user upload
description: Paper v0.7 + silent demo video + OpenAI-education email all produced; repos renamed and remotes updated. Repo still private — user makes it public, records audio, uploads video, posts launch themselves.
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
Launch-day execution session. Builds on
project_launch_ready_2026_05_15.md. User instruction:
"implement the plan, record the videos, inspect them, I will upload
them. the launch day is today. but I will make the repo public
myself." Repos already renamed by the user before this session.

## State as of 2026-05-16

**Code repo** `khayyam-math` (remote
git@github.com:arashkermaniprojects/khayyam-math.git):
- HEAD `33369ae` "launch-day: silent demo video recorder + OpenAI
  education contact email draft"
- Prior launch commits: `fd3f5fd` launch materials, `9e74c2b`
  production fixes, `11c1c09` launch prep, `1b99d0d` neural layout.

**Paper repo** `khayyam-math-paper` (remote
git@github.com:arashkermaniprojects/khayyam-math-paper.git):
- HEAD `8c4010c` "v0.7: Khayyam Math rebrand + multi-tool routing +
  neural-layout negative result". Compiles clean (~30pp, zero
  warnings). Title now mentions multi-tool routing, vision-audited
  generation, and the open negative result on neural layout.

## What was produced this session

- **`scripts/record_demo_video.py`** (committed) — Playwright
  captures the studio at 127.0.0.1:8765 as silent webm, transcodes
  to mp4 via ffmpeg. Three prompts: DFA (Graphviz route) → matrix
  inverse (template) → unit circle (LLM-SVG). Note: third prompt was
  deliberately switched from Pythagoras to unit-circle because the
  Pythagoras prompt triggers an LLM-emission bug (see below).
- **Demo video** at `/tmp/khayyam_demo/demo.mp4` — 2.87 MB, ~1:54,
  SILENT (Playwright does not capture system audio). Also
  demo.webm + frame_01..07.png in that dir. User will add
  voice-over / re-record with OBS, then upload to YouTube unlisted.
- **`docs/email_openai_education_contact.md`** (committed) — outreach
  email to user's OpenAI education-sector contact, 3 versions
  (short/medium/long) + sending notes. Has `[CONTACT NAME]` and
  `[YOUTUBE_URL_PLACEHOLDER]` to fill.

## Known unfixed issue (LLM emission, not a code bug)

The Pythagoras prompt occasionally renders wrong constants —
hypotenuse labelled `9` instead of `5`, and `Area = 25` duplicated
to `Area Area = 25`. The vision audit missed it that run. Defensive
post-processors can't fix wrong arithmetic. Two real fixes if user
wants it later: (a) strengthen the vision-audit inspector prompt to
check Pythagorean constants, or (b) add a deterministic `pythagoras`
template so this prompt class skips the LLM-SVG path.

## Placeholders still needing the real YouTube URL

Once the video is uploaded, replace in: `README.md` (`<YOUTUBE_URL>`),
`docs/LAUNCH_POSTS.md` (`<YOUTUBE_URL>` x several),
`docs/email_openai_education_contact.md` (`[YOUTUBE_URL_PLACEHOLDER]`),
then commit + push. Claude can do this step once user shares the URL.

## Left for the user (their explicit scope)

Make `khayyam-math` repo Public; add audio to / re-record the demo;
upload to YouTube unlisted; post HN/Twitter/Reddit from
`docs/LAUNCH_POSTS.md`; send the OpenAI email; refresh the Zenodo
preprint with the v0.7 PDF.

## Uncommitted working-tree leftovers (intentionally untouched)

`infra/cdk.context.json`, `scripts/generate_reference_corpus.py`
modified; untracked `scripts/{capture_screenshots,download_textbooks,
expand_prompt_pool,expand_prompt_pool_v5,expanded_prompts,
extract_textbook_figures}` and `uae_ip_registration/` — prior-session
leftovers, not part of launch work.
