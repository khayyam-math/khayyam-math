"""Generate the reference-grounded portion of the training corpus.

For each entry in ``scripts/reference_figures.REFERENCES``:
  1. Fetch the source figure (Wikipedia / Commons / open-textbook URL).
  2. Send it to gpt-4o (vision) as part of a chat-completion request,
     telling the model to produce an SVG that VISUALLY MATCHES the
     reference figure plus a textbook-style narration that names the
     theorem and cites the source.
  3. Append the resulting (prompt, svg, narration) row to the output
     JSONL — same chat-format the synthetic generator uses, plus a
     ``meta.source`` field with the citation.

Default model: gpt-4o-mini (cheap, ~$0.001 per reference).  Its
vision is weaker than full gpt-4o so the SVG won't be a pixel-
perfect rebuild of the reference, but for our purpose — anchoring
the LoRA to canonical figure layouts — gpt-4o-mini is close enough.
Switch to ``--model gpt-4o`` for higher fidelity at ~50× cost when
the reference is geometrically intricate.

Usage:
    OPENAI_API_KEY=… \\
    python scripts/generate_reference_corpus.py \\
        --out ~/.local/share/sevim/distill/teacher_v3_refs.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studio.express import _EXPRESS_SYSTEM, EXPRESS_SCHEMA  # noqa: E402


def _ref_user_text(prompt: str, citation: str) -> str:
    return (
        f"REFERENCE-GROUNDED MODE.\n\n"
        f"User's question: {prompt}\n\n"
        f"Above is a reference figure from a trusted source: {citation}.\n"
        f"\n"
        f"Your job: produce an SVG figure that VISUALLY MATCHES the "
        f"reference (same general layout, same labels, same construction "
        f"steps, same conventional colour role) BUT with our id "
        f"conventions on every visually distinct element so the "
        f"narration can highlight them.  Do not copy the reference "
        f"pixel-for-pixel — re-draw it cleanly in vector form, using "
        f"our notation conventions (Greek letters as Unicode, "
        f"sub/superscripts via tspan).  Add the standard captions you'd "
        f"expect in the cited source.  Cite the source by name in the "
        f"narration (e.g. 'By {citation}, …').\n"
        f"\n"
        f"Then write a textbook-style phrase-timed narration that walks "
        f"through the figure exactly the way the cited source would "
        f"explain it.  Match the source's tone (rigorous Spivak, "
        f"intuitive 3Blue1Brown, terse Rudin, etc.) — pick the right "
        f"flavour for the cited author."
    )


_UA = {
    "User-Agent": (
        "Khayyam-Math-Distillation/1.0 "
        "(https://khayyammath.com; arash_kermani@yahoo.com) httpx"
    ),
}


def _commons_filename(image_url: str) -> str | None:
    """Extract the Commons file basename from a thumb URL or a direct
    File: URL.  Returns just the filename (no ``File:`` prefix).
    """
    # Thumb URLs look like .../commons/thumb/X/XX/Filename.ext/640px-Filename.ext
    # We want just 'Filename.ext'.
    if "/thumb/" in image_url:
        parts = image_url.split("/thumb/")[1].split("/")
        if len(parts) >= 3:
            return parts[2]  # the Filename.ext segment
    # Or .../commons/X/XX/Filename.ext directly.
    if "/commons/" in image_url:
        parts = image_url.split("/commons/")[1].split("/")
        if len(parts) >= 3:
            return parts[2]
    return None


async def _resolve_commons_url(filename: str, client: httpx.AsyncClient
                               ) -> str | None:
    """Look up the live thumb URL for a Commons file via the API.
    Many of the static thumb URLs we hand-rolled were stale; the API
    always returns the current canonical thumb URL.
    """
    api = "https://commons.wikimedia.org/w/api.php"
    params = {
        "action": "query",
        "titles": f"File:{filename}",
        "prop": "imageinfo",
        "iiprop": "url",
        "iiurlwidth": "800",
        "format": "json",
    }
    try:
        r = await client.get(api, params=params, headers=_UA, timeout=20.0)
        if r.status_code != 200:
            return None
        data = r.json()
        for page in (data.get("query", {}).get("pages", {}) or {}).values():
            ii = (page.get("imageinfo") or [{}])[0]
            return ii.get("thumburl") or ii.get("url")
    except Exception:  # noqa: BLE001
        return None
    return None


async def _generate_one(
    entry: dict, base_url: str, api_key: str, model: str,
    client: httpx.AsyncClient,
) -> dict | None:
    """Resolve + fetch image, call gpt-4o-mini with it, return parsed
    JSON.  When image fetch fails, fall back to a text-only path:
    we still cite the source and ask the model to draw the figure
    'as it appears in <citation>', without an image attached.
    """
    data_url: str | None = None
    fname = _commons_filename(entry["image_url"])
    image_url = entry["image_url"]
    # Try the API to get a fresh URL — handles cases where the static
    # thumb URL we hard-coded has rotated to a different MD5 path.
    if fname:
        resolved = await _resolve_commons_url(fname, client)
        if resolved:
            image_url = resolved
    try:
        img_resp = await client.get(
            image_url, timeout=30.0,
            follow_redirects=True, headers=_UA,
        )
        if img_resp.status_code == 200:
            content_type = img_resp.headers.get("content-type", "image/png")
            if "svg" in content_type:
                try:
                    import cairosvg
                    png = cairosvg.svg2png(bytestring=img_resp.content,
                                           output_width=900)
                    content_type = "image/png"
                except Exception as exc:  # noqa: BLE001
                    print(f"  ! SVG→PNG: {exc}", flush=True)
                    png = None
            else:
                png = img_resp.content
            if png:
                b64 = base64.b64encode(png).decode("ascii")
                data_url = f"data:{content_type};base64,{b64}"
        else:
            print(f"  ! image fetch {img_resp.status_code} for "
                  f"{entry['domain']:18}: {fname or image_url}",
                  flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  ! image fetch error: {type(exc).__name__}: {exc}",
              flush=True)

    # Build the chat-completion payload.  When the image fetch
    # succeeded, attach it; otherwise fall through to text-only mode
    # — still citation-anchored, just without visual grounding.
    user_blocks: list[dict] = []
    if data_url:
        user_blocks.append({"type": "image_url", "image_url": {"url": data_url}})
    user_blocks.append({"type": "text", "text":
                        _ref_user_text(entry["prompt"], entry["citation"])})

    payload = {
        "model": model,
        "max_tokens": 6000,
        "temperature": 0.2,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "express_figure",
                "schema": EXPRESS_SCHEMA,
                "strict": True,
            },
        },
        "messages": [
            {"role": "system", "content": _EXPRESS_SYSTEM},
            {"role": "user", "content": user_blocks},
        ],
    }
    try:
        r = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload, timeout=180,
        )
        if r.status_code != 200:
            print(f"  ! gpt-4o {r.status_code}: {r.text[:200]}", flush=True)
            return None
        content = r.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as exc:  # noqa: BLE001
        print(f"  ! gen error: {type(exc).__name__}: {exc}", flush=True)
        return None


def _build_row(entry: dict, result: dict) -> dict:
    """Pack a reference result into the same chat-format the synthetic
    generator uses, plus a meta.source field for traceability."""
    return {
        "messages": [
            {"role": "system", "content": _EXPRESS_SYSTEM},
            {"role": "user", "content": entry["prompt"]},
            {"role": "assistant", "content": json.dumps({
                "svg": result.get("svg", ""),
                "narration": result.get("narration") or [],
                "title": result.get("title") or "",
            }, ensure_ascii=False)},
        ],
        "meta": {
            "mode": "reference",
            "prompt": entry["prompt"],
            "source": entry["citation"],
            "domain": entry["domain"],
            "image_url": entry["image_url"],
            "n_phrases": len(result.get("narration") or []),
        },
    }


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default="gpt-4o-mini",
                    help="Vision-capable teacher (default gpt-4o-mini; "
                         "use gpt-4o for higher fidelity)")
    ap.add_argument("--base-url",
                    default=os.environ.get("SEVIM_VLLM_URL",
                                           "https://api.openai.com/v1"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args(argv)

    from service.secrets import bootstrap as _boot
    _boot()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        return 1

    from scripts.reference_figures import REFERENCES

    # Resume: skip prompts already present in --out.
    seen: set[str] = set()
    if args.out.exists():
        for line in args.out.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            seen.add((row.get("meta") or {}).get("prompt", ""))

    pending = [r for r in REFERENCES if r["prompt"] not in seen]
    if args.limit:
        pending = pending[: args.limit]

    print(f"=== reference-grounded corpus generation ===")
    print(f"  model:          {args.model}")
    print(f"  output:         {args.out}")
    print(f"  references:     {len(REFERENCES)}")
    print(f"  already done:   {len(seen)}")
    print(f"  pending:        {len(pending)}")

    if not pending:
        print("\nNothing to do.")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(args.concurrency)
    fh = args.out.open("a")
    stats = {"ok": 0, "fail": 0}
    t0 = time.monotonic()

    async with httpx.AsyncClient() as client:
        async def run(entry: dict) -> None:
            async with sem:
                result = await _generate_one(entry, args.base_url,
                                             api_key, args.model, client)
            if result is None or not result.get("svg"):
                stats["fail"] += 1
                return
            row = _build_row(entry, result)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            stats["ok"] += 1
            n_done = stats["ok"] + stats["fail"]
            print(f"  [{n_done}/{len(pending)}] {entry['domain']:18} "
                  f"phrases={row['meta']['n_phrases']:2}  "
                  f"{entry['prompt'][:55]!r}", flush=True)

        await asyncio.gather(*(run(e) for e in pending))

    fh.close()
    elapsed = time.monotonic() - t0
    print(f"\n=== done in {elapsed:.1f}s ===")
    print(json.dumps(stats, indent=2))
    print(f"\noutput: {args.out}  ({args.out.stat().st_size:,} bytes)")
    return 0 if stats["ok"] > 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
