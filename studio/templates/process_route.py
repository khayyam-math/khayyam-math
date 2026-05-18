"""Deterministic process / cycle diagram route.

"<X> cycle" and "steps of the <process>" prompts (water cycle,
scientific method, carbon cycle, cell cycle) used to fall through to
the sequential route, which stacked LLM-drawn sub-figures and scored
~6/10 with empty placeholder boxes and overlapping text.

This route extracts the ordered stages with ONE LLM call and renders
them deterministically: a ring of nodes with arrows for a cyclic
process, or a vertical flow of boxes for a linear one.  Placement is
computed in Python, so there is never overlap or an empty box.

Public API:
    is_process_prompt(prompt) -> bool
    generate_process_svg(prompt, *, api_key, base_url, model)
        -> (svg, narration) | None
"""
from __future__ import annotations

import json
import math
import re
from typing import Optional

_CYCLE_KW = (
    "water cycle", "carbon cycle", "nitrogen cycle", "rock cycle",
    "cell cycle", "krebs cycle", "citric acid cycle", "calvin cycle",
    "oxygen cycle", "phosphorus cycle", "cardiac cycle", "lytic cycle",
    "life cycle", "business cycle", "product life cycle",
    "sleep cycle", "menstrual cycle", "lysogenic cycle",
)
_PROCESS_KW = (
    "scientific method", "engineering design process",
    "design thinking process", "writing process",
)


def is_process_prompt(prompt: str) -> bool:
    p = f" {(prompt or '').lower()} "
    if any(k in p for k in _CYCLE_KW + _PROCESS_KW):
        return True
    # generic "<word> cycle" (a space before "cycle" excludes
    # "bicycle", "recycle", etc.)
    return bool(re.search(r"\b\w+ cycle\b", p))


PROCESS_SYSTEM = """\
You extract the ordered stages of a process or cycle.  Return ONLY a
JSON object:

  {"title": "<short title>",
   "cyclic": true|false,
   "steps": [{"label": "<1-3 word name>",
              "detail": "<short clause, <= 9 words>"}, ...]}

Rules:
  1. 3 to 7 steps, in the order they happen.
  2. "cyclic" is true when the last stage leads back to the first
     (a true cycle — water cycle, cell cycle); false for a linear
     start-to-finish process (the scientific method).
  3. "label" is 1-3 words.  "detail" is one short clause, at most 9
     words, no trailing period.
  4. Use correct, textbook-accurate stages.

Respond with ONLY the JSON object.
"""


async def llm_process_spec(
    user_prompt: str, *, api_key: str,
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini", timeout_s: float = 22.0,
) -> Optional[dict]:
    import httpx
    payload = {
        "model": model, "max_tokens": 700, "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": PROCESS_SYSTEM},
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
    if not isinstance(steps, list) or not (3 <= len(steps) <= 7):
        return None
    return spec


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines or [""]


_FILL = ("#e8f0fa", "#eaf5ea", "#fdf0e3", "#f3eafa", "#fdeaea",
         "#e6f6f6", "#f6f6e3")
_STROKE = "#5878a8"


_ARROW = ('<defs><marker id="parrow" markerWidth="11" '
          'markerHeight="11" refX="8.5" refY="3" orient="auto">'
          '<path d="M0,0 L9,3 L0,6 Z" fill="#666"/></marker></defs>')


def _node(i, cx, cy, w, h, label, detail):
    """One rounded-rect process node centred at (cx, cy)."""
    x, y = cx - w / 2, cy - h / 2
    fill = _FILL[i % len(_FILL)]
    out = [
        f'<rect id="node_{i}" x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" '
        f'height="{h:.1f}" rx="11" fill="{fill}" stroke="{_STROKE}" '
        f'stroke-width="1.6"/>',
        f'<text x="{cx:.1f}" y="{cy - h / 2 + 23:.1f}" font-size="15" '
        f'text-anchor="middle" font-family="serif" font-weight="bold" '
        f'fill="#1a3252">{_esc(label)}</text>',
    ]
    dl = _wrap(detail, 26)[:2]
    dy = cy - h / 2 + 41
    for ln in dl:
        out.append(
            f'<text x="{cx:.1f}" y="{dy:.1f}" font-size="11.5" '
            f'text-anchor="middle" font-family="serif" fill="#444">'
            f'{_esc(ln)}</text>')
        dy += 14
    return out


