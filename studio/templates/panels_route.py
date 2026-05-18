"""Multi-panel composition route.

For prompts that ask for several coordinated sub-figures — "compare X
and Y side by side", "show the graph and its adjacency matrix" — the
LLM-SVG path fails: it draws one panel, or overlaps them, or omits
one (vision-judge ~3-5/10 on the multipanel / crossref categories).

This route instead:
  1. asks the LLM to DECOMPOSE the prompt into 2-4 self-contained
     single-figure sub-prompts;
  2. generates each panel independently through the normal routing
     (so each panel still benefits from templates / Graphviz /
     matplotlib / the LLM-SVG path);
  3. composites the panels into a deterministic grid — each panel in
     its own region, with its own title, as a nested <svg> — so the
     panels never collide and the layout is correct by construction.

Public API:
    is_panels_prompt(prompt) -> bool
    generate_panels_svg(prompt, *, api_key, base_url, model,
                        gen_panel) -> (svg, narration) | None
"""
from __future__ import annotations

import json
import re
from typing import Awaitable, Callable, Optional

_PANELS_KEYWORDS: tuple[str, ...] = (
    "compare ", " versus ", " vs ", "side by side", "side-by-side",
    "two panels", "three panels", "four panels", "multi-panel",
    "cross-referenc", "next to each other", "in two panels",
    "in three panels", "panel comparison",
)


def is_panels_prompt(prompt: str) -> bool:
    p = f" {(prompt or '').lower()} "
    return any(kw in p for kw in _PANELS_KEYWORDS)


PANELS_SYSTEM = """\
You split a figure request into a small set of side-by-side PANELS.

Return ONLY JSON:
  {"title": "<overall title>",
   "panels": [{"title": "<panel title>", "prompt": "<sub-prompt>"},
              ...]}

Rules:
  1. 2 to 4 panels.  Each panel is ONE self-contained figure.
  2. Each panel's "prompt" must be a complete standalone figure
     request that does NOT itself mention panels, comparison or
     "side by side" — it describes just that one figure.
  3. Keep the panels genuinely comparable / complementary: the same
     concept at different settings, a thing and its dual
     representation, before/after, etc.
  4. The overall "title" names what the comparison shows.

Respond with ONLY the JSON object.
"""


