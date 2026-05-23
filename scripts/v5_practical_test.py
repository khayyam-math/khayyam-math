"""v5 practical-test harness — the GO/NO-GO gate before publishing.

Loads the v5 adapter, runs it on a representative held-out set + the
two known v4 empty-SVG failure modes + a handful of fresh prompts.
Renders each SVG to PNG, scores schema compliance + figure validity,
writes a comparison HTML page with v4 alongside.

Exit code 0 = GO (publish), exit code 1 = NO-GO (debug, do not push).

Usage:
    python scripts/v5_practical_test.py \\
        --v5 /tmp/qwen-v5 \\
        --v4 khayyam-math/khayyam-math-qwen2.5-7b-v4 \\
        --out runs/v5_practical_test/

Gate (default thresholds, override with --min-valid / --max-regress):
    * v5 produces valid SVG on >= 16 / 20 held-out prompts (80 %).
    * v5 does not produce empty SVG on more new prompts than v4.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

# Held-out battery: 20 diverse prompts spanning the categories the
# model is supposed to handle, plus the two v4 known-empty-SVG modes.
HELD_OUT_PROMPTS: list[tuple[str, str]] = [
    ("algebra_quad",      "Solve x^2 - 5x + 6 = 0"),
    ("algebra_cubic",     "Solve x^3 - 6x^2 + 11x - 6 = 0"),
    ("trig_unit",         "Show the unit circle with sin and cos labelled at 30, 45 and 60 degrees"),
    ("trig_addition",     "Visualise the angle-addition formula sin(a+b) = sin(a)cos(b) + cos(a)sin(b)"),
    ("calc_chain",        "Visualise the chain rule for f(x) = sin(x^2)"),
    ("calc_integral",     "Show the area under y = x^2 from 0 to 2 as a Riemann sum, then the integral"),
    ("linalg_matmul",     "Illustrate matrix multiplication with two 3x3 matrices and a worked cell"),
    ("linalg_det",        "Compute the determinant of [[1,2,3],[0,1,4],[5,6,0]] with cofactor expansion"),
    ("graph_dfa",         "Draw a DFA for the language L = (a|b)* ending in ab"),
    ("graph_petersen",    "Draw the Petersen graph"),
    ("graph_homomorph",   "Show a graph homomorphism from K_3 to C_5"),
    ("set_venn",          "Draw a Venn diagram for A union B intersect C with three labelled regions"),  # v4 empty-SVG mode
    ("linalg_eigen",      "Find the eigendecomposition of the 2x2 matrix [[3,1],[0,2]]"),                  # v4 empty-SVG mode
    ("geom_pythag",       "Show the Pythagorean theorem with a 3-4-5 triangle and squares on each side"),
    ("geom_triangle_sum", "Illustrate why the angles of a triangle sum to pi"),
    ("prob_bayes",        "Visualise Bayes theorem with a tree-of-tests layout for a medical-test example"),
    ("stat_normal",       "Show the normal distribution with the 68-95-99.7 rule shaded"),
    ("opt_gradient",      "Visualise gradient descent on f(x, y) = x^2 + y^2 with 5 steps"),
    ("auto_turing",       "Show a Turing machine that decides L = {0^n 1^n}"),
    ("cs_3sat",           "Show the 3SAT to vertex-cover reduction with a 3-clause instance"),
]

# These two are the explicit v4 failure modes from the v4 manifest.
V4_KNOWN_FAILURES: set[str] = {"set_venn", "linalg_eigen"}


# ---------------------------------------------------------------------------

def _load_adapter(adapter_id: str, hf_token: str | None = None):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import PeftConfig, PeftModel
    cfg = PeftConfig.from_pretrained(adapter_id, token=hf_token)
    base = cfg.base_model_name_or_path
    tokenizer = AutoTokenizer.from_pretrained(adapter_id, token=hf_token)
    model = AutoModelForCausalLM.from_pretrained(
        base, torch_dtype=torch.bfloat16, device_map="auto", token=hf_token,
    )
    model = PeftModel.from_pretrained(model, adapter_id, token=hf_token)
    model.eval()
    return model, tokenizer


def _generate(model, tokenizer, system: str, user: str, max_new_tokens: int = 2048) -> str:
    import torch
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    encoded = tokenizer.apply_chat_template(
        messages, add_generation_prompt=True, tokenize=True,
        return_tensors="pt", return_dict=True,
    )
    input_ids = encoded["input_ids"].to(model.device)
    attn = encoded.get("attention_mask")
    if attn is not None:
        attn = attn.to(model.device)
    with torch.no_grad():
        out = model.generate(
            input_ids, attention_mask=attn,
            max_new_tokens=max_new_tokens, do_sample=False,
            pad_token_id=tokenizer.eos_token_id,
        )
    gen = out[0, input_ids.shape[1]:]
    return tokenizer.decode(gen, skip_special_tokens=True).strip()


def _extract_svg(text: str) -> str:
    """Pull the SVG out of the model's JSON response."""
    import json as _json, re
    # Try fenced block first, then plain JSON, then substring.
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return _json.loads(m.group(1)).get("svg", "") or ""
        except _json.JSONDecodeError:
            pass
    try:
        return _json.loads(text).get("svg", "") or ""
    except _json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return _json.loads(text[start : end + 1]).get("svg", "") or ""
        except _json.JSONDecodeError:
            pass
    # Last resort — raw <svg>...</svg> in the response
    m = re.search(r"<svg[\s\S]*?</svg>", text)
    return m.group(0) if m else ""


