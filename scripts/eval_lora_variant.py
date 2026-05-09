"""Run a specific LoRA adapter against the 20-prompt set, write
artefacts as qwen_<tag>.{json,svg,png} alongside the existing
gpt4o/qwen_base/qwen_lora outputs.

Used to compare LoRA hyperparameter variants without re-running the
full 3-way compare (which also re-loads the base model and re-renders
gpt-4o data).

Usage:
    /tmp/finetune_venv/bin/python3 scripts/eval_lora_variant.py \
        --adapter /tmp/qwen_sevim_lora_v2 --tag qwen_lora_v2
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.compare_models import (  # noqa: E402
    OUT_DIR, BASE_MODEL, slugify, run_qwen_inference, write_assistant_artefacts,
)
from scripts.diverse_prompts_test import PROMPTS  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", type=Path, required=True)
    ap.add_argument("--tag", type=str, required=True,
                    help="filename prefix, e.g. qwen_lora_v2")
    args = ap.parse_args()
    if not args.adapter.exists():
        print(f"adapter {args.adapter} missing", file=sys.stderr)
        return 1

    print(f"loading base {BASE_MODEL} + adapter {args.adapter}", flush=True)
    import torch  # noqa: PLC0415
    from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: PLC0415
    from peft import PeftModel  # noqa: PLC0415
    from studio.express import _EXPRESS_SYSTEM as SYSTEM_PROMPT  # noqa: PLC0415

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    base = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL, dtype=torch.bfloat16, device_map="auto",
    )
    model = PeftModel.from_pretrained(base, str(args.adapter))
    model.eval()

    for i, prompt in enumerate(PROMPTS, start=1):
        slug = f"{i:02d}_{slugify(prompt)}"
        dest = OUT_DIR / slug
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "prompt.txt").write_text(prompt)
        print(f"[{i}/{len(PROMPTS)}] {args.tag}: {prompt[:60]}", flush=True)
        try:
            raw = run_qwen_inference(model, tokenizer, SYSTEM_PROMPT, prompt)
            write_assistant_artefacts(dest, args.tag, raw, args.tag)
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED: {exc}", flush=True)
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
