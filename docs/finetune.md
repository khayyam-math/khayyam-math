# Fine-tuning Khayyam Math — LoRA on Qwen 2.5-7B

This document is the operational runbook for training a new Khayyam
Math LoRA adapter. It is the path we use end-to-end to produce every
release we push to
[`khayyam-math/khayyam-math-qwen2.5-7b-*`](https://huggingface.co/khayyam-math)
and to the production S3 adapter bucket.

There are two run modes:

| Mode | Corpus | Epochs | Rank | Wall-clock on RTX 5090 | Purpose |
|---|---|---|---|---|---|
| **Smoke** (`v*.1-smoke`) | 200 examples | 1 | 8 | **~5 min** | CI-style "the pipeline isn't broken" gate before a full run |
| **Full** (`v*`) | 3,395 (or more) examples | 3 | 16 | **~5 h** | Production candidate. Eval against the held-out bench before promotion. |

Always do the smoke run first. It surfaces transformers/peft/trl
version drift, GPU-OOM edge cases, and corpus-schema breakage in
minutes, not hours.

---

## 0. Hardware and software prerequisites

- **GPU**: NVIDIA Ampere or newer with ≥24 GB VRAM. The reference
  configuration is a single **RTX 5090** (32 GB). Qwen 2.5-7B in bf16
  + the LoRA adapter + AdamW state + gradients fits in ~22 GB.
- **System**: Linux (Ubuntu 22.04 / 24.04 tested), Python 3.12.
- **Disk**: ~80 GB free under `~/.cache/huggingface` for the base
  model + adapters + intermediate checkpoints.
- **Python deps** (already in this repo's `pyproject.toml [qwen]`
  extra): `torch>=2.1`, `transformers>=4.40`, `peft>=0.10`,
  `accelerate>=0.30`, `safetensors>=0.4`, `trl>=1.4`, `datasets>=4.0`.

From a fresh clone:

```bash
git clone https://github.com/khayyam-math/khayyam-math
cd khayyam-math
uv sync --extra qwen                 # pulls torch + transformers + peft + trl
```

---

## 1. The corpus

The reference corpus is at
[`data/distill/teacher_v6_mini.jsonl`](../data/distill/teacher_v6_mini.jsonl)
(3,395 examples). Each line is a JSON object in the chat schema:

```json
{
  "messages": [
    {"role": "system",    "content": "You are a math TEACHER…"},
    {"role": "user",      "content": "<prompt>"},
    {"role": "assistant", "content": "<structured JSON with svg, narration, math_claims, …>"}
  ],
  "meta": { "...": "..." }
}
```

The corpus is the output of three sources merged + filtered by an
automatic inspector that rejects malformed SVG, layout overlaps, and
math-correctness failures (SymPy + Z3 + Lean):

1. **`gpt-4o-mini` teacher distillation** — see
   [`scripts/generate_teacher_corpus.py`](../scripts/generate_teacher_corpus.py).
2. **Production telemetry winners** — turns where the user did not
   click "Not quite right?" and the figure passed the verifier.
   See [`studio/export_finetune.py`](../studio/export_finetune.py).
3. **Repair pairs** — `(bad SVG, critic, fixed SVG)` triples mined
   from the vision-audit retry loop in production.

For a fresh full run, mine the latest telemetry first:

```bash
python -m studio.export_finetune \
  --since 30d \
  --inspector-filter \
  --out data/distill/teacher_v7.jsonl
```

---

## 2. Smoke run (~5 min)

```bash
python -c "
import json, random
random.seed(42)
with open('data/distill/teacher_v6_mini.jsonl') as f:
    rows = [json.loads(line) for line in f]
random.shuffle(rows)
with open('/tmp/smoke_corpus_200.jsonl', 'w') as f:
    for r in rows[:200]:
        f.write(json.dumps(r) + chr(10))
"

python scripts/train_lora.py \
  --dataset /tmp/smoke_corpus_200.jsonl \
  --out    /tmp/qwen-smoke \
  --epochs 1 --rank 8 --alpha 16 --max-seq-len 4096
```

Expected output (RTX 5090):

```
loading dataset /tmp/smoke_corpus_200.jsonl
  200 examples
loading base model Qwen/Qwen2.5-7B-Instruct (bf16)
Loading weights: 100% 339/339 [00:03<00:00, 92.28it/s]
attaching LoRA adapter
trainable params: 20,185,088 || all params: 7,635,801,600 || trainable%: 0.26
starting training
… 50 steps in ~4 min …
{'train_runtime': '259.4', 'train_loss': '0.474', 'epoch': '1'}
saving adapter to /tmp/qwen-smoke
done
```

Training curve from a real smoke run (2026-05-23, v4.1-smoke):

<p align="center">
  <img src="screenshots/finetune/smoke_training_curve.png"
       alt="Smoke fine-tune loss + accuracy curves" width="900">
</p>

What "smoke green" looks like:

- Final loss **< 0.2** (memorisation expected at 200 ex / 1 ep)
- Final token accuracy **> 0.95**
- No NaN, no OOM, no `KeyError` on the corpus schema
- `adapter_model.safetensors` written to `--out`
- A test load via `PeftModel.from_pretrained(...)` succeeds

If any of those fail, **stop**. Fix the regression on the smoke loop
before paying for a full run.

---

## 3. Full run (~5 h)

```bash
python scripts/train_lora.py \
  --dataset data/distill/teacher_v6_mini.jsonl \
  --out    /tmp/qwen-v5 \
  --epochs 3 --rank 16 --alpha 32 --max-seq-len 6144
```

Recommended environment for the long run:

```bash
export HF_HUB_ENABLE_HF_TRANSFER=1     # 10× HF download speed
export NCCL_P2P_DISABLE=1              # single-GPU; silences the warning
nohup python scripts/train_lora.py …  > /tmp/v5_training.log 2>&1 &
disown
tail -f /tmp/v5_training.log
```

Re-run the held-out 20-prompt benchmark against the resulting adapter
before promotion:

```bash
python scripts/judge_blind.py \
  --adapter /tmp/qwen-v5 \
  --vs khayyam-math/khayyam-math-qwen2.5-7b-v4 \
  --out runs/v5_vs_v4.json
```

Promote only if v5 beats v4 head-to-head and does not regress the
two known empty-SVG failure modes (eigendecomposition 2×2,
three-set Venn).

---

## 4. Publishing the adapter

Push to **both** S3 (production inference cache) and HuggingFace
(public discovery surface). The two are kept in lockstep so the
production vLLM container and the public `khayyam-math` pip package
load the same bytes.

### 4.1 S3 (production source of truth)

```bash
AWS_PROFILE=sevim aws s3 sync /tmp/qwen-v5/ \
  s3://<your-lora-bucket>/qwen_lora_v5/
```

Update the active-model setting from `/studio/admin` (or via the
CDK secret value `SEVIM_DEFAULT_ACTIVE_MODEL`) once you're ready
to switch traffic.

### 4.2 HuggingFace (public + the pip package)

You need an HF write token scoped to the `khayyam-math` org. See
the **"HF token — exact configuration"** section in the README for
how to mint one.

```python
from huggingface_hub import HfApi, create_repo
REPO = "khayyam-math/khayyam-math-qwen2.5-7b-v5"
api  = HfApi(token="hf_...")
create_repo(repo_id=REPO, repo_type="model", private=True,
            token="hf_...", exist_ok=True)
api.upload_folder(
    repo_id=REPO,
    folder_path="/tmp/qwen-v5",
    repo_type="model",
    commit_message="v5 — full corpus, 3 epochs, r=16",
    ignore_patterns=["manifest.json", "checkpoint-*"],
)
```

Tag the release once published:

```bash
huggingface-cli tag khayyam-math/khayyam-math-qwen2.5-7b-v5 v5
```

After the first public release, the pip package's `KhayyamMath`
client can pin to a specific revision:

```python
client = KhayyamMath(provider="qwen",
                     model="khayyam-math/khayyam-math-qwen2.5-7b-v5",
                     revision="v5")
```

---

## 5. What every adapter ships

Every model repo in `khayyam-math/khayyam-math-qwen2.5-7b-*` contains:

| File | Purpose |
|---|---|
| `adapter_config.json` | PEFT LoRA configuration (rank, alpha, target modules) |
| `adapter_model.safetensors` | Trained LoRA weights |
| `chat_template.jinja` | Qwen's chat template — required for `apply_chat_template` to produce the right format |
| `tokenizer.json` + `tokenizer_config.json` | Tokenizer (copied from the base model) |
| `README.md` | HF model card (what / how to load / training details / eval) |
| `manifest.json` *(ignored on HF, kept on S3)* | Internal training metadata (loss curves, judge scores, hardware, intent) |

The S3 layout under `s3://…lorabucket…/qwen_lora_<version>/`
mirrors this 1:1, plus the `manifest.json`.

---

## 6. Reference: v4.1-smoke ground truth

The actual v4.1-smoke run (2026-05-23) produced exactly:

- **Final training loss:** 0.050 (from 1.881)
- **Final token accuracy:** 0.986 (from 0.606)
- **Wall-clock time:** 4 m 19 s (259 s)
- **Trainable params:** 20.2 M (0.26 % of base)
- **Output size:** 80.8 MB safetensors + 11.4 MB tokenizer

Pushed to:

- S3: `s3://<your-lora-bucket>/qwen_lora_v4.1_smoke/`
- HF: `https://huggingface.co/khayyam-math/khayyam-math-qwen2.5-7b-v4.1-smoke` (private)

If your run differs materially in any of the above metrics on the
same 200-example seed=42 subset, the pipeline has changed; root-cause
the delta before starting the full run.
