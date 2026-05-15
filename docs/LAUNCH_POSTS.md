# Launch-day posts

Coordinated copy for Hacker News, Twitter/X, Reddit, LinkedIn, and
the personal blog. Tone: confident but honest. Lead with the bold
premise + the live demo + the open-source link.

## Pre-launch checklist (run through this in order)

- [ ] Repo renamed `sevim-plugin` → `khayyam-math` on GitHub (Settings → Repository name)
- [ ] Repo set to **Public** (Settings → Danger Zone → Change visibility)
- [ ] LICENSE = MIT ✓ (already done)
- [ ] README, ARCHITECTURE, CONTRIBUTING, CODE_OF_CONDUCT all committed ✓
- [ ] `.github/` templates committed ✓
- [ ] Demo video recorded + uploaded to YouTube as **unlisted** (script: `docs/demo_video_script.md`)
- [ ] README image / video links use the published GitHub URLs (no localhost paths)
- [ ] Live site is healthy: `curl https://khayyammath.com/health` → 200
- [ ] Zenodo preprint up: <https://zenodo.org/records/20011107>
- [ ] Topics added to the GitHub repo: `ai`, `math`, `education`,
      `svg`, `llm`, `openai`, `vision-audit`, `tutoring`, `graphviz`
- [ ] Pinned the README and ARCHITECTURE in the repo

## 1. Hacker News submission

**Title** (HN allows ~80 chars):

```
Show HN: Khayyam Math – voice-narrated math figures, generated live (MIT)
```

**URL field**: `https://github.com/arashkermaniprojects/khayyam-math`

**First comment** (post this immediately after submission so the
context lands above the fold):

```
Author here. Khayyam Math turns a one-line math prompt into a custom
SVG figure with phrase-timed audio narration. Three routing paths
under one roof: deterministic templates for matrix ops, Graphviz for
graph-shaped figures (DFAs, Turing machines, Hasse diagrams), and a
vision-audited LLM-SVG path for everything else.

Live demo: https://khayyammath.com (magic-link signin, free)
60-second video: <YOUTUBE_URL>
Architecture: github.com/arashkermaniprojects/khayyam-math/blob/main/ARCHITECTURE.md
Paper: https://zenodo.org/records/20011107

A few non-obvious bits:

1. The vision-audit retry loop catches the LLM's most common failure:
   narration claims that don't match the rendered figure. gpt-4o looks
   at the rasterised PNG and issues structured fixes.

2. The neural-layout subsystem (studio/neural_layout/) is a complete
   negative result published openly: I trained LayoutDM-style discrete
   diffusion and a graph-conditioned GNN delta-predictor on 22 K
   (broken, fixed) pairs; neither beat no-op. The trained layout-
   quality scorer DID land a measurable +2 pp gpt-4o pass-rate when
   used as a re-ranker over CP-SAT candidates. All training data +
   benchmark code in the repo.

3. The Graphviz route is the highest-leverage addition; it took the
   "draw a DFA" class of prompts from 30-90 s with retries down to
   sub-10 s clean. Multi-tool routing > one-LLM-to-rule-them-all.

Happy to answer questions about the architecture, the negative
result, or the deploy story (AWS Fargate + CDK, all in the repo).
```

**Timing**: Tuesday-Thursday, 8-10 AM Eastern. Avoid weekends.

## 2. Twitter / X thread (10 tweets)

> Tweet 1 (HOOK):
```
I built an open-source math tutor where the figures narrate themselves
as they appear.

Type "draw a DFA for L = (a|b)* ending in ab" → state machine
appears in ~5 seconds with synchronised voice + visual highlighting.

Live: khayyammath.com
Code (MIT): github.com/arashkermaniprojects/khayyam-math
🧵
```

> Tweet 2 (demo embed):
```
60-second demo. Three prompts, three different routing paths under
one architecture:

→ matrix mul → deterministic template
→ DFA → Graphviz layout engine
→ Pythagorean theorem → LLM with vision audit

<YOUTUBE_URL>
```

> Tweet 3 (the bold framing):
```
The standard "AI generates an SVG figure" approach is: ask GPT-4
for SVG, hope it isn't wrong.

That breaks on overlap, off-canvas elements, hallucinated labels,
LaTeX leaking into <text>, etc.

Khayyam Math routes per prompt to whichever tool actually solves
that shape of figure. The integration is the moat.
```

> Tweet 4 (architecture diagram screenshot):
```
The express loop runs:

prompt → classify → (template | Graphviz | LLM-SVG) →
  LaTeX scrub → CP-SAT layout planner → vision audit →
  fail? retry. pass? narrate.

Whole architecture diagram in ARCHITECTURE.md
```

> Tweet 5 (the vision-audit retry):
```
For the LLM-SVG path: every candidate figure is rasterised to PNG
and sent to gpt-4o for a SECOND opinion against the narration script.

"You said q2 is the accepting state but it's not highlighted in the
SVG. Fix it."

Up to 3 correction rounds. The product the user sees is the figure
that passed.
```

> Tweet 6 (the negative result, honestly):
```
Open negative result: I trained LayoutDM-style discrete diffusion
and a 6.3M-param GNN delta-predictor on 22K (broken, fixed) pairs.

NEITHER beat the trivial no-op baseline.

One-shot delta regression from source features alone is structurally
ill-posed. The CP-SAT planner wins. Worth knowing.
```

> Tweet 7 (the one neural win):
```
What DID work: a 1.8M-param graph-conditioned binary classifier as
a CP-SAT candidate re-ranker. +2 pp gpt-4o pass-rate on 150 prompts.

Small but real. Trained model + training data + benchmark code
all in the repo (MIT).
```

