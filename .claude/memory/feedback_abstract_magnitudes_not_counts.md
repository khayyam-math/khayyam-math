---
name: Scientific abstracts: report magnitudes, not brittle counts
description: In scientific abstracts and conclusions, avoid bookkeeping numbers (18 examples, 19/20, 42-second fine-tune) that will go stale tomorrow. Use magnitudes that survive incremental experiments.
type: feedback
originSessionId: 6a854eca-8e61-49bc-b8b2-5b4735f52d51
---
In a scientific paper's abstract and conclusion, **do not** report exact bookkeeping counts like "18 accepted training examples", "0/20 to 19/20 schema compliance", "42-second LoRA fine-tune", "23,122 (broken, fixed) pairs". They will be stale the moment a 19th example is added, and they read like a project log rather than a journal article.

**Why:** The user explicitly called this out — *"what if tomorrow I use 19 accepted one? this is a scientific paper not a project report."*

**How to apply:**

- Replace exact fractions with magnitude language: *"near-complete schema compliance"* not *"19/20"*; *"a handful of accepted training examples"* not *"18"*; *"tens of thousands of pairs"* not *"23,122"*; *"few-million-parameter scale"* not *"6.3M"*.
- Reserve precise numbers for body tables (where they belong) — readers can verify them there.
- Headline numbers that convey a scientific finding (e.g., a benchmark score of 22.9/30) can stay if they're load-bearing for a claim; the test is whether changing the count would change the paper's thesis.
- Use round numbers for magnitudes: "tens of thousands", "two orders of magnitude", "a small but consistent margin", "approximately forty failure modes".

The body of the paper can and should have the exact counts — they live in tables, with a fixed audit trail. The abstract is a magnitude-level summary.
