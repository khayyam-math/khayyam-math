# Email to OpenAI Education contact

> Paste into your mail client. Fill in `[CONTACT NAME]` and any
> personal detail you want to add at the top (how you met, last
> conversation, etc.). Three lengths below — pick the one that
> fits your prior relationship. Send today; align with the public
> launch.

---

## SHORT version (best if you don't have a strong prior relationship)

**Subject:** Khayyam Math — live AI math tutor, open-sourced today

Hi [CONTACT NAME],

Quick note: today I'm open-sourcing **Khayyam Math** — a math tutor
that turns a one-line prompt into a custom voice-narrated SVG figure
in under 10 seconds. It's live at `khayyammath.com` and the code is
MIT-licensed at `github.com/arashkermaniprojects/khayyam-math`.

The architecture is heavily built on OpenAI: GPT-4o for vision-audit,
GPT-4o-mini for generation and prompt classification, `tts-1-hd` for
narration. Multi-tool routing (deterministic templates + Graphviz +
vision-audited LLM-SVG) under one express loop. The whole thing has
been in production on AWS Fargate since May.

A few results that I think might interest OpenAI's education team:

- **+2 percentage points GPT-4o vision-audit pass-rate** from a tiny
  (1.8M parameter) learned re-ranker over our CP-SAT label planner
- A clean **open negative result** on neural layout correction
  (LayoutDM, GNN delta-predictors) — useful for the literature, MIT
  code in the repo
- Phrase-timed voice narration **synced** to visual highlights — the
  feature non-AI tutors keep asking for

I'd love to share details and explore collaboration — whether that's
co-authoring a case study, contributing the eval harness to OpenAI's
public benchmark suite, or just an informal call so your team can
push the architecture further in parallel with me.

Demo video: [YOUTUBE_URL_PLACEHOLDER]
Paper preprint (Zenodo): https://zenodo.org/records/20011107

Happy to send a longer brief if useful. 30-minute call any time
that works for you.

Best,
Arash Kermani Kolankeh
Founder, Khayyam Math
arash_kermani@yahoo.com · khayyammath.com

---

## MEDIUM version (recommended if you've previously discussed AI in education)

**Subject:** Khayyam Math — live math tutor + open negative result on neural layout — would love your team's thoughts

Hi [CONTACT NAME],

Hope you're well. I wanted to share something I'm launching today,
because it touches a few topics I know OpenAI's education team cares
about.

**Khayyam Math** is a live, voice-narrated AI math tutor:
`khayyammath.com`. A learner types "draw a DFA for the language
L = (a|b)\* ending in ab" or "matrix inverse of [[4,7],[2,6]]"
and gets back a custom SVG figure with a phrase-timed audio
walk-through, in 3-15 seconds. Each spoken sentence highlights the
exact element it's describing.

I open-sourced the whole thing today under the MIT licence:
`github.com/arashkermaniprojects/khayyam-math`. The codebase is
substantial (~25k LOC Python, 58 tests, full AWS production
deployment) and the architecture leans on OpenAI throughout:

- GPT-4o vision audit on every generated figure (rasterise → audit
  → up to 3 structured retries)
- GPT-4o-mini for the prompt classifier that picks one of three
  rendering paths (deterministic templates, Graphviz, LLM-SVG)
- GPT-4o-mini as the SVG generator on the LLM-SVG path
- `tts-1-hd` for narration audio (with phrase-time-accurate
  manifest synced to the canvas)
- A self-distillation pipeline that turns GPT-4o-mini into a
  teacher for a self-hosted Qwen2.5-7B + LoRA fallback

A few results from a JAIR-formatted preprint that might interest
you specifically:

1. **Multi-tool routing matters more than any single neural
   improvement.** Once we route graph-shaped prompts to Graphviz
   and matrix-family prompts to deterministic templates, the
   residual handled by LLM-SVG is much smaller --- and the per-figure
   GPT-4o cost drops by an order of magnitude.

2. **An open negative result on neural layout correction**:
   I trained a 6.9M-parameter LayoutDM-style discrete diffusion
   model and a 6.3M-parameter graph-conditioned GNN delta-predictor
   on 22k (broken, fixed) layout pairs. Neither beat the trivial
   no-op baseline. Direct delta regression from source-only features
   is structurally under-determined; we publish this finding openly.

3. **The one neural component that does help**: a tiny (1.8M
   parameter) graph-conditioned binary quality classifier achieves
   71% pairwise win-rate on real (broken, fixed) labels and, used
   as a re-ranker over the existing CP-SAT planner, raises live
   GPT-4o vision-audit pass-rate from 21.3% to 23.3% on a 150-prompt
   benchmark.

