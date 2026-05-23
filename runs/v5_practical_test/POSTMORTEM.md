# v5 fine-tune — post-mortem (REJECTED)

**Date:** 2026-05-23
**Verdict:** NO-GO. v5 was **not** pushed to HuggingFace. The adapter is archived to S3 at
`s3://<your-lora-bucket>/qwen_lora_v5_rejected/` for analysis only.

The current production model on HF remains
[`khayyam-math/khayyam-math-qwen2.5-7b-v4`](https://huggingface.co/khayyam-math/khayyam-math-qwen2.5-7b-v4).

---

## Training (succeeded)

| Field | Value |
|---|---|
| Adapter | `qwen_lora_v5_rejected` |
| Base | `Qwen/Qwen2.5-7B-Instruct` |
| Corpus | `data/distill/teacher_v7.jsonl` — **2,402 examples**: 2,350 synthetic (gpt-4o-mini teacher + inspector filter, deduped) + 39 production sft-clean turns + 13 production sft-corrected repair pairs |
| Hyperparams | rank 16, alpha 32, dropout 0.05, lr 2e-4, 3 epochs, batch 1 × grad-accum 4, bf16, max-seq 6144 |
| Hardware | RTX 5090 (32 GB) |
| Wall-clock | 2 h 58 m 30 s (10,710 s) |
| Trainable | 40.4 M params (0.53 % of base) |
| Training loss | 1.5 → **0.048** |
| Token accuracy | 0.61 → **0.987** |

Training itself was clean — no OOM, no NaN, no infrastructure failures.

## Practical test (GO/NO-GO gate) — FAILED

20 prompts across algebra, trig, calculus, linear algebra, graph theory, set
theory, geometry, probability, optimisation, CS — including the two v4 known
empty-SVG failure modes (`set_venn`, `linalg_eigen`). v4 and v5 ran the same
prompts back-to-back.

|  | v4 | **v5** |
|---|---|---|
| Valid SVG | **20 / 20** | **15 / 20** |
| Regressions (v4 OK → v5 BAD) | — | **5** |
| v4 known failures recovered by v5 | n/a | 2 (`set_venn`, `linalg_eigen`) |
| Gate threshold | n/a | ≥ 16 / 20 + zero regressions |
| **Verdict** | reference | **NO-GO** |

### The 5 v5 regressions, by failure class

| Prompt | Failure | Class |
|---|---|---|
| `trig_addition` (angle-addition formula) | parse error: SVG escaped as raw JSON literal | Malformed JSON |
| `geom_triangle_sum` (triangle angles sum to π) | parse error: same JSON-escaped SVG | Malformed JSON |
| `auto_turing` (Turing machine for 0ⁿ1ⁿ) | parse error: same JSON-escaped SVG | Malformed JSON |
| `linalg_matmul` (3×3 matrix multiplication) | `"svg": ""` in valid JSON | Empty SVG |
| `cs_3sat` (3SAT → vertex cover reduction) | `"svg": ""` in valid JSON | Empty SVG |

### Root cause

v5 **over-fit**. The training-loss collapse to 0.048 (vs v4 final loss ~0.06)
and token accuracy 0.987 on memorisation-prone hyperparameters (rank 16, 3
epochs, 2.4 k corpus) caused two compound failures on inference:

1. **Malformed top-level JSON.** On complex prompts the model emits SVG with
   literal `\"` escapes inside what should be unescaped strings; the top-level
   JSON parser refuses, my extractor's regex fallback catches the literal
   text, and `xml.etree.ElementTree` then rejects it.
2. **Empty `svg` field on multi-element prompts.** On the two prompts that
   require the most elements (`linalg_matmul` with worked 3×3 cells,
   `cs_3sat` with clause + variable + edge subgraphs), v5 produces full JSON
   metadata (problem, solution, claims, narration) but an empty `svg` field.
   This is the same class of failure v4 had on `set_venn` and `linalg_eigen` —
   v5 has only moved the failure to different prompts.

### Things v5 did right (despite failing the gate)

- **Recovered both v4 known-failure modes:** `set_venn` and `linalg_eigen`
  now produce valid SVG. So the production telemetry signal (13 repair
  pairs) **did** help on the specific cases v4 struggled with.
- All v5 outputs that did parse were structurally sound (no orphan
  references, no broken namespaces).
- Inference latency per prompt was comparable to v4 (~25 s on 5090).

The lesson is not that v5 is universally worse — it's that v5 traded one
class of failures for another, which is a net regression on the held-out
set.

---

## What to do for v5.1

Based on the failure pattern, v5.1 should:

1. **Halve LoRA capacity.** Drop to rank 8 + alpha 16 (v3 settings that
   produced the most generalisable model historically). The 0.048 final
   loss is a memorisation signal, not a quality signal.
2. **One fewer epoch.** 2 epochs over 2,402 examples = 1,200 update steps.
3. **Same corpus.** The 39 production-clean + 13 production-repair turns
   are the most valuable signal we have right now; keep them. Wait until
   production volume crosses ~500 turns before retraining with a fresh
   pull.
4. **Add JSON-validity penalty to the eval.** The practical-test gate
   already catches malformed JSON via xml.etree; we should also instrument
   training-time eval (every 200 steps) so we see the over-fit *before* it
   compounds into hard failures at the end of epoch 3.
5. **Add longer-context examples to the corpus.** The two empty-SVG
   failures both happened on prompts requiring lots of elements. The
   teacher_v6_mini distribution probably under-represents long-output
   examples.

**Estimated v5.1 wall-clock on RTX 5090:** ~1 h 45 m (lower rank +
fewer epochs cut compute roughly in half).

---

## Artifacts

| What | Where |
|---|---|
| v5 adapter (rejected) | `s3://<your-lora-bucket>/qwen_lora_v5_rejected/` |
| Training log | `/tmp/v5_training.log` (not committed; ~3 MB) |
| Per-prompt test outputs (40 SVGs, 40 raw responses) | `runs/v5_practical_test/*_{v4,v5}.{svg,raw.txt}` |
| Test summary | `runs/v5_practical_test/summary.json` |
| This document | `runs/v5_practical_test/POSTMORTEM.md` |
| Also on S3 | `s3://<your-lora-bucket>/qwen_lora_v5_rejected/practical_test/` |

## What the user / contributors should take away

The release pipeline worked exactly as designed: fine-tune → automated
practical test → gate decision → archive (not ship) when the gate fails.
**A NO-GO is a success of the gate, not a failure of the project.** A v5
that shipped without the test would have introduced 5 regressions for
real learners.