> Tweet 8 (the Graphviz route):
```
The highest-leverage feature isn't a model. It's the Graphviz
route.

"draw a DFA" used to take 30-90 s with LLM retries. Now the LLM
emits DOT and `dot -Tsvg` produces a layout with zero overlap by
construction. 3-9 seconds, clean.

Mature tools > novel ML, often.
```

> Tweet 9 (community CTA):
```
Things I want contributors for:

→ More deterministic templates (Fourier, slope fields, vec addition)
→ Localisation (Arabic, Hindi, French narration)
→ A TikZ → SVG route for publication-quality figures
→ A real human-labelled scorer corpus

Issues: github.com/arashkermaniprojects/khayyam-math/issues
```

> Tweet 10 (close + signal):
```
Whole thing is MIT-licensed. UAE-built. Solo author + brother
joining as co-founder.

Star the repo if you'd like to see more of this:
github.com/arashkermaniprojects/khayyam-math

— Arash @arash_kermani
```

## 3. Reddit r/MachineLearning post

**Title**: `[P] Khayyam Math: open-source AI tutor with voice-narrated SVG figures + a negative result on neural layout`

**Body**: same as the HN first-comment, but with a section break:

```
## What it does

[paragraph from the HN comment]

## Why it might interest r/ML

[the negative-result section + the +2 pp scorer win]

## Open-source

MIT, all on GitHub: <repo URL>. Architecture doc + paper preprint
linked from the README.
```

**Flair**: `Project`

## 4. Reddit r/LocalLLaMA post

**Title**: `Khayyam Math: multi-tool LLM routing (templates + Graphviz + LLM-SVG) for math figure generation`

**Body**: lead with the multi-tool routing angle (this audience cares
about that more than the education angle). Mention the local Qwen v4
LoRA path (currently down on cost, but the inference contract is
model-agnostic).

## 5. Reddit r/educationaltech (or r/LearnMath)

**Title**: `Open-source AI tutor that generates voice-narrated math figures from one-line prompts — free, MIT-licensed`

**Body**: education-first framing. Less ML, more "this is what a
student / tutor / teacher can do with it". Emphasise the live demo +
that it's free.

## 6. LinkedIn post

```
🎓 Open-sourcing Khayyam Math.

After 6 months of building, I'm releasing Khayyam Math — a math
tutor that generates custom voice-narrated figures from one-line
prompts — as MIT-licensed open source.

Why open-source it? Two reasons:

1) Math education is too important to be closed-source. Any teacher,
   tutoring centre, or learner should be able to self-host this.

2) The architecture (vision-audit retry + multi-tool routing +
   CP-SAT layout planner) is the actual moat, not the underlying
   LLM. Sharing it accelerates the field.

→ Live demo: https://khayyammath.com
→ Code (MIT): https://github.com/arashkermaniprojects/khayyam-math
→ 60-second demo: <YOUTUBE_URL>
→ Paper: https://zenodo.org/records/20011107

UAE-built, registered IP, but free to use, modify, fork, and
extend. I want contributors. The CONTRIBUTING.md has a list of
concrete asks if you'd like to help.

#OpenSource #AI #EducationTechnology #UAE #MachineLearning
```

## 7. Personal blog post (longer, ~1500 words)

Title: `What I learned building Khayyam Math`

Outline:

1. The problem (math figures, why they're hard)
2. The bold framing (most "AI generates SVG" products are wrappers)
3. The architecture I converged on (multi-tool routing)
4. The vision-audit retry trick (most non-obvious win)
5. The neural-layout negative result (honest about what didn't work)
6. The one neural component that did work (the scorer)
7. Lessons (mature tools > new models; the integration is the moat)
8. What's next + how to contribute

Post on Substack / Medium / personal blog. Link from all the social
posts.

## 8. Reach-out list (DMs / emails to send launch day)

People who would likely retweet / boost if asked politely:

- **Researchers**: anyone with a public interest in AI + education
  (Cynthia Breazeal, John Stamper, Neil Heffernan, the
  Khanmigo team)
- **ML influencers**: Andrej Karpathy, Sebastian Raschka, Yann LeCun
  (low chance, high payoff)
- **AI-in-Ed founders**: Sal Khan team, Brilliant founders, Wolfram
- **MCP / Claude Code community**: ruvnet (RuView author), the
  Claude Code dev rels team
- **UAE tech ecosystem**: Hub71 founder relations, MBZUAI faculty,
  AI71

DM template:

```
Hi [name], I'm Arash. I just open-sourced an AI math tutor that
generates voice-narrated figures from one-line prompts. Live at
khayyammath.com, code (MIT) at github.com/arashkermaniprojects/khayyam-math.

Two reasons I think it might interest you: [reason 1, reason 2].

If you have 60 seconds, the demo video is here: <YOUTUBE_URL>.

Happy to chat if any of this resonates. No ask.
```

## After launch (first 48 hours)

- [ ] **Triage every issue / PR / comment within 24 h.** Don't ghost.
- [ ] Reply to ALL HN comments, even hostile ones. Be specific +
      honest.
- [ ] Pin a "what's next" comment on the HN post 12 h after launch
      with a roadmap based on the questions you got.
- [ ] Update the README with any FAQ that came up.
- [ ] On day 2: a follow-up tweet showing one community PR / issue
      that landed, with credit to the contributor.

## After launch (first 2 weeks)

- [ ] Write a follow-up blog post on lessons from the launch
- [ ] Submit to awesome-lists:
  - `awesome-llm-apps`
  - `awesome-ai-tutoring`
  - `awesome-svg`
  - `awesome-education-ai`
- [ ] Apply to Hub71 with the launch as proof-of-traction
- [ ] Reach out to AIED 2026 / NeurIPS MathAI workshop with the
      paper + open-source repo as additional credibility
