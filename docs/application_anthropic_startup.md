# Anthropic Startups Program — Application draft

Submit at: `anthropic.com/startups` (or via partner accelerator).

> Notes for filing:
> • The honest pitch here is "we're a heavy OpenAI user TODAY and want
>   to integrate Claude as a parallel model + as a more rigorous
>   evaluator". Don't pretend Claude is already in the stack — it
>   isn't, but the architecture is model-agnostic and we have a
>   credible plan to add it.
> • If you bring your brother in as Co-Founder/CTO before submission,
>   swap the paragraphs marked **[TEAM]**.

---

## Company / product

**Company name:** Khayyam Math
**Website:** https://khayyammath.com
**Founder:** Arash Kermani, Khayyam Math (UAE-registered)
**Stage:** Live in production, single founder, pre-revenue
**Location:** United Arab Emirates (IP registered locally)
**Demo video (60 s):** *TBD — replace with YouTube unlisted URL after recording.
See `docs/demo_video_script.md` for the script.*

## One-paragraph description

Khayyam Math turns a plain-English math prompt ("draw a DFA for
L = (a|b)\* ending in ab", "show the Pythagorean theorem with a
3-4-5 triangle") into a custom SVG figure with phrase-timed voice
narration in 3-15 seconds. A built-in vision-review loop catches
incorrect claims before they reach the learner. Live on
khayyammath.com, registered as IP in the UAE. Roadmap target: an
auditable, high-quality alternative to opaque AI tutors for math
education.

## Why Claude

Khayyam Math is currently built on the OpenAI stack but our
architecture is **deliberately model-agnostic**: the express loop
delegates SVG generation, vision audit, and narration-script
synthesis to swappable model endpoints (we already swap gpt-4o ↔
gpt-4o-mini ↔ a locally fine-tuned Qwen LoRA at runtime). Adding
Claude as a parallel option is a configuration change.

Three concrete reasons we want Claude in the stack:

1. **Claude is widely reported to be stronger at structured
   reasoning over math notation than the gpt-4o family.** Our
   express loop has a dedicated correctness inspector — adding
   Claude as a second-opinion inspector would catch claims gpt-4o
   misses, and let us publish A/B numbers.

2. **Constitutional AI / safety alignment matters for an
   education product.** The product is targeted at high-school and
   early-undergraduate learners. Claude's safety training is a
   better default for that audience than any other frontier model
   we've evaluated. We're comfortable making this an explicit
   case study.

3. **Long-context handling** in Claude is more reliable for
   refinement turns where the prior SVG (~10 KB XML) plus the
   prior narration script plus the user's follow-up have to fit
   in the same prompt without losing fidelity. Our internal data
   shows gpt-4o-mini drops elements at long-context boundaries.

## Specific integration plan (90 days)

- **Week 1-2**: add Claude Sonnet 4.x as a parallel `--model` choice
  in `studio/express.py`. Same JSON-output contract.
- **Week 3-4**: A/B benchmark on the existing 150-prompt vision-audit
  test set: gpt-4o vision audit pass-rate vs Claude Sonnet pass-rate,
  per math-topic bucket.
- **Week 5-8**: add Claude as a *second* inspector (a different
  model than the generator catches different errors). Publish the
  delta.
- **Week 9-12**: case study for Anthropic publication, if there's
  interest.

## How we'd use API credits

- Run the 90-day A/B benchmark above (≈ 5K Sonnet calls + 5K Opus
  calls for the inspector role)
- Generate a 5,000-example teacher corpus using Claude as the
  generator (current corpus uses gpt-4o-mini) for our next
  fine-tune iteration on the Qwen LoRA path
- Validate Anthropic's safety-training claims against our existing
  content filter for math-education prompts

## **[TEAM]** Team (single-founder version)

**Arash Kermani** — Founder, CEO, technical lead. Built the entire
production stack and the prompt-→-SVG architecture. Sole author of
the registered intellectual property in the UAE.

## **[TEAM]** Team (with co-founder version)

**Arash Kermani** — Founder/CEO. Built the production stack and the
prompt-→-SVG architecture. Sole author of the registered IP.

**[Brother's name]** — Co-founder/CTO. Eight+ years scaling
[his IT company] in the UAE; brings cloud-ops, customer-delivery,
and team-leadership experience.

## Traction snapshot

- Live on `khayyammath.com` since May 2026
- AWS Fargate, us-east-1
- Architecture has a public JAIR-target paper draft going on
  Zenodo this month
- Built-in quality scorer (1.83M-param graph-conditioned
  classifier) showing 71% pairwise win-rate on real (broken, fixed)
  layout pairs — i.e. we already have rigorous evaluation
  infrastructure that an Anthropic credit would extend, not have
  to build from scratch

## Roadmap (next 90 days)

1. Publish JAIR paper + Zenodo preprint (math AI architecture)
2. Niche validation: 20 paying users in UAE/GCC math-tutoring or
   Indian competitive-exam-prep market
3. **Integrate Claude as parallel model + second-opinion
   inspector** — the deliverable for the Anthropic credit
4. Submit NeurIPS workshop paper on the layered architecture +
   per-model comparison

## Asks

- **Standard-tier Anthropic Startup credits** ($1K–$5K self-serve)
  to fund the 90-day Claude integration + A/B benchmark above. We
  will apply for the partner tier later through Hub71 (Abu Dhabi)
  or another Anthropic-affiliated accelerator
- Optional: a technical contact for safe-deployment review of an
  AI tutor aimed at minors. We already implement a content filter +
  per-user rate limit + cost cap, but would benefit from
  Anthropic-side input
- Inclusion in the public Startups directory (distribution)
