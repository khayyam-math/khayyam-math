# OpenAI Startup Program — Application draft

Submit at: `openai.com/forms/startup-program`

> Notes for filing:
> • Solo founder today. If you bring your brother in as Co-Founder/CTO
>   before submission, swap the paragraphs marked **[TEAM]**.
> • Public live product: `https://khayyammath.com` — link this in
>   every "URL" field on the form.
> • Live screenshot gallery is now on the landing page (after the deploy
>   currently in flight finishes ~today).

---

## Company / product

**Company name:** Khayyam Math
**Website:** https://khayyammath.com
**Founder:** Arash Kermani, Khayyam Math (UAE-registered)
**Stage:** Live in production, single founder, pre-revenue, seeking
distribution + credits to validate niche.
**Location:** United Arab Emirates (IP registered locally)
**Live URL:** https://khayyammath.com (try `draw a DFA for L = (a|b)*`
or `matrix inverse of [[4,7],[2,6]]`)
**Demo video (60 s):** *TBD — replace with YouTube unlisted URL after recording.
See `docs/demo_video_script.md` for the script.*

## One-paragraph description

Khayyam Math turns a one-line math prompt ("show the unit circle with
sin and cos at 30°, 45°, 60°", "draw a DFA for L = (a|b)\* ending in
ab", "matrix inverse of [[4,7],[2,6]]") into a custom SVG figure with
phrase-timed voice narration, in 3-15 seconds. The model emits both
the figure AND a synchronised narration script in one structured
response; a built-in vision-review loop catches incorrect claims
before they reach the learner. Live on khayyammath.com, registered as
intellectual property in the UAE.

## How we use OpenAI

Khayyam Math is built **substantially on OpenAI**:

- **gpt-4o** for vision audit (rasterise candidate SVG → PNG → ask
  gpt-4o to verify every narration claim against the rendered figure)
- **gpt-4o-mini** for SVG + narration generation in the main express
  path, AND for the prompt classifier that routes graph-shaped
  prompts to a deterministic Graphviz fallback
- **gpt-4o-mini** for the structural critic that re-issues retry
  prompts when overlap / OOB / hallucinated highlight ids are detected
- **OpenAI tts-1-hd** for some narration; piper voices for the rest
- A custom fine-tune corpus (3,395 examples, gpt-4o-mini teacher,
  inspector-filter ON) was generated against the OpenAI fine-tune
  endpoint earlier this year

Approximate spend before credits: ~$200–$400/month on inference at
current usage, scaling roughly with active users.

## Why we need credits

We've validated the product end-to-end. The next step is a 3-month
push to (a) ship the JAIR paper, (b) onboard 20 paying users in our
chosen niche (UAE/GCC high-school math + Indian competitive exam
prep), (c) publish a benchmark for math-figure-generation quality.
Credits would let us:

- Run a larger inspector-filtered teacher corpus (~10K examples)
  for the next-gen express-loop fine-tune
- A/B test gpt-4o vs gpt-4o-mini on every step of the pipeline
  without burning user-acquisition runway
- Validate the vision audit's pass-rate at higher prompt volume
  (we already have a 71% pairwise reranker score from a separate
  evaluation experiment — would extend that to 1,000-prompt scale)

## Public-facing case study

Happy to co-author. We're a non-trivial integration: SVG generation
+ vision audit + multi-tool routing (deterministic templates +
Graphviz + LLM-SVG) + phrase-timed audio. The architecture is in
draft for JAIR (Journal of Artificial Intelligence Research) and
will be on Zenodo as a preprint by end of this month.

## **[TEAM]** Team (single-founder version)

**Arash Kermani** — Founder, CEO, technical lead. Built the entire
production stack (FastAPI + React/HTML5 + ECS Fargate + Postgres +
S3, plus the prompt-→-SVG architecture itself). Background in
applied ML / math tutoring. Sole author of the registered
intellectual property in the UAE.

## **[TEAM]** Team (with co-founder version)

**Arash Kermani** — Founder/CEO. Built the production stack and
the prompt-→-SVG architecture. Sole author of the registered IP.

**[Brother's name]** — Co-founder/CTO. Eight+ years scaling
[his IT company name] in the UAE; brings cloud-ops, customer-
delivery, and team-leadership experience that complements Arash's
research/product focus.

## Traction snapshot

- Live on `khayyammath.com` since May 2026
- ECS Fargate, AWS us-east-1, account REDACTED
- Built-in telemetry → ~XX active prompts/day [fill from admin page]
- 4 published rebuild iterations of the SVG generator with measured
  quality improvement at each step (JAIR paper)
- 23 unit tests on the Graphviz route, full Playwright UX-audit
  suite, A/B benchmark harness against gpt-4o vision audit

## Roadmap (next 90 days)

1. Publish JAIR paper + Zenodo preprint
2. Niche validation: 20 paying users in UAE/GCC math-tutoring or
   Indian competitive-exam-prep market
3. Open-source the Graphviz route + scene-graph parser
4. Submit NeurIPS workshop paper on the layered architecture

## Asks

- **Tier 1 OpenAI Startup credits** ($2,500 self-serve) to validate the
  scale-up plan; we'll apply for Tier 2+ via a VC partner referral
  once we onboard with Hub71 (Abu Dhabi) or a comparable accelerator
- Inclusion in the public Startup Program directory (distribution)
- Optional: an introduction to OpenAI's education research team
  if there's interest in the express-loop + vision-audit architecture
