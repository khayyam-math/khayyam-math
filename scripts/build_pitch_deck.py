"""Build the Hub71 pitch-deck PDF as one slide per page.

Each slide is a 16:9 page styled in clean monochrome with one
section heading and the body text. The product-screenshot slide
embeds the 4 gallery PNGs.

Usage:
    .venv/bin/python scripts/build_pitch_deck.py
"""
from __future__ import annotations

from pathlib import Path

from weasyprint import CSS, HTML

ROOT = Path(__file__).resolve().parent.parent
SHOT = ROOT / "service" / "static" / "screenshots"
OUT = ROOT / "docs" / "pitch_deck_hub71.pdf"

SLIDES = [
    # (Slide kicker, title, body HTML)
    ("01",
     "Khayyam Math",
     """<p class="lede">Voice-narrated math figures,
        generated live from a one-line prompt.</p>
        <p class="meta">khayyammath.com · Hub71+ AI · Cohort 20</p>
        <p class="meta">UAE-registered intellectual property ·
        Live in production on AWS Fargate since May 2026</p>"""),
    ("02 / Problem",
     "Math figures are the hardest part of math learning.",
     """<p>A textbook only has the figure its author drew. The learner
        stuck at 11 PM on a problem the textbook didn't cover gets
        nothing.</p>
        <p>Existing AI tutors (ChatGPT, Khanmigo, Brilliant) are
        <strong>text-first</strong>. Very few generate accurate,
        synchronised visual diagrams for the specific question
        being asked.</p>
        <p>The ones that do produce figures tend to have overlap,
        off-canvas elements, or labels that don't match the
        narration.</p>"""),
    ("03 / Solution",
     "One prompt → a custom figure + synced narration, in 3–15 s.",
     """<ul class="bullets">
          <li><strong>Three layout engines</strong>, routed per
              prompt: deterministic Python templates (matrices,
              equations), <strong>Graphviz</strong> for graph-shaped
              figures, LLM-driven generation for everything else.</li>
          <li><strong>Vision-audit retry loop</strong>: every
              generated figure is checked against its narration
              before reaching the learner.</li>
          <li><strong>Phrase-timed audio</strong> with synchronised
              visual highlighting — the spoken sentence and the
              spotlight on the canvas always agree.</li>
        </ul>"""),
    ("04 / Product",
     "Real output, not mockups.",
     """<div class="gallery">
          <figure>
            <img src="file://{SHOT}/landing_linalg_matrix_mul.png">
            <figcaption>"multiply [[1,2],[3,4]] and [[5,6],[7,8]]"</figcaption>
          </figure>
          <figure>
            <img src="file://{SHOT}/landing_auto_dfa_ab.png">
            <figcaption>"DFA for L = (a|b)* ending in ab"</figcaption>
          </figure>
          <figure>
            <img src="file://{SHOT}/landing_geo_pythagoras.png">
            <figcaption>"Pythagorean theorem with a 3-4-5 triangle"</figcaption>
          </figure>
          <figure>
            <img src="file://{SHOT}/landing_trig_unit_circle.png">
            <figcaption>"unit circle with sin and cos at 30, 45, 60"</figcaption>
          </figure>
        </div>"""),
    ("05 / Why now",
     "Frontier LLMs + GCC/India mobile penetration both crossed thresholds in the last 24 months.",
     """<ul class="bullets">
          <li>gpt-4o and Claude Sonnet 4 are the first models reliable
              enough at <strong>structured-output figure generation</strong>
              to drive a production tutor.</li>
          <li>Mobile penetration in the GCC + India means
              <strong>self-study math tutoring</strong> has tripled
              its addressable market.</li>
          <li>UAE policy push for AI-in-education (MBZUAI, AI71,
              UAE Ministry of Education) creates a friendly regulatory
              + procurement landscape for the first wedge.</li>
        </ul>"""),
    ("06 / Market",
     "Big global pie, focused first wedge.",
     """<table class="market">
          <tr><th>Layer</th><th>Size</th><th>Khayyam Math first wedge</th></tr>
          <tr><td>TAM — global K-12 + early-undergrad math tutoring</td>
              <td>~$80B</td><td>—</td></tr>
          <tr><td>SAM — UAE/GCC + India English-language self-study segment</td>
              <td>~$6B</td><td>—</td></tr>
          <tr><td>SOM — UAE/GCC tutoring-centre seats over first 3 years</td>
              <td>~$80M</td>
              <td>~3,000 centres in UAE alone, fragmented competition</td></tr>
        </table>
        <p class="meta">Sources: HolonIQ ed-tech market tracker; UAE
        Ministry of Education private-tutoring market study.</p>"""),
    ("07 / Traction",
     "Built, deployed, measured.",
     """<ul class="bullets">
          <li><strong>Live</strong> on AWS Fargate (us-east-1) since
              May 2026. 93 production deploys to date.</li>
          <li><strong>Full production stack</strong>: magic-link auth,
              per-user rate limit, cost guard, content filter, vision
              audit, telemetry pipeline.</li>
          <li><strong>4 published rebuild iterations</strong> of the
              SVG generator with measured quality gains at each step.
              JAIR-target paper in draft.</li>
          <li><strong>Quality scorer</strong> (1.83 M-param graph
              neural net) — 71 % pairwise win-rate on real
              (broken, fixed) layout pairs. Rigorous evaluation
              harness already in place.</li>
          <li><strong>23 unit tests + Playwright UX-audit suite</strong>
              for the production routing layer.</li>
        </ul>"""),
    ("08 / Competition",
     "Khayyam Math is the only product generating a custom visual + synchronised audio for the learner's specific question.",
     """<table class="compete">
          <tr><th></th><th>Custom visual</th><th>Synced audio</th><th>Per-question</th></tr>
          <tr><td>Khan Academy / Khanmigo</td><td>✗</td><td>partial</td><td>partial</td></tr>
          <tr><td>Brilliant</td><td>fixed-curriculum</td><td>✗</td><td>✗</td></tr>
          <tr><td>Wolfram Alpha</td><td>symbolic only</td><td>✗</td><td>✓</td></tr>
          <tr><td>ChatGPT / Claude direct</td><td>✗</td><td>✗</td><td>✓</td></tr>
          <tr class="us"><td><strong>Khayyam Math</strong></td><td>✓</td><td>✓</td><td>✓</td></tr>
        </table>"""),
    ("09 / Architecture moat",
     "The hard parts aren't the LLM — they're the integration around it.",
     """<ul class="bullets">
          <li><strong>Multi-tool routing</strong>: most competitors
              use one approach (LLM-only or template-only); per-figure
              quality varies wildly. Khayyam routes per prompt.</li>
          <li><strong>Vision-audit retry</strong>: most products
              ship whatever the LLM emits. We verify before showing.</li>
          <li><strong>Narration ↔ visual sync</strong>: phrase-timed
              highlighting is non-trivial to rebuild from scratch.</li>
          <li><strong>All differentiation is in the integration
              architecture</strong>, not the LLM (which is
              commodity and substitutable).</li>
          <li>UAE intellectual-property filing protects the
              architecture in our home market.</li>
        </ul>"""),
    ("10 / Founders",
     "Solo technical founder, opening to a co-founder this quarter.",
     """<p><strong>Arash Kermani</strong> — Founder, CEO, technical lead.
        Built the entire production stack (FastAPI · ECS Fargate ·
        Postgres · S3 · custom JavaScript canvas viewer) and the
        prompt-→-SVG architecture itself. Sole author of the
        UAE-registered intellectual property. JAIR paper in draft;
        Zenodo preprint forthcoming this month.</p>
        <p class="meta">Adding a co-founder/CTO this quarter to
        own cloud-ops and customer delivery in the GCC region.</p>"""),
    ("11 / Plans for Hub71 + Abu Dhabi",
     "Five concrete things we'd do with the cohort.",
     """<ol class="bullets">
          <li><strong>Relocate primary operations to Abu Dhabi.</strong>
              UAE IP already filed; founder UAE-based.</li>
          <li><strong>Workshop paper with MBZUAI</strong> on the
              express-loop + multi-tool-routing architecture, run a
              controlled study with MBZUAI students.</li>
          <li><strong>Sell into Abu Dhabi public-education and
              private-tutoring markets</strong> as the first
              commercial wedge — before KSA and India.</li>
          <li><strong>Use Hub71's Nvidia + AWS partnerships</strong> to
              scale the local fine-tune pipeline (currently a single
              RTX 5090).</li>
          <li><strong>Co-market with AI71 + Core42</strong> as a
              flagship UAE-built educational AI product.</li>
        </ol>"""),
    ("12 / Ask",
     "Hub71+ AI Cohort 20 — accept us.",
     """<ul class="bullets big">
          <li><strong>AED 250 k flexible incentives</strong> — AWS,
              Nvidia, MBZUAI, Google for Startups credits to scale
              the fine-tune + serving stack.</li>
          <li><strong>AED 250 k SAFE-note cash</strong> — to fund
              the first paid pilot with an Abu Dhabi tutoring chain.</li>
          <li><strong>Introductions</strong> to MBZUAI, AI71, Core42,
              UAE Ministry of Education, Abu Dhabi private-tutoring
              chains.</li>
        </ul>
        <p class="meta" style="margin-top:1.5em">Live product:
        <strong>khayyammath.com</strong> · Founder:
        Arash Kermani · UAE-registered.</p>"""),
]