async def llm_panels_spec(
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
        "max_tokens": 700,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": PANELS_SYSTEM},
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
    panels = spec.get("panels")
    if not isinstance(panels, list) or not (2 <= len(panels) <= 4):
        return None
    return spec


def _namespace(svg: str, narration: list, prefix: str
               ) -> tuple[str, list]:
    """Prefix every id and id-reference in one panel's SVG so panels
    composited into one document cannot collide on ids."""
    svg = re.sub(r'(\bid\s*=\s*["\'])([^"\']+)',
                 lambda m: f"{m.group(1)}{prefix}{m.group(2)}", svg)
    svg = re.sub(r'(\burl\(#)([^)]+)\)',
                 lambda m: f"{m.group(1)}{prefix}{m.group(2)})", svg)
    svg = re.sub(r'((?:xlink:)?href\s*=\s*["\']#)([^"\']+)',
                 lambda m: f"{m.group(1)}{prefix}{m.group(2)}", svg)
    out: list = []
    for ph in narration or []:
        if not isinstance(ph, dict):
            continue
        h = ph.get("highlight") or []
        if isinstance(h, str):
            h = [h]
        np_ = dict(ph)
        np_["highlight"] = [f"{prefix}{x}" for x in h
                            if isinstance(x, str) and x.strip()]
        out.append(np_)
    return svg, out


def _viewbox(svg: str) -> tuple[float, float]:
    m = re.search(r'viewBox\s*=\s*["\']([-\d.\seE]+)["\']', svg)
    if m:
        p = m.group(1).split()
        if len(p) == 4:
            try:
                return float(p[2]), float(p[3])
            except ValueError:
                pass
    return 900.0, 650.0


def _embed(svg: str, x: float, y: float, w: float, h: float) -> str:
    """Rewrite a panel SVG's root tag so it nests at (x,y) sized w*h,
    keeping its own viewBox so the content scales into the cell."""
    m = re.search(r"<svg\b[^>]*>", svg)
    if not m:
        return svg
    vb = re.search(r'viewBox\s*=\s*["\']([-\d.\seE]+)["\']', m.group(0))
    vbs = vb.group(1) if vb else f"0 0 {_viewbox(svg)[0]} {_viewbox(svg)[1]}"
    xlink = ('xmlns:xlink="http://www.w3.org/1999/xlink" '
             if "xmlns:xlink" in m.group(0) else "")
    new = (f'<svg xmlns="http://www.w3.org/2000/svg" {xlink}'
           f'x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
           f'viewBox="{vbs}" preserveAspectRatio="xMidYMid meet">')
    return svg[:m.start()] + new + svg[m.end():]


async def generate_panels_svg(
    user_prompt: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    gen_panel: Callable[[str], Awaitable[dict]],
) -> Optional[tuple[str, list]]:
    """Decompose the prompt into panels, generate each via gen_panel
    (an async callable: sub-prompt -> express result dict), and
    composite them into one figure.  Returns (svg, narration) or None.
    """
    import asyncio

    spec = await llm_panels_spec(user_prompt, api_key=api_key,
                                 base_url=base_url, model=model)
    if not spec:
        return None
    panels = spec["panels"]
    sub_prompts = [str(p.get("prompt") or "").strip() for p in panels]
    if not all(sub_prompts):
        return None
    results = await asyncio.gather(
        *(gen_panel(sp) for sp in sub_prompts),
        return_exceptions=True)

    cells: list[tuple[str, str, list]] = []  # (title, svg, narration)
    for i, (panel, res) in enumerate(zip(panels, results)):
        if isinstance(res, Exception) or not isinstance(res, dict):
            continue
        psvg = res.get("svg") or ""
        if "<svg" not in psvg:
            continue
        nsvg, nnar = _namespace(psvg, res.get("narration") or [],
                                f"p{i}_")
        cells.append((str(panel.get("title") or f"Panel {i+1}"),
                      nsvg, nnar))
    if len(cells) < 2:
        return None

    # Grid: <=3 panels in a row, 4 panels as 2x2.
    n = len(cells)
    cols = n if n <= 3 else 2
    rows = (n + cols - 1) // cols
    cell_w, cell_h = 540.0, 460.0
    ptitle_h = 30.0
    gap = 16.0
    top = 56.0
    W = cols * cell_w + (cols + 1) * gap
    H = top + rows * (cell_h + ptitle_h + gap) + gap

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
            f'<text id="title" x="{W/2:.0f}" y="36" font-size="24" '
            f'text-anchor="middle" font-family="serif" '
            f'font-weight="bold" fill="#111">{title}</text>')
    narration: list = []
    if title:
        narration.append({"speak": f"This figure compares: {title}.",
                           "highlight": ["title"]})
    for i, (ptitle, psvg, pnar) in enumerate(cells):
        r, c = divmod(i, cols)
        px = gap + c * (cell_w + gap)
        py = top + r * (cell_h + ptitle_h + gap)
        out.append(
            f'<text id="ptitle_{i}" x="{px + cell_w/2:.1f}" '
            f'y="{py + 20:.1f}" font-size="16" text-anchor="middle" '
            f'font-family="serif" font-weight="bold" fill="#111">'
            f'{ptitle}</text>')
        out.append(
            f'<rect x="{px:.1f}" y="{py + ptitle_h:.1f}" '
            f'width="{cell_w:.1f}" height="{cell_h:.1f}" fill="none" '
            f'stroke="#ccc" stroke-width="1"/>')
        out.append(_embed(psvg, px, py + ptitle_h, cell_w, cell_h))
        narration.append({
            "speak": f"Panel {i+1}: {ptitle}.",
            "highlight": [f"ptitle_{i}"]})
        # Carry up to 2 of the panel's own phrases.
        for ph in pnar[:2]:
            narration.append(ph)
    out.append("</svg>")
    return "".join(out), narration
