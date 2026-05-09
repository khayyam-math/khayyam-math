"""Run the diverse-prompt set through (base Qwen, fine-tuned Qwen,
gpt-4o-from-telemetry) and produce a side-by-side report.

Output:
  /tmp/sevim_compare/<i>_<slug>/
       prompt.txt
       gpt4o.svg + gpt4o.png + gpt4o.meta.json
       qwen_base.svg + qwen_base.png + qwen_base.json (raw assistant content)
       qwen_lora.svg + qwen_lora.png + qwen_lora.json
  /tmp/sevim_compare/REPORT.md       — markdown summary

Eval signal (cheap, deterministic):
  * Did the model produce parseable JSON with svg + narration?
  * Did the SVG render to PNG without error?
  * Length comparisons (svg chars, narration phrases).
  * Side-by-side PNGs for human inspection.

Run with the fine-tune venv (transformers + peft):
    /tmp/finetune_venv/bin/python3 scripts/compare_models.py
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

# Reuse the prompt set from the sender.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.diverse_prompts_test import PROMPTS  # noqa: E402

OUT_DIR = Path("/tmp/sevim_compare")
OUT_DIR.mkdir(parents=True, exist_ok=True)

BASE_MODEL = "Qwen/Qwen2.5-7B-Instruct"
LORA_DIR = Path("/tmp/qwen_sevim_lora")
TELEMETRY_DB = Path.home() / ".local/share/sevim/telemetry.db"


def slugify(s: str, n: int = 30) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:n]


def load_gpt4o_results() -> dict[str, dict]:
    """Map prompt → {svg, narration, title} from telemetry.

    Studio's express path stores the *cleaned* prompt gpt-4o passed to
    sevim_express, not the original user text.  We match by ordering:
    pull turns ordered by timestamp and zip with PROMPTS.  This works
    because the diverse_prompts_test sends prompts in order, one per
    fresh session.
    """
    if not TELEMETRY_DB.exists():
        return {}
    conn = sqlite3.connect(TELEMETRY_DB)
    rows = conn.execute(
        """
        SELECT t.timestamp, t.user_prompt, t.canvas_id, t.retries_used,
               c.svg, c.narration_json, c.title
          FROM turns t
          LEFT JOIN canvases c ON c.canvas_id = t.canvas_id
         WHERE t.session_id LIKE 'diverse_%'
         ORDER BY t.timestamp ASC
        """
    ).fetchall()
    conn.close()
    out: dict[str, dict] = {}
    if len(rows) >= len(PROMPTS):
        # Take the LAST len(PROMPTS) turns (most recent run).
        rows = rows[-len(PROMPTS):]
    for prompt, row in zip(PROMPTS, rows):
        ts, _user, cid, retries, svg, narration_json, title = row
        try:
            narr = json.loads(narration_json or "[]")
        except Exception:
            narr = []
        out[prompt] = {
            "svg": svg or "",
            "narration": narr,
            "title": title or "",
            "canvas_id": cid,
            "retries_used": retries or 0,
        }
    return out


def write_assistant_artefacts(dest: Path, name: str, raw_content: str,
                              source: str) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    json_path = dest / f"{name}.json"
    json_path.write_text(json.dumps(
        {"source": source, "raw_content": raw_content}, indent=2,
    ))
    parsed: dict = {}
    try:
        # Models sometimes wrap with ```json ... ```; strip if present.
        cleaned = raw_content.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*\n", "", cleaned)
            cleaned = re.sub(r"\n```\s*$", "", cleaned)
        parsed = json.loads(cleaned)
    except Exception:
        # Try to extract the SVG with a regex (fallback when the model
        # didn't follow the JSON schema).
        m = re.search(r"<svg[^>]*>.*?</svg>", raw_content, re.DOTALL)
        if m:
            parsed = {"svg": m.group(0), "narration": [], "title": ""}
    svg = parsed.get("svg") or ""
    narr = parsed.get("narration") or []
    title = parsed.get("title") or ""
    if svg:
        (dest / f"{name}.svg").write_text(svg)
        try:
            import cairosvg
            png = cairosvg.svg2png(bytestring=svg.encode("utf-8"),
                                   output_width=900)
            (dest / f"{name}.png").write_bytes(png)
        except Exception as exc:  # noqa: BLE001
            (dest / f"{name}.png_error.txt").write_text(str(exc))
    return {
        "valid_json": bool(parsed),
        "svg_len": len(svg),
        "n_phrases": len(narr),
        "title": title,
        "raw_len": len(raw_content),
    }


def run_qwen_inference(model, tokenizer, system_prompt: str,
                       user_prompt: str, max_new_tokens: int = 4096) -> str:
    import torch
    msgs = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    text = tokenizer.apply_chat_template(
        msgs, tokenize=False, add_generation_prompt=True,
    )
    inputs = tokenizer(text, return_tensors="pt").to(model.device)
    with torch.inference_mode():
        out = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            temperature=1.0,
            top_p=1.0,
            repetition_penalty=1.0,
            eos_token_id=tokenizer.eos_token_id,
            pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
        )
    new_tokens = out[0][inputs["input_ids"].shape[1]:]
    return tokenizer.decode(new_tokens, skip_special_tokens=True)


def main() -> int:
    print(f"compare run, output → {OUT_DIR}", flush=True)
    print("loading gpt-4o results from telemetry…", flush=True)
    gpt = load_gpt4o_results()
    print(f"  matched {len(gpt)}/{len(PROMPTS)} gpt-4o results", flush=True)

    print(f"loading Qwen base model {BASE_MODEL}…", flush=True)
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import PeftModel
    from studio.express import _EXPRESS_SYSTEM as SYSTEM_PROMPT

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    base = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL, dtype=torch.bfloat16, device_map="auto",
    )

    summary = []
    # Pass 1: base Qwen.
    print("\n=== pass 1/2: base Qwen2.5-7B-Instruct ===", flush=True)
    for i, prompt in enumerate(PROMPTS, start=1):
        slug = f"{i:02d}_{slugify(prompt)}"
        dest = OUT_DIR / slug
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "prompt.txt").write_text(prompt)
        print(f"[{i}/{len(PROMPTS)}] base   : {prompt[:60]}", flush=True)
        try:
            raw = run_qwen_inference(base, tokenizer, SYSTEM_PROMPT, prompt)
            stats = write_assistant_artefacts(dest, "qwen_base", raw, "qwen_base")
        except Exception as exc:  # noqa: BLE001
            stats = {"error": f"{type(exc).__name__}: {exc}"}
        summary.append({"prompt": prompt, "qwen_base": stats})

    # Pass 2: fine-tuned Qwen.
    print("\n=== pass 2/2: Qwen + LoRA ===", flush=True)
    if LORA_DIR.exists():
        peft_model = PeftModel.from_pretrained(base, str(LORA_DIR))
        peft_model.eval()
        for i, prompt in enumerate(PROMPTS, start=1):
            slug = f"{i:02d}_{slugify(prompt)}"
            dest = OUT_DIR / slug
            print(f"[{i}/{len(PROMPTS)}] lora   : {prompt[:60]}", flush=True)
            try:
                raw = run_qwen_inference(peft_model, tokenizer, SYSTEM_PROMPT, prompt)
                stats = write_assistant_artefacts(dest, "qwen_lora", raw, "qwen_lora")
            except Exception as exc:  # noqa: BLE001
                stats = {"error": f"{type(exc).__name__}: {exc}"}
            summary[i - 1]["qwen_lora"] = stats
    else:
        print(f"LoRA dir {LORA_DIR} missing; skipping fine-tuned pass", flush=True)

    # Pass 3: gpt-4o (already captured).
    print("\n=== pass 3/3: gpt-4o (from telemetry) ===", flush=True)
    for i, prompt in enumerate(PROMPTS, start=1):
        slug = f"{i:02d}_{slugify(prompt)}"
        dest = OUT_DIR / slug
        g = gpt.get(prompt, {})
        if g.get("svg"):
            (dest / "gpt4o.svg").write_text(g["svg"])
            try:
                import cairosvg
                png = cairosvg.svg2png(bytestring=g["svg"].encode("utf-8"),
                                       output_width=900)
                (dest / "gpt4o.png").write_bytes(png)
            except Exception as exc:  # noqa: BLE001
                (dest / "gpt4o.png_error.txt").write_text(str(exc))
            (dest / "gpt4o.meta.json").write_text(json.dumps({
                "canvas_id": g.get("canvas_id"),
                "retries_used": g.get("retries_used"),
                "title": g.get("title"),
                "n_phrases": len(g.get("narration", [])),
            }, indent=2))
            stats = {
                "svg_len": len(g.get("svg", "")),
                "n_phrases": len(g.get("narration", [])),
                "title": g.get("title", ""),
                "retries_used": g.get("retries_used", 0),
            }
        else:
            stats = {"missing": True}
        summary[i - 1]["gpt4o"] = stats

    # Markdown report.
    report = ["# Sevim model comparison\n",
              f"Compared {len(PROMPTS)} diverse math/CS prompts across:\n",
              "* `gpt-4o` (via Studio + sevim_express, from telemetry)\n",
              f"* `Qwen2.5-7B-Instruct` (base, no fine-tune)\n",
              f"* `Qwen2.5-7B-Instruct + LoRA` (trained on {len(PROMPTS)} examples)\n",
              "\n## Per-prompt results\n",
              "| # | prompt | gpt-4o | qwen-base | qwen-lora |\n",
              "|---|---|---|---|---|\n"]

    def cell(s: dict) -> str:
        if not s:
            return "—"
        if s.get("missing"):
            return "missing"
        if "error" in s:
            return f"ERROR: {s['error'][:40]}"
        svg_ok = (s.get("svg_len") or 0) > 100
        n = s.get("n_phrases", 0)
        return (f"svg {s.get('svg_len','-')}b "
                f"phrases {n} "
                f"{'✓' if svg_ok else '✗'}")

    for i, row in enumerate(summary, start=1):
        prompt_short = row["prompt"][:50] + ("…" if len(row["prompt"]) > 50 else "")
        report.append(
            f"| {i} | {prompt_short} | {cell(row.get('gpt4o', {}))} | "
            f"{cell(row.get('qwen_base', {}))} | "
            f"{cell(row.get('qwen_lora', {}))} |\n"
        )

    # Roll-ups
    def valid_count(field: str) -> int:
        return sum(1 for r in summary
                   if (r.get(field, {}).get("svg_len") or 0) > 100)

    report.append("\n## Roll-up\n")
    report.append(f"* gpt-4o produced valid SVG: **{valid_count('gpt4o')}/{len(summary)}**\n")
    report.append(f"* Qwen base produced valid SVG: **{valid_count('qwen_base')}/{len(summary)}**\n")
    report.append(f"* Qwen + LoRA produced valid SVG: **{valid_count('qwen_lora')}/{len(summary)}**\n")
    report.append(f"\nArtefacts per prompt under `{OUT_DIR}/<i>_<slug>/`.\n")

    (OUT_DIR / "REPORT.md").write_text("".join(report))
    print(f"\nreport: {OUT_DIR / 'REPORT.md'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