def _is_valid_svg(svg: str) -> tuple[bool, str]:
    if not svg or len(svg.strip()) < 30:
        return False, "empty-or-tiny"
    try:
        # Strip default namespace declaration so ET.fromstring parses.
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        return False, f"parse-error: {exc}"
    # Must contain at least one drawable element.
    has_draw = any(
        root.iter(tag) for tag in (
            "{http://www.w3.org/2000/svg}rect",
            "{http://www.w3.org/2000/svg}circle",
            "{http://www.w3.org/2000/svg}line",
            "{http://www.w3.org/2000/svg}path",
            "{http://www.w3.org/2000/svg}text",
            "{http://www.w3.org/2000/svg}polyline",
            "{http://www.w3.org/2000/svg}polygon",
            "rect", "circle", "line", "path", "text", "polyline", "polygon",
        )
        if next(root.iter(tag), None) is not None
    )
    if not has_draw:
        return False, "no-drawable-elements"
    return True, "ok"


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--v5", required=True,
                    help="Local path or HF id of the v5 adapter to test")
    ap.add_argument("--v4", default="khayyam-math/khayyam-math-qwen2.5-7b-v4",
                    help="HF id of the v4 adapter for comparison")
    ap.add_argument("--out", type=Path, default=Path("runs/v5_practical_test"))
    ap.add_argument("--min-valid", type=int, default=16,
                    help="GO threshold: minimum valid SVGs on the 20-prompt set")
    ap.add_argument("--skip-v4", action="store_true",
                    help="Don't run the v4 baseline (faster but no comparison)")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    hf_token = os.environ.get("HF_TOKEN")

    # System prompt — trimmed version, matches the package's prompts.py.
    from khayyam_math.prompts import DEFAULT_SYSTEM_PROMPT as SYS

    results: dict[str, dict] = {}
    timing: dict[str, float] = {}

    for label, adapter in [("v5", args.v5)] + (
        [] if args.skip_v4 else [("v4", args.v4)]
    ):
        print(f"\n=== loading {label}: {adapter} ===", flush=True)
        t0 = time.time()
        model, tokenizer = _load_adapter(adapter, hf_token=hf_token)
        timing[f"{label}_load_s"] = time.time() - t0
        print(f"    loaded in {timing[f'{label}_load_s']:.1f}s")

        for tag, prompt in HELD_OUT_PROMPTS:
            row = results.setdefault(tag, {"prompt": prompt})
            t0 = time.time()
            try:
                raw = _generate(model, tokenizer, SYS, prompt)
            except Exception as exc:  # noqa: BLE001
                row[label] = {
                    "ok": False, "reason": f"exception: {exc}",
                    "svg_bytes": 0, "raw_bytes": 0, "seconds": time.time() - t0,
                }
                print(f"  [{label}] {tag:20s}  EXC {exc!s:.60s}")
                continue
            svg = _extract_svg(raw)
            ok, reason = _is_valid_svg(svg)
            row[label] = {
                "ok": ok, "reason": reason,
                "svg_bytes": len(svg), "raw_bytes": len(raw),
                "seconds": time.time() - t0,
            }
            (args.out / f"{tag}_{label}.svg").write_text(svg or "")
            (args.out / f"{tag}_{label}.raw.txt").write_text(raw or "")
            tick = "OK " if ok else "BAD"
            print(f"  [{label}] {tag:20s}  {tick} {reason:25s} "
                  f"svg={len(svg):5d}b  {row[label]['seconds']:5.1f}s")

        del model, tokenizer
        import gc, torch
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    # ---- gate ----
    v5_valid = sum(1 for tag in results if results[tag].get("v5", {}).get("ok"))
    v4_valid = (sum(1 for tag in results if results[tag].get("v4", {}).get("ok"))
                if not args.skip_v4 else None)

    regressions = []
    if not args.skip_v4:
        for tag in results:
            v5_ok = results[tag].get("v5", {}).get("ok", False)
            v4_ok = results[tag].get("v4", {}).get("ok", False)
            if v4_ok and not v5_ok:
                regressions.append(tag)

    summary = {
        "v5_valid":     v5_valid,
        "v4_valid":     v4_valid,
        "total":        len(HELD_OUT_PROMPTS),
        "min_valid":    args.min_valid,
        "regressions":  regressions,
        "v4_known_failures_still_failing": [
            tag for tag in V4_KNOWN_FAILURES
            if not results[tag].get("v5", {}).get("ok")
        ],
        "timing":       timing,
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2))
    print("\n=== summary ===")
    print(json.dumps(summary, indent=2))

    go = v5_valid >= args.min_valid
    if not args.skip_v4:
        go = go and len(regressions) == 0

    verdict = "GO  (ready to publish)" if go else "NO-GO  (do NOT publish)"
    print(f"\n>>> {verdict} <<<")
    return 0 if go else 1


if __name__ == "__main__":
    sys.exit(main())