def _arrow(x1, y1, x2, y2):
    return (f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" '
            f'y2="{y2:.1f}" stroke="#666" stroke-width="2" '
            f'marker-end="url(#parrow)"/>')


def _render_cycle(title, steps) -> tuple[str, list]:
    n = len(steps)
    nw, nh = 188.0, 78.0
    rnode = 0.5 * math.hypot(nw, nh)
    # radius so adjacent nodes never collide
    R = max(196.0, (nw + 46.0) / (2.0 * math.sin(math.pi / n)))
    margin = 34.0
    cx = cy = R + rnode + margin
    H_extra = 64.0  # title band
    W = 2 * cx
    H = 2 * cy + H_extra
    pos = []
    for i in range(n):
        ang = -math.pi / 2 + i * 2 * math.pi / n
        pos.append((cx + R * math.cos(ang),
                    cy + H_extra + R * math.sin(ang)))
    body: list[str] = [_ARROW]
    # arrows first (under nodes)
    for i in range(n):
        x1, y1 = pos[i]
        x2, y2 = pos[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        d = math.hypot(dx, dy) or 1.0
        ux, uy = dx / d, dy / d
        body.append(_arrow(x1 + ux * rnode, y1 + uy * rnode,
                            x2 - ux * (rnode + 7), y2 - uy * (rnode + 7)))
    narration: list = [{
        "speak": f"This diagram shows {title}, a repeating cycle.",
        "highlight": ["title"]}]
    for i, st in enumerate(steps):
        x, y = pos[i]
        body.extend(_node(i, x, y, nw, nh,
                          st.get("label", f"Stage {i + 1}"),
                          st.get("detail", "")))
        narration.append({
            "speak": (f"{st.get('label', '')}: "
                      f"{st.get('detail', '')}.").strip(": ."),
            "highlight": [f"node_{i}"]})
    narration.append({
        "speak": "The final stage leads back to the first, so the "
                 "cycle repeats.", "highlight": ["node_0"]})
    return _frame(title, W, H, H_extra, body), narration


def _render_linear(title, steps) -> tuple[str, list]:
    n = len(steps)
    nw, nh = 460.0, 70.0
    gap = 46.0
    margin = 30.0
    H_extra = 60.0
    W = nw + 2 * margin
    cx = W / 2
    body: list[str] = [_ARROW]
    narration: list = [{
        "speak": f"This diagram shows the steps of {title}.",
        "highlight": ["title"]}]
    y = H_extra + margin + nh / 2
    for i, st in enumerate(steps):
        body.extend(_node(i, cx, y, nw, nh,
                          st.get("label", f"Step {i + 1}"),
                          st.get("detail", "")))
        narration.append({
            "speak": (f"Step {i + 1}, {st.get('label', '')}: "
                      f"{st.get('detail', '')}.").strip(": ."),
            "highlight": [f"node_{i}"]})
        if i < n - 1:
            body.append(_arrow(cx, y + nh / 2,
                               cx, y + nh / 2 + gap - 6))
        y += nh + gap
    H = y - nh / 2 + margin
    return _frame(title, W, H, H_extra, body), narration


def _frame(title, W, H, h_extra, body) -> str:
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {W:.0f} {H:.0f}" width="{W:.0f}" '
        f'height="{H:.0f}">',
        f'<rect width="{W:.0f}" height="{H:.0f}" fill="white"/>',
        f'<text id="title" x="{W / 2:.0f}" y="{h_extra - 22:.0f}" '
        f'font-size="22" text-anchor="middle" font-family="serif" '
        f'font-weight="bold" fill="#111">{_esc(title)}</text>',
    ]
    out.extend(body)
    out.append("</svg>")
    return "".join(out)


async def generate_process_svg(
    user_prompt: str, *, api_key: str, base_url: str, model: str,
) -> Optional[tuple[str, list]]:
    spec = await llm_process_spec(user_prompt, api_key=api_key,
                                  base_url=base_url, model=model)
    if not spec:
        return None
    steps = spec["steps"]
    clean: list[dict] = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        label = str(s.get("label") or "").strip()
        if not label:
            continue
        clean.append({"label": label,
                      "detail": str(s.get("detail") or "").strip()})
    if not (3 <= len(clean) <= 7):
        return None
    title = str(spec.get("title") or "this process").strip()
    if bool(spec.get("cyclic")):
        return _render_cycle(title, clean)
    return _render_linear(title, clean)
