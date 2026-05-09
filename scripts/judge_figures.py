"""Blind LLM-judge over the per-prompt PNGs in /tmp/sevim_compare/.

For each prompt, presents up to three PNGs (gpt-4o, qwen-base, qwen-lora)
to gpt-4o vision in randomised order with neutral labels (Figure A/B/C).
Asks for three 1-10 scores per figure:
  * visual_clarity     — readable, non-overlapping, well laid-out
  * math_correctness   — depicts the right concept; no factual errors
  * completeness       — has worked example/labels matching the prompt
plus a one-sentence rationale.  After all judgements, de-anonymises and
writes a CSV + appends a "## Judge scores" section to REPORT.md.

If a model's PNG is missing for a given prompt, that model gets 0/0/0
for that prompt automatically (no API call made for it).

Usage (needs OPENAI_API_KEY in env):
    python scripts/judge_figures.py
"""
from __future__ import annotations

import base64
import csv
import json
import os
import random
import sys
import time
from pathlib import Path

import httpx

OUT_DIR = Path("/tmp/sevim_compare")
MODEL = "gpt-4o"
MODELS = ["gpt4o", "qwen_base", "qwen_lora"]


JUDGE_SYSTEM = """You are a meticulous judge of math/CS explanatory figures.
You will see one prompt and 1-3 candidate figures (PNGs) labelled
'Figure A', 'Figure B', 'Figure C'.  Score each figure independently on
three axes, integers 1-10:
  * visual_clarity     — is it readable? labels not overlapping? lines
                         go to real targets? layout uses the canvas?
  * math_correctness   — does it depict the actual concept the prompt
                         asked for, with no factual errors?
  * completeness       — does it include a worked example / numbers /
                         labels that match what the prompt requested?
Be strict.  A blank or near-empty figure is 1/1/1.  A figure that is
visually tidy but depicts the wrong concept is high on clarity and low
on math_correctness.  Return STRICT JSON exactly matching the schema —
no commentary outside the JSON."""


JUDGE_SCHEMA = {
    "name": "judgements",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "judgements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "figure": {"type": "string", "enum": ["A", "B", "C"]},
                        "visual_clarity": {"type": "integer"},
                        "math_correctness": {"type": "integer"},
                        "completeness": {"type": "integer"},
                        "rationale": {"type": "string"},
                    },
                    "required": [
                        "figure", "visual_clarity", "math_correctness",
                        "completeness", "rationale",
                    ],
                },
            },
        },
        "required": ["judgements"],
    },
}