CSS_STYLE = """
@page {
  size: 297mm 167mm;  /* 16:9 close to A4 landscape */
  margin: 0;
}
body { margin: 0; padding: 0;
       font-family: -apple-system, "Segoe UI", Roboto,
                    "Helvetica Neue", Arial, sans-serif;
       color: #14141a; }
.slide {
  page-break-after: always;
  width: 297mm; height: 167mm;
  box-sizing: border-box;
  padding: 14mm 18mm;
  display: flex; flex-direction: column;
  background: #fff;
  position: relative;
}
.slide:last-child { page-break-after: auto; }
.kicker {
  font-size: 9.5pt; letter-spacing: 0.18em; color: #5b5b62;
  text-transform: uppercase;
  margin-bottom: 0.6em;
}
.title {
  font-size: 22pt; line-height: 1.18; font-weight: 700;
  margin: 0 0 0.55em; color: #0a0a14;
}
.body { font-size: 11.5pt; line-height: 1.55; }
.body p { margin: 0.4em 0 0.55em; }
.body .lede { font-size: 14.5pt; font-weight: 500; color: #0a0a14;
              margin-top: 0.6em; margin-bottom: 1em; }
.body .meta { color: #6c6c75; font-size: 10.5pt; }
.body ul.bullets, .body ol.bullets {
  margin: 0.3em 0 0.5em 0; padding-left: 1.3em;
}
.body ul.bullets.big, .body ol.bullets.big {
  font-size: 12.5pt; line-height: 1.65;
}
.body li { margin: 0.35em 0; }
.body strong { color: #0a0a14; }
.body em { font-style: italic; color: #4d4d56; }

.body table.market, .body table.compete {
  width: 100%; border-collapse: collapse; margin: 0.5em 0;
  font-size: 10.5pt;
}
.body table.market th, .body table.market td,
.body table.compete th, .body table.compete td {
  padding: 0.45em 0.7em; border: 1px solid #d6d6dc;
  text-align: left; vertical-align: top;
}
.body table.market th, .body table.compete th {
  background: #f3f3f5; font-weight: 600; color: #0a0a14;
}
.body table.compete tr.us td { background: #fff7e6; font-weight: 600; }

.gallery {
  display: grid;
  grid-template-columns: 1fr 1fr;
  grid-template-rows: 1fr 1fr;
  gap: 6mm; margin-top: 0.5em;
  height: 110mm;
}
.gallery figure {
  margin: 0; border: 1px solid #d6d6dc; border-radius: 4mm;
  overflow: hidden; display: flex; flex-direction: column;
  background: #fff;
}
.gallery img {
  flex: 1; width: 100%; min-height: 0;
  object-fit: contain; background: #fff;
}
.gallery figcaption {
  font-size: 9pt; color: #5b5b62;
  padding: 1.5mm 2.5mm; border-top: 1px solid #d6d6dc;
  background: #fafafa;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}

.pagefoot {
  position: absolute; bottom: 8mm; right: 18mm;
  font-size: 8.5pt; color: #b5b5bd;
  letter-spacing: 0.12em; text-transform: uppercase;
}
"""


def build_html() -> str:
    parts = ["<!DOCTYPE html><html><head><meta charset='utf-8'>"
             "<title>Khayyam Math — Hub71+ AI</title>"
             f"<style>{CSS_STYLE}</style></head><body>"]
    n = len(SLIDES)
    for i, (kicker, title, body) in enumerate(SLIDES, 1):
        body_html = body.replace("{SHOT}", str(SHOT))
        parts.append(
            f"<section class='slide'>"
            f"<div class='kicker'>{kicker}</div>"
            f"<h1 class='title'>{title}</h1>"
            f"<div class='body'>{body_html}</div>"
            f"<div class='pagefoot'>"
            f"khayyammath.com · {i:02d} / {n:02d}"
            f"</div>"
            f"</section>"
        )
    parts.append("</body></html>")
    return "".join(parts)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    html = build_html()
    HTML(string=html, base_url=str(ROOT)).write_pdf(OUT)
    print(f"  → {OUT}  ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
