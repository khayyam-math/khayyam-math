"""Extract math figures from legal textbook PDFs using gpt-4o-mini vision.

For each page: render with pymupdf, send the PNG to gpt-4o-mini and ask
it to return bounding boxes of any math figures plus a one-line user
prompt that would naturally request that figure.  Crop the bboxes,
save the crops, write a JSONL manifest the corpus generator can read.

The prompt + image are tightly bounded so the model returns
structured JSON via response_format.

Usage:
    python scripts/extract_textbook_figures.py \\
        --textbook-dir ~/.local/share/sevim/textbooks \\
        --out ~/.local/share/sevim/distill/textbook_figures.jsonl \\
        --max-pages-per-book 200

Cost: ~$0.0015 / page × ~1500 pages ≈ $2.25 for a typical full run.
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

# Per-book metadata: filename → (full citation, default domain hint).
# Citations are propagated into the narration so the LoRA learns to
# attribute claims to the source.
BOOK_META: dict[str, tuple[str, str]] = {
    "Murphy_PML_book1.pdf":  ("Murphy, Probabilistic Machine Learning: An Introduction", "machine learning"),
    "Murphy_PML_book2.pdf":  ("Murphy, Probabilistic Machine Learning: Advanced Topics", "machine learning"),
    "Bishop_PRML.pdf":       ("Bishop, Pattern Recognition and Machine Learning", "machine learning"),
    "ISLR.pdf":              ("James, Witten, Hastie, Tibshirani — Introduction to Statistical Learning", "statistics"),
    "MML.pdf":               ("Deisenroth, Faisal, Ong — Mathematics for Machine Learning", "linear algebra"),
    "Boyd_Convex_Optimization.pdf":
                             ("Boyd, Vandenberghe — Convex Optimization", "optimisation"),
    "Boyd_VMLS.pdf":         ("Boyd, Vandenberghe — Introduction to Applied Linear Algebra", "linear algebra"),
    "DiveIntoDL.pdf":        ("Zhang, Lipton, Li, Smola — Dive Into Deep Learning", "deep learning"),
}


_VISION_PROMPT = (
    "You're looking at a single page from a math/CS/ML textbook.  "
    "Identify each STANDALONE figure (diagram, graph, plot, geometric "
    "construction).  Skip inline equations, tables of numbers, "
    "purely-textual content, code blocks, page headers / footers / "
    "page numbers.\n"
    "\n"
    "For each figure, return:\n"
    "  - bbox: [x0, y0, x1, y1] in PIXELS of the page image as I "
    "    rendered it (assume the image's natural width/height).  "
    "    Pad the box ~10 px around the figure so labels aren't "
    "    clipped, and INCLUDE the caption beneath the figure when "
    "    one is present.\n"
    "  - label:  the figure's number / label as printed (e.g. "
    "    'Figure 3.4', 'Fig. 1', 'Table 2'); empty string if none.\n"
    "  - description: one short sentence describing what the figure "
    "    shows (e.g. 'Riemann sum approximating integral of x^2 "
    "    with 8 rectangles').\n"
    "  - suggested_user_prompt: a one-line natural-language question "
    "    a learner might type to request a figure like this — short, "
    "    self-contained, no references to figure numbers (e.g. "
    "    'show a Riemann sum approximating integral of x^2 from 0 "
    "    to 2 using 8 rectangles').\n"
    "\n"
    "Quality bar: prefer fewer, cleaner figures over noisy crops.  "
    "Skip any region that is 90% text or smaller than ~80 pixels in "
    "either dimension.  If the page has no figures, return "
    "{\"figures\": []}."
)


_VISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "figures": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "bbox": {"type": "array", "items": {"type": "number"},
                             "minItems": 4, "maxItems": 4},
                    "label": {"type": "string"},
                    "description": {"type": "string"},
                    "suggested_user_prompt": {"type": "string"},
                },
                "required": ["bbox", "label", "description",
                             "suggested_user_prompt"],
            },
        },
    },
    "required": ["figures"],
}


async def _identify_figures_on_page(
    page_png: bytes, page_w: int, page_h: int,
    api_key: str, base_url: str, model: str,
    client: httpx.AsyncClient,
) -> list[dict]:
    b64 = base64.b64encode(page_png).decode("ascii")
    payload = {
        "model": model,
        "max_tokens": 1500,
        "temperature": 0.0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "figures", "schema": _VISION_SCHEMA,
                            "strict": True},
        },
        "messages": [
            {"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text":
                    f"{_VISION_PROMPT}\n\n"
                    f"(Image dimensions: {page_w} × {page_h} pixels.)"},
            ]},
        ],
    }
    try:
        r = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload, timeout=60,
        )
        if r.status_code != 200:
            return []
        return json.loads(r.json()["choices"][0]["message"]["content"]) \
                .get("figures", [])
    except Exception:  # noqa: BLE001
        return []


def _crop_bbox(page_png: bytes, bbox: list, page_w: int, page_h: int) -> bytes | None:
    """Crop a sub-image from the rendered page PNG using PIL.  Skip
    obviously-bad crops (too small, out-of-bounds)."""
    from PIL import Image
    import io
    if len(bbox) != 4:
        return None
    x0, y0, x1, y1 = (max(0, int(bbox[0])), max(0, int(bbox[1])),
                     min(page_w, int(bbox[2])), min(page_h, int(bbox[3])))
    if x1 - x0 < 80 or y1 - y0 < 80:
        return None
    if x1 - x0 > page_w or y1 - y0 > page_h:
        return None
    img = Image.open(io.BytesIO(page_png))
    crop = img.crop((x0, y0, x1, y1))
    out = io.BytesIO()
    crop.save(out, format="PNG", optimize=True)
    return out.getvalue()


async def _process_book(
    pdf_path: Path, out_jsonl: Path, crops_dir: Path,
    api_key: str, base_url: str, model: str,
    max_pages: int | None, client: httpx.AsyncClient,
    sem: asyncio.Semaphore, seen_pages: set[tuple[str, int]],
) -> dict:
    import fitz
    citation, default_domain = BOOK_META.get(
        pdf_path.name, (pdf_path.stem.replace("_", " "), "math"))
    print(f"\n=== {pdf_path.name} — {citation} ===", flush=True)
    doc = fitz.open(str(pdf_path))
    pages_total = len(doc)
    pages_to_scan = min(pages_total, max_pages) if max_pages else pages_total
    stats = {"book": pdf_path.name, "pages_total": pages_total,
             "pages_scanned": pages_to_scan,
             "pages_with_figures": 0, "figures_kept": 0,
             "figures_dropped": 0}
    fh = out_jsonl.open("a")

    async def _do_page(pno: int) -> None:
        if (pdf_path.name, pno) in seen_pages:
            return
        page = doc[pno]
        # Render at 150 DPI.  Higher DPI → bigger PNG → vision tokens
        # increase quadratically; 150 is the sweet spot for legibility
        # without runaway cost.
        pix = page.get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72))
        png = pix.tobytes("png")
        page_w, page_h = pix.width, pix.height
        async with sem:
            figs = await _identify_figures_on_page(
                png, page_w, page_h, api_key, base_url, model, client)
        if figs:
            stats["pages_with_figures"] += 1
        for fi, fig in enumerate(figs):
            crop = _crop_bbox(png, fig["bbox"], page_w, page_h)
            if not crop:
                stats["figures_dropped"] += 1
                continue
            fname = (f"{pdf_path.stem}__p{pno:04d}_f{fi}.png")
            fpath = crops_dir / fname
            fpath.write_bytes(crop)
            row = {
                "prompt": fig["suggested_user_prompt"],
                "image_path": str(fpath),
                "citation": citation,
                "domain": default_domain,
                "label": fig.get("label", ""),
                "description": fig.get("description", ""),
                "source_book": pdf_path.name,
                "source_page": pno + 1,
            }
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            stats["figures_kept"] += 1
        if pno % 25 == 0:
            print(f"  [page {pno+1:4}/{pages_to_scan}]  "
                  f"kept={stats['figures_kept']:4}  "
                  f"dropped={stats['figures_dropped']:3}",
                  flush=True)

    # Process pages concurrently (capped via outer semaphore).
    await asyncio.gather(*(_do_page(pno) for pno in range(pages_to_scan)))

    fh.close()
    print(f"  → {pdf_path.name} done.  "
          f"figures kept={stats['figures_kept']}, "
          f"dropped={stats['figures_dropped']}, "
          f"pages with figures={stats['pages_with_figures']}/{pages_to_scan}",
          flush=True)
    return stats


async def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--textbook-dir", type=Path,
                    default=Path.home() / ".local/share/sevim/textbooks")
    ap.add_argument("--out", type=Path, required=True,
                    help="Output JSONL (figure manifest)")
    ap.add_argument("--crops-dir", type=Path, default=None,
                    help="Where to save cropped figure PNGs "
                         "(default: <out_dir>/figure_crops/)")
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--base-url",
                    default=os.environ.get("SEVIM_VLLM_URL",
                                           "https://api.openai.com/v1"))
    ap.add_argument("--concurrency", type=int, default=6,
                    help="Concurrent vision calls per book")
    ap.add_argument("--max-pages-per-book", type=int, default=None)
    ap.add_argument("--books", nargs="*", default=None,
                    help="Only process these PDFs (basenames)")
    args = ap.parse_args(argv)

    from service.secrets import bootstrap as _boot
    _boot()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        return 1

    crops_dir = args.crops_dir or args.out.parent / "figure_crops"
    crops_dir.mkdir(parents=True, exist_ok=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)

    # Resume support: skip pages already in output.
    seen_pages: set[tuple[str, int]] = set()
    if args.out.exists():
        for line in args.out.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                seen_pages.add((row["source_book"], row["source_page"] - 1))
            except (json.JSONDecodeError, KeyError):
                continue
    print(f"resuming with {len(seen_pages)} pages already processed",
          flush=True)

    pdfs = sorted(args.textbook_dir.glob("*.pdf"))
    if args.books:
        pdfs = [p for p in pdfs if p.name in args.books]
    if not pdfs:
        print(f"No PDFs in {args.textbook_dir}", file=sys.stderr)
        return 1

    sem = asyncio.Semaphore(args.concurrency)
    all_stats = []
    t0 = time.monotonic()
    async with httpx.AsyncClient() as client:
        for pdf in pdfs:
            try:
                stats = await _process_book(
                    pdf, args.out, crops_dir, api_key,
                    args.base_url, args.model,
                    args.max_pages_per_book, client, sem, seen_pages)
                all_stats.append(stats)
            except Exception as exc:  # noqa: BLE001
                print(f"  !! {pdf.name}: {type(exc).__name__}: {exc}",
                      flush=True)

    total_kept = sum(s.get("figures_kept", 0) for s in all_stats)
    total_pages = sum(s.get("pages_scanned", 0) for s in all_stats)
    elapsed = time.monotonic() - t0
    print(f"\n=== done in {elapsed:.1f}s "
          f"({total_kept} figures from {total_pages} pages "
          f"across {len(all_stats)} books) ===")
    print(f"manifest: {args.out}")
    print(f"crops:    {crops_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
