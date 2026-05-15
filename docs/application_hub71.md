# Hub71+ AI — Application draft

**Submit at**: https://www.hub71.com/program/access-programme/apply
**Cohort 20 deadline**: 2 August 2026
**Programme start**: February 2027
**Decision timeline**: feedback within 3 months of applying

> **What Hub71 actually asks for** (per current program page):
> 1. Fill the online form (problem / solution / product / business model)
> 2. **Upload a PDF pitch deck** covering: problem + solution + value
>    proposition + business model; competition + market + traction +
>    funds raised; founders / founding team; plans for Hub71 + Abu Dhabi
> 3. At least one founder must commit to relocating long-term to Abu Dhabi
>    (you already hold UAE IP registration — this should be straightforward)
>
> **Awards if accepted**: AED 250,000 flexible incentives (services,
> credits, AWS / Nvidia / Google for Startups, MBZUAI tie-ins) PLUS
> AED 250,000 cash in exchange for equity via a SAFE note.

---

## Online-form text (paste into the corresponding fields)

### Company name
Khayyam Math

### Website
https://khayyammath.com

### Demo video
*TBD — replace with YouTube unlisted URL after recording. See
`docs/demo_video_script.md` for the script.*

### Headquarters
United Arab Emirates (IP registered locally)

### Sector
EdTech / Applied AI / Generative AI for Education

### Stage
Live in production, pre-revenue, single founder pursuing niche validation
(UAE/GCC + India competitive-exam-prep math tutoring)

### One-paragraph product description (use this everywhere)
Khayyam Math turns a plain-English math question — *"draw a DFA for
L = (a|b)\* ending in ab"*, *"matrix inverse of [[4,7],[2,6]]"*,
*"show the Pythagorean theorem with a 3-4-5 triangle and squares on
each side"* — into a custom SVG figure with phrase-timed voice
narration in 3–15 seconds. A built-in vision-review loop catches
incorrect claims before they reach the learner. Live on
`khayyammath.com`, registered as intellectual property in the UAE,
running on AWS Fargate (us-east-1).

### Problem
Math figures are the highest-friction part of math learning.
Textbooks have them, teachers draw them on whiteboards — but the
learner who studies alone (millions of high-schoolers preparing
for EmSAT, IGCSE, IB, JEE) only gets the figure their textbook
chose, not the one for the specific problem they're stuck on.
Existing AI tutors (Khanmigo, ChatGPT, etc.) explain math in text
or talk through verbal descriptions; very few generate accurate,
phrase-synchronised visual diagrams on demand. The ones that do
struggle with overlap, off-canvas elements, and incorrect labels.

### Solution
A custom express-loop architecture that combines three layout
strategies in priority order: (1) deterministic Python templates
for known operation families (matrix multiplication, inverse,
determinant, transpose, system of equations) — sub-second, perfect
output; (2) a Graphviz route for graph-shaped figures (state
machines, Turing machines, DAGs, trees, Hasse diagrams) —
sub-10-second renders with deterministic, no-overlap layouts;
(3) a fully LLM-driven path with vision-audit retry for everything
else. Each generated figure is paired with a phrase-timed audio
narration script that highlights the relevant SVG element while
each phrase plays. The vision audit checks every narration claim
against the rendered figure before showing it to the learner.

### Business model
Three revenue paths under active validation:
1. **Direct B2C** — subscription for high-school learners
   (target $4–8 / month per learner) in the UAE/GCC + Indian
   competitive-exam markets
2. **B2B for tutoring centres** — per-seat licensing to math
   tutoring centres (target $20–40 / seat / month, AED equivalents).
   UAE has ~3,000 tutoring centres; competitor share is fragmented.
3. **API for textbook publishers + ed-tech platforms** — generate
   figures for textbook companion apps + LMSes (target $0.05–0.15
   per figure for higher-volume customers)

### Plans for Hub71 + Abu Dhabi
1. **Relocate** primary operations to Abu Dhabi (one founder
   already UAE-based; UAE IP registration in place)
2. **Leverage Hub71's MBZUAI tie-in** to co-author a workshop
   paper on the express-loop + multi-tool-routing architecture
   and run a controlled study with MBZUAI students
3. **Sell into Abu Dhabi public-education + private-tutoring
   ecosystem** as the first commercial wedge, before expanding
   to KSA + India
