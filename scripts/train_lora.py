"""LoRA fine-tune Qwen2.5-7B-Instruct on the Sevim corpus.

Reads the JSONL produced by ``studio.export_finetune`` and trains a
small LoRA adapter against the chat-format messages.  Output is a
PEFT adapter directory that vLLM (with --enable-lora) or transformers
(via PeftModel.from_pretrained) can load alongside the base model.

Usage:
    python scripts/train_lora.py \
        --dataset /tmp/sevim_finetune.jsonl \
        --out /tmp/qwen_sevim_lora

Notes
-----
* Targets Qwen2.5-7B-Instruct base (in HF cache from prior vLLM runs).
* LoRA rank 16, alpha 32, dropout 0.05 — modest, fits a small dataset
  without overfitting catastrophically.
* 3 epochs by default; with ~20 examples this is 60 update steps.
* No quantisation: the RTX 5090's 32 GB easily holds a 7B model in
  bf16 + LoRA on top.  Drop to 4-bit (bitsandbytes) if VRAM tight.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Avoid HF Hub trying to download anything we don't have locally — we
# already have Qwen2.5-7B-Instruct cached.  Set offline mode if no
# network is desired.
os.environ.setdefault("TRANSFORMERS_OFFLINE", "0")


def load_dataset(path: Path) -> "datasets.Dataset":  # noqa: F821
    from datasets import Dataset  # noqa: PLC0415
    rows = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return Dataset.from_list(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", type=Path, required=True)
    ap.add_argument("--base-model", type=str,
                    default="Qwen/Qwen2.5-7B-Instruct")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--alpha", type=int, default=32)
    ap.add_argument("--max-seq-len", type=int, default=4096)
    args = ap.parse_args()

    if not args.dataset.exists():
        print(f"dataset not found: {args.dataset}", file=sys.stderr)
        return 1

    print(f"loading dataset {args.dataset}", flush=True)
    ds = load_dataset(args.dataset)
    print(f"  {len(ds)} examples", flush=True)
    if len(ds) == 0:
        print("empty dataset; aborting", file=sys.stderr)
        return 1

    print(f"loading base model {args.base_model} (bf16)", flush=True)
    import torch  # noqa: PLC0415
    from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: PLC0415

    tokenizer = AutoTokenizer.from_pretrained(args.base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        dtype=torch.bfloat16,
        device_map="auto",
    )

    print("attaching LoRA adapter", flush=True)
    from peft import LoraConfig, get_peft_model  # noqa: PLC0415
    peft_cfg = LoraConfig(
        r=args.rank,
        lora_alpha=args.alpha,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
    )
    model = get_peft_model(model, peft_cfg)
    model.print_trainable_parameters()

    # Format examples by applying the model's chat template — this is
    # how Qwen2.5-Instruct expects to see conversations.
    def formatting_func(example: dict) -> str:
        return tokenizer.apply_chat_template(
            example["messages"], tokenize=False, add_generation_prompt=False,
        )

    from trl import SFTConfig, SFTTrainer  # noqa: PLC0415

    sft_cfg = SFTConfig(
        output_dir=str(args.out),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=4,
        learning_rate=args.lr,
        bf16=True,
        logging_steps=2,
        save_strategy="epoch",
        save_total_limit=1,
        report_to="none",
        max_length=args.max_seq_len,
        packing=False,
        completion_only_loss=False,  # train on the whole sequence
    )
    trainer = SFTTrainer(
        model=model,
        train_dataset=ds,
        args=sft_cfg,
        formatting_func=formatting_func,
    )
    print("starting training", flush=True)
    trainer.train()
    print(f"saving adapter to {args.out}", flush=True)
    model.save_pretrained(str(args.out))
    tokenizer.save_pretrained(str(args.out))
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
