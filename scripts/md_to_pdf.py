"""Render a Markdown document to a clean A4 PDF.

Used to convert docs/application_*.md into submittable PDFs.

Usage:
    .venv/bin/python scripts/md_to_pdf.py docs/application_openai_startup.md
"""
from __future__ import annotations

import sys
from pathlib import Path

import markdown
from weasyprint import CSS, HTML

CSS_STYLE = """
@page { size: A4; margin: 18mm 16mm; }
body {
  font-family: -apple-system, "Segoe UI", Roboto, "Helvetica Neue",
               Arial, sans-serif;
  font-size: 10.5pt;
  line-height: 1.5;
  color: #1c1c1c;
}
h1 {
  font-size: 20pt; margin: 0 0 0.4em;
  padding-bottom: 0.3em; border-bottom: 2px solid #1c1c1c;
}
h2 {
  font-size: 14pt; margin: 1.4em 0 0.4em;
  color: #1c1c1c;
}
h3 { font-size: 11.5pt; margin: 1em 0 0.3em; }
p { margin: 0.5em 0; }
ul, ol { margin: 0.4em 0; padding-left: 1.4em; }
li { margin: 0.15em 0; }
code, pre {
  font-family: "SF Mono", Menlo, Consolas, monospace;
  font-size: 0.9em;
  background: #f4f4f6;
  border-radius: 4px;
  padding: 0.05em 0.35em;
}
pre { padding: 0.6em 0.8em; overflow-x: auto; }
blockquote {
  border-left: 3px solid #888;
  margin: 0.6em 0; padding: 0.05em 0 0.05em 0.9em;
  color: #555;
  font-style: italic;
}
hr { border: none; border-top: 1px solid #ccc;
     margin: 1.2em 0; }
table {
  border-collapse: collapse; width: 100%;
  margin: 0.7em 0; font-size: 0.93em;
}
th, td { padding: 0.45em 0.7em; border: 1px solid #d0d0d4;
         text-align: left; vertical-align: top; }
th { background: #f4f4f6; }
strong { color: #1c1c1c; }
em { color: #444; }
"""


def render(md_path: Path, out_path: Path) -> None:
    md_text = md_path.read_text(encoding="utf-8")
    html_body = markdown.markdown(
        md_text,
        extensions=["extra", "sane_lists", "tables", "fenced_code",
                    "toc"],
    )
    html_doc = (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{md_path.stem}</title></head>"
        f"<body>{html_body}</body></html>"
    )
    HTML(string=html_doc).write_pdf(
        out_path, stylesheets=[CSS(string=CSS_STYLE)],
    )
    print(f"  → {out_path}  ({out_path.stat().st_size:,} bytes)")


def main():
    if len(sys.argv) < 2:
        # Default: render both application drafts.
        targets = sorted(
            Path("docs").glob("application_*.md"),
        )
    else:
        targets = [Path(p) for p in sys.argv[1:]]
    for md in targets:
        if not md.exists():
            print(f"  skip (missing): {md}")
            continue
        pdf = md.with_suffix(".pdf")
        render(md, pdf)


if __name__ == "__main__":
    main()
