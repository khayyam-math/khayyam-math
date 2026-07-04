---
name: 2026-05-15 — Khayyam Math launch-ready (repo private, awaiting trigger)
description: Repo + docs + application packs + launch posts all prepared for a coordinated open-source launch. Repo intentionally stays private until user records the demo video and chooses a launch date. Switched license to MIT, renamed plan to khayyam-math.
type: project
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
The user pivoted away from chasing OpenAI/Anthropic startup-program
credits (the OpenAI page redesign hid the self-serve Apply CTA, and
both top-tier programs require VC partner referrals that solo founder
in UAE doesn't have). New strategy: **open-source launch for fame +
scientific reputation + community contributions**, modelled after
ruvnet/RuView's playbook.

## Strategic decisions made on 2026-05-15

- **License**: switched from CC BY-NC 4.0 → **MIT** (maximises adoption
  and GitHub star momentum; user can later wrap commercial extensions
  if needed without violating MIT)
- **Repo rename target**: `sevim-plugin` → `khayyam-math` (LAUNCH-DAY
  action; redirect from old URL is automatic on GitHub)
- **Demo video before launch**: 60 s screen-capture per
  `docs/demo_video_script.md`, three prompts demonstrating all three
  routing paths (template / Graphviz / LLM-SVG)
- **Repo stays PRIVATE until coordinated launch** — user explicit:
  "for now the repo stays private till we have planned everything"

## What's on disk + pushed (origin/main, private repo)

Three commits landed on 2026-05-15:

```
fd3f5fd  launch materials: HN/Twitter/Reddit drafts + application packs
9e74c2b  production fixes: LaTeX scrubber + canvas-refresh + typography
<sha>    launch prep: MIT + README + ARCH + CONTRIBUTING + CoC + .github
```

**Top-level files for launch:**

- `LICENSE` — MIT (was CC BY-NC 4.0)
- `NOTICE` — citation now requested rather than required
- `CITATION.cff` — license field flipped to MIT
- `README.md` — launch-quality narrative; badges (MIT, Python, tests,
  live demo, Zenodo DOI); hero screenshot (Pythagoras); 4-figure
  gallery; three-route table; quickstart; honest negative-result
  mention; contributor CTA
- `ARCHITECTURE.md` — full system map with ASCII request lifecycle
  diagram + subsystem walkthrough + file-level capability table
- `CONTRIBUTING.md` — dev setup; concrete "what we want help with"
  table; code style; PR flow; what we WON'T accept
- `CODE_OF_CONDUCT.md` — Contributor Covenant 2.1
- `.github/ISSUE_TEMPLATE/{bug_report,template_idea,architecture_change}.md`
- `.github/PULL_REQUEST_TEMPLATE.md`
- `service/static/screenshots/landing_*.png` — 4 real product
  screenshots (matrix mul, DFA, Pythagoras, unit circle) used in
  README + landing page

**Launch-day materials (docs/):**

- `docs/LAUNCH_POSTS.md` — HN title + first comment + 10-tweet
  thread + Reddit posts (r/MachineLearning, r/LocalLLaMA,
  r/educationaltech) + LinkedIn copy + blog outline + DM template +
  pre-launch checklist + 48 h post-launch triage plan + 2-week
  follow-up (awesome-lists, Hub71, NeurIPS MathAI workshop)
- `docs/SUBMIT_INSTRUCTIONS.md` — OpenAI/Anthropic/Hub71
  application paths
- `docs/application_{openai,anthropic,hub71}_startup.md` — three
  ready-to-paste application drafts with single-founder +
  co-founder team paragraph variants
- `docs/demo_video_script.md` — 60-second screen-capture script
  (DFA → matrix → Pythagoras + outro)
- `docs/pitch_deck_hub71.pdf` — 12-slide 16:9 Hub71 pitch deck

## Production fixes shipped on 2026-05-14 to 2026-05-15

(Already deployed as ECS rev 92-96; details in commit `9e74c2b`.)

- **LaTeX scrubber** (`strip_latex_in_svg_text` in
  `studio/express.py`) — defensive post-processor that converts ~80
  LaTeX patterns (`\frac{}{}`, `\times`, `\theta`, etc.) to Unicode
  before the SVG reaches the canvas. Fixed the `\( \frac{1}{2}
  \times h \)` bug on the trapezoid prompt.
- **Canvas-refresh follow-up classifier** (`_looks_like_followup`
  in `studio/app.py`) — current canvas is now only attached as
  REFINEMENT MODE context when the prompt actually looks like a
  follow-up. Pinned canvases unaffected. Fixed the "Venn diagram
  overlaid on Pythagoras" contamination bug.
- **Matrix template typography**: `A^(-1)` → `A⁻¹`, `A * A^(-1) = I`
  → `A · A⁻¹ = I`, `5x5` → `5×5` (HTML entities so SVG renders the
  correct Unicode superscripts).
- **Graphviz route node/edge counter** in `service/canvas.py` —
  parses `<g class="node">` / `<g class="edge">` from the SVG so
  the canvas header shows "4 nodes / 8 edges" instead of "0/0".
- **Landing-page hero + gallery** (`service/static/landing.html`) —
  replaced hand-drawn inline-SVG triangle with the real
  Pythagorean-theorem screenshot; added 4-figure gallery below.
- **Chat-snapshot responsive sizing** (`studio/static/studio.html`,
  `takeSnapshotForThisTurn`) — clones the live SVG, strips fixed
  width/height, synthesises viewBox if missing, adds
  preserveAspectRatio. Fixes mobile chat bubble overflow.

## What's left for the user to do

| | Step | When |
|---|---|---|
| 1 | Plan the launch DATE | Whenever ready |
| 2 | Record 60-second demo video per `docs/demo_video_script.md` | ~30 min |
| 3 | Upload to YouTube **Unlisted** | ~5 min |
| 4 | Tell Claude the YouTube URL; Claude plugs it into README + LAUNCH_POSTS + applications and pushes | ~5 min Claude |
| 5 | LAUNCH DAY: rename repo → flip Public → paste posts to HN/Twitter/Reddit (drafts already in `docs/LAUNCH_POSTS.md`) | ~30 min coordinated |

## Why not OpenAI/Anthropic startup credits anymore

- openai.com/startups no longer has a visible "Apply Now" CTA (page
  was redesigned; only "Join the community" + "Start building" CTAs
  visible)
- Anthropic's higher tiers require a VC partner referral
- Self-serve tiers ($2.5K / $1-5K) aren't worth the credibility hit
  vs the open-source launch trajectory
- User pivoted to: open-source first → fame + community →
  Hub71/AIED/NeurIPS submissions land easier with traction

## Why not Hub71 yet either

- Cohort 20 deadline is **2 August 2026** (per docs search), launch
  could go first and provide traction signal for the Hub71 application
- `docs/application_hub71.md` and `docs/pitch_deck_hub71.pdf` are
  ready when user wants to file

## Resume hint for future sessions

If user says "let's launch": pull `docs/LAUNCH_POSTS.md` + check that
the YouTube placeholder `<YOUTUBE_URL>` has been replaced with a real
URL. If not, ask user for the URL first. Then walk through the
pre-launch checklist in that file.

If user says "let's apply to Hub71 now": pull
`docs/application_hub71.md` + `docs/pitch_deck_hub71.pdf`. Tell user
to fill any `XX active prompts/day` placeholders before submitting.