4. **Use Hub71 Nvidia + AWS partnership** to scale the local
   fine-tune pipeline (currently runs on a single RTX 5090 in
   the founder's home office)
5. **Co-marketing with AI71 / Core42** as a flagship UAE-built
   educational AI product

---

## Pitch-deck content (12 slides — used to generate the PDF)

### Slide 1 — Cover
**Khayyam Math**
*Voice-narrated math figures, generated live from one-line prompts*
khayyammath.com · Hub71+ AI · Cohort 20

### Slide 2 — Problem
Math figures are the hardest part of math learning.
A textbook only has the figure its author drew. The learner stuck
on a specific problem at 11 PM gets nothing.
Existing AI tutors are text-first; very few produce accurate,
synchronised visual diagrams.

### Slide 3 — Solution
One-line prompt → custom SVG figure + phrase-timed voice narration,
in 3–15 seconds.
Three layout engines under one routing layer: deterministic
templates, Graphviz for graph-shapes, LLM-driven for the rest.
Vision-audit retry guarantees the figure matches the narration.

### Slide 4 — Product (real outputs, not mockups)
[Insert the 4 gallery screenshots from /screenshots/ — matrix mul,
DFA, Pythagoras, unit circle]

### Slide 5 — Why Now
Frontier LLMs (gpt-4o, Claude Sonnet 4) just became reliable
enough at structured output to drive on-demand figure generation.
Mobile penetration in the GCC + India means the addressable
market for self-study math tutoring tripled in the last 3 years.

### Slide 6 — Market
- **TAM** (global K-12 + early-undergrad math tutoring): ~$80B
- **SAM** (UAE/GCC + India English-language self-study segment): ~$6B
- **SOM** (first 3 years targeting UAE/GCC tutoring centres): ~$80M
Sources: HolonIQ ed-tech tracker; UAE Ministry of Education
private-tutoring market study.

### Slide 7 — Traction
- Live product on AWS Fargate since May 2026
- 93 production deploys to date (CDK + ECS rolling)
- Built-in telemetry, magic-link auth, cost guard, content filter
- 4 published rebuild iterations with measured per-iteration quality
  gains documented in a JAIR-target paper (Zenodo preprint
  forthcoming this month)
- Quality scorer (1.83M-param GNN) — 71 % pairwise win-rate on
  real (broken, fixed) layout pairs — i.e. rigorous evaluation
  harness already in place

### Slide 8 — Competition
- **Khan Academy / Khanmigo** — text-heavy, no on-demand figures
- **Brilliant** — fixed-curriculum, no personalisation per problem
- **Wolfram Alpha** — symbolic, not narrated, no follow-up
- **ChatGPT / Claude direct** — no visual diagrams, no narration sync
- **Khayyam Math** — only product that generates a custom *visual*
  for the learner's specific question, with synchronised audio

### Slide 9 — Architecture moat
- Multi-tool routing (templates + Graphviz + LLM) — most competitors
  use one approach; quality varies per figure type
- Vision-audit retry — most "AI generates figure" products ship
  whatever the LLM emits; we verify before showing
- Narration-synchronised visual highlighting — non-trivial to
  rebuild from scratch
- All differentiators are in the integration architecture, not
  the underlying LLM (which is commodity)

### Slide 10 — Founders
**Arash Kermani** — Founder, CEO, technical lead
Built the entire production stack (FastAPI + ECS Fargate + Postgres
+ S3 + custom JS canvas viewer) and the prompt-→-SVG architecture
itself. Sole author of the UAE-registered intellectual property.
JAIR paper in draft.

*[If adding co-founder]:* **[Brother's name]** — Co-founder / CTO.
Eight+ years scaling [his IT company] in the UAE; brings cloud-ops,
customer-delivery, and team-leadership experience.

### Slide 11 — Plans for Hub71 + Abu Dhabi
1. Relocate primary operations to Abu Dhabi
2. Co-author workshop paper with MBZUAI on the express-loop
   architecture
3. Sell into Abu Dhabi public-education + private-tutoring
   ecosystem as commercial wedge
4. Use Hub71's Nvidia + AWS partnerships to scale the
   fine-tune pipeline
5. Co-market with AI71 / Core42 as a flagship UAE-built EdTech AI

### Slide 12 — Ask
- Hub71+ AI Cohort 20 acceptance
- AED 250,000 flexible incentives + AED 250,000 SAFE note
- Introductions to: MBZUAI, AI71, Core42, UAE Ministry of Education,
  Abu Dhabi private-tutoring chains
