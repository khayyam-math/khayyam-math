"""Sequential step-frame route.

"Show X step by step" prompts scored 3.6/10 on the vision judge: the
system draws ONE figure and crams every step into it, so the steps
overlap into an unreadable pile.

This route decomposes the prompt into an ordered list of steps,
generates a clean figure for EACH step through the normal routing,
and stacks them vertically — each step in its own bordered region
with a "Step N" header.  Every step is a correct standalone figure;
the learner scrolls top-to-bottom through the sequence.

Public API:
    is_sequential_prompt(prompt) -> bool
    generate_sequential_svg(prompt, *, api_key, base_url, model,
                            gen_step) -> (svg, narration) | None
"""
from __future__ import annotations

import json
from typing import Awaitable, Callable, Optional

from studio.templates.panels_route import _embed, _namespace, _viewbox

_SEQ_KEYWORDS: tuple[str, ...] = (
    "step by step", "step-by-step", "steps of", "step by step",
    "each step", "one step at a time", "trace through",
    "walk through the steps", "show the steps", "iteration by iteration",
)


def is_sequential_prompt(prompt: str) -> bool:
    p = f" {(prompt or '').lower()} "
    return any(kw in p for kw in _SEQ_KEYWORDS)


SEQ_SYSTEM = """\
You split a "show it step by step" request into an ORDERED list of
steps.  Return ONLY JSON:

  {"title": "<overall title>",
   "steps": [{"label": "<short step label>",
              "prompt": "<sub-prompt for this step's figure>"}, ...]}

Rules:
  1. 3 to 6 steps, in the order they happen.
  2. Each step's "prompt" is a COMPLETE standalone figure request for
     the state of the work AT that step — it must not mention "step
     by step" or the other steps.  It should show the concrete
     result of that step (e.g. the array after that pass, the matrix
     after that row operation, the equation after that rearrangement).
  3. Use concrete numbers; carry them consistently across steps.
     Double-check every arithmetic result is correct.
  4. The "label" is a few words ("Pass 1", "Eliminate column 1",
     "Substitute back").
  5. Each "prompt" MUST ask for a DRAWN figure — a labelled grid,
     boxes, arrows, a plotted curve, a number line — never a bulleted
     text list.  Begin every prompt with a concrete visual verb
     ("Draw the array as labelled boxes …", "Draw the matrix as a
     grid …").

Respond with ONLY the JSON object.
"""


async def llm_seq_spec(
    user_prompt: str,
    *,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini",
    timeout_s: float = 25.0,
) -> Optional[dict]:
    import httpx
    payload = {
        "model": model,
        "max_tokens": 900,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SEQ_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
    }
    headers = {"content-type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                json=payload, headers=headers)
        if r.status_code != 200:
            return None
        spec = json.loads(
            r.json()["choices"][0]["message"]["content"] or "")
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(spec, dict):
        return None
    steps = spec.get("steps")
    if not isinstance(steps, list) or not (2 <= len(steps) <= 6):
        return None
    return spec


async def generate_sequential_svg(
    user_prompt: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    gen_step: Callable[[str], Awaitable[dict]],
) -> Optional[tuple[str, list]]:
    """Decompose into ordered steps, generate each via gen_step, and
    stack them vertically into one figure.  Returns (svg, narration)
    or None."""
    import asyncio

    spec = await llm_seq_spec(user_prompt, api_key=api_key,
                              base_url=base_url, model=model)
    if not spec:
        return None
    steps = spec["steps"]
    sub_prompts = [str(s.get("prompt") or "").strip() for s in steps]
    if not all(sub_prompts):
        return None
    results = await asyncio.gather(
        *(gen_step(sp) for sp in sub_prompts),
        return_exceptions=True)

    frames: list[tuple[str, str, list]] = []  # (label, svg, narration)
    for i, (step, res) in enumerate(zip(steps, results)):
        if isinstance(res, Exception) or not isinstance(res, dict):
            continue
        ssvg = res.get("svg") or ""
        if "<svg" not in ssvg:
            continue
        nsvg, nnar = _namespace(ssvg, res.get("narration") or [],
                                f"s{i}_")
        frames.append((str(step.get("label") or f"Step {i+1}"),
                       nsvg, nnar))
    if len(frames) < 2:
        return None

    cell_w = 820.0
    hdr_h = 34.0
    gap = 18.0
    top = 58.0
    margin = 24.0
    # Size each cell to its sub-figure's own aspect ratio so a short
    # figure does not sit in a tall box of dead whitespace.
    cell_hs: list[float] = []
    for _, ssvg, _ in frames:
        vb_w, vb_h = _viewbox(ssvg)
        ch = cell_w * (vb_h / vb_w) if vb_w > 0 else 360.0
        cell_hs.append(min(560.0, max(160.0, ch)))
    W = cell_w + 2 * margin
    H = top + sum(hdr_h + ch + gap for ch in cell_hs) + margin

    title = str(spec.get("title") or "")
    out: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" '
        f'height="{H:.0f}">',
        f'<rect x="0" y="0" width="{W:.0f}" height="{H:.0f}" '
        f'fill="white"/>',
    ]
    if title:
        out.append(
            f'<text id="title" x="{W/2:.0f}" y="38" font-size="24" '
            f'text-anchor="middle" font-family="serif" '
            f'font-weight="bold" fill="#111">{title}</text>')
    narration: list = [{
        "speak": (f"Let's work through this step by step"
                  + (f": {title}." if title else ".")),
        "highlight": ["title"] if title else []}]
    fy = top
    for i, (label, ssvg, snar) in enumerate(frames):
        cell_h = cell_hs[i]
        out.append(
            f'<rect x="{margin:.1f}" y="{fy:.1f}" width="{cell_w:.1f}" '
            f'height="{hdr_h}" fill="#eef2f8" stroke="#bbb" '
            f'stroke-width="1"/>')
        out.append(
            f'<text id="step_{i}" x="{margin + 12:.1f}" '
            f'y="{fy + 23:.1f}" font-size="16" font-family="serif" '
            f'font-weight="bold" fill="#1a3a5c">'
            f'Step {i+1}: {label}</text>')
        out.append(
            f'<rect x="{margin:.1f}" y="{fy + hdr_h:.1f}" '
            f'width="{cell_w:.1f}" height="{cell_h:.1f}" fill="none" '
            f'stroke="#bbb" stroke-width="1"/>')
        out.append(_embed(ssvg, margin, fy + hdr_h, cell_w, cell_h))
        narration.append({
            "speak": f"Step {i+1}: {label}.",
            "highlight": [f"step_{i}"]})
        for ph in snar[:1]:
            narration.append(ph)
        fy += hdr_h + cell_h + gap
    narration.append({
        "speak": "Those are all the steps, in order.",
        "highlight": [f"step_{len(frames)-1}"]})
    out.append("</svg>")
    return "".join(out), narration