4. **Production cost economics for an AI math tutor**: detailed
   per-stack tables in the paper (GPT-4o vs GPT-4o-mini vs
   self-hosted Qwen LoRA, with three TTS backends) so anyone can
   project monthly cost for their own scenario.

There are several places this could plug into OpenAI's education
work, and I'd love to hear which (if any) resonates:

- **Public case study**: I'm comfortable being on the record about
  what does and doesn't work when building on OpenAI's stack for
  education. Happy to co-author with your team.

- **Benchmark contribution**: my 150-prompt vision-audit benchmark
  set + the per-mode pass-rate harness could be useful as a
  community evaluation for any LLM-driven figure-generation work.
  MIT-licensed in the repo.

- **Parallel push**: I want this technology to keep advancing. If
  OpenAI's education team wants to take the architecture and run
  with it (with attribution, but without licence friction --- it's
  MIT), that's exactly what I'm hoping for. I'll keep going on my
  end too.

- **Direct collaboration**: anything from a discovery call to a
  longer-term technical-advisory relationship is on the table.

A few links if you want to look first:

- Live demo: `https://khayyammath.com` (magic-link sign-in, free)
- 60-second video: [YOUTUBE_URL_PLACEHOLDER]
- Code (MIT): `https://github.com/arashkermaniprojects/khayyam-math`
- Paper preprint: `https://zenodo.org/records/20011107`
- Architecture doc:
  `https://github.com/arashkermaniprojects/khayyam-math/blob/main/ARCHITECTURE.md`

Happy to write up a longer brief targeted at whichever angle is
most useful. Either way, I'd love a 30-minute call when you have
time --- whatever week works for you in the next two months.

Best,
Arash Kermani Kolankeh
Founder, Khayyam Math
School of Engineering, Applied Science, and Technology, Canadian
University Dubai
arash_kermani@yahoo.com · khayyammath.com

---

## LONG / TECHNICAL version (if your contact is a researcher or PM
## who'll forward to engineering)

**Subject:** Khayyam Math architecture + dataset + benchmarks for OpenAI's education team

Hi [CONTACT NAME],

Today I'm open-sourcing Khayyam Math (`khayyammath.com`, MIT) — a
production AI math tutor I've been building for the last six months.
I'd like to share what worked, what didn't, and where I see
opportunities for OpenAI's education team to plug in, in parallel
with or independently of my own roadmap.

**What it is.** A learner types a one-line prompt. The system
returns a custom SVG figure synchronised with phrase-timed audio
narration. Live at `khayyammath.com`; AWS Fargate; built for
deployment in classrooms and on individual learners' devices.

**Architecture (one-paragraph).** A prompt-routing layer
dispatches each request to one of three execution paths:
deterministic Python templates for known operation families
(matrix mul / inverse / determinant / transpose / Ax = b /
state-diagram); Graphviz for graph-shaped figures (DFAs, Turing
machines, DAGs, trees, Hasse, Cayley); or the LLM-SVG path
(GPT-4o-mini generation, with a GPT-4o vision-audit retry loop)
for the residual. A CP-SAT (Google OR-Tools) constraint solver
handles label placement after the SVG arrives. A defensive
LaTeX scrubber catches LaTeX commands the LLM leaks into SVG
`<text>` bodies. Phrase-timed audio narration synthesised via
`tts-1-hd` and synchronised to visual highlights via a manifest
computed from WAV header timings.

**OpenAI's role in the stack** (because this might be relevant for
internal awareness):

- GPT-4o vision-audit on every generated figure ($\sim$$0.008/audit)
- GPT-4o-mini for the prompt classifier and the SVG generator
  ($\sim$$0.001-0.002/prompt)
- `tts-1-hd` for narration ($\sim$$0.024/turn at typical lengths)
- Total $\sim$$0.05 per clean turn; the self-distillation pipeline
  is designed to bring this down by an order of magnitude when the
  self-hosted Qwen LoRA matures

**What worked (the positive results we publish):**

1. **Routing is the largest single quality lever.** Once
   graph-shaped figures route to Graphviz, the residual that LLM-SVG
   has to handle becomes much more tractable. We measure
   $\sim$22% of incoming prompts in the Graphviz bucket, $\sim$8%
   in the template bucket, $\sim$70% in the residual.

2. **Vision-audit retry loop converges fast.** In a 20-prompt
   diverse benchmark every accepted figure was produced with zero
   retries; in live production, $\sim$7-14% of turns hit at least
   one audit fail, and 99%+ converge within three attempts.