def b64_data_url(path: Path) -> str:
    raw = path.read_bytes()
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def judge_one(prompt: str, png_by_model: dict[str, Path],
              client: httpx.Client, api_key: str) -> dict[str, dict]:
    """Returns {model_name: {visual_clarity, math_correctness,
    completeness, rationale}}."""
    have = [(m, p) for m, p in png_by_model.items() if p and p.exists()]
    if not have:
        return {m: {"visual_clarity": 0, "math_correctness": 0,
                    "completeness": 0, "rationale": "no figure produced"}
                for m in MODELS}
    # Randomise to blind the judge.
    rng = random.Random(prompt)  # deterministic per-prompt
    rng.shuffle(have)
    letters = ["A", "B", "C"][:len(have)]
    letter_to_model = dict(zip(letters, [m for m, _ in have]))
    user_blocks = [{
        "type": "text",
        "text": (
            f"Prompt the figures are trying to illustrate:\n\n"
            f"{prompt}\n\n"
            f"Score the {len(have)} figure(s) below."
        ),
    }]
    for letter, (_model, png) in zip(letters, have):
        user_blocks.append({"type": "text", "text": f"Figure {letter}:"})
        user_blocks.append({"type": "image_url",
                            "image_url": {"url": b64_data_url(png)}})

    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": user_blocks},
        ],
        "temperature": 0,
        "response_format": {"type": "json_schema", "json_schema": JUDGE_SCHEMA},
    }
    r = client.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"authorization": f"Bearer {api_key}",
                 "content-type": "application/json"},
        json=body, timeout=120.0,
    )
    r.raise_for_status()
    payload = r.json()
    content = payload["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    judged = {}
    for j in parsed["judgements"]:
        model = letter_to_model.get(j["figure"])
        if model is None:
            continue
        judged[model] = {
            "visual_clarity": j["visual_clarity"],
            "math_correctness": j["math_correctness"],
            "completeness": j["completeness"],
            "rationale": j["rationale"],
        }
    # Fill in any missing models as 0s.
    for m in MODELS:
        judged.setdefault(m, {"visual_clarity": 0, "math_correctness": 0,
                              "completeness": 0,
                              "rationale": "no figure produced"})
    return judged


def main() -> int:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY not set", file=sys.stderr)
        return 1
    if not OUT_DIR.exists():
        print(f"{OUT_DIR} not found", file=sys.stderr)
        return 1

    prompt_dirs = sorted(p for p in OUT_DIR.iterdir() if p.is_dir())
    print(f"judging {len(prompt_dirs)} prompts with {MODEL}", flush=True)

    rows = []
    client = httpx.Client(http2=False)
    t0 = time.monotonic()
    for d in prompt_dirs:
        prompt = (d / "prompt.txt").read_text().strip()
        png_by_model = {
            "gpt4o": d / "gpt4o.png",
            "qwen_base": d / "qwen_base.png",
            "qwen_lora": d / "qwen_lora.png",
        }
        avail = [m for m, p in png_by_model.items() if p.exists()]
        print(f"[{d.name[:40]}] judging {len(avail)} figures: {avail}",
              flush=True)
        try:
            judged = judge_one(prompt, png_by_model, client, api_key)
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED: {type(exc).__name__}: {exc}", flush=True)
            judged = {m: {"visual_clarity": 0, "math_correctness": 0,
                          "completeness": 0, "rationale": f"error: {exc}"}
                      for m in MODELS}
        for m in MODELS:
            j = judged[m]
            rows.append({
                "prompt_dir": d.name, "prompt": prompt, "model": m,
                "visual_clarity": j["visual_clarity"],
                "math_correctness": j["math_correctness"],
                "completeness": j["completeness"],
                "total": (j["visual_clarity"] + j["math_correctness"]
                          + j["completeness"]),
                "rationale": j["rationale"],
            })
        # tiny delay to be nice to API
        time.sleep(0.5)
    client.close()
    elapsed = time.monotonic() - t0

    # CSV
    csv_path = OUT_DIR / "judge_scores.csv"
    with csv_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {csv_path}", flush=True)

    # Roll-up per model
    print("\n=== mean scores (out of 10) ===")
    summary: dict[str, dict] = {m: {"vc": 0.0, "mc": 0.0, "c": 0.0,
                                    "n": 0, "wins_total": 0,
                                    "produced": 0}
                                for m in MODELS}
    by_prompt: dict[str, dict[str, dict]] = {}
    for r in rows:
        m = r["model"]
        s = summary[m]
        s["vc"] += r["visual_clarity"]
        s["mc"] += r["math_correctness"]
        s["c"] += r["completeness"]
        s["n"] += 1
        if r["visual_clarity"] + r["math_correctness"] + r["completeness"] > 0:
            s["produced"] += 1
        by_prompt.setdefault(r["prompt_dir"], {})[m] = r

    for d, per in by_prompt.items():
        best_total = max(per[m]["total"] for m in MODELS)
        if best_total == 0:
            continue
        for m in MODELS:
            if per[m]["total"] == best_total:
                summary[m]["wins_total"] += 1

    n_prompts = len(by_prompt)

    # Markdown report append
    md_lines = ["\n\n## Judge scores (gpt-4o-as-judge, blinded)\n",
                f"\nJudged {n_prompts} prompts.  Each figure rated 1-10 on "
                "visual_clarity, math_correctness, completeness.  Missing "
                "figures count as 0.\n\n",
                "| Model | mean clarity | mean correctness | mean completeness | "
                "mean total / 30 | produced figure | top-rank ties |\n",
                "|---|---|---|---|---|---|---|\n"]
    for m in MODELS:
        s = summary[m]
        if s["n"] == 0:
            continue
        md_lines.append(
            f"| {m} | "
            f"{s['vc']/s['n']:.1f} | "
            f"{s['mc']/s['n']:.1f} | "
            f"{s['c']/s['n']:.1f} | "
            f"{(s['vc']+s['mc']+s['c'])/s['n']:.1f} | "
            f"{s['produced']}/{n_prompts} | "
            f"{s['wins_total']}/{n_prompts} |\n"
        )
        print(f"  {m:<10}  clarity={s['vc']/s['n']:.1f}  "
              f"correct={s['mc']/s['n']:.1f}  "
              f"complete={s['c']/s['n']:.1f}  "
              f"total/30={(s['vc']+s['mc']+s['c'])/s['n']:.1f}  "
              f"top-rank={s['wins_total']}/{n_prompts}")

    md_lines.append("\n### Per-prompt totals (out of 30)\n")
    md_lines.append("| # | prompt | gpt4o | qwen_base | qwen_lora |\n")
    md_lines.append("|---|---|---|---|---|\n")
    for d in sorted(by_prompt):
        per = by_prompt[d]
        i = d.split("_", 1)[0]
        prompt_short = per["gpt4o"]["prompt"][:50]
        if len(per["gpt4o"]["prompt"]) > 50:
            prompt_short += "…"
        md_lines.append(
            f"| {i} | {prompt_short} | "
            f"{per['gpt4o']['total']} | "
            f"{per['qwen_base']['total']} | "
            f"{per['qwen_lora']['total']} |\n"
        )

    md_lines.append(f"\n_Judge run took {elapsed:.0f}s._\n")

    report_path = OUT_DIR / "REPORT.md"
    report_path.write_text(report_path.read_text() + "".join(md_lines))
    print(f"\nappended judge section to {report_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