3. **Learned reranker over CP-SAT.** A 1.8M-parameter graph-
   conditioned binary classifier predicts GPT-4o vision-audit
   verdict at 71% pairwise win-rate; used as a re-ranker over
   CP-SAT candidates it raises live pass-rate from 21.3% to 23.3%
   on a 150-prompt benchmark. Small but real win; deployed.

4. **Self-distillation closes the loop.** Captured user
   (prompt, SVG, narration) triples + GPT-4o-mini teacher data +
   reference-grounded figures extracted from 9 open textbooks
   produce a 5,528-example corpus. v4 LoRA on Qwen2.5-7B trained
   on this corpus beats v3 (128 examples) by 2.05/30 on the
   blind-judge axis, conditional on producing valid output.

**What didn't work (the negative results we publish):**

1. **Direct delta regression from source features alone is
   structurally under-determined.** Neither a 6.3M-param GNN
   delta-predictor nor a 6.9M-param LayoutDM-style discrete-
   diffusion denoiser, trained on 22k (broken, fixed) pairs,
   beats the trivial no-op baseline on overlap-pair-count or OOB
   metrics. The LayoutDM model learns the in-bounds constraint
   (0 OOB / regenerated graph), but otherwise both regress.

2. **Synthetic perturbation data has a quality ceiling.** Many
   destinations are equally valid for any randomly-displaced
   group; the model averages over modes and outputs something
   close to nothing.

3. **The empty-SVG failure mode.** v4 introduced a deterministic
   failure on 2/20 held-out prompts: parseable JSON envelope,
   empty SVG body. Defers production promotion of v4 pending a
   repair-pair retrain (v5 in flight).

**Three places I think OpenAI's team could plug in:**

(a) **As a public case study and benchmark contribution.** My
150-prompt vision-audit benchmark + per-mode pass-rate harness
is MIT and reproducible. Adding it to a public OpenAI benchmark
suite would help the field calibrate vision-audit-driven
generation work in general.

(b) **As an education-track research partner.** I'd be glad to
co-author follow-on work targeting the empty-SVG failure mode,
the LoRA-promotion threshold, the empty-mode failure on Bayes
problems, or the multimodal-judge calibration question. All
data is MIT-licensed.

(c) **As a parallel push.** I want the underlying capability
(custom, voice-narrated math figures at scale, in real
classrooms) to advance regardless of whether I'm the one
building it. The architecture and corpora are released so
OpenAI's team can take any piece and run with it. If there's
appetite for a tighter collaboration, I'm interested; if not,
the open-source release is enough on its own.

If any of this resonates, I'd love a 30-minute exploratory call
in the next two months — whichever week works for you.

Links:
- Live demo: `https://khayyammath.com`
- 60-second video: [YOUTUBE_URL_PLACEHOLDER]
- Code (MIT): `https://github.com/arashkermaniprojects/khayyam-math`
- Paper preprint: `https://zenodo.org/records/20011107`
- Architecture: `https://github.com/arashkermaniprojects/khayyam-math/blob/main/ARCHITECTURE.md`
- 150-prompt benchmark: `https://github.com/arashkermaniprojects/khayyam-math/blob/main/scripts/vision_audit_rerank.py`

Best,
Arash Kermani Kolankeh
Founder, Khayyam Math
School of Engineering, Applied Science, and Technology, Canadian
University Dubai
arash_kermani@yahoo.com · khayyammath.com

---

## Notes on sending

1. **Pick ONE version** (short, medium, long) — don't send all
   three. My recommendation: **medium**. Long is for when the
   contact is technical and likely to forward to engineering;
   short is for cold or near-cold contacts; medium hits the
   right balance for someone you know but aren't deeply close to.

2. **Send today**, after the launch posts are live. The HN /
   Twitter / Reddit posts give your email the credibility of an
   already-launched product; sending before launch makes it feel
   speculative.

3. **Replace `[YOUTUBE_URL_PLACEHOLDER]`** with the real demo
   video URL once recorded + uploaded.

4. **Don't ask for credits or money** in the first email. Ask
   for a call. Money comes later, or not at all — and either
   outcome is fine for what you actually need (visibility +
   reputation).

5. **CC nobody on the first email.** A direct, single-recipient
   email reads as personal; cc'ing six people in OpenAI Edu
   reads as a press release.

6. **Follow-up**: if no response in 10 business days, send one
   short follow-up ("just wanted to make sure this didn't get
   buried — happy to wait if your week is full, but wanted to
   check"). After two follow-ups with no response, drop it.
