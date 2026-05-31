"""sevim_express — single-tool SVG-direct figure pipeline.

The LLM emits a complete SVG figure plus a phrase-timed narration script
in one structured response.  Sevim runs a vision-review loop (≤3
retries) on the rendered PNG, then synthesises piper TTS for the
narration script and ships the result to the canvas viewer.

Bypasses the structured layout pipeline (`sevim_plan` / `sevim_apply`)
entirely: there's no SceneGraph, no S3→S5, no caption placement
algorithm.  The SVG is the LLM's, served as-is.

When this works well: static figures where the LLM has strong training
priors (matrix mult, set diagrams, function plots, geometry).
When the structured pipeline still wins: narrated walkthroughs that
need progressive reveal of nodes/edges, dense graphs needing overlap-
free layout, anything where determinism matters.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from typing import Any, Awaitable, Callable

import httpx


# ---------------------------------------------------------------------
# Streaming SVG extractor — pulls the value of the top-level "svg"
# field out of a streaming JSON response as it arrives, character by
# character.  Used by ``express_figure`` to surface a partial SVG to
# the chat surface so the figure appears on the canvas while the rest
# of the response (narration, title) is still being emitted.
#
# Works on the raw concatenated text of all streamed content deltas
# from an OpenAI-compatible /chat/completions endpoint.  The express
# schema declares fields in property order ``[svg, narration, title]``
# so the SVG arrives first; once we see its closing unescaped quote
# we stop scanning.
#
# Handles standard JSON string escapes (\" \\ \/ \n \r \t \b \f) and
# is tolerant of partial escape sequences across feed() boundaries.
# Unicode \uXXXX escapes are passed through as the literal '?' so
# the partial render doesn't break; the full JSON parse at the end
# of the stream recovers the actual character.
# ---------------------------------------------------------------------

class _StreamingSvgExtractor:
    """Stateful extractor for the 'svg' field of a streaming JSON
    response.  Feed accumulated deltas via ``.feed(chunk)``; after
    each call ``.partial_svg`` returns the decoded SVG seen so far,
    ``.done`` flips True once the SVG value's closing quote arrives.
    """

    # Three states:  before_field → in_value → after_value.
    _BEFORE = 0
    _IN     = 1
    _AFTER  = 2

    def __init__(self) -> None:
        self._buf: list[str] = []         # pending text not yet scanned
        self._state = self._BEFORE
        self._svg: list[str] = []         # decoded SVG chars
        self._escape = False              # last char was '\\' while in value
        self._unicode_left = 0            # remaining hex digits of a \\uXXXX
        self._unicode_hex: list[str] = [] # collected hex digits

    @property
    def partial_svg(self) -> str:
        return "".join(self._svg)

    @property
    def done(self) -> bool:
        return self._state == self._AFTER

    def feed(self, text: str) -> bool:
        """Append ``text`` to the buffer; advance the state machine.

        Returns True iff new SVG content was produced.  The caller can
        then read ``.partial_svg`` and emit it to the client.
        """
        if not text or self._state == self._AFTER:
            return False
        before_len = len(self._svg)

        if self._state == self._BEFORE:
            # Append to pending buffer and look for the opening
            # `"svg":"` sequence.  Tolerate whitespace around the colon
            # and the field separator.
            self._buf.append(text)
            joined = "".join(self._buf)
            # Find `"svg"` first (case-sensitive — JSON keys are exact).
            key_idx = joined.find('"svg"')
            if key_idx < 0:
                # Keep only the last ~5 chars in the buffer so we don't
                # grow unbounded.  '"svg"' is 5 chars; an opening quote
                # we need to recognise may straddle a feed boundary.
                if len(joined) > 64:
                    self._buf = [joined[-64:]]
                return False
            j = key_idx + len('"svg"')
            # Skip whitespace + colon + whitespace.
            while j < len(joined) and joined[j] in " \t\n\r":
                j += 1
            if j >= len(joined) or joined[j] != ":":
                # Not enough data yet — wait for more.
                self._buf = [joined[key_idx:]]
                return False
            j += 1
            while j < len(joined) and joined[j] in " \t\n\r":
                j += 1
            if j >= len(joined):
                self._buf = [joined[key_idx:]]
                return False
            if joined[j] != '"':
                # Value is not a string?  Unusual under our schema; bail.
                self._state = self._AFTER
                return False
            # Enter value state; the rest of joined is value-content.
            self._state = self._IN
            self._buf = []
            self._consume_value(joined[j + 1:])
        else:
            # Already inside the value.
            self._consume_value(text)

        return len(self._svg) > before_len

    def _consume_value(self, chunk: str) -> None:
        """Walk ``chunk`` char-by-char inside the SVG string value,
        appending decoded chars to ``self._svg`` and flipping state
        to AFTER when an unescaped closing quote is seen."""
        for ch in chunk:
            if self._state != self._IN:
                return
            if self._unicode_left > 0:
                # Mid-\uXXXX escape: collect the hex digit, and when
                # all 4 are in, decode to the actual character.  The
                # earlier "emit '?' as placeholder" version leaked
                # hex digits like "?D7" when downstream code used the
                # intermediate buffer (the canvas viewer was painting
                # streamed chunks directly into the iframe before the
                # final JSON parse could fix things).
                self._unicode_hex.append(ch)
                self._unicode_left -= 1
                if self._unicode_left == 0:
                    hex_str = "".join(self._unicode_hex)
                    self._unicode_hex = []
                    try:
                        self._svg.append(chr(int(hex_str, 16)))
                    except ValueError:
                        # Malformed escape — pass through as literal
                        # `\uXXXX` so downstream JSON parse can flag
                        # it.  Better than silently dropping bytes.
                        self._svg.append("\\u" + hex_str)
                continue
            if self._escape:
                if ch == '"':
                    self._svg.append('"')
                elif ch == "\\":
                    self._svg.append("\\")
                elif ch == "/":
                    self._svg.append("/")
                elif ch == "n":
                    self._svg.append("\n")
                elif ch == "t":
                    self._svg.append("\t")
                elif ch == "r":
                    self._svg.append("\r")
                elif ch == "b":
                    self._svg.append("\b")
                elif ch == "f":
                    self._svg.append("\f")
                elif ch == "u":
                    # Start of \uXXXX escape — collect the next four
                    # hex chars in self._unicode_hex; the unicode_left
                    # branch above emits the decoded char once they
                    # all arrive.  No placeholder character is emitted
                    # at this stage so downstream consumers never see
                    # half-decoded escapes leak through.
                    self._unicode_left = 4
                    self._unicode_hex = []
                else:
                    # Unknown escape — pass through.
                    self._svg.append(ch)
                self._escape = False
                continue
            if ch == "\\":
                self._escape = True
                continue
            if ch == '"':
                # End of SVG value.
                self._state = self._AFTER
                return
            self._svg.append(ch)


# ── Deterministic text-region layout ──────────────────────────────────────
#
# Free-form `<text x=… y=…>` placement is the dominant source of text-
# text overlap in figures: the LLM picks pixel coordinates one element
# at a time and forgets which y-positions are already taken in nearby
# columns.  Instead, the schema exposes a `text_blocks` field where the
# LLM names a REGION and a list of LINES; Python deterministically
# positions each line.  Two overlapping `text_blocks` lines is
# impossible by construction.
#
# Canvas layout (viewBox 900×620): the SHAPE_ZONE is reserved for the
# LLM's shapes; every text region is placed OUTSIDE the shape zone so
# text and shapes cannot physically overlap.  This is the design that
# the first text_blocks attempt missed — earlier regions overlapped
# the shape area, producing fresh overlap rather than removing it.
#
#   ┌────────────────────────────────────────────────────┐
#   │         TITLE       (y 10-80, full-width, centred) │
#   ├────────────────────────────────────────────────────┤
#   │         TOP-BAND    (y 90-175, full-width)         │
#   ├──────────┬─────────────────────────────┬───────────┤
#   │  LEFT-   │                             │  RIGHT-   │
#   │  COLUMN  │       SHAPE ZONE            │  COLUMN   │
#   │ (x 0-235)│      (x 245-660)            │ (x 670+)  │
#   │ y 180-   │      y 180-490              │ y 180-490 │
#   │  490     │   LLM DRAWS HERE ONLY       │           │
#   ├──────────┴─────────────────────────────┴───────────┤
#   │         BOTTOM-BAND (y 500-580, full-width)        │
#   ├────────────────────────────────────────────────────┤
#   │       CENTER-CAPTION (y 585-615, centred)          │
#   └────────────────────────────────────────────────────┘
#
# `x` / `y` mark the START position of the first line (baseline for
# the first row).  `line_height` is the vertical step between
# consecutive lines.  `width` is advisory — the renderer doesn't
# word-wrap; the LLM should keep each line short enough.

# Reserved shape area: (x_min, y_min, x_max, y_max) in viewBox units.
# The system prompt instructs the LLM to keep every geometric
# primitive (circle, rect, path, polygon, line) inside this box.
# Raw <text> outside this box is permitted (per [project_uae_…
# session: "accept LLM choice"]) — the LLM may emit annotations
# anywhere; we don't strip them.  But all text_blocks regions are
# DISJOINT from this box by design, so the deterministic text never
# clashes with the shape area.
SHAPE_ZONE: tuple[float, float, float, float] = (245.0, 180.0, 660.0, 490.0)


class _ShapeCheckSkip(Exception):
    """Sentinel raised by the structural critic's shape-zone check
    when the figure isn't using the new zone architecture (no
    text-region groups present)."""

TEXT_REGIONS: dict[str, dict[str, Any]] = {
    # Top of canvas — large centred title.
    "title": {
        "x": 450, "y": 38, "width": 880, "anchor": "middle",
        "font_size": 20, "line_height": 26,
    },
    # Below the title, above the shape zone — full-width band for
    # definitions, problem statements, opening formulas.
    "top-band": {
        "x": 20, "y": 110, "width": 860, "anchor": "start",
        "font_size": 14, "line_height": 20,
    },
    # Left of the shape zone — narrow column for lists, step labels,
    # short input data.  ~25 chars per line at fs=14.
    "left-column": {
        "x": 15, "y": 200, "width": 220, "anchor": "start",
        "font_size": 14, "line_height": 20,
    },
    # Right of the shape zone — narrow column for examples,
    # contrasts, output data.  ~25 chars per line.
    "right-column": {
        "x": 670, "y": 200, "width": 220, "anchor": "start",
        "font_size": 14, "line_height": 20,
    },
    # Below the shape zone — full-width band for conclusions,
    # observations, multi-line explanatory prose.
    "bottom-band": {
        "x": 20, "y": 520, "width": 860, "anchor": "start",
        "font_size": 14, "line_height": 20,
    },
    # Single-line caption near the bottom edge — centred, smaller.
    "center-caption": {
        "x": 450, "y": 600, "width": 600, "anchor": "middle",
        "font_size": 13, "line_height": 17,
    },
}


def render_text_blocks(text_blocks: list[dict[str, Any]]) -> str:
    """Convert a list of {region, lines} entries into SVG markup.

    Output is a sequence of <g class="text-region-NAME"> groups, each
    containing one <text> per line, with y-coordinates auto-stacked at
    the region's `line_height`.  Unknown regions fall back to
    "left-column".  Empty lines (whitespace-only) are skipped so the
    LLM can pad without leaving blank rows.
    """
    if not text_blocks:
        return ""
    import html as _html
    pieces: list[str] = []
    for block in text_blocks:
        if not isinstance(block, dict):
            continue
        region_name = block.get("region") or "left-column"
        if region_name not in TEXT_REGIONS:
            region_name = "left-column"
        region = TEXT_REGIONS[region_name]
        lines = block.get("lines") or []
        if not isinstance(lines, list):
            continue
        cleaned = [str(line).strip() for line in lines if str(line).strip()]
        if not cleaned:
            continue
        x = region["x"]
        y0 = region["y"]
        anchor = region["anchor"]
        fs = region["font_size"]
        lh = region["line_height"]
        pieces.append(f'<g class="text-region-{region_name}">')
        for i, line in enumerate(cleaned):
            y = y0 + i * lh
            escaped = _html.escape(line, quote=False)
            pieces.append(
                f'<text x="{x}" y="{y}" font-size="{fs}" '
                f'text-anchor="{anchor}" fill="#222">{escaped}</text>'
            )
        pieces.append("</g>")
    return "".join(pieces)


def inject_text_blocks(svg: str, text_blocks: list[dict[str, Any]]) -> str:
    """Splice rendered text-block markup into the SVG just before
    </svg>.  No-op when text_blocks is empty.  When the SVG has no
    closing tag (malformed), append the markup at the end so it still
    renders inside the broken document instead of being lost."""
    rendered = render_text_blocks(text_blocks)
    if not rendered:
        return svg
    if not svg:
        return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 620">{rendered}</svg>'
    idx = svg.rfind("</svg>")
    if idx < 0:
        return svg + rendered
    return svg[:idx] + rendered + svg[idx:]


# ── JSON schema the LLM is forced to follow ───────────────────────────────

EXPRESS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "svg": {
            "type": "string",
            "description": (
                "Complete, valid SVG document beginning with '<svg xmlns=...>'."
                "  Required attrs on root: width, height, viewBox.  Every "
                "visually distinct element that the narration may reference "
                "must carry a unique id (e.g. id='cell_a_1_2', "
                "id='matrix_a_label').  Use proper math notation: subscripts "
                "via <tspan baseline-shift='sub' font-size='80%'>, ∑ ∏ ∈ ∀ ∃ "
                "∨ ∧ ¬ as Unicode characters, never ASCII substitutes.  "
                "Output the SVG inline; do NOT reference external files."
            ),
        },
        "narration": {
            "type": "array",
            "description": (
                "Phrase-timed narration script.  Each phrase must be a full "
                "sentence or independent clause; piper synthesises one WAV "
                "per phrase.  Cover the figure systematically: state what's "
                "shown, name each labelled piece, walk through the relation "
                "or computation, and END WITH A CONCLUSION PHRASE that "
                "STATES THE RESULT (not just 'this completes the proof' or "
                "'we have shown the figure').  The last phrase MUST name "
                "the concrete answer the learner now knows — e.g. "
                "\"Therefore the derivative is 3x squared.\", "
                "\"So the area equals nine pi.\", "
                "\"Thus 3SAT is NP-complete because every SAT instance "
                "reduces to it in polynomial time.\".  10-25 phrases for a "
                "non-trivial figure."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "speak": {"type": "string"},
                    "highlight": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "List of SVG element ids to highlight while "
                            "this phrase plays.  Use multiple ids when the "
                            "phrase mentions multiple things, e.g. "
                            "['n_clause_C1','n_x1','n_not_x2'] for "
                            "'clause C1 contains x1 and ¬x2'.  Use [] for "
                            "phrases that don't reference a specific "
                            "element.  Each id MUST match an element id "
                            "present in the svg; unknown ids are dropped."
                        ),
                    },
                },
                "required": ["speak", "highlight"],
            },
        },
        "title": {
            "type": "string",
            "description": "Short title for the figure (3-6 words).",
        },
        "problem_statement": {
            "type": "string",
            "description": (
                "Brief restatement, in math terms, of what is being "
                "shown — e.g. \"compute f'(x) where f(x)=x^3\".  Empty "
                "string ONLY if the request has no specific math "
                "problem (e.g. \"show me a labelled triangle\")."
            ),
        },
        "solution": {
            "type": "string",
            "description": (
                "Worked-out solution to the problem (1-4 sentences).  "
                "The figure must DEPICT this solution — they cannot "
                "disagree.  Solve before drawing.  Empty string only "
                "for non-problem figures."
            ),
        },
        "math_claims": {
            "type": "array",
            "description": (
                "Symbolically verifiable claims the figure depends on.  "
                "A CAS (SymPy) checks every claim before the figure is "
                "allowed to ship.  Empty array is acceptable when no "
                "symbolic claim is involved — but if your figure "
                "asserts a derivative, an integral, an identity, a "
                "Hessian, a sum, an equality, LIST IT here.  Wrong "
                "claims block the figure."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {
                        "type": "string", "enum": ["identity", "value"],
                        "description": (
                            "\"identity\": a == b as algebraic objects "
                            "(SymPy simplify(a-b)==0).  \"value\": a "
                            "evaluates to the numeric value b."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": (
                            "Short human-readable claim, used in "
                            "error feedback if verification fails."
                        ),
                    },
                    "a": {
                        "type": "string",
                        "description": (
                            "Left side, Python/SymPy syntax.  You may "
                            "use diff(expr, var, order), "
                            "integrate(expr, var), hessian(expr, "
                            "(x,y)), Matrix([[...]]), sin/cos/exp/log/"
                            "sqrt, pi.  e.g. diff(x**3, x) — never "
                            "compute the result yourself."
                        ),
                    },
                    "b": {
                        "type": "string",
                        "description": (
                            "Right side, same syntax.  e.g. \"3*x**2\"."
                        ),
                    },
                },
                "required": ["kind", "description", "a", "b"],
            },
        },
        "text_blocks": {
            "type": "array",
            "description": (
                "DETERMINISTIC TEXT LAYOUT — use this for ANY multi-line "
                "explanatory text (definitions, lists of clauses, "
                "step-by-step prose, captions of 4+ words).  Each entry "
                "names a REGION and a list of LINES; Python positions "
                "every line at non-overlapping coordinates automatically.  "
                "Use raw `<text>` in the svg field ONLY for short math "
                "labels (≤4 chars: 'q0', 'x_1', 'A', '∑', formulas under "
                "20 chars) that must be anchored to a specific geometric "
                "point.  EVERY other text MUST go in text_blocks — "
                "explanatory sentences placed by hand at x/y coordinates "
                "are the #1 source of overlap.  Empty array if the figure "
                "needs no captions."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "region": {
                        "type": "string",
                        "enum": [
                            "title", "top-band", "left-column",
                            "right-column", "bottom-band", "legend",
                            "center-caption",
                        ],
                        "description": (
                            "Where the lines appear:  "
                            "title (top centre, 20pt) | "
                            "top-band (full-width strip y=70) | "
                            "left-column (x<430, definitions / steps) | "
                            "right-column (x>470, examples / contrasts) | "
                            "bottom-band (footer line, full width) | "
                            "legend (small text, top-right corner) | "
                            "center-caption (single caption above bottom)."
                        ),
                    },
                    "lines": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "One short line each — keep each under ~60 "
                            "characters; the renderer does NOT word-wrap."
                        ),
                    },
                },
                "required": ["region", "lines"],
            },
        },
    },
    "required": ["svg", "narration", "title", "problem_statement",
                 "solution", "math_claims", "text_blocks"],
}


_LANGUAGE_RULE = (
    "\n\n"
    "LANGUAGE MATCHING — HARD RULE.\n"
    "Detect the language of the user's prompt and respond in EXACTLY "
    "that language for every word of narration, caption, primer, and "
    "any other user-facing prose.  If the prompt is in German, every "
    "spoken phrase is in German.  If the prompt is in Persian, every "
    "spoken phrase is in Persian.  Same for French, Chinese, Arabic, "
    "Spanish, Italian, Russian, Hindi, Turkish, and any other "
    "language.  Never silently switch to English just because math "
    "symbols are universal.  Math notation (π, ∫, x², √) is the same "
    "in every language; the surrounding prose must match the user.\n"
    "\n"
    "NUMBERS IN SPOKEN NARRATION (when language ≠ English).\n"
    "TTS engines often mis-pronounce or swallow numerical digits in "
    "non-English text.  When writing any `speak` / narration string "
    "in a language other than English, spell every number out as "
    "words in that language:\n"
    "    German   1.5 → eineinhalb  (or 'eins Komma fünf');  3 → "
    "drei; ≈1.26 → ungefähr eins Komma zwei sechs.\n"
    "    Persian  1.5 → یک و نیم (yek o nim); 3 → سه; 1.26 → یک "
    "ممیز بیست و شش صدم.\n"
    "    French   1.5 → un virgule cinq; 3 → trois.\n"
    "    Chinese  1.5 → 一点五; 3 → 三; 1.26 → 一点二六.\n"
    "The figure itself (text labels, captions, integral bounds) can "
    "keep numerals as digits — only the spoken narration text needs "
    "the word form.  Constants like π, e, ∞ stay as symbols.\n"
)


_EXPRESS_SYSTEM = (
    "MATH CORRECTNESS IS NON-NEGOTIABLE.  Before any figure, you must "
    "(1) state the problem in `problem_statement`, (2) work out the "
    "answer in `solution`, and (3) list every symbolically verifiable "
    "fact the figure depends on as a `math_claims` entry.  A CAS "
    "(SymPy) checks every claim before the figure ships; any false "
    "claim BLOCKS the figure and you will be asked to fix it.  A "
    "figure that displays a false claim — a wrong derivative, an "
    "incorrect identity, a tangle of arrows mislabelled as a "
    "homomorphism — is WORSE than no figure, because it teaches "
    "something false.  If you cannot be sure the math is right, say "
    "so in `solution` and emit only what you can verify.  Solve, "
    "THEN draw.\n"
    "\n"
    "Claims must be CONCRETE and UNCONDITIONAL identities or values, "
    "not theorems-with-context.  Bad: a='exterior_angle', "
    "b='alpha+beta' (the verifier doesn't know your triangle).  Good: "
    "instantiate it — a='pi - pi/2', b='pi/4 + pi/4' for a specific "
    "case the figure draws.  When the figure makes a general claim, "
    "either (a) instantiate one or two concrete examples and verify "
    "those, or (b) leave math_claims empty and rely on the solution "
    "field for the general statement.\n"
    "\n"
    "You are a math TEACHER illustrating a concept.  The figure must "
    "TEACH the operation, not merely label it.  A reader who has never "
    "seen this concept should be able to learn it from the figure + "
    "narration alone.  Aim for the depth of a Khan Academy / 3Blue1Brown "
    "explainer, rendered as a static SVG.\n"
    "\n"
    "TEXT LAYOUT — DETERMINISTIC ZONES, NOT FREEFORM PLACEMENT.\n"
    "The canvas (viewBox 900×620) is divided into reserved zones.  The "
    "LLM only chooses content; Python positions it.  Two text lines "
    "cannot overlap.\n"
    "\n"
    "Canvas zone map:\n"
    "  +----------------------------------------------------+\n"
    "  |                  TITLE  (y 10-80)                  |  centred, 20pt\n"
    "  +----------------------------------------------------+\n"
    "  |              TOP-BAND  (y 90-175)                  |  full-width, 14pt\n"
    "  +----------+---------------------------+-------------+\n"
    "  |  LEFT-   |                           |  RIGHT-     |\n"
    "  |  COLUMN  |      SHAPE ZONE           |  COLUMN     |  side columns 14pt,\n"
    "  |(x 0-235) |    (x 245-660, y 180-490) |(x 670-900)  |  ~25 chars wide\n"
    "  | y 180-   |  PUT EVERY SHAPE INSIDE   | y 180-490   |\n"
    "  | 490      |    THIS BOX               |             |\n"
    "  +----------+---------------------------+-------------+\n"
    "  |             BOTTOM-BAND  (y 500-580)               |  full-width, 14pt\n"
    "  +----------------------------------------------------+\n"
    "  |          CENTER-CAPTION  (y 585-615)               |  centred, 13pt\n"
    "  +----------------------------------------------------+\n"
    "\n"
    "RULES:\n"
    "  1. EVERY shape primitive (<circle>, <rect>, <path>, <polygon>, "
    "<line>, <polyline>, <ellipse>) MUST be drawn STRICTLY INSIDE the "
    "SHAPE ZONE: x in [245, 660], y in [180, 490].  Before emitting "
    "each primitive, mentally check: is its x-range fully inside "
    "[245, 660]?  Is its y-range fully inside [180, 490]?  A <rect "
    "x='250' width='420'> ends at x=670 — that's OUTSIDE the zone "
    "(>660) and will visually clash with the right-column text "
    "region.  Resize or move it.  Use the full 415×310 area "
    "generously but DO NOT cross the boundary.\n"
    "  2. ALL explanatory text (definitions, clause lists, prose, "
    "captions of 4+ words, conclusions, formulas longer than ~20 "
    "chars) MUST go in `text_blocks` — never in raw <text> elements.  "
    "Pick a region from {title, top-band, left-column, right-column, "
    "bottom-band, center-caption} and emit one string per line.\n"
    "  3. Raw <text> in the SVG is ALLOWED only for short math labels "
    "(≤4 chars: 'q0', 'x₁', 'A', '∑') or short inline formulas (<20 "
    "chars: 'a²=9') glued to a specific geometric point INSIDE the "
    "shape zone.  Wider prose must use text_blocks.\n"
    "  4. text_blocks lines should be SHORT — under ~60 chars for "
    "top/bottom bands, under ~25 chars for left/right columns.  The "
    "renderer does NOT word-wrap; over-wide lines visually overflow.\n"
    "  5. ONE CANONICAL HOME PER PIECE OF CONTENT.  Each fact, "
    "formula, clause string, or label appears in EXACTLY ONE place — "
    "never twice.  Concrete examples of the bug to avoid:\n"
    "     * Don't put 'P(Disease)=0.01' in left-column AND inside a "
    "<rect> in the shape zone — pick one home.\n"
    "     * Don't put SAT clauses in left-column AND as <text> inside "
    "clause-boxes in the shape zone — pick one home.\n"
    "     * Don't put a heading 'Reduction from SAT to 3-SAT' as a "
    "raw <text> AND in the 'title' text-block region.\n"
    "  When in doubt, prefer text_blocks for the textual content and "
    "let the shape zone hold STRUCTURE ONLY (boxes, arrows, "
    "connectors — without redundant labels naming the items the "
    "columns already name).  Numbered tags like '①②③' or single "
    "letters like 'A B C' can label structures in the shape zone — "
    "those are short and don't duplicate text_blocks content.\n"
    "\n"
    "WORKED EXAMPLE — 'Prove that 3SAT is NP-Complete':\n"
    "  text_blocks: [\n"
    "    {region: 'title', lines: ['Reduction from SAT to 3-SAT']},\n"
    "    {region: 'top-band', lines: [\n"
    "       'SAT is NP-complete (Cook-Levin).',\n"
    "       'We show 3SAT is NP-hard by polynomial reduction.']},\n"
    "    {region: 'left-column', lines: [\n"
    "       'SAT clauses:',\n"
    "       '(x_1 ∨ x_2 ∨ x_3)',\n"
    "       '(¬x_1 ∨ x_2)',\n"
    "       '(x_3 ∨ ¬x_2 ∨ x_4)']},\n"
    "    {region: 'right-column', lines: [\n"
    "       '3-SAT clauses:',\n"
    "       '(x_1 ∨ x_2 ∨ x_3)',\n"
    "       '(¬x_1 ∨ x_2 ∨ y_1)',\n"
    "       '(y_1 ∨ ¬x_2 ∨ y_2)',\n"
    "       '(x_3 ∨ ¬x_2 ∨ x_4)']},\n"
    "    {region: 'bottom-band', lines: [\n"
    "       'Reduction preserves satisfiability; runs in polynomial "
    "time.']},\n"
    "  ]\n"
    "  svg: ONLY shapes inside x in [245, 660], y in [180, 490].  For "
    "example, three small <rect> on the left of the shape zone labelled "
    "ONLY '①' '②' '③' (vertex tags, ≤4 chars) for the original "
    "clauses, three more <rect> on the right side of the shape zone "
    "labelled '①′' '②′' '③′' for the 3-SAT clauses, and three <line> "
    "arrows connecting corresponding pairs.  Rect widths: keep each "
    "≤80 units so 'left + width' stays well inside x=660.  The shape "
    "zone shows the STRUCTURE (boxes + arrows + numeric tags).  The "
    "text_blocks columns show the CONTENT (the actual clause strings).  "
    "A learner reads ① in the box, looks left, finds the matching "
    "clause string in the left-column text-block.\n"
    "\n"
    "ANTI-EXAMPLES — common bugs to avoid (do NOT emit these):\n"
    "  ❌ <rect x='250' y='200' width='400'><text>...x_1 ∨ x_2...</text></rect>\n"
    "     — Long clause string INSIDE a shape-zone box.  The same text "
    "is already in left-column text_blocks; this duplicates it AND "
    "uses up canvas room that should hold arrows/structure.  Fix: emit "
    "the rect with ONLY a short label '①' (≤4 chars) inside, the "
    "clause string is in left-column.\n"
    "  ❌ <rect x='670' y='200' width='200' .../> — A rect that starts "
    "or extends past x=660.  This invades the right-column text zone; "
    "the structural critic will flag it.  Fix: make the rect narrower "
    "and place it inside [245, 660].\n"
    "  ❌ <text x='450' y='30' font-size='20'>Reduction from SAT</text> "
    "AND text_blocks title 'Reduction from SAT' — the heading is "
    "emitted twice (once raw in SVG, once as a text_block).  Fix: pick "
    "ONE.  Prefer the text_block (deterministic position).\n"
    "  ❌ <rect x='250' y='200' width='400' height='250' "
    "fill='#fff'/> with 7 lines of computation <text> inside it — that "
    "computation is the kind of multi-line prose that belongs in "
    "top-band / bottom-band / center-caption text_blocks, NOT laid "
    "out by hand inside a rect.  The rect bbox extending to y=450 "
    "also crowds the shape zone for any geometry shown alongside.\n"
    "\n"
    "EVERY NARRATION MUST END WITH A CONCLUSION — STRICT, NO EXCEPTIONS.\n"
    "The LAST phrase of the narration list MUST state the concrete "
    "RESULT the learner now knows.  Not a recap, not a sign-off, not "
    "'this completes the explanation' — the ANSWER itself.\n"
    "\n"
    "Every figure exists to deliver a result.  A derivative figure "
    "ends with the derivative.  A 3-4-5-triangle Pythagoras figure "
    "ends with '9 + 16 = 25, so the hypotenuse is 5'.  A 3SAT proof "
    "ends with 'therefore 3SAT is NP-complete'.  A unit-circle figure "
    "ends with 'so sin(30°) = 0.5 and cos(30°) = √3/2'.  The walkthrough "
    "stops being valuable to the learner the moment it doesn't lead to "
    "a stated conclusion they can write down.\n"
    "\n"
    "Required form for the last phrase:\n"
    "  * Starts with a conclusion connector: 'Therefore', 'So', "
    "'Hence', 'Thus', 'Which gives', 'And so', 'We conclude that'.\n"
    "  * Contains the concrete value, equation, classification, or "
    "named result — never just the technique name.\n"
    "  * Is highlighted on the visible element that DISPLAYS the "
    "result (the final cell, the equality, the boxed answer).\n"
    "\n"
    "  ❌ 'And that is how we prove the Pythagorean theorem.'  "
    "(Names the technique, not the result.)\n"
    "  ❌ 'This completes the reduction.'  (Recaps without stating "
    "the conclusion the reduction shows.)\n"
    "  ❌ 'We have now seen the integration by parts formula.'  "
    "(Visible-obvious recap; no answer.)\n"
    "  ✓ 'So the derivative of sin x squared is 2x cos of x squared.'\n"
    "  ✓ 'Therefore 9 plus 16 equals 25, so the hypotenuse is five.'\n"
    "  ✓ 'Hence 3SAT is NP-complete because SAT, which is NP-complete, "
    "reduces to it in polynomial time.'\n"
    "\n"
    "SHOW DON'T JUST TELL — STRICT RULE.  Every narration phrase MUST "
    "highlight a VISIBLE element drawn in the SVG.  If the narration "
    "says 'compute the adjugate', the adjugate matrix must be rendered "
    "as actual cells with a stable id, and that id must be in the "
    "phrase's `highlight` array.  If the narration says 'apply the "
    "formula c = a²+b²', the formula text must be drawn as a <text> "
    "element and highlighted.  NEVER narrate a step that is not visible "
    "on the canvas.  If a step is too complex to draw, leave it out of "
    "the narration too — a learner cannot follow audio describing "
    "elements that aren't there.\n"
    "\n"
    "NEVER DESCRIBE WHAT IS VISUALLY OBVIOUS.  The learner can SEE the "
    "diagram — their eyes recognise shapes, labels, colours, "
    "connections without help from the audio.  Telling them 'we see a "
    "triangle with vertices A, B, C' or 'on the left there are three "
    "circles' or 'A is connected to B' adds ZERO math knowledge and "
    "wastes the audio budget.  Save every phrase for the CONCLUSION, "
    "the REASONING, the WHY.  Object recognition is the eye's job; "
    "your job is to provide the math content the eye cannot extract.\n"
    "\n"
    "  ❌ 'We see Graph G on the left with vertices v1, v2, v3, and "
    "Graph H on the right with vertices u1, u2.  v1 connects to v2…'\n"
    "  ✓ 'Vertices sharing a colour map to the same target, so every "
    "edge of G lands on the single edge of H — that is exactly what a "
    "homomorphism demands.'\n"
    "\n"
    "  ❌ 'Here we see a circle with an inscribed triangle ABC.'\n"
    "  ✓ 'The inscribed angle subtends the same arc as the central "
    "angle, so it must be half its measure — Thales' theorem in one "
    "line.'\n"
    "\n"
    "  ❌ 'On the left is matrix A, in the middle is matrix B, the "
    "arrow shows multiplication, on the right is the product C.'\n"
    "  ✓ 'Each entry of C is the dot product of one row of A with one "
    "column of B — highlighted here for c₂,₃.'\n"
    "\n"
    "HARD RULE — if you would start a phrase with 'we see…', 'here "
    "is…', 'on the left/right…', 'the figure shows…', 'note that "
    "[X] is connected to [Y]…' — STOP, delete that opener, and start "
    "the phrase with the math idea you were about to explain.  Use "
    "the `highlight` array to POINT at a component; the spoken phrase "
    "is for the IDEA, not the inventory.\n"
    "\n"
    "ALSO AVOID re-defining concepts the prompt already assumes.  If "
    "the user asked 'show the homomorphism C_4 → K_2', do NOT open "
    "with 'a homomorphism is a function that preserves edges' — they "
    "already know the term, that's why they asked.  Open with the "
    "specific insight for THIS case.\n"
    "\n"
    "MATCH THE FIGURE TO THE CONCEPT — do not over-build.  When a "
    "concept is elementary or not inherently spatial (basic "
    "arithmetic, a one-line definition, a single fact), draw ONE "
    "minimal, honest figure that genuinely illustrates it — a number "
    "line, one labelled worked example, a single clean diagram — and "
    "NOT a flowchart of boxes-and-arrows that merely restates the "
    "words.  A box chart reading '23 → 15 → 38' teaches nothing a "
    "reader couldn't get from the sentence.  For such prompts the "
    "teaching-depth checklist below is satisfied by that single clear "
    "figure plus a short caption; do not pad it with pseudo-steps "
    "that add no insight.  The full step-by-step treatment below "
    "applies to genuinely multi-step operations (matrix "
    "multiplication, reductions, proofs, multi-stage constructions).\n"
    "\n"
    "TEACHING DEPTH — every figure must include:\n"
    "  1. The CONCEPT NAME and its definition as a caption.\n"
    "  2. CONCRETE EXAMPLE: pick small specific numbers (don't leave "
    "everything abstract).  E.g. for matrix multiplication, fill cells "
    "with actual integers (e.g. a₁₁=2, a₁₂=3, ...) — not just symbols.\n"
    "  3. THE OPERATION SHOWN STEP-BY-STEP.  Don't just write 'A · B = "
    "C' and stop.  Show:\n"
    "       • The general formula (e.g. cᵢⱼ = Σₖ aᵢₖ · bₖⱼ).\n"
    "       • One worked cell computed in full (e.g. c₂₃ = a₂₁·b₁₃ + "
    "a₂₂·b₂₃ + a₂₃·b₃₃ + a₂₄·b₄₃ + a₂₅·b₅₃ = 2·1 + 3·0 + ... = 17).\n"
    "       • Visual cues showing WHICH row × column produced that cell "
    "(coloured arrows, highlighted strip, dotted overlay).\n"
    "  4. WHY THE OPERATION IS DEFINED THIS WAY (one short caption: "
    "linear combinations / function composition / etc.).\n"
    "  5. A concluding caption stating the result (e.g. dimensions of C, "
    "or the satisfiability claim, or the limit value).\n"
    "\n"
    "FINISH THE PROBLEM — hard rule.  When the user asks 'solve X', "
    "'compute Y', 'find Z', 'evaluate W', 'what is …', the figure AND "
    "the LAST narration phrase MUST state the explicit final answer "
    "(a number, a closed form, the set of roots, the value of the "
    "expression).  Do NOT stop the narration on a setup line, on a "
    "factored intermediate, on a formula awaiting substitution, or on "
    "a generic 'this illustrates the technique' sentence.  The reader "
    "must walk away knowing the answer.\n"
    "\n"
    "  ❌ Last phrase: 'This shows the quadratic and its roots.'\n"
    "  ✓ Last phrase: 'The roots are x = 2 and x = 3.'\n"
    "\n"
    "  ❌ Last phrase: 'We apply the formula int sin(x)dx = -cos(x).'\n"
    "  ✓ Last phrase: '-cos(pi) - (-cos(0)) = 1 - (-1) = 2.  The "
    "integral equals 2.'\n"
    "\n"
    "  ❌ Last phrase: 'Subtracting the equations gives the value of x.'\n"
    "  ✓ Last phrase: 'x = 3 and y = 2 solve the system.'\n"
    "\n"
    "If the problem is open-ended (illustrate / explain / state a "
    "theorem) the rule does not bind — but for any imperative verb "
    "asking for a value, the last phrase IS the answer.\n"
    "\n"
    "REFERENCE STYLE — emulate trusted math literature.  Every figure "
    "should look like it could appear in one of these canonical sources, "
    "and every narration should sound like it was written by a teacher "
    "from that tradition.  Pick whichever fits the topic best:\n"
    "  • Geometry, classical proofs → **Euclid's Elements** (numbered "
    "constructions, parallel-line + auxiliary-line tricks, formal "
    "vertex labels A B C, angles α β γ, sides a b c opposite their "
    "vertices).  e.g. triangle angle sum = Elements I.32.\n"
    "  • Calculus → **Spivak's Calculus** or **Apostol** (rigorous "
    "limit-and-area framing, named theorems, ε-δ when relevant); for "
    "intuition-first plots, **3Blue1Brown / Better Explained** (clean "
    "color-coded geometric metaphors).\n"
    "  • Linear algebra → **Strang's Introduction to Linear Algebra** "
    "(matrix-as-grid layouts, row × column highlighted in contrasting "
    "colors, four-fundamental-subspaces framing) or **Axler's Linear "
    "Algebra Done Right** (basis-free thinking, operators-not-matrices).\n"
    "  • Real / complex analysis → **Rudin's Principles** (concise "
    "theorem-proof boxes, ε-δ, named inequalities).\n"
    "  • Discrete & combinatorics → **Concrete Mathematics** (Knuth/"
    "Graham/Patashnik): Iverson brackets, ⌊⌋⌈⌉, named identities.\n"
    "  • Algorithms → **CLRS** (pseudocode block + array-state diagram + "
    "loop invariants stated explicitly).\n"
    "  • Probability → **Bertsekas/Tsitsiklis** or **Feller** (event "
    "diagrams, conditional-probability trees, Bayes table).\n"
    "  • Topology → **Munkres** (open-cover figures, basis arguments).\n"
    "  • Abstract algebra → **Dummit & Foote** or **Artin** (Cayley "
    "tables, group-action diagrams, lattice of subgroups).\n"
    "  • Number theory → **Hardy & Wright** (modular-arithmetic clocks, "
    "sieve diagrams).\n"
    "  • Complexity → **Sipser** or **Arora & Barak** (reduction "
    "diagrams, gadget figures with input/output wires).\n"
    "  • Mathematical physics → standard textbook diagrams "
    "(**Feynman**, **Griffiths**, **Goldstein**): coordinate axes "
    "with arrows, free-body decomposition, vector decomposition.\n"
    "Cite the named theorem in a caption when one applies (e.g. "
    "'Elements I.47', 'FTC', 'Cayley-Hamilton', 'Master Theorem', "
    "'Bayes' theorem').\n"
    "\n"
    "LAYOUT — match canonical textbook form:\n"
    "  • A matrix MUST be drawn as ONE coherent N×N grid — a single "
    "<g> wrapping an outer rect (or pair of bracket strokes) plus N² "
    "cell-text elements positioned on a regular row × column lattice.  "
    "Do NOT emit a matrix as N separate 1×N strips or N standalone "
    "single-column groups; that renders as 'N different mini-matrices "
    "in a row,' not as one matrix.  An m×n matrix has m*n cells; an "
    "m×n matrix figure must contain exactly m*n cell-text elements "
    "inside a single matrix group, none missing, none repeated.\n"
    "  • Matrix multiplication: 2-D grids for A (m×n), B (n×p), C (m×p) "
    "with '·' and '=' between them; row-i of A and column-j of B both "
    "highlighted; the worked sum-of-products written as a separate "
    "caption.  (Strang style.)\n"
    "  • 3SAT→clique: 3 columns of literals with cross-cluster edges; "
    "highlight one valid k-clique.  (Sipser-style reduction diagram.)\n"
    "  • Sets: overlapping circles with example elements drawn inside.\n"
    "  • Derivative / integral: function curve + tangent line / shaded "
    "area + the actual computed value.  (Spivak-style.)\n"
    "  • Euclidean proofs: auxiliary construction drawn with dashed "
    "stroke; numbered steps in a side caption (Elements style).\n"
    "  • Group theory: Cayley table as a square grid; group elements "
    "as labelled nodes if showing a group action.\n"
    "\n"
    "STYLE:\n"
    "  • Notation conventional: SVG is NOT MathJax.  NEVER put raw "
    "    LaTeX inside a <text> element — `a_{11}` renders as the "
    "    literal 5 characters `a`, `_`, `{`, `1`, `1`, `}`, not as "
    "    an a with subscript 11.  Use SVG markup instead:\n"
    "      - subscript:   <tspan baseline-shift='sub'   font-size='80%'>ij</tspan>\n"
    "      - superscript: <tspan baseline-shift='super' font-size='80%'>n</tspan>\n"
    "    Greek letters as Unicode (α β θ φ), operators as Unicode "
    "    (∑ ∏ ∈ ∀ ∨ ∧ ¬ · ≤ ≥ ≠).  Never ASCII substitutes, never "
    "    `\\\\sum`, `\\\\theta`, `\\\\frac`.  If you find yourself "
    "    typing a backslash inside a <text>, stop and rewrite.\n"
    "  • ABSOLUTELY NEVER emit these literal strings inside any "
    "    <text> in the SVG:  `\\(`, `\\)`, `$`, `\\frac{`, `\\times`, "
    "    `\\cdot`, `\\sqrt{`, `\\sum`, `\\int`, `\\to`, `\\le`, `\\ge`. "
    "    The canvas renders the SVG as-is, with no MathJax pass, so "
    "    these print as garbage.  Convert at write-time:\n"
    "      `\\frac{1}{2}` → `½` or `1/2`\n"
    "      `\\times`      → `×` (or `·` for dot-product)\n"
    "      `\\cdot`       → `·`\n"
    "      `\\sqrt{x}`    → `√x` (or `√(x)` for compound arguments)\n"
    "      `\\pi \\theta` → `π θ`\n"
    "      `\\to`         → `→`\n"
    "      `\\le \\ge`    → `≤ ≥`\n"
    "      `\\sum_{i=1}^{n}` → `Σ ᵢ₌₁ⁿ` (or `Σ (i=1 to n)`)\n"
    "    Areas, perimeters and other computed values: write them as "
    "    plain numbers — `Area = (1/2) × (a + b) × h = 30000` — NOT "
    "    `\\( \\frac{1}{2} \\times (a+b) \\times h \\)`.\n"
    "  • Formulas longer than ~60 characters CANNOT fit on one line "
    "    in a 900-wide viewBox.  Break them across multiple stacked "
    "    <text> elements (same x, y stepped by 22-28 px).  Example: "
    "    instead of one 130-char det(A) = a_11(…) - a_12(…) + a_13(…), "
    "    emit THREE <text> blocks: line 1 'det(A) = '; line 2 "
    "    '  + a₁₁(a₂₂a₃₃ - a₂₃a₃₂)' on the next y; line 3 the rest.\n"
    "  • TWO-COLUMN LAYOUT — when you have many formulas / steps to "
    "    show, use the WIDTH before going tall.  The 900-wide canvas "
    "    naturally splits into two 440-wide columns (left x=20-460, "
    "    right x=480-880).  Put the diagram + first half of formulas "
    "    in the left column, the rest of the formulas in the right "
    "    column starting at x=480.  Never let the y of any element "
    "    exceed viewBox_height - 30 = ~620; if it would, START A NEW "
    "    COLUMN at x=480 instead of stacking more lines vertically.  "
    "    Reset y back to ~80 when you switch columns.\n"
    "  • Every visually distinct element has a unique SVG id "
    "(matrix_a_label, cell_a_1_2, sum_step_1, formula_general).\n"
    "  • viewBox sized to fit comfortably (typical: 0 0 900 650).\n"
    "  • EVERY drawn element MUST sit inside the viewBox.  No text "
    "    starting beyond x=viewBox_width-10 or y=viewBox_height-10; "
    "    no text starting before x=10 or y=20.  If a long formula "
    "    can't fit on one line, BREAK it across multiple <text> "
    "    elements on stacked y values — never let it run off the "
    "    canvas edge.\n"
    "  • CAPTIONS AND FORMULAS go in the canvas MARGINS with their "
    "    own dedicated band — top band (y < 60) or bottom band "
    "    (y > viewBox_height - 80) or a right-side column (x > "
    "    viewBox_width * 0.7).  Diagrams occupy the central area.  "
    "    Caption bands MUST NOT intrude on the diagram region.\n"
    "  • No overlapping text.  Margins for captions.  Use colour "
    "purposefully (highlight the active row/column in a contrasting hue).\n"
    "  • Narration is spoken by piper TTS — write spoken words, not "
    "symbols (say 'a sub i j' not 'a_{ij}'; 'sigma from k equals 1 to n' "
    "not '∑').  Each phrase highlights ONE element.  Walk through the "
    "computation step by step but keep it tight: TARGET 8-12 PHRASES "
    "(maximum 14).  Each phrase 1-2 sentences max.  More than 14 "
    "phrases means the learner is waiting for synthesis instead of "
    "watching the figure.  Combine adjacent micro-steps when they "
    "highlight the same element.\n"
    "\n"
    "GRANULAR HIGHLIGHTS — when a narration phrase NAMES a specific "
    "variable, symbol, row, column, cell, or sub-formula, that named "
    "thing MUST be its own SVG element with a unique id, and its id "
    "MUST appear in the phrase's highlight array.  Concretely:\n"
    "  • If narration says 'the variable x', the SVG must contain "
    "    <text id='var_x'>x</text> AND the phrase highlights "
    "    ['var_x'].\n"
    "  • If narration says 'row 2 of matrix A', the SVG must contain "
    "    <g id='matrix_a_row_2'>...</g> grouping the row's cells, "
    "    AND the phrase highlights ['matrix_a_row_2'].\n"
    "  • If narration says 'the discriminant b² - 4ac', the formula "
    "    'b² - 4ac' lives inside its own <text id='discriminant'> OR "
    "    is the entire content of one <tspan id='discriminant'> "
    "    inside a larger formula, AND the phrase highlights "
    "    ['discriminant'].\n"
    "  • If narration says 'the second clause', the second clause is "
    "    <text id='clause_2'>C2: (x ∨ y ∨ z)</text> AND the phrase "
    "    highlights ['clause_2'].\n"
    "It is NOT enough to highlight the whole formula group when the "
    "narration is pointing at one term inside it — the learner needs "
    "the eye-tracking cue to land on the specific thing being said.\n"
    "  • SELF-CHECK before finishing: EVERY id in EVERY highlight "
    "array must appear verbatim as an id='...' attribute on an element "
    "you actually drew in the SVG.  An id that is not in the SVG "
    "highlights nothing and wastes the phrase.  Never reference an id "
    "you did not draw; never let the id drift in spelling or case.\n"
    "\n"
    "SEMANTIC VALUES ≠ SVG COORDINATES — the numbers in the user's "
    "prompt (radius r = 5, base b = 8, side a = 3, angle θ = 30°) are "
    "SEMANTIC labels.  They tell you what to print on the figure as a "
    "<text> next to the shape.  THEY ARE NOT TO BE USED AS viewBox "
    "COORDINATES.\n"
    "  WRONG: <circle cx='450' cy='300' r='5'/> for 'circle with r=5'.  "
    "    A 5-pixel-radius circle is invisible at viewBox scale and the "
    "    figure renders as empty space with one text label.\n"
    "  RIGHT: <circle cx='450' cy='300' r='180'/>  AND  "
    "    <text>r = 5</text> next to a radius line.  The DRAWN size "
    "    fills the canvas; the label conveys the semantic value.\n"
    "  LABEL TEXT MUST BE THE USER'S NUMBER, NOT THE SVG COORD.  If "
    "    the user wrote 'r = 5' in their prompt, the <text> next to "
    "    the radius reads 'r = 5' — NEVER 'r = 180' or 'r = 200' or "
    "    whatever value you chose for the SVG attribute.  Same for "
    "    base/height/side/angle: keep the user's number in the label "
    "    and any computed final answer; the SVG coord is purely a "
    "    rendering detail the learner never sees as a number.\n"
    "  EXAMPLE — user prompt 'compute the circumference of a circle "
    "    with radius r = 5':  SVG has <circle r='180'/> AND the labels "
    "    on the figure read 'r = 5', 'C = 2π·5 = 10π ≈ 31.42'.  Even "
    "    though the visual radius is 180 px, the label and the "
    "    computation use r = 5.\n"
    "Sizing rule of thumb (for a 900x650 viewBox):\n"
    "  • Main shapes (circle, polygon, triangle, parallelogram, "
    "    parabola, function curve) occupy 50-80% of the diagram area "
    "    — typically 300-600 px in their longest dimension.\n"
    "  • Triangle/trapezoid base: ~300-500 px wide.\n"
    "  • Triangle/trapezoid height: ~200-350 px tall.\n"
    "  • Circle radius: ~150-250 px.\n"
    "  • Line segment, chord, radius arrow: ~200-400 px.\n"
    "  • Axes for function plots: ~500-700 px wide, ~300-450 px tall.\n"
    "  • Matrix cell: ~50-80 px square; total matrix grid 200-400 px.\n"
    "These are GUIDELINES, not absolutes — adapt to the figure — but "
    "NEVER make a primary shape smaller than ~80 px in any dimension.\n"
    "When the user prompt contains 'r = 5' or 'base = 8' or 'angle = "
    "30°', treat those numbers ONLY as the label content; the visual "
    "size is independent of them.\n"
    "\n"
    "DRAW THE SHAPE — every figure for a geometric/visual topic MUST "
    "include at least one geometric primitive (<polygon>, <circle>, "
    "<ellipse>, <line>, <path>, <rect>) sized per the rules above.  A "
    "'figure' that is only a heading + <text> formulas with no shape "
    "is NOT acceptable — it's a textbook page, not a teaching "
    "illustration.  Specifically:\n"
    "  • A circle question requires a <circle>, sized to fill the "
    "    diagram, with a radius line drawn to a labelled point on the "
    "    boundary.\n"
    "  • An arc / sector / central-angle question requires a <circle> "
    "    PLUS a <path d='M ... A ...'> for the arc, with both radii "
    "    drawn.\n"
    "  • A parabola / quadratic question requires axes (<line>) plus "
    "    a curve (<path d='M ... Q ...' or many <line> segments) "
    "    through ~10+ sample points, plus roots marked as dots "
    "    (<circle r='4'>) on the x-axis.\n"
    "  • A triangle / trapezoid / polygon question requires a "
    "    <polygon> with vertex coords arranged so the shape is "
    "    visible at the sizes above.\n"
    "  • A cone / pyramid / cylinder / 3-D solid: draw an oblique 2-D "
    "    projection — base ellipse (cone/cylinder) plus side lines to "
    "    the apex, OR an isometric outline; use dashed strokes for "
    "    hidden edges.\n"
    "  • An algebraic-transformation question (complete-the-square, "
    "    factor, expand) — show the algebra step-by-step in stacked "
    "    <text> rows AND visualise the geometric meaning (e.g. for "
    "    completing the square, draw the corresponding rectangle / "
    "    square geometry on coordinates).\n"
    "If the topic genuinely has NO visual content (e.g. 'define a "
    "field axiomatically'), the figure may consist mostly of labelled "
    "<text>; otherwise a missing shape is a hard failure.\n"
    "\n"
    "TOPIC-REQUIRED PRIMITIVE — a non-negotiable subset of DRAW THE "
    "SHAPE: certain prompts have an ABSOLUTELY REQUIRED visual element "
    "without which the figure is wrong even if labels are correct. "
    "Self-check before emitting:\n"
    "  • Prompt or narration mentions 'unit circle', 'arc length', "
    "    'chord', 'sector', or 'circle of radius'  →  there MUST be a "
    "    <circle> with r in the 150-220 vb-unit range. A tiny <circle "
    "    r='5'> dot is NOT enough.\n"
    "  • Prompt mentions 'derivative', 'integral', 'area under the "
    "    curve', 'f(x) = …', 'the parabola y = …', or any other "
    "    function plot  →  there MUST be a <path d='M … L … L … '> "
    "    with at least 10 sample points (or C/Q Bezier commands) "
    "    tracing the curve, IN ADDITION TO axes lines. Empty axes are "
    "    not a function plot.\n"
    "  • Prompt mentions 'tangent line at x = x₀' or 'slope of the "
    "    tangent'  →  there MUST be both (a) the curve <path> AND "
    "    (b) a separate <line> through the tangent point, spanning "
    "    ~200-300 vb units, with slope = f'(x₀).\n"
    "  • Prompt mentions 'set A and set B', 'overlapping sets', Venn, "
    "    set union/intersection/difference  →  there MUST be at least "
    "    TWO <ellipse rx='180' ry='130'> (or large overlapping "
    "    <circle>) shapes, NOT plain numbers floating in space.\n"
    "  • Prompt asks to 'show / construct / illustrate / reduce' a "
    "    graph, tree, or gadget structure  →  there MUST be at least "
    "    3 <line>/<path> edges connecting <circle> vertices; an empty "
    "    framed area with a title is a hard failure.\n"
    "  • Prompt names an iterative algorithm (Euclidean / gcd / long "
    "    division / Newton's method)  →  there MUST be at least 3 "
    "    <text> rows containing the actual numeric step (e.g. "
    "    '252 = 2·105 + 42'). Empty horizontal rules with no numbers "
    "    are a hard failure.\n"
    "Before emitting the JSON: re-read the user's prompt, identify "
    "which of these categories applies, and confirm the matching "
    "primitive is present. The deterministic critic will flag any "
    "miss and force a retry.\n"
    "\n"
    "MENTION = SHOW — every measurable quantity you NAME in narration "
    "must also be VISIBLY DRAWN on the figure as a labelled element "
    "with the same letter.  No exceptions.  Concretely:\n"
    "  • If narration says 'the height h is 4', the SVG must include "
    "    a dashed perpendicular segment between the parallel sides AND "
    "    a <text> reading 'h' (or 'h = 4') next to that segment.  Don't "
    "    just say 'the height h' without drawing it.\n"
    "  • If narration says 'the base b₁ = 6', the SVG must show that "
    "    base with a <text> 'b₁' or 'b₁ = 6' adjacent to the segment.\n"
    "  • If narration says 'angle θ = 30°', the SVG must show an arc at "
    "    the vertex with a <text> 'θ' or 'θ = 30°' next to the arc.\n"
    "  • Same for radius, diameter, hypotenuse, altitude, side a/b/c, "
    "    angle α/β/γ, perimeter, area — if the narration NAMES it with "
    "    a letter, the figure must DRAW it AND LABEL it with that letter.\n"
    "Rule of thumb: before emitting the JSON, scan your own narration "
    "for every '<quantity-word> <letter>' pair and verify each letter "
    "appears as the content of a <text> element in your SVG.  If any "
    "doesn't, EITHER add the labelled element to the SVG, OR drop the "
    "mention from the narration.  Naming a quantity you didn't draw is "
    "a hard failure — the learner hears a measurement that isn't on "
    "the page.\n"
    "\n"
    "VERIFY ARITHMETIC — before stating any final numeric answer in "
    "narration:\n"
    "  • Write the formula on the figure as a <text> element "
    "    (symbolic).\n"
    "  • Write the substituted form with the actual numbers on the "
    "    figure as a separate <text> element.\n"
    "  • Compute the final value DELIBERATELY: do the arithmetic step "
    "    by step in your head, then write the result on the figure too.\n"
    "  • RE-CHECK the arithmetic before emitting.  A wrong final "
    "    number in the narration is a hard failure; treat the value "
    "    you compute with the same care a textbook author treats a "
    "    worked example.  Common error modes to guard against: "
    "    dropped factor of 1/2, sign flip, off-by-one on subscripts, "
    "    forgetting a unit, transcription error between symbolic and "
    "    numeric form.\n"
    "  • The narration's concluding phrase MUST match the value "
    "    written on the figure.  If they disagree, recompute both.\n"
    "\n"
    "FIRST NARRATION PHRASE — must be specific to THE QUESTION the "
    "user asked.  Not a generic transition.  Examples:\n"
    "  • User asks 'show how the angles of a triangle sum to π' → first "
    "    phrase: 'In any triangle, the three interior angles always add "
    "    up to π radians — here's why.'  (concrete, names the claim)\n"
    "  • User asks 'show matrix multiplication' → first phrase: 'Matrix "
    "    multiplication combines a row of A with a column of B to "
    "    produce one cell of C.'  (states the operation)\n"
    "  • User asks 'draw the unit circle' → first phrase: 'The unit "
    "    circle is the circle of radius 1 centred at the origin.'  "
    "    (defines the object)\n"
    "BANNED openings (these sound like filler from a continuation):\n"
    "  • 'Now let's…'  — implies we were just discussing something else\n"
    "  • 'Let's see…' / 'Let's look at…'  — empty hedge\n"
    "  • 'First, let's…' / 'To begin…'  — meta-narration; just begin\n"
    "  • 'OK so…' / 'Alright,…'  — verbal-tic preamble\n"
    "  • 'And now please look at the diagram.'  — leftover transition\n"
    "Open with the IDEA, not a transition into it.\n"
    "\n"
    "NARRATION TONE — write as a TEXTBOOK author, not a chatbot.\n"
    "  • Precise, declarative sentences.  Define things before using them.\n"
    "  • Use the named theorem when one applies.  'By the Pythagorean "
    "theorem,…' / 'By Bayes' rule,…' / 'By the FTC,…'  not 'we know that…'\n"
    "  • Standard notation pronounced naturally: say 'pi' for π, 'theta' "
    "for θ, 'sigma from k equals one to n' for ∑ₖ₌₁ⁿ.  Read fractions as "
    "'a over b'.  Read x² as 'x squared'.  Use 'such that' for ':' or "
    "'|' in set-builder.\n"
    "  • Avoid colloquialisms ('super easy', 'kind of like', 'awesome').\n"
    "  • For proofs, use 'Given… | Construction… | By [theorem]… | "
    "Therefore…' — Euclid-style markers if appropriate.\n"
    "  • Length: aim for 8-15 phrases for a typical concept; up to 25 "
    "for a multi-step proof.  Each phrase ≤ 15 words; this is read "
    "aloud, not read.\n"
    "\n"
    "FORBIDDEN:\n"
    "  • Empty boxes labelled just 'A', 'B', 'C' with nothing inside.\n"
    "  • Three-column-of-indices layouts that just enumerate cells "
    "without showing how the operation works.\n"
    "  • Captions that point at empty canvas (leader lines must end on "
    "real elements with matching ids).\n"
    "  • Stopping after one or two narration phrases — go through the "
    "whole computation.\n"
    "  • Regenerating a prior figure from scratch when the user asked "
    "for a targeted change.  When a PRIOR FIGURE block is in the "
    "conversation, the SVG must keep every prior element (same ids, "
    "same coordinates, same captions) and only add or modify the "
    "elements the user's edit touches.  Visual continuity matters — "
    "the user is iterating on a figure, not asking for an unrelated "
    "one each turn.\n"
    "  • Re-narrating prior content on a refinement.  When a PRIOR "
    "FIGURE block is in the conversation, the ``narration`` field "
    "must contain ONLY the NEW phrases that describe the change — "
    "NOT the prior narration verbatim plus new tail.  The user has "
    "ALREADY heard the prior audio; restating it word-for-word is "
    "exactly the wrong behaviour.  Example: prior narration was "
    "['Here is triangle ABC.', 'The interior angles are α, β, γ.', "
    "'They sum to π.'].  User says 'now add the median from A'.  "
    "Correct narration: ['I'm adding the median from A to the "
    "midpoint of BC.', 'It bisects side BC at point M.'].  WRONG: "
    "the original three phrases plus the two new ones.\n"
    "\n"
    "If the user's request can't reasonably be drawn, emit a small SVG "
    "saying so plus a one-phrase narration explaining why.\n"
    "\n"
    "==== TEMPLATES FOR PROMPTS WHERE THE MODEL HISTORICALLY FAILS ====\n"
    "Two prompt families are routinely produced as EMPTY figures because "
    "the model can't decide on a layout.  When the user's prompt falls "
    "into one of these families, follow the matching template closely "
    "(adapt to the user's actual numbers, but keep the layout).\n"
    "\n"
    "(A) ITERATIVE-ALGORITHM TRACE (gcd, long division, Newton's method, "
    "Euclidean algorithm):\n"
    "  • Lay out 4-5 rows of step text, each one <text> on its own row, "
    "    y values spaced 60 px apart, font-size 28-32 px, x = 80.\n"
    "  • Each row shows the equation for that step using ACTUAL numbers, "
    "    not blanks.  For Euclidean gcd(a,b): row 1 'a = q1·b + r1', row "
    "    2 'b = q2·r1 + r2', etc., until remainder = 0.  Final row: "
    "    'gcd = <last non-zero remainder>'.\n"
    "  • Optional: a 2-column grid <g> with 'dividend', 'quotient', "
    "    'remainder' headers on top and the per-step values below.\n"
    "  • Concrete example for gcd(252, 105):\n"
    "    <text x='80' y='120' font-size='30'>252 = 2 · 105 + 42</text>\n"
    "    <text x='80' y='180' font-size='30'>105 = 2 · 42 + 21</text>\n"
    "    <text x='80' y='240' font-size='30'>42  = 2 · 21 + 0</text>\n"
    "    <text x='80' y='320' font-size='34' font-weight='bold'>gcd(252, 105) = 21</text>\n"
    "  • The narration walks through one row per phrase, highlighting "
    "    the matching <text id='step_k'>.\n"
    "\n"
    "(B) REDUCTION CONSTRUCTION (3SAT → vertex cover, SAT → 3SAT, "
    "Hamiltonian → TSP, etc.):\n"
    "  • Draw a SMALL concrete instance, not the general abstract "
    "    construction.  For 3SAT → VC, use a 2-clause / 2-variable "
    "    example like φ = (x₁ ∨ x₂) ∧ (¬x₁ ∨ x₂).\n"
    "  • Lay out two vertical bands:\n"
    "    LEFT BAND (variable gadgets, x ≈ 150-400): for each variable "
    "    xᵢ draw two <circle r='28'> with labels 'xᵢ' and '¬xᵢ', "
    "    connected by a <line>.  Stack the gadgets vertically.\n"
    "    RIGHT BAND (clause gadgets, x ≈ 600-850): for each clause "
    "    draw a triangle of three <circle r='28'> with the literal "
    "    labels of the clause inside; connect the three circles with "
    "    <line> elements to form the triangle.\n"
    "  • Then draw cross-edges (<line>) from each clause vertex to "
    "    its corresponding variable vertex in the left band.\n"
    "  • Highlight the chosen vertex cover (e.g., colour those "
    "    <circle> with a thicker stroke or distinct fill).\n"
    "  • Add a <text> at the bottom stating |VC| = k for the matching "
    "    SAT assignment.\n"
    "  • The narration walks through: variable gadgets → clause gadgets "
    "    → cross-edges → vertex cover choice → why it works.\n"
    "\n"
    "(C) MATRIX DECOMPOSITION (spectral theorem, eigendecomposition "
    "A = QΛQᵀ, SVD, LU, Cholesky, diagonalisation): the model "
    "routinely emits headings like 'Step 1: Compute QΛ' and 'Step 2: "
    "Compute QΛQᵀ' with EMPTY space after them — the matrices "
    "themselves never get drawn.  Always render the actual numeric "
    "matrices, not placeholder labels:\n"
    "  • Pick a SMALL concrete instance — for spectral on a 2×2, the "
    "    cleanest worked example is A = [[2, 1], [1, 2]]:\n"
    "       det(A − λI) = (2−λ)² − 1 = 0  →  λ₁ = 3, λ₂ = 1\n"
    "       eigenvectors: v₁ = (1, 1)/√2  for λ=3\n"
    "                     v₂ = (1, −1)/√2  for λ=1\n"
    "       Q = (1/√2) · [[1, 1], [1, −1]]\n"
    "       Λ = [[3, 0], [0, 1]]\n"
    "       check: QΛQᵀ = [[2, 1], [1, 2]] = A ✓\n"
    "  • Lay out FOUR labelled matrix boxes in a row, each as a "
    "    <g> group with a border <rect> + inner <text> cells:\n"
    "      LEFT  : A   = [[2, 1], [1, 2]]                       (yellow tint)\n"
    "      THEN  : Q   = (1/√2) [[1, 1], [1, −1]]                (blue tint)\n"
    "      THEN  : Λ   = [[3, 0], [0, 1]]                         (green tint)\n"
    "      RIGHT : Qᵀ  = (1/√2) [[1, 1], [1, −1]]                (blue tint)\n"
    "    Optional: a fifth box on a second row showing QΛQᵀ "
    "    multiplied out cell-by-cell, ending in A.\n"
    "  • Every cell MUST contain its concrete numeric value (or "
    "    1/√2-prefixed value).  Never emit a matrix that reads "
    "    'a b / c d' — substitute actual numbers.\n"
    "  • Below the matrices, a single <text> states the verification: "
    "    'QΛQᵀ = A — the spectral decomposition is verified.'\n"
    "  • The narration walks through: matrix A → eigenvalues → "
    "    eigenvectors → assembled Q and Λ → verification.\n"
    "\n"
    "When the prompt does NOT fall into these families, ignore the "
    "templates and use your own layout.  But never emit an empty "
    "framed canvas — that is always wrong."
    + _LANGUAGE_RULE
)


# ── Vision review (used between retries) ─────────────────────────────────

_REVIEW_SYSTEM = (
    "MATH CORRECTNESS COMES FIRST — IT IS NOT OPTIONAL.  Before any "
    "layout judgement, check whether the math in the figure is "
    "actually correct.  If the user is given the FIGURE'S OWN stated "
    "solution and `math_claims`, cross-check the rendered figure "
    "against them: does what is drawn agree?  Specifically FAIL on:\n"
    "  • a wrong derivative / integral / Hessian / value rendered\n"
    "  • an algebraic identity that doesn't hold (e.g. "
    "(a+b)^2 = a^2 + b^2 missing the 2ab cross-term)\n"
    "  • a graph homomorphism whose arrows do not preserve edges, a "
    "graph colouring with same-coloured adjacent vertices, a triangle "
    "whose drawn angle measures don't fit the claim\n"
    "  • a worked example whose final number is wrong\n"
    "  • the figure CONTRADICTS the user's question or the stated "
    "solution\n"
    "A wrong-math figure is WORSE than no figure, because it teaches "
    "the learner something false.  Math correctness is the single "
    "most important check you do.\n"
    "\n"
    "NARRATION–FIGURE GEOMETRIC VOCABULARY MUST MATCH.  When the "
    "narration uses a geometric term — TANGENT, SECANT, PERPENDICULAR, "
    "PARALLEL, INTERSECTS, CROSSES, TOUCHES, PASSES THROUGH, MIDPOINT, "
    "VERTEX, ASYMPTOTE, NORMAL, BISECTOR — the corresponding figure "
    "element MUST actually exhibit that geometric property, not just "
    "approximate it.  Specifically FAIL when:\n"
    "  • the narration calls a line 'tangent to' a curve but the line "
    "    visibly does not touch the curve at exactly one point with the "
    "    curve's local slope (parallel lines, secants crossing the "
    "    curve at two points, or lines floating away from the curve "
    "    are all NOT tangent — flag this);\n"
    "  • the narration says 'perpendicular' but the lines do not meet "
    "    at a visible right angle;\n"
    "  • the narration says 'parallel' but the lines visibly converge "
    "    or diverge;\n"
    "  • the narration says 'crosses the x-axis at x = 3' but the "
    "    visible crossing is at a different x;\n"
    "  • the narration names a point as 'midpoint' / 'vertex' / "
    "    'centroid' / 'focus' but the marker is placed somewhere else.\n"
    "If the narration's geometric vocabulary does not match the figure's "
    "geometry, it is a math-correctness failure (teaches the learner a "
    "false relationship between two visual elements) — FAIL with a "
    "specific fix that names both the narrated term and the actual "
    "geometric defect.\n"
    "\n"
    "You are a pragmatic reviewer of mathematical figures AND the "
    "narration that explains them.  You are given the rendered figure "
    "(as a PNG) and the spoken narration script (as text).  Default to "
    "PASS for visual polish — partial figures, mid-quality labelling, "
    "and missing-but-non-essential captions are PASS.\n"
    "\n"
    "NEVER FAIL on narration highlights.  The narration `highlight` "
    "field references SVG element IDs that the VIEWER colors at "
    "playback time — they DO NOT appear in the static PNG you are "
    "reviewing.  Do not flag a figure for 'highlights not matching "
    "elements', 'elements not visibly emphasized', 'narration not "
    "synchronized with figure', or any similar variant.  Whether the "
    "highlight IDs map to real SVG elements is checked separately by "
    "a deterministic structural critic; you do not need to second-"
    "guess it.\n"
    "\n"
    "FAIL on these BROKEN-FIGURE problems:\n"
    "  • orphan leader lines pointing to empty canvas\n"
    "  • notation mismatches the user's request (wrong dimensions, "
    "wrong concept)\n"
    "  • main content missing entirely (e.g. 'matrix multiplication' "
    "with no matrices visible at all)\n"
    "  • TEXT OVERLAP — any text element whose bounding box visibly "
    "overlaps another text element, a shape interior, a stroke, a "
    "drawn CURVE, or an axis tick label, such that one of the strings "
    "is partially or fully hidden.  Look carefully at the rendered "
    "PNG: if a learner would have to mentally separate two collided "
    "strings to read them, that's a FAIL.  Common forms: caption "
    "overlapping a label INSIDE a polygon; two labels stacked at the "
    "same y; a paragraph of body text with a function curve drawn "
    "straight THROUGH it; a long formula crossing an axis or arrow; "
    "tick labels of different series sharing pixel space.  Be strict "
    "— overlapping text is the #1 reason learners give up on a "
    "figure.\n"
    "  • EMPTY PLACEHOLDER SHAPES — a rectangle, box or region that is "
    "drawn but left blank when it was clearly meant to contain "
    "something (a labelled box with no label inside, three empty "
    "coloured rectangles standing in for content).  An empty box "
    "teaches nothing — FAIL.\n"
    "  • MISSING DEFINING CONTENT — the figure omits the element that "
    "IS the concept: a 'Riemann sum' with no rectangles, a 'histogram' "
    "with no bars, a 'bifurcation diagram' with no branching, a "
    "'phase portrait' with no trajectories.  The caption naming the "
    "concept is not enough; the defining visual must actually be "
    "drawn.\n"
    "  • NEAR-EMPTY FIGURE — the canvas is mostly blank, or carries "
    "only a title and a couple of stray strokes.  If a learner opening "
    "this would see almost nothing, FAIL.\n"
    "  • wrong topology (3SAT-clique drawn as a tree, etc.)\n"
    "  • the figure is clearly the WRONG TOPIC compared to the user's "
    "prompt (e.g. user asked for an integral, the figure shows a "
    "triangle with no curve), OR the figure visibly carries over "
    "content from a previous turn that does not belong in this one "
    "(stale matrix sitting next to a new circle, etc.).\n"
    "  • OVERSIZED ELEMENTS — a single shape so large it dominates the "
    "canvas and crowds out the axes, labels or other content (e.g. an "
    "SVM figure where the class 'blobs' are huge filled circles "
    "covering the separating line and margins).  Every element must be "
    "scaled sensibly relative to the figure.\n"
    "  • IRRELEVANT ELEMENTS — a drawn element with no pedagogical "
    "purpose for THIS prompt: a coordinate grid or axes behind a "
    "pure-algebra derivation, a decorative curve that illustrates "
    "nothing, a shape the narration never refers to.  Each element "
    "must earn its place; flag the ones that do not.\n"
    "\n"
    "FAIL on these MATH-CORRECTNESS problems (these matter at least as "
    "much as visual problems — a beautiful figure that teaches a wrong "
    "fact is worse than a sloppy figure that teaches the truth):\n"
    "  • Factually wrong claim in any narration phrase, on-canvas "
    "caption, or text label.  Examples of false claims to catch: "
    "'interior angles of a triangle sum to 2π' (it is π); 'a prime "
    "number has exactly three divisors' (it has two); 'sin²θ + cos²θ "
    "= 2'; 'the derivative of x² is x'; mis-stated formulas, wrong "
    "constants, wrong dimensions, false set-theoretic identities, "
    "wrong limits, swapped definitions.  Be especially alert when a "
    "claim is plausible-sounding but the constant or coefficient is "
    "wrong.\n"
    "  • Claim↔figure mismatch — the narration or caption asserts "
    "something specific that the figure does not actually show.  "
    "Examples: 'observe how the three angle arcs at A, B, C sum to π' "
    "but no angle arcs are drawn; caption says 'shaded region is the "
    "intersection' but nothing is shaded; narration names element "
    "id='cell_a_2_3' which doesn't exist in the SVG; claim says "
    "'equilateral triangle' but the rendered sides are visibly "
    "unequal; claim labels a point as the centroid but it's clearly "
    "not at the average of the vertices.\n"
    "  • Geometric impossibility visible in the figure — three points "
    "claimed collinear but the rendered line bends; circle claimed to "
    "pass through a labelled point but the point sits clearly off the "
    "circle; an angle marked '90°' that visibly is not.\n"
    "\n"
    "When you FAIL, list concrete actionable fixes.  Use:\n"
    "  • fix_narration_phrase — for false claims in spoken narration "
    "(give the phrase index + the corrected text in details).\n"
    "  • fix_caption_text — for false text on the canvas (give the "
    "current wrong text + the corrected text in details).\n"
    "  • add_element / highlight_relation — when the claim is "
    "correct but the figure needs the visual demonstration the claim "
    "references (e.g. add the angle arcs the narration is talking "
    "about).\n"
    "  • the existing actions (add_label, fix_layout, fix_notation, "
    "etc.) for visual-only problems.\n"
    "\n"
    "Pedagogical perfection (e.g. 'no concrete worked example shown') "
    "is NOT a FAIL condition; the user can ask for that as a "
    "follow-up.  But a wrong claim is always FAIL, regardless of how "
    "polished the figure looks."
)


# Structured JSON schema the reviewer is forced to follow, so retries
# get an actionable diff (with specific text values, positions, etc.)
# instead of vague prose the generator can't translate into changes.
REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["PASS", "FAIL"],
        },
        "summary": {
            "type": "string",
            "description": (
                "One-sentence overall assessment.  On PASS this is the "
                "praise; on FAIL this is the headline of what's wrong."
            ),
        },
        "fixes": {
            "type": "array",
            "description": (
                "Ordered list of concrete corrective actions.  Empty "
                "list when verdict is PASS.  When verdict is FAIL this "
                "MUST contain at least one fix; vague review without "
                "specific fixes is itself a review failure."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "add_element", "modify_element", "remove_element",
                            "fill_with_data", "add_label", "add_formula",
                            "add_conclusion", "fix_layout", "highlight_relation",
                            "fix_notation",
                            "fix_narration_phrase", "fix_caption_text",
                        ],
                    },
                    "what": {
                        "type": "string",
                        "description": (
                            "The specific element by name or role (e.g. "
                            "'matrix A label', 'cell c_2_3 value', "
                            "'edge between x1 and C2', 'general formula "
                            "for the operation')."
                        ),
                    },
                    "where": {
                        "type": "string",
                        "description": (
                            "Position guidance (e.g. 'above the figure', "
                            "'inside the leftmost grid', 'between A and "
                            "B', 'replacing the existing label')."
                        ),
                    },
                    "details": {
                        "type": "string",
                        "description": (
                            "The exact content / values / text the "
                            "generator should use.  E.g. 'fill cells "
                            "with the matrix [[2,3,1,5,7],[1,4,2,6,3],"
                            "[5,2,8,1,4]]'; 'caption text: C1 = (x1 ∨ "
                            "¬x2 ∨ x3)'; 'use a 2-D grid arrangement, "
                            "not a column of labels'."
                        ),
                    },
                },
                "required": ["action", "what", "where", "details"],
            },
        },
    },
    "required": ["verdict", "summary", "fixes"],
}


def _review_user_prompt(
    user_prompt: str,
    narration: list[dict[str, Any]] | None = None,
    svg_text: str | None = None,
    solution: str | None = None,
    math_claims: list | None = None,
    figure_ground_truth: Any = None,
) -> str:
    """Build the user message body for a figure review.

    When ``svg_text`` is provided (text-mode review), the SVG source is
    inlined so a text-only reviewer can read structure/ids directly.
    When None (vision-mode review), the caller attaches the rendered
    PNG separately as a multi-modal image_url block.
    """
    svg_block = ""
    if svg_text:
        # Keep this bounded so a runaway SVG doesn't blow the context.
        # 16 KB is enough for any reasonable figure (the express
        # schema's max useful output is ~6-8 KB).
        if len(svg_text) > 16_000:
            svg_text = svg_text[:16_000] + "\n<!-- truncated for review -->"
        svg_block = (
            "\nSVG source (literal text, ids and attributes intact):\n"
            "```svg\n" + svg_text + "\n```\n"
        )
    narration_block = ""
    if narration:
        lines = []
        for i, phrase in enumerate(narration, start=1):
            speak = (phrase or {}).get("speak", "")
            highlight = (phrase or {}).get("highlight") or []
            if highlight:
                lines.append(f"  [{i}] {speak!r}  → highlights: {highlight}")
            else:
                lines.append(f"  [{i}] {speak!r}")
        narration_block = (
            "\nNarration script (spoken aloud while the figure plays):\n"
            + "\n".join(lines)
            + "\n"
        )
    # The math LLM's own stated solution + claims are the reference
    # truth: the reviewer cross-checks the rendered figure against it.
    math_block = ""
    if solution:
        math_block += f"\nLLM's stated solution:\n  {solution[:600]}\n"
    if math_claims:
        lines = []
        for c in math_claims[:10]:
            if isinstance(c, dict):
                lines.append(
                    f"  - {c.get('description','?')}: "
                    f"{c.get('a','?')} == {c.get('b','?')}")
        if lines:
            math_block += ("\nMath claims it asserts (already verified "
                           "by a CAS):\n" + "\n".join(lines) + "\n")
    # Independent figure-level ground truth: positional / relational /
    # value claims derived from the prompt by a separate proposer +
    # SymPy validator, NOT from the figure LLM's own narration.  Empty
    # string when extraction returned nothing useful, so this append
    # is always safe.
    ground_truth_block = ""
    if figure_ground_truth is not None:
        try:
            from studio.templates.figure_ground_truth import (
                render_for_reviewer as _render_gt,
            )
            ground_truth_block = _render_gt(figure_ground_truth)
        except Exception:  # noqa: BLE001
            ground_truth_block = ""
    return (
        f"User asked: {user_prompt!r}\n"
        f"{math_block}"
        f"{ground_truth_block}"
        f"{narration_block}"
        f"{svg_block}\n"
        "Review the figure AND the narration together.  Two independent "
        "judgements:\n"
        "\n"
        "(A) Visual polish.  PASS unless the figure is genuinely broken "
        "or wildly off-topic.  Specifically PASS when the main content "
        "matches the request (matrices for matrix mult, clauses-and-"
        "edges for 3SAT-clique, etc.), even if some polish is missing.  "
        "FAIL on: main content missing entirely; wrong topology; orphan "
        "leader lines; dimensions don't match the request; text "
        "completely overlapping text.\n"
        "\n"
        "(A.0) Granular highlight — every narration phrase that NAMES "
        "a specific variable, symbol, row, column, cell, or sub-formula "
        "must highlight an id for THAT specific thing, not the whole "
        "containing group.  FAIL when a phrase says 'the variable x' "
        "or 'row 2' or 'the discriminant' or 'the second clause' but "
        "the highlight array points at the whole figure / matrix / "
        "formula instead of an id that wraps just that one piece.  "
        "The fix is to give the named piece its own <text id='...'> "
        "(or <tspan id='...'> inside a longer formula) and to update "
        "the phrase's highlight array to reference that id.\n"
        "\n"
        "(A.1) Label placement — every text label must sit at or "
        "ADJACENT to the element it names, not floating in empty space "
        "and not on top of an unrelated element.  FAIL when:\n"
        "  • A vertex label letter (A, B, v_1, …) lands inside a "
        "    different vertex's circle, or far away from its own "
        "    vertex (more than ~one vertex diameter).\n"
        "  • An edge weight / edge label sits on a different edge "
        "    than the one it labels, or far from any edge.\n"
        "  • An axis-tick number is on the wrong side of the axis or "
        "    not aligned with its tick.\n"
        "  • A caption that names an element points at empty space "
        "    instead of that element, OR its leader line crosses "
        "    through other figure content.\n"
        "  • Two labels collide so the text becomes unreadable.\n"
        "Light overlap that doesn't obscure either label is fine.\n"
        "\n"
        "(A.2) Lines / arrows logic — every drawn edge, arrow, "
        "chord, or connector must connect endpoints that are "
        "logically related by the figure's topic.  FAIL when:\n"
        "  • An arrow's direction contradicts the claim (causal "
        "    arrow pointing from effect to cause; flow arrow "
        "    pointing against the flow).\n"
        "  • A graph edge connects two vertices that the prompt or "
        "    narration says are NOT adjacent, OR a claimed edge is "
        "    missing from the figure.\n"
        "  • A line crosses through a vertex/region it shouldn't "
        "    (e.g. a chord drawn outside the circle, a radius not "
        "    starting at the center).\n"
        "  • An arrow's head is at the wrong end (origin vs target "
        "    swapped relative to the narration / caption).\n"
        "  • A connector ends in empty space with no clear endpoint.\n"
        "\n"
        "(B) Math-correctness inspection.  Check each narration phrase "
        "AND each on-canvas caption/label for factual truth and for "
        "consistency with the figure.  FAIL on:\n"
        "  • A claim that is mathematically false on its face "
        "    (e.g. 'angles of a triangle sum to 2π' — the truth is π; "
        "    'derivative of x² is x' — the truth is 2x).  Pay attention "
        "    to constants, signs, exponents, and direction of "
        "    inequalities — these are where false claims hide.\n"
        "  • A claim that names something the figure does not show "
        "    (e.g. 'observe the angle arcs at A, B, C' but no arcs are "
        "    drawn; highlight ids that don't exist in the SVG; caption "
        "    'shaded intersection' but nothing is shaded).\n"
        "  • A geometric assertion the figure visibly contradicts "
        "    (claimed equilateral triangle with unequal sides, claimed "
        "    right angle that is not 90°, claimed point on circle that "
        "    is plainly off it).\n"
        "\n"
        "Verdict='PASS' only when BOTH (A) and (B) pass.  A wrong claim "
        "is always FAIL even if the figure looks good — fixing the math "
        "matters more than visual polish.\n"
        "\n"
        "If FAIL, populate fixes[] with concrete actions:\n"
        "  • fix_narration_phrase — what='phrase N' (1-indexed), "
        "    details='exact corrected text the speaker should say'.\n"
        "  • fix_caption_text — what=current wrong text or element id, "
        "    details='exact corrected caption text'.\n"
        "  • add_element / highlight_relation — when the claim is "
        "    correct but the figure lacks the visual the claim names "
        "    (give what to draw, where, and any text).\n"
        "  • existing visual actions (add_label, fix_layout, "
        "    fix_notation, etc.) for purely visual issues."
    )


# ── Streaming chat-completion helper ──────────────────────────────────────

async def _stream_chat_completion(
    *,
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    on_svg_chunk: Callable[[str], Awaitable[None]],
    log: Callable[[str], None],
) -> str:
    """Issue a streaming chat-completion request; while deltas arrive,
    pull the partial 'svg' field out and surface it to the caller via
    ``on_svg_chunk``.  Returns the accumulated raw JSON content the
    same way the non-streaming branch does, so the rest of the
    pipeline (structural review, vision review, retries) is unchanged.

    Errors are surfaced the same way as the non-streaming branch:
    HTTP errors raise via ``raise_for_status``; any other exception
    propagates up to ``express_figure``'s outer try.
    """
    extractor = _StreamingSvgExtractor()
    last_emitted_len = 0
    full = []

    try:
        async with client.stream(
            "POST", url, headers=headers, json=payload,
        ) as r:
            log(f"main request returned status={r.status_code} (stream)")
            if r.status_code != 200:
                body = (await r.aread()).decode(errors="replace")
                log(f"main request body: {body[:500]}")
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line:
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = (choices[0].get("delta") or {}).get("content")
                if not delta:
                    # Final chunk may carry the full content under
                    # 'message' instead of 'delta' (OpenAI behaviour
                    # on stream finish for some response_format
                    # variants); pick it up if so.
                    msg = choices[0].get("message") or {}
                    delta = msg.get("content")
                    if not delta:
                        continue
                full.append(delta)
                if extractor.feed(delta):
                    partial = extractor.partial_svg
                    if len(partial) > last_emitted_len:
                        last_emitted_len = len(partial)
                        try:
                            await on_svg_chunk(partial)
                        except Exception as cb_exc:  # noqa: BLE001
                            log(
                                f"on_svg_chunk raised "
                                f"{type(cb_exc).__name__}: {cb_exc} "
                                f"(continuing)"
                            )
    except Exception as exc:  # noqa: BLE001
        log(f"main request errored (stream): {type(exc).__name__}: {exc}")
        raise

    content = "".join(full)
    log(f"stream finished total_len={len(content)} svg_emitted={last_emitted_len}")
    return content


# ── Theory primer (streamed in parallel with figure generation) ───────────

_PRIMER_SYSTEM = (
    "You are a mathematics tutor.  The learner has asked a question that "
    "will be answered visually by a separate figure generator running "
    "in parallel.  Your job: write a thorough, spoken-style PRIMER "
    "(6 to 12 sentences, roughly 150 to 280 words) that introduces "
    "the concept, explains the intuition for WHY it works, states "
    "every key formula the learner needs to follow the upcoming "
    "figure, and gives a small concrete example if it helps the "
    "learner ground the formulas.  Treat this as the first half of "
    "a 1-on-1 tutorial — explain enough that the learner UNDERSTANDS "
    "the technique, not just sees it.  Length follows depth of "
    "topic: a Newton's-method primer or a 3-SAT-reduction primer "
    "earns the full 12 sentences; a 'what is a derivative' primer "
    "for a beginner might land closer to 8.  Lean LONG over "
    "SHORT — a student forgives extra detail faster than they "
    "forgive a half-explanation.\n\n"
    "RULES:\n"
    "  * Plain prose, no headings, no bullet lists, no markdown.\n"
    "  * Write so it can be SPOKEN aloud at natural pace.\n"
    "  * MATH NOTATION — every mathematical symbol or formula MUST be "
    "    wrapped in `$...$` (inline) or `$$...$$` (display).  This is "
    "    a hard rule.  KaTeX renders ONLY content inside `$` "
    "    delimiters; ANY LaTeX outside `$` (e.g. a bare `\\theta` "
    "    or `\\sum_{i=1}^n` floating in prose) will be shown to the "
    "    learner as raw source like `\\theta` — never acceptable.\n"
    "    Examples:\n"
    "      RIGHT:  'The angle $\\theta$ satisfies $\\sin^2\\theta + "
    "              \\cos^2\\theta = 1$.'\n"
    "      WRONG:  'The angle \\theta satisfies sin^2 + cos^2 = 1.'\n"
    "      WRONG:  'For a 3x3 matrix A, det(A) = a_{11}(...).'\n"
    "      RIGHT:  'For a $3 \\times 3$ matrix $A$, $\\det(A) = "
    "              a_{11}(\\dots)$.'\n"
    "    EVERY variable name, every formula, every `_{...}`, every "
    "    backslash-command goes inside `$...$`.  When in doubt, wrap.\n"
    "  * Do NOT describe the figure (you cannot see it).  Do NOT say "
    "    'as shown below' or 'in the diagram.'  Speak only the theory.\n"
    "  * Stop after the formula(s).  Do NOT add a closing summary.\n"
    "  * NEVER mention that another component is generating a figure."
    + _LANGUAGE_RULE
)


async def localise_narration(
    narration: list[dict[str, Any]],
    original_user_prompt: str,
    *,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini",
    timeout_s: float = 20.0,
) -> list[dict[str, Any]]:
    """Translate every `speak` string in ``narration`` into the same
    language as ``original_user_prompt``.

    English prompts pass through unchanged (the LLM detects English
    and returns the phrases verbatim).  For non-English target
    languages, the model is instructed to spell numbers out as words
    so the downstream TTS doesn't swallow digits — German user reports
    confirmed that "1.5" / "1,5" pronounce poorly while "eineinhalb"
    is clean.

    Failure is non-fatal: any error (no key, parse mismatch, request
    timeout) returns the original narration unchanged.  Adds at most
    one ~200-token gpt-4o-mini call per express turn (~$0.0001).

    Disable with SEVIM_LOCALISE_NARRATION=off.
    """
    import os, json, re, unicodedata, httpx
    if os.environ.get("SEVIM_LOCALISE_NARRATION", "on").lower() == "off":
        return narration
    if not narration or not original_user_prompt or not api_key:
        return narration

    speaks = [(p.get("speak") or "").strip() for p in narration]
    if not any(speaks):
        return narration

    # Fast-path: if the user prompt contains NO non-Latin script
    # characters (no Persian / Arabic / Chinese / Japanese / Korean /
    # Cyrillic / Devanagari / Hebrew / Greek / Thai), and no
    # non-ASCII Latin diacritics (no ü ö ä ñ é ç ...), it is
    # overwhelmingly likely English.  Skip the LLM round-trip
    # entirely.  An earlier prod run had gpt-4o-mini hallucinate an
    # English-to-Spanish translation on the plain English Newton
    # prompt; this fast-path makes that impossible by construction.
    def _looks_like_plain_english(s: str) -> bool:
        for ch in s:
            if ord(ch) < 128:
                continue
            cat = unicodedata.category(ch)
            # Letters outside ASCII -> not plain English.
            if cat.startswith("L"):
                return False
            # Math symbols / punctuation / digits in the non-ASCII
            # range (× ÷ π ∫ √ ≈ ≤ ≥ ∞) are fine.
        return True

    if _looks_like_plain_english(original_user_prompt):
        return narration

    system_msg = (
        "You are a narration localiser for a math-figure tutor.  You "
        "are given (a) the user's original prompt and (b) an array of "
        "spoken narration phrases (currently in English).  Detect the "
        "language of the user's prompt.\n"
        "\n"
        "DEFAULT TO ENGLISH.  If the prompt is ambiguous, mixed, or "
        "could be English with a couple of foreign loan-words, output "
        "language='en' and return the phrases array VERBATIM.  Only "
        "translate when the prompt is UNAMBIGUOUSLY non-English (the "
        "majority of words are in a non-English language).\n"
        "\n"
        "If language='en', return the phrases array verbatim — do NOT "
        "translate, paraphrase, normalise, or 'improve' anything.\n"
        "\n"
        "Otherwise, translate every phrase into the user's language, "
        "preserving math content exactly.  Math symbols (π, x², ∫, √) "
        "stay as symbols; the prose around them switches to the user's "
        "language.\n"
        "\n"
        "When translating into a non-English language, SPELL EVERY "
        "NUMBER OUT AS A WORD in the target language so downstream TTS "
        "engines (Piper, OpenAI tts-1) don't swallow digits.  "
        "Examples:\n"
        "  English '1.5'  →  German 'eineinhalb'  /  French 'un "
        "virgule cinq'  /  Persian 'یک و نیم' / Chinese '一点五'.\n"
        "  English 'three' stays 'drei' / 'trois' / 'سه' / '三'.\n"
        "  English '≈ 1.26'  →  German 'ungefähr eins Komma zwei "
        "sechs'.\n"
        "\n"
        "Return JSON: {\"language\": \"<ISO 639-1 code>\", \"phrases\": "
        "[\"...\", \"...\", ...]}.  The phrases array MUST have "
        "exactly the same length as the input."
    )
    user_msg = json.dumps({
        "user_prompt": original_user_prompt[:400],
        "phrases": speaks,
    }, ensure_ascii=False)
    payload = {
        "model": model,
        "temperature": 0.0,
        "max_tokens": 1800,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
    }
    headers = {"content-type": "application/json",
               "Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers=headers, json=payload,
            )
        if r.status_code != 200:
            return narration
        content = r.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
        # Trust the model's language decision: if it says 'en' (or
        # any English variant), keep the originals.  A prior prod
        # run had the model say language='en' but still translate
        # the phrases to Spanish; this short-circuit makes that
        # impossible.
        lang_decision = str(data.get("language") or "").strip().lower()
        if lang_decision in ("en", "eng", "english", "en-us", "en-gb"):
            return narration
        translated = data.get("phrases") or []
    except Exception:  # noqa: BLE001
        return narration
    if not isinstance(translated, list) or len(translated) != len(speaks):
        return narration
    out: list[dict[str, Any]] = []
    for i, p in enumerate(narration):
        new_speak = (translated[i] or "").strip()
        if not new_speak:
            new_speak = speaks[i]  # fall back to original on empty
        out.append({**p, "speak": new_speak})
    return out


# Defensive post-processor: catches bare LaTeX (commands or subscripts
# not wrapped in $...$) that slipped past the prompt and wraps them.
# Runs on the full assembled primer string (after streaming ends) so
# we don't have to handle chunk boundaries.

_BARE_LATEX_RE = __import__("re").compile(
    r"""
    (?<!\$)              # not already preceded by a $
    (
        \\[A-Za-z]+      # a backslash command like \theta, \sum
        (?:_\{[^}]*\})?  # optional subscript
        (?:\^\{[^}]*\})? # optional superscript
        |
        [A-Za-z]_\{[^}]*\}   # variable with subscript like a_{ij}
        |
        [A-Za-z]\^\{[^}]*\}  # variable with superscript like x^{2}
    )
    (?!\$)               # not followed by a $
    """,
    __import__("re").VERBOSE,
)


def wrap_bare_latex(text: str) -> str:
    """Wrap any bare LaTeX command/subscript that ESCAPED the primer's
    `$...$` rule.  Idempotent — text whose math is already wrapped
    passes through unchanged.

    Conservatively skips spans already inside `$` (single OR double)
    so we don't double-wrap valid `$\\theta$`.  Also skips spans
    inside `\\(..\\)` / `\\[..\\]` which KaTeX also recognises.
    """
    import re

    # Split text into "math regions" (inside $...$ / $$...$$ / \\(..\\) /
    # \\[..\\]) and "prose regions" (everything else).  Only run the
    # bare-LaTeX wrapper on prose regions.
    delim_re = re.compile(
        r"(\$\$[^$]*\$\$|\$[^$\n]*\$|\\\([^)]*\\\)|\\\[[^\]]*\\\])"
    )
    out: list[str] = []
    cursor = 0
    for m in delim_re.finditer(text):
        prose = text[cursor:m.start()]
        out.append(_BARE_LATEX_RE.sub(r"$\1$", prose))
        out.append(m.group(0))
        cursor = m.end()
    out.append(_BARE_LATEX_RE.sub(r"$\1$", text[cursor:]))
    return "".join(out)


async def generate_theory_primer(
    user_prompt: str,
    base_url: str,
    model: str,
    api_key: str | None,
    on_text_chunk: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """Quick parallel call that produces a 3-5 sentence theoretical
    primer with LaTeX-formatted formulas for the learner's prompt.

    Designed to run *concurrently* with ``express_figure`` so the
    learner reads/hears the theory while the figure is still being
    generated.  Each streamed text delta is forwarded to
    ``on_text_chunk`` (raw delta, not cumulative) so the chat surface
    can render and vocalise it in real time.  The full assembled
    primer is returned for downstream use (e.g. telemetry, or as the
    canvas's prelude string for non-streaming clients).

    Errors do not raise — the primer is decorative; if the call
    fails the chat just falls through to the figure with no primer.
    """
    headers = {"content-type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        # Bumped from 220 -> 700 so the primer can hit the new
        # 6-12 sentence / ~280-word target without truncation.
        # Each token ≈ 0.75 words; 700 tokens ≈ 525 words of
        # English, which comfortably accommodates the upper end.
        "max_tokens": 700,
        "temperature": 0.3,
        "stream": True,
        "messages": [
            {"role": "system", "content": _PRIMER_SYSTEM},
            {"role": "user",   "content": user_prompt},
        ],
    }

    import sys as _sys
    def _log(msg: str) -> None:
        print(f"[primer] {msg}", flush=True, file=_sys.stderr)

    _log(f"start prompt={user_prompt[:60]!r} model={model} url={base_url}")
    full: list[str] = []
    chunks_emitted = 0
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream(
                "POST",
                f"{base_url.rstrip('/')}/chat/completions",
                headers=headers, json=payload,
            ) as r:
                if r.status_code != 200:
                    body = (await r.aread()).decode(errors="replace")
                    _log(f"primer non-200 status={r.status_code}: {body[:300]}")
                    return ""
                async for line in r.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = (choices[0].get("delta") or {}).get("content")
                    if not delta:
                        msg = choices[0].get("message") or {}
                        delta = msg.get("content")
                        if not delta:
                            continue
                    full.append(delta)
                    if on_text_chunk is not None:
                        try:
                            await on_text_chunk(delta)
                            chunks_emitted += 1
                        except Exception as cb_exc:  # noqa: BLE001
                            _log(
                                f"on_text_chunk raised "
                                f"{type(cb_exc).__name__}: {cb_exc}"
                            )
    except Exception as exc:  # noqa: BLE001
        _log(f"primer call failed: {type(exc).__name__}: {exc}")
        return ""

    out = "".join(full)
    _log(f"done total_len={len(out)} chunks_emitted={chunks_emitted}")
    # Server-side rescue: if the model emitted bare LaTeX (a `\theta`
    # or `a_{ij}` floating in prose without `$...$` delimiters), wrap
    # it.  Streaming has already finished by now so this is a no-cost
    # post-process on the assembled string.  The wrapper is
    # idempotent — already-wrapped text passes through unchanged.
    fixed = wrap_bare_latex(out)
    if fixed != out:
        delta = fixed[len(out):] if fixed.startswith(out) else fixed
        # Send the FULL corrected text as one final chunk so the
        # frontend can replace what it streamed earlier.  Use a
        # special leading marker the chat handler recognises:
        # primerEl.textContent = primerRaw will reset to clean text.
        if on_text_chunk is not None:
            try:
                # Emit a sentinel: the prefix
                # "[[KMTRP_REPLACE_PRIMER]]" tells the chat handler
                # to wipe primerRaw and re-render from this string
                # instead of appending.  Chosen to never collide with
                # legitimate math or prose content.
                await on_text_chunk("[[KMTRP_REPLACE_PRIMER]]" + fixed)
                _log(
                    f"bare-LaTeX rescue: wrapped {fixed.count('$') - out.count('$')} "
                    "extra delimiters"
                )
            except Exception as cb_exc:  # noqa: BLE001
                _log(
                    f"on_text_chunk replace raised "
                    f"{type(cb_exc).__name__}: {cb_exc}"
                )
        out = fixed
    return out


# ── Pipeline entry point ──────────────────────────────────────────────────

async def express_figure(
    user_prompt: str,
    base_url: str,
    model: str,
    api_key: str | None,
    # max_retries=2 — total budget = 3 attempts.  Previously 1 (2 total
    # attempts) on the assumption that attempt 0 was usually best.
    # Bumped back to 2 (3 total) after the Tier-5 figure-ground-truth
    # audit started firing: with the new audit, retries become more
    # productive (the critique now carries SymPy-verified positional
    # answers, not just vague layout complaints).  Cost: ~10-15 s extra
    # latency on a failing prompt, none on a passing one.  See
    # studio/templates/figure_ground_truth.py for the audit details.
    max_retries: int = 2,
    context_canvases: list[dict[str, Any]] | None = None,
    on_svg_chunk: Callable[[str], Awaitable[None]] | None = None,
    allow_panels: bool = True,
    allow_sequential: bool = True,
    # The user's literal message, passed through the tool-call layer
    # without paraphrasing.  When provided, ALL deterministic-routing
    # decisions (template classifier, algorithm-trace gate, sequential
    # gate, panels gate, graphviz gate) run against this prompt rather
    # than the chat-LLM's reworded ``user_prompt``.  This keeps the
    # template router on the user's actual words and prevents the chat
    # LLM from accidentally hijacking us into the LLM-SVG path with
    # paraphrases like "Illustrate ... step by step ..." when the user
    # said "Show Newton's method".  Falls back to ``user_prompt`` when
    # not supplied (so internal callers — recursive express calls from
    # the sequential / panels routes — keep working unchanged).
    original_user_prompt: str | None = None,
) -> dict[str, Any]:
    """Run the SVG-direct + vision-review loop.

    ``context_canvases`` is an optional list of prior figures the user
    is refining or combining.  Each entry: ``{"id": str, "svg": str,
    "prompt": str, "narration": list[dict] | None}``.  Each prior
    figure's SVG is rendered to PNG and attached to the LLM's first
    user message as a multi-modal block, so gpt-4o sees the actual
    pixels of what the user is referring to.

    Returns ``{"svg": str, "narration": list, "title": str,
                "review_history": [str, ...], "retries_used": int}``.
    """
    headers = {"content-type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Build the user message --- multi-modal when prior context exists.
    # On refinement turns, send the USER'S LITERAL request rather than
    # the chat-LLM's paraphrased tool prompt.  When the user says
    # "change the colour of the curve to red" the chat LLM frequently
    # rewrites it into a self-contained math request that loses the
    # edit intent (verified post-deploy: prompt "Please change the
    # colour of the function curve to red" came through as title
    # "Tangent to x² at x = 3").  REFINEMENT MODE asks the model to
    # preserve unchanged elements byte-for-byte, so it needs to see
    # the user's actual instruction.
    figure_prompt = user_prompt
    if context_canvases and original_user_prompt:
        figure_prompt = original_user_prompt
    user_content = _build_user_content(figure_prompt, context_canvases or [])

    # Text-only backends (Qwen2.5-7B-Instruct, base Qwen, etc.) reject
    # multimodal image_url blocks with a 500.  The refinement intent is
    # still preserved via the text portions (the prior SVG XML + the
    # prompt that made it), so we just drop the image attachments when
    # the target is text-only.  Detection by model name keeps this
    # robust if SEVIM_QWEN_VLLM_URL is repointed at a different host.
    text_only_models = ("qwen_lora_v4", "qwen_base",
                        "Qwen/Qwen2.5-7B-Instruct")
    if model in text_only_models and isinstance(user_content, list):
        text_blocks = [b for b in user_content
                       if isinstance(b, dict) and b.get("type") == "text"]
        stripped_images = len(user_content) - len(text_blocks)
        user_content = text_blocks if text_blocks else user_prompt
        if stripped_images:
            print(f"[express] stripped {stripped_images} image block(s) "
                  f"for text-only backend {model!r}",
                  flush=True, file=__import__("sys").stderr)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _EXPRESS_SYSTEM},
        {"role": "user", "content": user_content},
    ]

    review_history: list[str] = []
    result: dict[str, Any] = {}
    # Snapshot of the most recent FAILED attempt's (svg, narration, critique).
    # When a later attempt PASSes the reviewer, this snapshot pairs with the
    # passing result to form a (bad → critique → good) repair triple — the
    # core distillation signal export_finetune.py turns into DPO/SFT data.
    prev_fail: tuple[str, list[dict[str, Any]], str] | None = None
    repairs: list[dict[str, Any]] = []
    # Best-attempt accumulator: every fully post-processed attempt
    # records its svg, narration, title, structural-issue count, and
    # vision verdict.  If all attempts fail review, the loop exit
    # picks the lowest-scoring one to ship (NOT the last one) — the
    # 3-SAT case had attempt 1 with 5 overlap pairs vs attempt 0 with
    # 1; the old code shipped attempt 2 (worst).  See _attempt_score().
    attempts: list[dict[str, Any]] = []

    import sys as _sys
    def _log(msg: str) -> None:
        print(f"[express] {msg}", flush=True, file=_sys.stderr)

    _log(f"start prompt={user_prompt[:60]!r} model={model}")

    # Routing-decision prompt: prefer the user's literal message if it
    # was threaded through (chat-loop sets it), otherwise fall back to
    # ``user_prompt`` (which is what internal recursive callers pass).
    # All six deterministic gates below use ``routing_prompt`` so the
    # chat LLM's paraphrase ("Illustrate ... step by step ...") cannot
    # hijack us into the sequential / LLM-SVG path when the user
    # actually said something like "Show Newton's method".
    # Default to the user's literal message for routing — that's what
    # the deterministic template classifiers want for fresh prompts.
    # EXCEPT on Case B / Case C refinement (a prior canvas is
    # attached AND the request is not a narrow targeted edit).  In
    # that case the chat LLM has already bundled the prior topic
    # into its tool-call prompt ('Illustrate Newton's method for
    # finding roots using f(x) = x^3 - 2 ...') -- the user's
    # literal ('Explain visually and with proper formulas') lacks
    # the topic context and would steer FDL / template router to a
    # generic figure.  On Case B / C use the chat LLM's enriched
    # prompt so the newton_method template / FDL extractor see the
    # topic.
    if (context_canvases
            and not is_narrow_targeted_edit(
                original_user_prompt or user_prompt or "")):
        routing_prompt = (user_prompt or original_user_prompt or "").strip()
    else:
        routing_prompt = (original_user_prompt or user_prompt or "").strip()
    if original_user_prompt and original_user_prompt != user_prompt:
        _log(f"routing prompt differs from tool prompt: "
             f"routing={routing_prompt[:80]!r}")

    # ── Refinement-mode gate ──────────────────────────────────────
    # Only SKIP deterministic templates when the user is making a
    # narrow Case A targeted edit ('change the curve to red', 'add a
    # label x_3') — that's the only mode where byte-for-byte
    # preservation of the prior figure is correct, and the
    # deterministic templates would clobber the user's edit by
    # re-running with their default colours / labels.
    #
    # For Case B (complaint: 'these are not tangent lines') and
    # Case C (elaboration: 'explain visually with proper formulas'),
    # the deterministic templates (newton_method, FDL TangentAt,
    # …) produce clean, math-correct, single-figure layouts that
    # are strictly better than what gpt-4o-LLM-SVG manages on a
    # redraw.  Let them fire normally.
    _refining = bool(context_canvases) and is_narrow_targeted_edit(
        original_user_prompt or user_prompt or ""
    )
    if _refining:
        _log(f"refinement mode (narrow Case A edit): "
             f"{len(context_canvases or [])} prior canvas(es) attached "
             f"— skipping deterministic routes")
    elif context_canvases:
        _log(f"refinement context attached ({len(context_canvases)} "
             f"canvas(es)) but request is Case B/C — deterministic "
             f"templates still eligible")

    # ── Deterministic algorithm-trace route ───────────────────────
    # "Show <sorting / search / Gaussian elimination / determinant>
    # step by step" — compute every intermediate state in Python and
    # render it as a deterministic stack of grids.  Runs FIRST so a
    # step-by-step algorithm prompt never reaches the sequential
    # route (which used to let the LLM redraw — and mis-number —
    # every step independently).
    if (api_key and allow_sequential and not _refining
            and os.environ.get("SEVIM_ALGO_TRACE", "on").lower()
            != "off"):
        try:
            from studio.templates.algorithm_trace import (
                generate_algorithm_trace_svg, is_algorithm_trace_prompt,
            )
            if is_algorithm_trace_prompt(routing_prompt):
                trace = await generate_algorithm_trace_svg(
                    user_prompt, api_key=api_key or "",
                    base_url=base_url, model=model)
                if trace is not None:
                    tr_svg, tr_narr = trace
                    _log(f"algorithm-trace fast-path: svg={len(tr_svg)} "
                         f"chars narration={len(tr_narr)} phrases")
                    if on_svg_chunk is not None:
                        try:
                            await on_svg_chunk(tr_svg)
                        except Exception:  # noqa: BLE001
                            pass
                    return {
                        "svg": tr_svg,
                        "narration": tr_narr,
                        "title": "",
                        "review_history": [],
                        "retries_used": 0,
                        "repairs": [],
                        "template": "algorithm_trace",
                    }
        except Exception as exc:  # noqa: BLE001
            _log(f"algorithm-trace route errored: "
                 f"{type(exc).__name__}: {exc}")

    # ── Deterministic process / cycle route ───────────────────────
    # "<X> cycle" / "scientific method" — extract the ordered stages
    # and render a deterministic ring (cyclic) or vertical flow
    # (linear).  Runs before the sequential route so a process prompt
    # never gets stacked as LLM-drawn sub-figures.
    if (api_key and allow_sequential and not _refining
            and os.environ.get("SEVIM_PROCESS_ROUTE", "on").lower()
            != "off"):
        try:
            from studio.templates.process_route import (
                generate_process_svg, is_process_prompt,
            )
            if is_process_prompt(routing_prompt):
                proc = await generate_process_svg(
                    user_prompt, api_key=api_key or "",
                    base_url=base_url, model=model)
                if proc is not None:
                    pr_svg, pr_narr = proc
                    _log(f"process route fast-path: svg={len(pr_svg)} "
                         f"chars narration={len(pr_narr)} phrases")
                    if on_svg_chunk is not None:
                        try:
                            await on_svg_chunk(pr_svg)
                        except Exception:  # noqa: BLE001
                            pass
                    return {
                        "svg": pr_svg,
                        "narration": pr_narr,
                        "title": "",
                        "review_history": [],
                        "retries_used": 0,
                        "repairs": [],
                        "template": "process",
                    }
        except Exception as exc:  # noqa: BLE001
            _log(f"process route errored: "
                 f"{type(exc).__name__}: {exc}")

    # ── Symbolic-math route ───────────────────────────────────────
    # Derivatives, Hessians, gradients, integrals, limits, and
    # "find / classify the critical points".  The LLM only extracts
    # the function + operation; SymPy SOLVES it exactly and matplotlib
    # typesets the result.  Runs BEFORE the multi-panel / sequential
    # routes on purpose: a "solve this problem" prompt must be solved
    # exactly, not decomposed into panels whose intermediate values
    # the decomposer LLM would guess (and get wrong).
    # Disabled via SEVIM_SYMBOLIC_ROUTE=off.
    if (api_key and not _refining
            and os.environ.get("SEVIM_SYMBOLIC_ROUTE", "on").lower()
            != "off"):
        try:
            from studio.templates.symbolic_route import (
                generate_symbolic_svg, is_symbolic_prompt,
            )
            if is_symbolic_prompt(user_prompt):
                sym_result = await generate_symbolic_svg(
                    user_prompt, api_key=api_key or "", base_url=base_url)
                if sym_result is not None:
                    sym_svg, sym_narration = sym_result
                    _log(f"symbolic fast-path: svg={len(sym_svg)} chars "
                         f"narration={len(sym_narration)} phrases")
                    if on_svg_chunk is not None:
                        try:
                            await on_svg_chunk(sym_svg)
                        except Exception:  # noqa: BLE001
                            pass
                    return {
                        "svg": sym_svg,
                        "narration": sym_narration,
                        "title": "",
                        "review_history": [],
                        "retries_used": 0,
                        "repairs": [],
                        "template": "symbolic",
                    }
        except Exception as exc:  # noqa: BLE001
            _log(f"symbolic route errored: {type(exc).__name__}: {exc}")

    # ── Graph-homomorphism route ─────────────────────────────────
    # The LLM emits two graphs and a function f:V(G)->V(H); a
    # deterministic O(|E_G|) verifier confirms the mapping IS a
    # homomorphism BEFORE the figure is rendered.  Fixes the earlier
    # tangle-of-dashed-arrows class of failures.
    if (api_key and not _refining
            and os.environ.get("SEVIM_HOMOM_ROUTE", "on").lower()
            != "off"):
        try:
            from studio.templates.graph_homomorphism import (
                generate_homomorphism_svg, is_homomorphism_prompt,
            )
            if is_homomorphism_prompt(user_prompt):
                hm_result = await generate_homomorphism_svg(
                    user_prompt, api_key=api_key or "",
                    base_url=base_url)
                if hm_result is not None:
                    hm_svg, hm_narration = hm_result
                    _log(f"homomorphism fast-path: svg={len(hm_svg)} "
                         f"chars narration={len(hm_narration)} phrases")
                    if on_svg_chunk is not None:
                        try:
                            await on_svg_chunk(hm_svg)
                        except Exception:  # noqa: BLE001
                            pass
                    return {
                        "svg": hm_svg,
                        "narration": hm_narration,
                        "title": "",
                        "review_history": [],
                        "retries_used": 0,
                        "repairs": [],
                        "template": "homomorphism",
                    }
        except Exception as exc:  # noqa: BLE001
            _log(f"homomorphism route errored: "
                 f"{type(exc).__name__}: {exc}")

    # ── Multi-panel route ─────────────────────────────────────────
    # For "compare X and Y side by side" / cross-referenced-panel
    # prompts, decompose into sub-figures and composite them into a
    # deterministic grid.  Runs FIRST — before the single-figure
    # routes — and recurses into express_figure per panel with
    # allow_panels=False so a sub-prompt cannot re-enter this route.
    if (api_key and allow_panels and not _refining
            and os.environ.get("SEVIM_PANELS_ROUTE", "on").lower()
            != "off"):
        try:
            from studio.templates.panels_route import (
                generate_panels_svg, is_panels_prompt,
            )
            if is_panels_prompt(routing_prompt):
                async def _gen_panel(sub: str) -> dict[str, Any]:
                    return await express_figure(
                        sub, base_url=base_url, model=model,
                        api_key=api_key, max_retries=1,
                        allow_panels=False, allow_sequential=False)
                pan = await generate_panels_svg(
                    user_prompt, api_key=api_key or "",
                    base_url=base_url, model=model,
                    gen_panel=_gen_panel)
                if pan is not None:
                    pan_svg, pan_narr = pan
                    _log(f"panels fast-path: svg={len(pan_svg)} chars "
                         f"narration={len(pan_narr)} phrases")
                    if on_svg_chunk is not None:
                        try:
                            await on_svg_chunk(pan_svg)
                        except Exception:  # noqa: BLE001
                            pass
                    return {
                        "svg": pan_svg,
                        "narration": pan_narr,
                        "title": "",
                        "review_history": [],
                        "retries_used": 0,
                        "repairs": [],
                        "template": "panels",
                    }
        except Exception as exc:  # noqa: BLE001
            _log(f"panels route errored: {type(exc).__name__}: {exc}")

    # NOTE: the sequential step-frame route used to live HERE (before
    # the template router and FDL).  Production showed that "step by
    # step" Newton's-method prompts were being decomposed by sequential
    # into LLM-drawn sub-figures, losing continuity and accuracy, when
    # the deterministic newton_method template (and FDL) already
    # handle the iteration correctly in one cohesive figure.  Moved
    # sequential to AFTER the template router and FDL so iterative-
    # math prompts hit those deterministic paths first; sequential
    # only fires for genuinely step-shaped prompts that no
    # deterministic path catches (e.g. "explain the scientific
    # method step by step").  See the block further down.

    # ── Template fast-path ────────────────────────────────────────
    # Graphviz fast-path: for prompts that look graph-shaped (state
    # machines, Turing machines, DAGs, trees, Hasse diagrams, Cayley
    # graphs, etc.), have the LLM emit DOT source and render with
    # the `dot` binary instead of going through the full LLM-SVG
    # loop. Graphviz's 30+ year-old layout engine handles positioning
    # and overlap-avoidance with hard correctness guarantees.
    # Disabled via SEVIM_GRAPHVIZ_ROUTE=off or when `dot` is missing.
    if (api_key and not _refining
            and os.environ.get("SEVIM_GRAPHVIZ_ROUTE", "on").lower() != "off"):
        try:
            from studio.templates.graphviz_route import (
                generate_graphviz_svg, is_graphviz_binary_available,
                is_graphviz_prompt, narrate_graphviz,
            )
            if (is_graphviz_binary_available()
                    and is_graphviz_prompt(routing_prompt)):
                gv_result = await generate_graphviz_svg(
                    user_prompt,
                    api_key=api_key or "",
                    base_url=base_url,
                )
                if gv_result is not None:
                    gv_svg, gv_dot = gv_result
                    _log(f"graphviz fast-path: dot={len(gv_dot)} chars "
                         f"svg={len(gv_svg)} chars")
                    if on_svg_chunk is not None:
                        try:
                            await on_svg_chunk(gv_svg)
                        except Exception:  # noqa: BLE001
                            pass
                    # Synthesise phrase-synced narration that
                    # highlights the Graphviz node/edge ids — without
                    # this the figure renders with nothing spotlighted
                    # while the audio plays.
                    gv_narration = await narrate_graphviz(
                        user_prompt, gv_svg,
                        api_key=api_key or "", base_url=base_url,
                    )
                    _log(f"graphviz narration: {len(gv_narration)} phrases")
                    return {
                        "svg": gv_svg,
                        "narration": gv_narration,
                        "title": "",
                        "review_history": [],
                        "retries_used": 0,
                        "repairs": [],
                        "template": "graphviz",
                    }
        except Exception as exc:  # noqa: BLE001
            _log(f"graphviz route errored: {type(exc).__name__}: {exc}")

    # Matplotlib fast-path: for plot-shaped prompts (regression, SVM /
    # decision boundaries, function curves, 3-D surfaces, contour
    # plots) the LLM emits a structured plot spec and matplotlib
    # renders it deterministically — in-bounds and correctly scaled by
    # construction, with genuine 3-D projection.  No LLM code is ever
    # executed; the route accepts only a closed-vocabulary spec.
    # Disabled via SEVIM_MATPLOTLIB_ROUTE=off.
    if (api_key and not _refining
            and os.environ.get("SEVIM_MATPLOTLIB_ROUTE", "on").lower()
            != "off"):
        try:
            from studio.templates.matplotlib_route import (
                generate_matplotlib_svg, is_matplotlib_prompt,
            )
            if is_matplotlib_prompt(user_prompt):
                mpl_result = await generate_matplotlib_svg(
                    user_prompt, api_key=api_key or "",
                    base_url=base_url,
                )
                if mpl_result is not None:
                    mpl_svg, mpl_narration = mpl_result
                    _log(f"matplotlib fast-path: svg={len(mpl_svg)} "
                         f"chars narration={len(mpl_narration)} phrases")
                    if on_svg_chunk is not None:
                        try:
                            await on_svg_chunk(mpl_svg)
                        except Exception:  # noqa: BLE001
                            pass
                    return {
                        "svg": mpl_svg,
                        "narration": mpl_narration,
                        "title": "",
                        "review_history": [],
                        "retries_used": 0,
                        "repairs": [],
                        "template": "matplotlib",
                    }
        except Exception as exc:  # noqa: BLE001
            _log(f"matplotlib route errored: {type(exc).__name__}: {exc}")

    # ── Figure Description Language (FDL) route ──────────────────
    # Sits BETWEEN the specific-template router (Newton, sphere, cone,
    # ...) and the general LLM-SVG path.  FDL = a small set of concept
    # primitives (Plot, MarkPoint, TangentAt, AxisMark, Caption) that
    # the LLM emits as structured JSON; a deterministic renderer
    # composes them.  This covers prompts that don't match a
    # template-classifier rule but ARE function-graphable: "Explain
    # Newton's method visually", "Show the derivative of f at x=3",
    # "Plot f and its tangent at x=2", etc.  By construction, tangent
    # lines drawn here are real tangents (slope = f'(x) via SymPy),
    # so the LLM-SVG's secant-as-tangent failure mode cannot recur.
    # The template router still runs FIRST so explicit
    # Newton's-method prompts pin to the bespoke newton template.
    if (api_key and not _refining
            and os.environ.get("SEVIM_TEMPLATE_ROUTER", "on").lower() != "off"):
        try:
            from studio.templates.router import (
                classify_prompt, render_template,
            )
            classified = await classify_prompt(
                routing_prompt,
                api_key=api_key or "",
                base_url=base_url,
            )
            if classified:
                tpl_name, tpl_args = classified
                rendered = render_template(tpl_name, tpl_args)
                if rendered:
                    tpl_svg, tpl_narration = rendered
                    _log(
                        f"template fast-path: {tpl_name} args="
                        f"{sorted(tpl_args.keys())} svg={len(tpl_svg)} chars"
                    )
                    if on_svg_chunk is not None:
                        # Stream the SVG to the iframe so progressive
                        # paint still happens (instant on a template).
                        try:
                            await on_svg_chunk(tpl_svg)
                        except Exception:  # noqa: BLE001
                            pass
                    return {
                        "svg": tpl_svg,
                        "narration": tpl_narration,
                        "title": tpl_name.replace("_", " ").title(),
                        "review_history": [],
                        "retries_used": 0,
                        "repairs": [],
                        "template": tpl_name,
                    }
                else:
                    _log(f"template classifier picked {tpl_name} but "
                         f"render rejected args; falling back to LLM")
        except Exception as exc:  # noqa: BLE001
            _log(f"template router errored: {type(exc).__name__}: {exc}")

    # ── FDL fallback ──────────────────────────────────────────────
    # Specific-template classifier missed.  Try the general Figure
    # Description Language: the LLM extracts a small Scene of concept
    # primitives (Plot + MarkPoint + TangentAt + ...) from the
    # prompt; the deterministic renderer composes them.  By
    # construction the rendered tangents have the correct slope
    # (f'(x) via SymPy), so the LLM-SVG's "draw a generic dashed
    # line and call it a tangent" failure mode cannot recur.  An
    # empty / unparseable extraction returns None and we fall
    # through to the LLM-SVG path unchanged.  Disabled via
    # SEVIM_FDL_ROUTE=off.
    if (api_key and not _refining
            and os.environ.get("SEVIM_FDL_ROUTE", "on").lower() != "off"):
        try:
            from studio.templates.fdl import (
                llm_extract_scene, render_scene,
            )
            scene = await llm_extract_scene(
                routing_prompt, api_key=api_key or "",
                base_url=base_url,
            )
            if scene is not None:
                fdl_svg, fdl_narration = render_scene(scene)
                _log(
                    f"FDL fast-path: title={scene.title!r} "
                    f"prims={len(scene.primitives)} "
                    f"svg={len(fdl_svg)} chars"
                )
                if on_svg_chunk is not None:
                    try:
                        await on_svg_chunk(fdl_svg)
                    except Exception:  # noqa: BLE001
                        pass
                return {
                    "svg": fdl_svg,
                    "narration": fdl_narration,
                    "title": scene.title,
                    "review_history": [],
                    "retries_used": 0,
                    "repairs": [],
                    "template": "fdl",
                }
        except Exception as exc:  # noqa: BLE001
            _log(f"FDL route errored "
                 f"(falling through to LLM-SVG): "
                 f"{type(exc).__name__}: {exc}")

    # ── Sequential step-frame route (moved here from before the
    # template router) ─────────────────────────────────────────────
    # For "show X step by step" prompts that DO NOT match any
    # deterministic template and DO NOT extract as an FDL Scene,
    # decompose into ordered steps and stack a per-step figure
    # vertically.  Each sub-step recurses with allow_sequential=False
    # / allow_panels=False so the decomposition is one level deep.
    # Newton's-method / iterative-algorithm prompts that mention
    # "step by step" hit the template (or FDL) above first; sequential
    # only catches genuinely sequential prompts like "explain the
    # scientific method step by step" or "show the writing process".
    if (api_key and allow_sequential and not _refining
            and os.environ.get("SEVIM_SEQUENTIAL_ROUTE", "on").lower()
            != "off"):
        try:
            from studio.templates.sequential_route import (
                generate_sequential_svg, is_sequential_prompt,
            )
            if is_sequential_prompt(routing_prompt):
                async def _gen_step(sub: str) -> dict[str, Any]:
                    return await express_figure(
                        sub, base_url=base_url, model=model,
                        api_key=api_key, max_retries=1,
                        allow_panels=False, allow_sequential=False)
                seq = await generate_sequential_svg(
                    user_prompt, api_key=api_key or "",
                    base_url=base_url, model=model,
                    gen_step=_gen_step)
                if seq is not None:
                    seq_svg, seq_narr = seq
                    _log(f"sequential fast-path: svg={len(seq_svg)} "
                         f"chars narration={len(seq_narr)} phrases")
                    if on_svg_chunk is not None:
                        try:
                            await on_svg_chunk(seq_svg)
                        except Exception:  # noqa: BLE001
                            pass
                    return {
                        "svg": seq_svg,
                        "narration": seq_narr,
                        "title": "",
                        "review_history": [],
                        "retries_used": 0,
                        "repairs": [],
                        "template": "sequential",
                    }
        except Exception as exc:  # noqa: BLE001
            _log(f"sequential route errored: "
                 f"{type(exc).__name__}: {exc}")

    # Compute the figure-level ground truth ONCE per express call,
    # outside the retry loop.  This is a separate small LLM proposer
    # (gpt-4o-mini by default) followed by a SymPy validator that
    # produces a list of positional / relational / value claims a
    # correct figure must visibly satisfy.  The validated list is
    # used in TWO places:
    #   (1) fed FORWARD to the figure-generating LLM (below) so it
    #       draws the figure with the right numbers the first time;
    #   (2) passed to _vision_review on every retry attempt so the
    #       reviewer always has independent ground truth to compare
    #       the rendered figure against.
    # Failure here is non-fatal: empty result skips both injections,
    # pipeline continues unchanged.  See
    # studio/templates/figure_ground_truth.py for the design rationale.
    figure_ground_truth = None
    try:
        from studio.templates.figure_ground_truth import (
            extract_figure_ground_truth as _extract_fgt,
            render_for_generator as _render_fgt_for_generator,
        )
        figure_ground_truth = await _extract_fgt(user_prompt)
        if figure_ground_truth and figure_ground_truth.claims:
            _log(
                f"figure ground truth: {figure_ground_truth.validated} "
                f"of {figure_ground_truth.proposed} claim(s) validated "
                f"(dropped: {len(figure_ground_truth.dropped_reasons)})"
            )
            # Sample of the dropped reasons (first 3, truncated).
            # Helps tune the proposer prompt over time without
            # spamming CloudWatch.
            for reason in figure_ground_truth.dropped_reasons[:3]:
                _log(f"  fgt dropped: {reason[:160]}")
            # (1) Splice the directive block into the user message we
            # send to the figure LLM.  Prepended so it sits BEFORE the
            # original prompt and the LLM has the validated values in
            # front of it while drawing.
            try:
                gt_block_for_generator = _render_fgt_for_generator(
                    figure_ground_truth
                )
            except Exception as exc:  # noqa: BLE001
                _log(f"render_for_generator FAILED: "
                     f"{type(exc).__name__}: {exc}")
                gt_block_for_generator = ""
            if gt_block_for_generator and isinstance(user_content, str):
                user_content = (
                    gt_block_for_generator + "\n" + user_content
                )
            elif gt_block_for_generator and isinstance(user_content, list):
                # Refinement-mode multi-modal payload: prepend the
                # ground-truth as a leading text block so the LLM
                # reads it before the prior-canvas attachments.
                user_content = (
                    [{"type": "text", "text": gt_block_for_generator}]
                    + user_content
                )
            # Rebuild the messages list with the augmented user_content.
            messages = [
                {"role": "system", "content": _EXPRESS_SYSTEM},
                {"role": "user", "content": user_content},
            ]
        else:
            _log(
                "figure ground truth: no validated claims "
                "(empty or all dropped)"
            )
    except Exception as exc:  # noqa: BLE001
        _log(f"figure ground truth FAILED: {type(exc).__name__}: {exc}")
        figure_ground_truth = None

    for attempt in range(max_retries + 1):
        _log(f"attempt={attempt} sending main request")
        # 1. Ask LLM for {svg, narration, title} in structured form.
        payload = {
            "model": model,
            # 16k output: a complex figure (dense reduction diagram,
            # bifurcation plot) can exceed an 8k SVG and truncate the
            # JSON response mid-string.  You only pay for tokens
            # actually generated, so the higher ceiling is free for
            # normal figures and prevents the truncation crash.
            "max_tokens": 16384,
            "temperature": 0.2,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "express_figure",
                    "schema": EXPRESS_SCHEMA,
                    "strict": True,
                },
            },
            "messages": messages,
        }
        # Streaming variant lights up only on the first attempt of a
        # turn — retries already have an SVG on the canvas, so a
        # partial-SVG replay there would briefly wipe a finished
        # figure mid-correction.  The non-streaming branch matches
        # the original behaviour for any caller that didn't supply a
        # chunk callback.
        # Stream every attempt — including retries — so the learner
        # always sees the figure being redrawn instead of staring at
        # the (failed) first attempt for 30+ seconds while the retry
        # runs invisibly.  The earlier first-attempt-only gate was a
        # defensive choice from when streaming was new; the canvas
        # iframe already swaps content cleanly on each chunk, so a
        # retry stream is functionally identical to a fresh stream.
        stream_this_attempt = on_svg_chunk is not None
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                if stream_this_attempt:
                    content = await _stream_chat_completion(
                        client=client,
                        url=f"{base_url.rstrip('/')}/chat/completions",
                        headers=headers,
                        payload={**payload, "stream": True},
                        on_svg_chunk=on_svg_chunk,
                        log=_log,
                    )
                else:
                    try:
                        r = await client.post(
                            f"{base_url.rstrip('/')}/chat/completions",
                            headers=headers, json=payload,
                        )
                    except Exception as exc:  # noqa: BLE001
                        _log(f"main request errored: {type(exc).__name__}: {exc}")
                        raise
                    _log(f"main request returned status={r.status_code}")
                    if r.status_code != 200:
                        body = (await r.aread()).decode(errors="replace")
                        _log(f"main request body: {body[:500]}")
                    r.raise_for_status()
                    content = r.json()["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as http_exc:
            # OpenAI 5xx / our backend 5xx — transient.  Don't let it
            # raise out of express_figure and kill the chat-loop SSE
            # connection; treat as a failed attempt and continue to
            # the next retry or fall through to best-attempt.  If
            # this is the FIRST attempt and no prior attempts exist,
            # the loop will exit with `attempts` empty and the safe
            # fallthrough at line ~2700 ships an empty SVG (chat-loop
            # then emits tool_result with error body, browser shows a
            # diagnostic instead of hanging forever).
            sc = getattr(http_exc.response, "status_code", "?")
            _log(
                f"upstream HTTP {sc} on attempt={attempt}: "
                f"{type(http_exc).__name__}; "
                f"{'retrying' if attempt < max_retries else 'falling back to best prior attempt'}"
            )
            continue
        except (httpx.RequestError, httpx.TimeoutException) as net_exc:
            # Network-level failure (connection reset, timeout,
            # DNS).  Same treatment as HTTPStatusError above.
            _log(
                f"upstream network error on attempt={attempt}: "
                f"{type(net_exc).__name__}: {net_exc}; "
                f"{'retrying' if attempt < max_retries else 'falling back to best prior attempt'}"
            )
            continue
        _log(f"got content length={len(content)}")
        try:
            result = json.loads(content)
        except json.JSONDecodeError as exc:
            # A truncated response (the model hit max_tokens mid-SVG on
            # a very large figure) yields unterminated JSON.  Never let
            # that crash the turn: log it and retry, or fall through to
            # the exhausted-return path on the last attempt.
            _log(f"JSON parse failed ({exc}); content was likely "
                 f"truncated — {'retrying' if attempt < max_retries else 'giving up'}")
            continue
        if not isinstance(result, dict) or "svg" not in result:
            _log(f"parsed JSON missing 'svg' field; "
                 f"{'retrying' if attempt < max_retries else 'giving up'}")
            continue
        _log(f"parsed: svg_len={len(result.get('svg',''))} phrases={len(result.get('narration',[]))}")
        # Deterministic text-block injection — splice <g> groups
        # containing region-positioned <text> elements into the SVG.
        # LLM-emitted text_blocks bypass the freeform <text x= y=>
        # placement that's the #1 source of overlap; each line in each
        # region is auto-stacked at non-overlapping y-coordinates.
        try:
            text_blocks = result.get("text_blocks") or []
            if text_blocks:
                pre_len = len(result["svg"])
                result["svg"] = inject_text_blocks(result["svg"], text_blocks)
                n_lines = sum(
                    len([ln for ln in (b.get("lines") or []) if str(ln).strip()])
                    for b in text_blocks if isinstance(b, dict)
                )
                _log(
                    f"inject_text_blocks: {len(text_blocks)} region(s), "
                    f"{n_lines} line(s); svg {pre_len} -> "
                    f"{len(result['svg'])} chars"
                )
        except Exception as exc:  # noqa: BLE001
            _log(f"inject_text_blocks FAILED: {type(exc).__name__}: {exc}")
        # Escape stray & / < the model left raw inside <text> content
        # (LaTeX matrix `&` separators, inequalities like `|x| < d`).
        # Must run FIRST — a malformed SVG breaks every XML-parsing
        # pass below and renders as a blank canvas in the browser.
        try:
            fixed_svg = escape_bare_xml_in_svg(result["svg"])
            if fixed_svg != result["svg"]:
                _log(f"escape_bare_xml_in_svg: rewrote "
                     f"{len(result['svg'])} -> {len(fixed_svg)} chars")
                result["svg"] = fixed_svg
        except Exception as exc:  # noqa: BLE001
            _log(f"escape_bare_xml_in_svg FAILED: {type(exc).__name__}: {exc}")
        # (Prose-strip post-processor removed in the Phase 1A cleanup.
        # The deterministic text-region layout prevents the original
        # "narration prose stacked at the same y" failure at the
        # source, so the post-hoc stripper is no longer needed.)
        # Deterministic layout pass — auto-fit every <g>'s outer
        # rectangle to its child elements so a 3×3 matrix drawn with
        # a 200×200 rect but cells extending to (350, 340) gets the
        # rect expanded to wrap everything.  Idempotent: a correctly-
        # sized group passes through unchanged.  Errors are swallowed
        # because layout polish must never block a working figure.
        # Earliest polish pass: pull every <text> back inside the
        # viewBox.  Model occasionally puts section headers at y=-36
        # hoping for clipping — instead they all stack visually at
        # the top edge.  Clamp first, then the structural critic +
        # other passes can work with sane coordinates.
        try:
            fixed_svg = clamp_text_to_viewbox(result["svg"])
            if fixed_svg != result["svg"]:
                _log(
                    f"clamp_text_to_viewbox: rewrote {len(result['svg'])} -> "
                    f"{len(fixed_svg)} chars"
                )
                result["svg"] = fixed_svg
        except Exception as exc:  # noqa: BLE001
            _log(f"clamp_text_to_viewbox FAILED: {type(exc).__name__}: {exc}")
        # Group-transform clamp: when a <g transform="translate(150 0)">
        # has children at local y=0, the rendered glyphs poke above
        # the viewBox top and get clipped (Q=, Λ=, Qᵀ= disappeared in
        # the spectral-theorem audit).  Raise dy so the topmost
        # child's glyph top sits at TOP_MARGIN.
        try:
            fixed_svg = clamp_group_transforms(result["svg"])
            if fixed_svg != result["svg"]:
                _log(
                    f"clamp_group_transforms: rewrote {len(result['svg'])} -> "
                    f"{len(fixed_svg)} chars"
                )
                result["svg"] = fixed_svg
        except Exception as exc:  # noqa: BLE001
            _log(f"clamp_group_transforms FAILED: {type(exc).__name__}: {exc}")
        # First polish pass: convert HTML <sup>/<sub> tags to the
        # proper SVG <tspan baseline-shift='...'> form.  SVG ignores
        # HTML inline tags, so `A<sup>-1</sup>` rendered as the
        # literal "A-1".  Idempotent for SVGs that already use tspan.
        try:
            fixed_svg = fix_html_subsup(result["svg"])
            if fixed_svg != result["svg"]:
                _log(
                    f"fix_html_subsup: rewrote {len(result['svg'])} -> "
                    f"{len(fixed_svg)} chars"
                )
                result["svg"] = fixed_svg
        except Exception as exc:  # noqa: BLE001
            _log(f"fix_html_subsup FAILED: {type(exc).__name__}: {exc}")
        # Matrix-grid normaliser: when text elements look like cell
        # labels (a_11=4, a₁₂=3, …), re-layout them on a regular N×M
        # lattice anchored at the original (min x, min y).  Fixes the
        # "4×4 matrix laid out as 3 columns + 1 stacked separately"
        # failure mode that no overlap pass can repair on its own.
        try:
            # First scrub any LaTeX the model leaked into <text> bodies.
            # SVG is not MathJax — `\( \frac{1}{2} \times h \)` renders
            # as literal characters and looks broken on the canvas.
            fixed_svg = strip_latex_in_svg_text(result["svg"])
            if fixed_svg != result["svg"]:
                _log(
                    f"strip_latex_in_svg_text: rewrote {len(result['svg'])} -> "
                    f"{len(fixed_svg)} chars"
                )
                result["svg"] = fixed_svg
        except Exception as exc:  # noqa: BLE001
            _log(f"strip_latex_in_svg_text FAILED: {type(exc).__name__}: {exc}")
        try:
            fixed_svg = normalize_matrix_layout(result["svg"])
            if fixed_svg != result["svg"]:
                _log(
                    f"normalize_matrix_layout: rewrote {len(result['svg'])} -> "
                    f"{len(fixed_svg)} chars"
                )
                result["svg"] = fixed_svg
        except Exception as exc:  # noqa: BLE001
            _log(f"normalize_matrix_layout FAILED: {type(exc).__name__}: {exc}")
        try:
            fixed_svg = autofit_group_rects(result["svg"])
            if fixed_svg != result["svg"]:
                _log(
                    f"autofit_group_rects: rewrote {len(result['svg'])} -> "
                    f"{len(fixed_svg)} chars"
                )
                result["svg"] = fixed_svg
        except Exception as exc:  # noqa: BLE001
            _log(f"autofit_group_rects FAILED: {type(exc).__name__}: {exc}")
        # Slide overlapping <g> groups apart — handles the case where
        # the model places matrix_a and matrix_a_inverse at the same y
        # but with overlapping x ranges.  Runs BEFORE reflow_overlap-
        # ping_text so the text reflow sees the corrected group bboxes.
        try:
            fixed_svg = reflow_overlapping_groups(result["svg"])
            if fixed_svg != result["svg"]:
                _log(
                    f"reflow_overlapping_groups: rewrote {len(result['svg'])} -> "
                    f"{len(fixed_svg)} chars"
                )
                result["svg"] = fixed_svg
        except Exception as exc:  # noqa: BLE001
            _log(f"reflow_overlapping_groups FAILED: {type(exc).__name__}: {exc}")
        # Second layout pass — reflow top-level <text> elements whose
        # bounding boxes overlap.  Walks every text in document order,
        # shifts later ones down (then over to a new column if
        # needed) until they don't collide with earlier ones.  Handles
        # the "long formula at x=20,y=290 covers three short formulas
        # at x=300/450/600,y=290" failure mode the model keeps emitting.
        # Auto-wrap over-wide text into multiple stacked lines.  Runs
        # AFTER autofit/matrix-grid (so group sizing is settled) but
        # BEFORE reflow_overlapping_text (so the wrapped lines are
        # treated as separate elements by reflow).
        try:
            fixed_svg = wrap_overlong_text(result["svg"])
            if fixed_svg != result["svg"]:
                _log(
                    f"wrap_overlong_text: rewrote {len(result['svg'])} -> "
                    f"{len(fixed_svg)} chars"
                )
                result["svg"] = fixed_svg
        except Exception as exc:  # noqa: BLE001
            _log(f"wrap_overlong_text FAILED: {type(exc).__name__}: {exc}")
        # Run reflow up to 3 times until idempotent.  A single pass
        # can introduce new overlaps when it shifts text into a region
        # that already has neighbours; iterating converges the layout.
        try:
            for _pass in range(3):
                reflowed = reflow_overlapping_text(result["svg"])
                if reflowed == result["svg"]:
                    break
                _log(
                    f"reflow_overlapping_text pass={_pass}: rewrote "
                    f"{len(result['svg'])} -> {len(reflowed)} chars"
                )
                result["svg"] = reflowed
        except Exception as exc:  # noqa: BLE001
            _log(f"reflow_overlapping_text FAILED: {type(exc).__name__}: {exc}")

        # Final pass: globally-optimal label placement via CP-SAT.
        # The greedy reflow above can leave overlaps when an early
        # shift pushes a label into a region with neighbours; the
        # planner formulates the problem as Point-Feature Label
        # Placement and solves to optimality.  Fails open if ortools
        # is missing or the model is infeasible — the SVG passes
        # through unchanged, so reflow's output remains the floor.
        # Env-toggle for A/B testing — bench_layout_planner.py uses
        # SEVIM_LAYOUT_PLANNER=off to compare overlap counts and
        # screenshots planner-on vs off.
        if os.environ.get("SEVIM_LAYOUT_PLANNER", "on").lower() == "off":
            _log("plan_layout: SKIPPED (SEVIM_LAYOUT_PLANNER=off)")
        else:
            try:
                from studio.layout_planner import (
                    plan_layout, extract_text_items,
                    _viewbox, _bbox_at, _bboxes_overlap,
                )

                # Pre-planner overlap count for telemetry — same bbox
                # estimator the planner uses internally.
                def _overlap_count(svg_str: str) -> int:
                    vb = _viewbox(svg_str)
                    if vb is None:
                        return -1
                    items = extract_text_items(svg_str)
                    boxes = [_bbox_at(it.anchor_x, it.anchor_y, it.width,
                                      it.height, it.text_anchor)
                             for it in items]
                    n = 0
                    for i in range(len(boxes)):
                        for j in range(i + 1, len(boxes)):
                            if _bboxes_overlap(boxes[i], boxes[j]):
                                n += 1
                    return n

                pre = _overlap_count(result["svg"])
                # Collect every element id referenced by narration
                # highlights — those get pinned so the canvas
                # viewer's highlight rect (which uses getBBox on the
                # id'd element) stays over the right primitive.
                narration = result.get("narration") or []
                protected: set[str] = set()
                for phrase in narration:
                    hl = phrase.get("highlight") or []
                    if isinstance(hl, str):
                        hl = [hl]
                    for h in hl:
                        if isinstance(h, str) and h:
                            protected.add(h)
                planned = plan_layout(
                    result["svg"],
                    time_limit_s=2.0,
                    protected_ids=protected,
                )
                post = (_overlap_count(planned)
                        if planned != result["svg"] else pre)
                if planned != result["svg"]:
                    _log(
                        f"plan_layout: CP-SAT moved labels, overlaps "
                        f"{pre} -> {post}, svg {len(result['svg'])} -> "
                        f"{len(planned)} chars"
                    )
                    result["svg"] = planned
                elif pre > 0:
                    # Ran but couldn't improve — infeasible, narrow
                    # candidate set, or solver hit time limit.
                    _log(f"plan_layout: NO IMPROVEMENT, overlaps stayed at {pre}")
            except Exception as exc:  # noqa: BLE001
                _log(f"plan_layout FAILED: {type(exc).__name__}: {exc}")

        # Final deterministic text-overlap resolver.  Handles <text>
        # ANYWHERE in the SVG (including inside <g> groups, which
        # reflow_overlapping_text and plan_layout skip by design).
        # Uses the same bbox estimator the structural critic uses, so
        # any overlap the critic would flag is what this pass tries to
        # resolve.  Iterates up to 8 times; greedy by document order.
        try:
            pre_len = len(result["svg"])
            resolved = resolve_text_overlaps(result["svg"])
            if resolved != result["svg"]:
                _log(
                    f"resolve_text_overlaps: rewrote {pre_len} -> "
                    f"{len(resolved)} chars (deterministic text-overlap fix)"
                )
                result["svg"] = resolved
        except Exception as exc:  # noqa: BLE001
            _log(f"resolve_text_overlaps FAILED: "
                 f"{type(exc).__name__}: {exc}")

        # Cap oversized <marker> arrowheads.
        try:
            shrunk = shrink_arrowheads(result["svg"])
            if shrunk != result["svg"]:
                _log("shrink_arrowheads: capped oversized markers")
                result["svg"] = shrunk
        except Exception as exc:  # noqa: BLE001
            _log(f"shrink_arrowheads FAILED: {type(exc).__name__}: {exc}")

        # Raise label text above the shapes/lines so it is not covered.
        try:
            fronted = raise_text_to_front(result["svg"])
            if fronted != result["svg"]:
                _log("raise_text_to_front: moved labels above shapes")
                result["svg"] = fronted
        except Exception as exc:  # noqa: BLE001
            _log(f"raise_text_to_front FAILED: {type(exc).__name__}: {exc}")

        # Snap floating connector endpoints onto the nodes they reach
        # for, so edges between circles actually touch instead of
        # ending in mid-air with a visible gap.
        try:
            snapped = snap_edges_to_nodes(result["svg"])
            if snapped != result["svg"]:
                _log("snap_edges_to_nodes: snapped connector endpoints")
                result["svg"] = snapped
        except Exception as exc:  # noqa: BLE001
            _log(f"snap_edges_to_nodes FAILED: {type(exc).__name__}: {exc}")

        # Refit the viewBox tightly to the content — removes a big
        # empty band at the top (the model often draws everything low)
        # and includes anything that overflowed the original viewBox.
        try:
            fitted = fit_viewbox_to_content(result["svg"])
            if fitted != result["svg"]:
                _log("fit_viewbox_to_content: refit viewBox to content")
                result["svg"] = fitted
        except Exception as exc:  # noqa: BLE001
            _log(f"fit_viewbox_to_content FAILED: "
                 f"{type(exc).__name__}: {exc}")

        # Bind the narration to the FINAL SVG.  The generator nearly
        # always draws the figure WITHOUT id attributes, so narration
        # highlight ids resolve to nothing and the viewer spotlights
        # nothing while the audio plays.  This injects ids on every
        # <text> and grounds each phrase to the element it describes —
        # the deterministic guarantee that highlighting actually works.
        try:
            _narr = result.get("narration") or []
            _bsvg, _bnarr, _n_ground = bind_narration_to_svg(
                result["svg"], _narr)
            result["svg"] = _bsvg
            result["narration"] = _bnarr
            if _n_ground:
                _log(f"bind_narration_to_svg: grounded {_n_ground} "
                     f"phrase(s) to figure text")
        except Exception as exc:  # noqa: BLE001
            _log(f"bind_narration_to_svg FAILED: "
                 f"{type(exc).__name__}: {exc}")

        # Final XML-safety pass: an earlier post-processor may have
        # re-introduced a stray & or <.  Re-run the escaper so a
        # malformed SVG can never reach the browser — an invalid SVG
        # renders as a fully blank canvas.
        try:
            result["svg"] = escape_bare_xml_in_svg(result["svg"])
        except Exception:  # noqa: BLE001
            pass

        # Inspection on streamed SVG: while the LLM was streaming, the
        # canvas iframe was painting the RAW model output into #stage
        # via svg_chunk events — including any malformed XML, mis-
        # placed cells, or undersized container rects.  Now that the
        # cleaned SVG is ready, emit ONE more svg_chunk with the
        # post-autofit/reflow version so the iframe ends up showing
        # the corrected figure even if the reviewer FAILs the attempt
        # and we then go silent for the retry.  Without this, the
        # user sat looking at the broken raw stream for the entire
        # retry window.
        if on_svg_chunk is not None and stream_this_attempt:
            try:
                await on_svg_chunk(result["svg"])
            except Exception as cb_exc:  # noqa: BLE001
                _log(
                    f"final-clean on_svg_chunk raised "
                    f"{type(cb_exc).__name__}: {cb_exc}"
                )

        # 2a-pre. Math-correctness verifier (Tier 2 — symbolic, exact).
        # The LLM declared verifiable claims alongside the figure; we
        # check every one with SymPy BEFORE anything else.  A wrong
        # derivative / identity / Hessian / value blocks the figure;
        # the verifier's reasons become the retry critique so the
        # LLM knows exactly what to fix.
        math_review_lines: list[str] = []
        try:
            from studio.templates.math_verifier import (
                verify_claims, failures_critique,
            )
            _math_results = verify_claims(result.get("math_claims") or [])
            _math_critique = failures_critique(_math_results)
            n_skipped = sum(1 for r in _math_results
                            if r.get("skipped"))
            if _math_critique:
                math_review_lines.append(_math_critique)
                n_failed = sum(1 for r in _math_results
                               if not r.get("ok", True)
                               and not r.get("skipped"))
                skip_tag = (f" ({n_skipped} skipped as unparseable)"
                            if n_skipped else "")
                _log(f"math-correctness verifier: "
                     f"{n_failed} of {len(_math_results)} "
                     f"claim(s) FAILED{skip_tag}")
            elif _math_results:
                n_z3 = sum(1 for r in _math_results
                           if r.get("engine") == "z3"
                           and not r.get("skipped"))
                n_lean = sum(1 for r in _math_results
                             if r.get("engine") == "lean"
                             and not r.get("skipped"))
                tags: list[str] = []
                if n_z3:
                    tags.append(f"z3={n_z3}")
                if n_lean:
                    tags.append(f"lean={n_lean}")
                if n_skipped:
                    tags.append(f"skipped={n_skipped}")
                engine_tag = (f" ({', '.join(tags)})" if tags else "")
                n_verified = (len(_math_results) - n_skipped)
                _log(f"math-correctness verifier: "
                     f"{n_verified} verified, {n_skipped} skipped "
                     f"(unparseable, no retry) of {len(_math_results)} "
                     f"claim(s){engine_tag}")
        except Exception as exc:  # noqa: BLE001
            _log(f"math-correctness verifier errored "
                 f"(skipping): {type(exc).__name__}: {exc}")

        # 2a. Cheap deterministic structural review BEFORE the vision
        # call.  Catches failures the vision LLM can't reliably detect
        # from a rendered PNG alone — most importantly, narration
        # phrases that reference SVG ids that don't exist (the
        # "highlights don't fire" symptom the learner sees as
        # "the artifact under attention was not highlighted").
        structural_issues = _structural_review(
            result.get("svg", ""), result.get("narration") or [],
            user_prompt=user_prompt,
        )
        # Math-claim failures are stop-the-line: prepend them to the
        # structural-issues list so the existing retry path picks them
        # up as critique input.
        if math_review_lines:
            structural_issues = math_review_lines + list(structural_issues)
        if structural_issues:
            # Log the actual issues (truncated) so a slow-retry session
            # can be diagnosed from CloudWatch without code changes.
            issue_snip = " | ".join(
                (i[:80] + "…") if len(i) > 80 else i
                for i in structural_issues[:5]
            )
            _log(
                f"structural review: {len(structural_issues)} issue(s) — "
                f"{issue_snip}"
            )

        # 2b. Figure review.  _vision_review consults _review_config()
        # internally for mode/model/url/key, so we always pass the
        # same three positional args for backwards-compat; they're
        # ignored on the routing front.  By default the reviewer is
        # gpt-4o-mini in SVG-as-text mode (cheap, works for any
        # generator backend).  Flip to PNG-vision review by setting
        # SEVIM_REVIEW_MODE=vision and SEVIM_REVIEW_MODEL=gpt-4o.
        verdict = await _vision_review(
            user_prompt=user_prompt,
            svg=result["svg"],
            base_url=base_url,  # unused, kept for signature stability
            model=model,        # unused, kept for signature stability
            api_key=api_key,    # unused, kept for signature stability
            narration=result.get("narration") or [],
            solution=result.get("solution"),
            math_claims=result.get("math_claims"),
            figure_ground_truth=figure_ground_truth,
        )

        # Snapshot pre-merge state so the best-attempt accumulator
        # below sees the raw vision verdict (None=PASS) and the raw
        # structural issue list — the merge below mutates `verdict`
        # by appending structural complaints to it for the retry
        # critique, which would otherwise corrupt the score.
        raw_vision_pass = (verdict is None)

        # Merge structural issues into the verdict so a single retry
        # covers both classes.  If vision passed but structural failed,
        # we still need to retry; if vision failed too, the critic
        # checklist gets both kinds of fixes.
        #
        # Exception: when the vision reviewer (which represents user
        # perception of the rendered PNG) says PASS and every
        # remaining structural issue is in the visual-only set
        # (text_text_overlap, oversized_element, caption_overlaps_…,
        # …), accept the figure and stop retrying.  Retries on those
        # often regress the figure — the 3-SAT case had attempt 0
        # with 1 overlap pair, attempt 1 with 5.  Functional issues
        # (missing_required_primitive, narration_highlight_id_missing,
        # math-claim failures) still gate retries.
        visual_only_after_pass = (
            raw_vision_pass
            and structural_issues
            and _is_visual_only_issues(structural_issues)
        )
        if structural_issues and not visual_only_after_pass:
            structural_block = (
                "Structural review: FAIL.\n\n"
                "Apply these specific fixes, in order:\n"
                + "\n".join(f"  {i}. {s}" for i, s in enumerate(structural_issues, 1))
            )
            verdict = (
                structural_block
                if verdict is None
                else verdict + "\n\n" + structural_block
            )
        elif visual_only_after_pass:
            _log(
                f"structural review: {len(structural_issues)} visual-only "
                "issue(s) and vision PASSED — accepting figure, skipping "
                "further retries (retries on these regress quality)"
            )

        # Record this attempt for best-of selection on retry-exhaust.
        attempts.append({
            "svg": result["svg"],
            "narration": result.get("narration") or [],
            "title": result.get("title") or "",
            "structural_issues": list(structural_issues),
            "raw_vision_pass": raw_vision_pass,
            "score": _attempt_score(structural_issues,
                                    None if raw_vision_pass else "FAIL"),
        })
        if verdict is None:  # PASS or unable to review
            # If a previous attempt failed and this one passed, the pair
            # is a repair triple worth keeping for distillation.
            if prev_fail is not None:
                bad_svg, bad_narration, bad_critique = prev_fail
                repairs.append({
                    "attempt_index": attempt,
                    "bad_svg": bad_svg,
                    "bad_narration": bad_narration,
                    "critique": bad_critique,
                    "good_svg": result["svg"],
                    "good_narration": result.get("narration") or [],
                })
            return {
                "svg": result["svg"],
                "narration": result.get("narration") or [],
                "title": result.get("title") or "",
                "review_history": review_history,
                "retries_used": attempt,
                "repairs": repairs,
            }
        review_history.append(verdict)
        # Stash this failed attempt; if the next attempt PASSes it pairs
        # with this one to form a repair triple.  Overwrites any earlier
        # failure: only the LAST fail-then-pass pair is captured per turn.
        prev_fail = (
            result["svg"],
            result.get("narration") or [],
            verdict,
        )

        # 3. Inject critique + image + prior SVG, ask for a PATCH.
        if attempt >= max_retries:
            break
        # Patch-retry framing: hand the LLM the prior SVG verbatim
        # and ask for a MODIFIED copy that fixes only the listed
        # issues, preserving every working element (same ids,
        # positions, narration unless an issue names it).  The old
        # "re-emit corrected svg + narration" framing made the model
        # regenerate from scratch — in the 3-SAT case attempt 1's
        # fresh emission had 5 overlap pairs vs attempt 0's 1.
        prior_svg = result.get("svg", "")
        # Cap inline SVG at 4k chars.  Larger prior-SVG inputs
        # measurably slow the retry call (attempt 1 was ~2x slower
        # than attempt 0 in production when prior was 1.7k chars).
        # 4k covers >95 % of LLM-emitted SVGs without truncation
        # while keeping the retry prompt lean.
        if len(prior_svg) > 4000:
            prior_svg = prior_svg[:4000] + "\n<!-- ...truncated... -->"
        retry_text = (
            "Your previous figure failed review.  Below is the "
            "rendered PNG, a structured list of specific fixes, and "
            "your previous SVG verbatim.\n\n"
            "Return a MODIFIED VERSION of the previous SVG that "
            "fixes ONLY the listed issues.  Preserve every element "
            "that isn't called out — same ids, same positions, same "
            "attributes, same narration unless an issue names it.  "
            "Edit surgically; do NOT regenerate the figure from "
            "scratch (fresh regenerations have regressed quality in "
            "past runs).\n\n"
            "Each fix names a concrete action, the element it "
            "applies to, where it goes, and the exact content/values "
            "to use.\n\n"
            + verdict +
            "\n\nPrevious SVG (apply patches to this):\n"
            "```svg\n" + prior_svg + "\n```\n\n"
            "Now return the patched svg + narration in the same "
            "JSON schema."
        )
        messages.append({"role": "assistant", "content": content})
        # Text-only backends can't see the PNG — the patch framing
        # above already includes the prior SVG inline, so they have
        # everything they need.
        if model in text_only_models:
            messages.append({
                "role": "user",
                "content": retry_text,
            })
        else:
            # Rasterising can fail if the model emitted malformed XML
            # (unclosed tag, unescaped &, etc.).  Without this guard,
            # the cairosvg ParseError bubbles all the way up and the
            # whole turn crashes — the user sees a tool error instead
            # of a corrected figure.  When PNG render fails, the text
            # critique still includes the prior SVG inline above, so
            # the model can still see what it produced and fix it.
            try:
                png = _svg_to_png(result["svg"])
                b64 = base64.b64encode(png).decode("ascii")
                messages.append({"role": "user", "content": [
                    {"type": "text", "text": retry_text},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ]})
            except Exception as render_exc:  # noqa: BLE001
                _log(
                    f"retry PNG render FAILED ({type(render_exc).__name__}: "
                    f"{render_exc}); sending text-only critique with "
                    f"the raw SVG"
                )
                messages.append({
                    "role": "user",
                    "content": (
                        retry_text
                        + "\n\nNote: your previous SVG could not be "
                        "rasterised, likely because the XML is malformed "
                        "(unclosed tag, unescaped & or <, mismatched "
                        "quotes).  Patch the SVG above to be CLEAN and "
                        "well-formed."
                    ),
                })

    # Loop exited with every attempt failing review.  Ship the BEST
    # of the attempts (lowest _attempt_score) — not the last one.
    # CloudWatch shows attempts can regress: the 3-SAT proof case had
    # attempt 0 with 1 overlap pair, attempt 1 with 5; the old code
    # shipped attempt 2 (worst).  This picks the cleanest figure the
    # model produced across the retry budget.
    if attempts:
        best = min(attempts, key=lambda a: a["score"])
        best_idx = attempts.index(best)
        if best_idx != len(attempts) - 1:
            _log(
                f"best-attempt: shipping attempt {best_idx} "
                f"(score={best['score']}, "
                f"{len(best['structural_issues'])} structural issue(s), "
                f"vision_pass={best['raw_vision_pass']}) instead of last "
                f"attempt {len(attempts) - 1} (score="
                f"{attempts[-1]['score']})"
            )
        else:
            _log(
                f"best-attempt: last attempt was already the best "
                f"(score={best['score']})"
            )
        final_svg = best["svg"]
        last_narration = best["narration"]
        last_title = best["title"]
    else:
        # No attempt succeeded.  Reachable when the upstream LLM
        # (OpenAI) returned 5xx on every retry (rare but happens
        # during OpenAI outages).  Build a small "we couldn't render
        # this prompt" SVG so the canvas iframe shows something
        # readable instead of going blank, and ship a one-phrase
        # narration explaining the failure.
        _log("no attempt succeeded — upstream LLM error on every retry; "
             "shipping fallback error figure")
        final_svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 620">'
            '<rect x="50" y="200" width="800" height="220" rx="12" '
            'fill="#fff7ed" stroke="#f59e0b" stroke-width="2"/>'
            '<text x="450" y="270" text-anchor="middle" '
            'font-size="22" fill="#7c2d12" font-weight="bold">'
            "Couldn't render this figure</text>"
            '<text x="450" y="320" text-anchor="middle" '
            'font-size="15" fill="#7c2d12">'
            'The upstream AI service returned an error '
            '(probably temporary).</text>'
            '<text x="450" y="350" text-anchor="middle" '
            'font-size="15" fill="#7c2d12">'
            'Please try again in a few seconds — your prompt was fine.'
            "</text>"
            '</svg>'
        )
        last_narration = [{
            "speak": ("The upstream AI service returned an error and we "
                      "couldn't render this figure. The problem is on "
                      "their end, not your prompt. Please try again in "
                      "a few seconds."),
            "highlight": [],
        }]
        last_title = "Generation failed"

    # Salvage step: if the chosen best happens to have a very short
    # narration (LLM over-collapsed when responding to a critique),
    # prefer an earlier attempt's narration so the spoken explanation
    # stays substantial.  Looks across ALL recorded attempts, not just
    # the last fail.  Re-binds highlight ids to the chosen SVG.
    salvaged = False
    if len(last_narration) < 3 and len(attempts) > 1:
        candidates = [a["narration"] for a in attempts
                      if len(a["narration"]) >= 3]
        if candidates:
            # Pick the longest available narration as the salvage source.
            best_narr = max(candidates, key=len)
            if len(best_narr) > len(last_narration):
                _log(
                    f"narration salvage: chosen attempt had "
                    f"{len(last_narration)} phrase(s); using "
                    f"{len(best_narr)}-phrase narration from another "
                    "attempt"
                )
                last_narration = best_narr
                salvaged = True
    if salvaged:
        try:
            final_svg, last_narration, _ = bind_narration_to_svg(
                final_svg, last_narration)
        except Exception:  # noqa: BLE001
            pass
    return {
        "svg": final_svg,
        "narration": last_narration,
        "title": last_title,
        "review_history": review_history,
        "retries_used": max_retries,
        "repairs": repairs,
    }


# ---------------------------------------------------------------------
# Figure review.  Default path: send SVG-as-text + narration JSON to a
# cheap text-capable reviewer (gpt-4o-mini).  This works for any
# generator backend (Qwen, GPT, anything that emits structured JSON)
# at a fraction of the per-turn cost of a vision-PNG review, and gives
# the reviewer literal access to ids and attributes that a rasterised
# PNG flattens away.
#
# Override knobs (env vars, all optional):
#
#   SEVIM_REVIEW_MODE   text  (default)  -- send SVG + narration as text
#                       vision           -- rasterise to PNG, send image_url
#                       off              -- skip the review entirely
#
#   SEVIM_REVIEW_MODEL  reviewer model name (default: gpt-4o-mini)
#
#   SEVIM_REVIEW_URL    base_url for the reviewer (default: OpenAI)
#
#   SEVIM_REVIEW_KEY_ENV
#                       env var name to pull the reviewer API key from
#                       (default: OPENAI_API_KEY)
#
# Future operators can flip to vision review on a heavier model by
# setting SEVIM_REVIEW_MODE=vision and SEVIM_REVIEW_MODEL=gpt-4o
# without any code changes.
# ---------------------------------------------------------------------

def _review_config() -> tuple[str, str, str, str | None]:
    """Return (mode, model, base_url, api_key) for the reviewer.

    Mode is one of 'text', 'vision', 'off'.  Default is now vision-on-
    gpt-4o: the reviewer rasterises the SVG to PNG and inspects the
    pixels, which is the only way to catch real text-overlap.  Text
    mode (cheaper, no vision needed) remains available via env.
    """
    mode = (os.environ.get("SEVIM_REVIEW_MODE") or "vision").lower().strip()
    if mode not in ("text", "vision", "off"):
        mode = "vision"
    model = os.environ.get("SEVIM_REVIEW_MODEL") or "gpt-4o"
    base_url = (os.environ.get("SEVIM_REVIEW_URL")
                or os.environ.get("SEVIM_VLLM_URL")
                or "https://api.openai.com/v1").rstrip("/")
    key_env = os.environ.get("SEVIM_REVIEW_KEY_ENV") or "OPENAI_API_KEY"
    api_key = os.environ.get(key_env)
    return mode, model, base_url, api_key


async def _vision_review(
    user_prompt: str,
    svg: str,
    base_url: str,
    model: str,
    api_key: str | None,
    narration: list[dict[str, Any]] | None = None,
    solution: str | None = None,
    math_claims: list | None = None,
    figure_ground_truth: Any = None,
) -> str | None:
    """Ask the reviewer LLM to audit the (svg, narration) pair via the
    structured REVIEW_SCHEMA.  Returns ``None`` on PASS (or if the
    review call itself fails); returns a formatted critique string on
    FAIL that lists each fix as 'ACTION: what -- where -- details'.

    The reviewer's *mode*, *model*, *url* and *key* are pulled from
    ``_review_config()`` and ignore the generator's own (base_url,
    model, api_key) -- those parameters are kept for backwards-
    compatibility with older callers but are no longer used to route
    the review.  This way we can have Qwen generate while GPT reviews,
    or flip back to PNG-vision review on gpt-4o, without touching the
    express call site.
    """
    import sys as _sys
    def _log(msg: str) -> None:
        print(f"[express:review] {msg}", flush=True, file=_sys.stderr)

    review_mode, review_model, review_url, review_key = _review_config()
    if review_mode == "off":
        _log("mode=off -- skipping review")
        return None

    _log(f"mode={review_mode}  model={review_model}  url={review_url[:40]}...")

    headers = {"content-type": "application/json"}
    if review_key:
        headers["Authorization"] = f"Bearer {review_key}"

    if review_mode == "text":
        # Send SVG-as-text + narration.  No rasterisation, no
        # multi-modal block, works on any text LLM.  The reviewer has
        # literal access to ids/attrs/structure that a PNG would hide.
        user_msg = _review_user_prompt(user_prompt, narration, svg_text=svg,
                                        solution=solution,
                                        math_claims=math_claims,
                                        figure_ground_truth=figure_ground_truth)
        messages = [
            {"role": "system", "content": _REVIEW_SYSTEM},
            {"role": "user", "content": user_msg},
        ]
    else:
        # Vision mode: rasterise to PNG and ship as image_url.  Use
        # only when the reviewer model supports image input (gpt-4o,
        # gpt-4o-mini, gpt-4-vision, etc.).
        try:
            png = await asyncio.to_thread(_svg_to_png, svg)
            _log(f"rendered SVG ({len(svg)} chars) -> PNG ({len(png)} bytes)")
        except Exception as exc:  # noqa: BLE001
            _log(f"PNG render FAILED: {type(exc).__name__}: {exc} -- skipping review")
            return None
        b64 = base64.b64encode(png).decode("ascii")
        messages = [
            {"role": "system", "content": _REVIEW_SYSTEM},
            {"role": "user", "content": [
                {"type": "text",
                 "text": _review_user_prompt(
                     user_prompt, narration,
                     solution=solution, math_claims=math_claims,
                     figure_ground_truth=figure_ground_truth)},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]},
        ]

    payload = {
        "model": review_model,
        "max_tokens": 1200,
        "temperature": 0.0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "figure_review",
                "schema": REVIEW_SCHEMA,
                "strict": True,
            },
        },
        "messages": messages,
    }
    # The caller used to supply base_url, but the reviewer now takes
    # its config from _review_config() exclusively.  Rebind for the
    # request below.
    base_url = review_url
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers=headers, json=payload,
            )
            if r.status_code != 200:
                body = (await r.aread()).decode(errors="replace")
                _log(f"review HTTP {r.status_code}: {body[:200]}")
                return None
            content = r.json()["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        _log(f"review request FAILED: {type(exc).__name__}: {exc}")
        return None
    try:
        review = json.loads(content)
    except json.JSONDecodeError:
        _log(f"review JSON parse failed: {content[:200]!r}")
        return None
    verdict = (review.get("verdict") or "").upper()
    summary = review.get("summary") or ""
    fixes = review.get("fixes") or []
    _log(f"verdict={verdict!r}  fixes={len(fixes)}  summary={summary[:120]!r}")
    if verdict == "PASS":
        return None
    if not fixes:
        # Reviewer marked FAIL but didn't provide fixes — accept rather
        # than loop pointlessly.
        _log("FAIL with no fixes — accepting figure to break loop")
        return None
    # Format as an actionable checklist for the generator.
    lines = [f"Vision review: FAIL.  {summary}", "",
             "Apply these specific fixes, in order:"]
    for i, f in enumerate(fixes, start=1):
        action = f.get("action", "?")
        what = f.get("what", "")
        where = f.get("where", "")
        details = f.get("details", "")
        lines.append(f"  {i}. [{action}] {what} — at: {where} — {details}")
    return "\n".join(lines)


def _chrome_binary() -> str | None:
    """Locate a headless-Chrome-capable binary, or None."""
    import shutil
    for name in ("google-chrome-stable", "google-chrome",
                 "chromium", "chromium-browser"):
        p = shutil.which(name)
        if p:
            return p
    for p in ("/usr/bin/chromium", "/usr/bin/chromium-browser",
              "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"):
        if os.path.exists(p):
            return p
    return None


def _normalise_svg_root(svg: str) -> str:
    """Rewrite the root <svg> tag so it fills its container exactly.

    Drops any intrinsic width/height/style (template SVGs hard-code
    900x650; graphviz SVGs use width:100%) and keeps the viewBox so the
    figure scales cleanly into the screenshot window.
    """
    import re
    m = re.search(r"<svg\b[^>]*>", svg)
    if not m:
        return svg
    tag = m.group(0)
    vb = re.search(r'viewBox="[^"]*"', tag)
    xlink = ('xmlns:xlink="http://www.w3.org/1999/xlink" '
             if "xmlns:xlink" in tag else "")
    new = (f'<svg xmlns="http://www.w3.org/2000/svg" {xlink}'
           + (vb.group(0) + " " if vb else "")
           + 'width="100%" height="100%" '
           'preserveAspectRatio="xMidYMid meet">')
    return svg[:m.start()] + new + svg[m.end():]


def _svg_to_png_chrome(svg: str, width: int) -> bytes | None:
    """Rasterise via headless Chrome — the SAME engine as the canvas
    viewer — so the reviewer sees pixel-for-pixel what the learner
    sees. Returns None on any failure so the caller can fall back."""
    binary = _chrome_binary()
    if not binary:
        return None
    import re
    import subprocess
    import tempfile
    height = width
    m = re.search(r'viewBox="([-\d.eE\s]+)"', svg)
    if m:
        parts = m.group(1).split()
        if len(parts) == 4:
            try:
                vw, vh = float(parts[2]), float(parts[3])
                if vw > 0 and vh > 0:
                    height = max(1, round(width * vh / vw))
            except ValueError:
                pass
    html = (
        '<!doctype html><html><head><meta charset="utf-8"><style>'
        'html,body{margin:0;padding:0;background:#fff}'
        f'#wrap{{width:{width}px;height:{height}px}}'
        '#wrap>svg{display:block}</style></head><body>'
        f'<div id="wrap">{_normalise_svg_root(svg)}</div></body></html>'
    )
    with tempfile.TemporaryDirectory() as td:
        page = os.path.join(td, "page.html")
        out = os.path.join(td, "shot.png")
        with open(page, "w", encoding="utf-8") as fh:
            fh.write(html)
        cmd = [
            binary, "--headless", "--disable-gpu", "--no-sandbox",
            "--disable-dev-shm-usage", "--hide-scrollbars",
            "--force-device-scale-factor=1",
            "--default-background-color=FFFFFFFF",
            f"--window-size={width},{height}",
            f"--screenshot={out}", f"file://{page}",
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=25,
                           check=False)
        except (subprocess.TimeoutExpired, OSError):
            return None
        if not os.path.exists(out):
            return None
        with open(out, "rb") as fh:
            data = fh.read()
        return data or None


def _svg_to_png(svg: str, width: int = 1200) -> bytes:
    """Rasterise an SVG to PNG for the vision reviewer.

    Prefers headless Chrome (browser-grade, matches the canvas viewer
    exactly — correct math glyphs, correct percentage tspan sizing).
    Falls back to cairosvg only when no Chrome binary is available;
    cairosvg mis-sizes percentage tspan font-sizes and lacks math
    glyphs, so the reviewer would otherwise audit a garbled image.
    """
    png = _svg_to_png_chrome(svg, width)
    if png:
        return png
    import cairosvg
    return cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=width)


# ---------------------------------------------------------------------------
# Deterministic structural review of the (svg, narration) pair.  Runs on
# the raw SVG text and the parsed narration array — it can therefore
# catch failures that the vision reviewer cannot reliably detect from
# the rendered PNG alone:
#
#   * narration_highlight_id_missing — a phrase's highlight array
#     points at an SVG id that doesn't exist.  The viewer's
#     getElementById(highlight) returns null, the .sevim-highlight
#     class never gets attached, the learner sees no flashing
#     element while the phrase is spoken.  This is the
#     "the artifact under attention was not highlighted" failure.
#
#   * vertex_labels_missing — for graph-style figures the model
#     sometimes emits the <circle> vertices but forgets to label
#     them.  The text count being much lower than the circle count
#     is a strong signal.  This is the
#     "not all vertices were indicated on the graph" failure.
# ---------------------------------------------------------------------------

# --------------------------------------------------------------------
# Defensive LaTeX scrubber for SVG <text> content.
#
# SVG <text> elements are NOT MathJax. When the LLM emits LaTeX
# directly into a <text> (a common failure mode for area / formula
# captions), it renders as literal `\( \frac{1}{2} \times ... \)`
# garbage on the canvas. The chat panel renders LaTeX correctly
# because the chat HTML is post-processed by MathJax; the canvas
# is plain SVG.
#
# We scan every <text> / <tspan> body, detect LaTeX commands, and
# rewrite them to Unicode / ASCII equivalents. Aggressive but safe:
# any backslash-command inside a text body is either a typo or a
# LaTeX command, and either way we want it gone.
# --------------------------------------------------------------------

# Order matters: longest patterns first so `\leq` doesn't match
# `\l` then leave `eq` behind.
_LATEX_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    # Delimiters
    (r"\(", ""), (r"\)", ""),
    (r"\[", ""), (r"\]", ""),
    # Greek letters
    (r"\alpha", "α"), (r"\beta", "β"), (r"\gamma", "γ"),
    (r"\delta", "δ"), (r"\epsilon", "ε"), (r"\varepsilon", "ε"),
    (r"\zeta", "ζ"), (r"\eta", "η"), (r"\theta", "θ"),
    (r"\vartheta", "ϑ"), (r"\iota", "ι"), (r"\kappa", "κ"),
    (r"\lambda", "λ"), (r"\mu", "μ"), (r"\nu", "ν"),
    (r"\xi", "ξ"), (r"\pi", "π"), (r"\varpi", "ϖ"),
    (r"\rho", "ρ"), (r"\sigma", "σ"), (r"\varsigma", "ς"),
    (r"\tau", "τ"), (r"\upsilon", "υ"), (r"\phi", "φ"),
    (r"\varphi", "φ"), (r"\chi", "χ"), (r"\psi", "ψ"),
    (r"\omega", "ω"),
    (r"\Gamma", "Γ"), (r"\Delta", "Δ"), (r"\Theta", "Θ"),
    (r"\Lambda", "Λ"), (r"\Xi", "Ξ"), (r"\Pi", "Π"),
    (r"\Sigma", "Σ"), (r"\Phi", "Φ"), (r"\Psi", "Ψ"),
    (r"\Omega", "Ω"),
    # Operators
    (r"\times", "×"), (r"\cdot", "·"), (r"\div", "÷"),
    (r"\pm", "±"), (r"\mp", "∓"),
    (r"\le", "≤"), (r"\leq", "≤"),
    (r"\ge", "≥"), (r"\geq", "≥"),
    (r"\ne", "≠"), (r"\neq", "≠"),
    (r"\approx", "≈"), (r"\equiv", "≡"),
    (r"\propto", "∝"), (r"\sim", "∼"),
    (r"\sum", "Σ"), (r"\prod", "Π"), (r"\int", "∫"),
    (r"\partial", "∂"), (r"\nabla", "∇"),
    (r"\infty", "∞"),
    (r"\in", "∈"), (r"\notin", "∉"),
    (r"\subset", "⊂"), (r"\supset", "⊃"),
    (r"\subseteq", "⊆"), (r"\supseteq", "⊇"),
    (r"\cup", "∪"), (r"\cap", "∩"),
    (r"\emptyset", "∅"), (r"\varnothing", "∅"),
    (r"\forall", "∀"), (r"\exists", "∃"),
    (r"\rightarrow", "→"), (r"\leftarrow", "←"),
    (r"\Rightarrow", "⇒"), (r"\Leftarrow", "⇐"),
    (r"\leftrightarrow", "↔"), (r"\Leftrightarrow", "⇔"),
    (r"\to", "→"), (r"\implies", "⇒"), (r"\iff", "⇔"),
    (r"\mapsto", "↦"),
    (r"\wedge", "∧"), (r"\vee", "∨"), (r"\neg", "¬"), (r"\lnot", "¬"),
    (r"\angle", "∠"),
    (r"\perp", "⊥"), (r"\parallel", "∥"),
    # Spacing / formatting (just drop)
    (r"\quad", " "), (r"\qquad", "  "),
    (r"\,", " "), (r"\;", " "), (r"\:", " "),
    (r"\!", ""),
    (r"\\", " "),  # forced line break inside text — degrade to space
    (r"\left", ""), (r"\right", ""),
    (r"\big", ""), (r"\Big", ""),
    (r"\bigg", ""), (r"\Bigg", ""),
    (r"\text{", ""), (r"\mathrm{", ""), (r"\mathbf{", ""),
    (r"\mathit{", ""), (r"\mathcal{", ""), (r"\mathbb{", ""),
)

_LATEX_FRAC_RE = re.compile(
    r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}"
)
_LATEX_SQRT_RE = re.compile(
    r"\\sqrt\s*\{([^{}]*)\}"
)
_LATEX_SUPSUB_BRACE_RE = re.compile(
    r"([_^])\{([^{}]+)\}"
)
_LATEX_BACKSLASH_LEFTOVER_RE = re.compile(
    r"\\[a-zA-Z]+"
)
# Find SVG <text> ... </text> AND <tspan> ... </tspan> bodies so we
# only scrub user-facing text — not attribute values, comments,
# or `<title>` metadata.
_TEXT_BODY_RE = re.compile(
    r"(<(?:text|tspan)\b[^>]*>)([^<]+)(</(?:text|tspan)>)",
    flags=re.DOTALL,
)


def _scrub_latex(s: str) -> str:
    """Convert LaTeX commands in `s` to Unicode equivalents."""
    out = s
    # Pairs first (frac, sqrt) — they consume `{...}` braces.
    out = _LATEX_FRAC_RE.sub(r"(\1)/(\2)", out)
    out = _LATEX_SQRT_RE.sub(r"√(\1)", out)
    # Simple keyword replacements.
    for k, v in _LATEX_REPLACEMENTS:
        out = out.replace(k, v)
    # Subscripts / superscripts: `x_{ij}` → `x_ij`, `x^{n+1}` → `x^(n+1)`.
    # We can't always do real Unicode super/sub (only some chars exist),
    # so degrade to underscore / caret form rather than leave braces.
    out = _LATEX_SUPSUB_BRACE_RE.sub(
        lambda m: (
            m.group(1) + m.group(2) if len(m.group(2)) == 1
            else f"{m.group(1)}({m.group(2)})"
        ),
        out,
    )
    # Anything still backslash-something at the end is unrecognised:
    # strip the backslash so the literal name survives but doesn't
    # look like a typo.
    out = _LATEX_BACKSLASH_LEFTOVER_RE.sub(
        lambda m: m.group(0)[1:], out,
    )
    # `$ ... $` MathJax inline delimiters — drop them.
    out = out.replace("$", "")
    return out


def escape_bare_xml_in_svg(svg: str) -> str:
    """Entity-escape stray ``&`` and ``<`` the model left raw inside
    element content, which would otherwise make the SVG invalid XML.

    The model frequently writes math straight into ``<text>`` — matrix
    LaTeX with ``&`` column separators, inequalities like ``|x| < d`` —
    emitting characters XML reserves.  We scan both element content AND
    quoted attribute values and:

      * ``&`` not already starting a valid entity  -> ``&amp;``
      * ``<`` not starting a real tag (tag names begin with a letter,
        ``/``, ``!`` or ``?``), or any ``<`` inside an attribute value
                                                    -> ``&lt;``

    ``>`` in element content is legal XML, so it is left alone.
    Idempotent: already-escaped entities are recognised and passed
    through.  Fails open — any error returns the input unchanged.
    """
    try:
        entity = re.compile(
            r"&(?:#\d+|#x[0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]*);")
        out: list[str] = []
        i, n = 0, len(svg)
        in_tag = False
        quote = ""
        while i < n:
            c = svg[i]
            if in_tag:
                if quote:
                    # Inside an attribute value — & and < are illegal.
                    if c == quote:
                        quote = ""
                        out.append(c)
                    elif c == "&":
                        out.append(c if entity.match(svg, i)
                                   else "&amp;")
                    elif c == "<":
                        out.append("&lt;")
                    else:
                        out.append(c)
                else:
                    out.append(c)
                    if c in "\"'":
                        quote = c
                    elif c == ">":
                        in_tag = False
                i += 1
                continue
            if c == "<":
                nxt = svg[i + 1] if i + 1 < n else ""
                if nxt.isalpha() or nxt in "/!?":
                    in_tag = True
                    out.append(c)
                else:
                    out.append("&lt;")
                i += 1
                continue
            if c == "&":
                out.append(c if entity.match(svg, i) else "&amp;")
                i += 1
                continue
            out.append(c)
            i += 1
        return "".join(out)
    except Exception:  # noqa: BLE001
        return svg


_BIND_STOPWORDS = frozenset("""
a an the this that these those it its is are was were be been being
of to in on at for and or but with as by we us our you your they them
their he she his her i me my here now then so if when which what how
why where who whom each both also not no all any some one two three
into from up down out over under again more most very can could will
would shall should may might must do does did done has have had see
look note show shows shown let lets gives give given get gets take
takes use used using step phrase first second third next finally
""".split())


def _word_tokens(s: str) -> set[str]:
    """Lowercased content-word tokens, stopwords removed."""
    s = re.sub(r"[^\w ]+", " ", (s or "").lower(), flags=re.UNICODE)
    return {w for w in s.split()
            if w and w not in _BIND_STOPWORDS}


def _assign_text_ids(svg: str) -> tuple[str, list[tuple[str, str]]]:
    """Give every id-less <text> a stable ``auto_t{N}`` id.

    Returns the rewritten SVG and a list of ``(id, plain_text)`` for
    every <text> element (existing-id ones included).  The injected ids
    are purely additive — no visual change — and give the narration a
    real DOM target to highlight even though the model drew the figure
    without ids.
    """
    import html
    entries: list[tuple[str, str]] = []
    counter = [0]

    def repl(m: "re.Match[str]") -> str:
        attrs, inner = m.group(1), m.group(2)
        # Match id="..." OR id='...' — the model mixes quote styles,
        # and missing a single-quoted id injects a DUPLICATE id
        # attribute, which is invalid XML.
        idm = re.search(r"""\bid\s*=\s*(['"])(.*?)\1""", attrs)
        if idm:
            tid = idm.group(2)
            new = m.group(0)
        else:
            tid = f"auto_t{counter[0]}"
            new = f'<text id="{tid}"{attrs}>{inner}</text>'
        counter[0] += 1
        plain = html.unescape(re.sub(r"<[^>]+>", "", inner)).strip()
        entries.append((tid, plain))
        return new

    svg2 = re.sub(r"<text\b([^>]*)>(.*?)</text>", repl, svg, flags=re.S)
    return svg2, entries


def bind_narration_to_svg(
    svg: str, narration: list,
) -> tuple[str, list, int]:
    """Guarantee every narration phrase highlights a real element.

    The generator nearly always emits the SVG without ``id`` attributes
    while the narration references logical ids — so the viewer
    spotlights nothing.  This deterministic pass:

      1. Injects a stable id on every id-less ``<text>`` element.
      2. For each phrase, keeps any highlight id that already resolves
         (exact / case-insensitive).
      3. For phrases whose highlight resolves to nothing, GROUNDS the
         phrase to the figure: it picks the ``<text>`` element whose
         content best overlaps the spoken sentence and highlights that.

    Returns ``(svg, narration, n_grounded)``.  Fails open.
    """
    try:
        if not narration:
            return svg, narration, 0
        svg2, text_entries = _assign_text_ids(svg)
        all_ids = set(re.findall(r"""\bid\s*=\s*['"]([^'"]+)['"]""", svg2))
        all_ids |= set(re.findall(
            r"""\bdata-nid\s*=\s*['"]([^'"]+)['"]""", svg2))
        all_ids |= set(re.findall(
            r"""\bdata-eid\s*=\s*['"]([^'"]+)['"]""", svg2))
        lower_map: dict[str, str] = {}
        for sid in all_ids:
            lower_map.setdefault(sid.lower(), sid)
        # Pre-tokenise each text element once.
        scored = [(tid, plain, _word_tokens(plain))
                  for tid, plain in text_entries if plain]
        n_grounded = 0
        out: list = []
        for ph in narration:
            if not isinstance(ph, dict):
                out.append(ph)
                continue
            h = ph.get("highlight") or []
            if isinstance(h, str):
                h = [h]
            keep: list[str] = []
            for x in h:
                if not isinstance(x, str) or not x.strip():
                    continue
                if x in all_ids:
                    keep.append(x)
                elif x.lower() in lower_map:
                    keep.append(lower_map[x.lower()])
            if not keep:
                speak = ph.get("speak") or ""
                sp = _word_tokens(speak)
                speak_lc = speak.lower()
                best_id = None
                best_score = 0.0
                for tid, plain, tt in scored:
                    score = float(len(sp & tt))
                    # Strong boost when the element's own text appears
                    # verbatim in the spoken sentence.
                    if len(plain) >= 3 and plain.lower() in speak_lc:
                        score += 3.0
                    if score > best_score:
                        best_score = score
                        best_id = tid
                if best_id is not None and best_score >= 1.0:
                    keep = [best_id]
                    n_grounded += 1
            seen: set[str] = set()
            deduped = [x for x in keep
                       if not (x in seen or seen.add(x))]
            new_ph = dict(ph)
            new_ph["highlight"] = deduped
            out.append(new_ph)
        return svg2, out, n_grounded
    except Exception:  # noqa: BLE001
        return svg, narration, 0


def shrink_arrowheads(svg: str) -> str:
    """Cap oversized ``<marker>`` arrowheads.

    The model sometimes defines an arrowhead marker with
    ``markerWidth`` / ``markerHeight`` large enough that the rendered
    arrowhead rivals the node circles and buries them.  We cap the
    larger dimension at ``MAX``, scale both to preserve the aspect
    ratio, and --- when the marker carries no ``viewBox`` --- add one
    equal to the original dimensions so the content scales into the
    smaller viewport instead of being clipped.  Only ``<marker>``
    elements are touched, so genuine figure geometry (a clause-gadget
    triangle, an arrowhead drawn as a deliberate polygon) is never
    affected.  Idempotent; fails open.
    """
    max_dim = 10.0
    try:
        def repl(m: "re.Match[str]") -> str:
            tag = m.group(0)
            wm = re.search(
                r'\bmarkerWidth\s*=\s*["\']([-\d.eE]+)', tag)
            hm = re.search(
                r'\bmarkerHeight\s*=\s*["\']([-\d.eE]+)', tag)
            if not wm or not hm:
                return tag
            try:
                mw, mh = float(wm.group(1)), float(hm.group(1))
            except ValueError:
                return tag
            big = max(mw, mh)
            if big <= max_dim or big <= 0:
                return tag
            f = max_dim / big
            out = tag
            if "viewBox" not in tag:
                out = out.replace(
                    "<marker",
                    f'<marker viewBox="0 0 {mw:.2f} {mh:.2f}"', 1)
            out = re.sub(
                r'(\bmarkerWidth\s*=\s*["\'])[-\d.eE]+',
                lambda x: f"{x.group(1)}{mw * f:.2f}", out, count=1)
            out = re.sub(
                r'(\bmarkerHeight\s*=\s*["\'])[-\d.eE]+',
                lambda x: f"{x.group(1)}{mh * f:.2f}", out, count=1)
            return out
        return re.sub(r"<marker\b[^>]*>", repl, svg)
    except Exception:  # noqa: BLE001
        return svg


def raise_text_to_front(svg: str) -> str:
    """Move top-level <text> elements to the end of the SVG so labels
    render ON TOP of shapes and connector lines instead of behind them.

    SVG z-order is document order; the LLM frequently emits label text
    BEFORE the circles and lines, so the labels end up covered and
    unreadable.  Only ``<text>`` elements that are direct children of
    the root <svg> are moved — text nested inside a <g> (which may
    carry a transform) or inside <defs> is left in place so its
    coordinates and visibility are not disturbed.  Idempotent.
    """
    import bisect
    try:
        g_open = [m.start() for m in re.finditer(r"<g[\s>]", svg)]
        g_close = [m.start() for m in re.finditer(r"</g\s*>", svg)]
        d_open = [m.start() for m in re.finditer(r"<defs[\s>]", svg)]
        d_close = [m.start() for m in re.finditer(r"</defs\s*>", svg)]
        spans: list[tuple[int, int]] = []
        for tm in re.finditer(r"<text\b.*?</text>", svg, re.S):
            s = tm.start()
            g_depth = (bisect.bisect_right(g_open, s)
                       - bisect.bisect_right(g_close, s))
            d_depth = (bisect.bisect_right(d_open, s)
                       - bisect.bisect_right(d_close, s))
            if g_depth == 0 and d_depth == 0:
                spans.append((tm.start(), tm.end()))
        if not spans:
            return svg
        close = svg.rfind("</svg>")
        if close == -1:
            return svg
        # Already all contiguous at the very end → nothing to do.
        if spans[-1][1] == close and all(
                spans[i][1] == spans[i + 1][0]
                for i in range(len(spans) - 1)):
            return svg
        blocks = [svg[s:e] for s, e in spans]
        new = svg
        for s, e in reversed(spans):
            new = new[:s] + new[e:]
        close = new.rfind("</svg>")
        return new[:close] + "".join(blocks) + new[close:]
    except Exception:  # noqa: BLE001
        return svg


def snap_edges_to_nodes(svg: str) -> str:
    """Snap floating connector endpoints onto the nodes they connect.

    LLM-drawn graph figures routinely place an edge's endpoints near —
    but not on — the node circles, so connectors visibly float.  Two
    cases are repaired, for every ``<line>`` and every two-point
    ``<path>``:

      * **small gap** — an endpoint within ~2.6 node radii of a centre
        is snapped onto that node's perimeter.
      * **loose edge** — when a connector's two endpoints have
        DIFFERENT nearest nodes and each endpoint is within a generous
        range of its node, BOTH endpoints are snapped onto their
        respective node perimeters.  This is what makes an edge meant
        for nodes A and B actually run A-perimeter to B-perimeter even
        when the model placed the raw coordinates 100+ px off — the
        common "edges reference a node column that was never drawn"
        failure.

    Endpoints far from every node, or whose both ends resolve to the
    SAME node, are left untouched.  Idempotent; fails open.
    """
    import math
    try:
        def _af(attrs: str, name: str):
            mm = re.search(rf'\b{name}\s*=\s*["\']([-\d.eE]+)', attrs)
            try:
                return float(mm.group(1)) if mm else None
            except ValueError:
                return None

        nodes: list[tuple[float, float, float]] = []
        for m in re.finditer(r"<circle\b([^>]*)>", svg):
            cx, cy, r = (_af(m.group(1), "cx"), _af(m.group(1), "cy"),
                         _af(m.group(1), "r"))
            if cx is not None and cy is not None and r and r > 0:
                nodes.append((cx, cy, r))
        for m in re.finditer(r"<ellipse\b([^>]*)>", svg):
            cx, cy = _af(m.group(1), "cx"), _af(m.group(1), "cy")
            rx, ry = _af(m.group(1), "rx"), _af(m.group(1), "ry")
            if (cx is not None and cy is not None and rx and ry):
                nodes.append((cx, cy, (rx + ry) / 2.0))
        if not nodes:
            return svg

        def nearest(px: float, py: float):
            best = None
            bd = 1e18
            for cx, cy, r in nodes:
                d = math.hypot(px - cx, py - cy)
                if d < bd:
                    bd, best = d, (cx, cy, r)
            return best, bd

        def perim(node, tx: float, ty: float) -> tuple[float, float]:
            cx, cy, r = node
            dx, dy = tx - cx, ty - cy
            dl = math.hypot(dx, dy) or 1.0
            return cx + dx / dl * r, cy + dy / dl * r

        def fix(x1, y1, x2, y2):
            n1, d1 = nearest(x1, y1)
            n2, d2 = nearest(x2, y2)
            if n1 is None or n2 is None:
                return x1, y1, x2, y2
            # "Generous" range: an endpoint within this of a node is
            # treated as meant for it.  Scales with node size and the
            # connector's own length (a long edge with sloppy ends
            # gets more slack), capped so a short stray line cannot
            # reach across the figure and grab an unrelated node.
            length = math.hypot(x2 - x1, y2 - y1)
            gen1 = min(320.0, max(160.0, n1[2] * 5, length * 0.55))
            gen2 = min(320.0, max(160.0, n2[2] * 5, length * 0.55))
            # loose-edge: both ends near DISTINCT nodes -> snap both.
            if n1 != n2 and d1 <= gen1 and d2 <= gen2:
                p1 = perim(n1, x2, y2)
                p2 = perim(n2, x1, y1)
                return p1[0], p1[1], p2[0], p2[1]
            # small-gap, per endpoint.  Two cases we always fix:
            #   (a) endpoint INSIDE the node (d < r) → arrow marker
            #       would render inside the node, hiding the head;
            #   (b) endpoint within 2.6·r OFF the perimeter (d > r)
            #       and not already on the perimeter (>4 px off).
            nx1, ny1, nx2, ny2 = x1, y1, x2, y2
            r1, r2 = n1[2], n2[2]
            inside1 = d1 < r1 - 1.0
            inside2 = d2 < r2 - 1.0
            if inside1 or (d1 <= r1 * 2.6 and abs(d1 - r1) > 4.0):
                nx1, ny1 = perim(n1, x2, y2)
            if inside2 or (d2 <= r2 * 2.6 and abs(d2 - r2) > 4.0):
                nx2, ny2 = perim(n2, x1, y1)
            return nx1, ny1, nx2, ny2

        def _line_repl(m: "re.Match[str]") -> str:
            a = m.group(1)
            vals = [_af(a, k) for k in ("x1", "y1", "x2", "y2")]
            if any(v is None for v in vals):
                return m.group(0)
            x1, y1, x2, y2 = vals
            nv = fix(x1, y1, x2, y2)
            if nv == (x1, y1, x2, y2):
                return m.group(0)
            out = m.group(0)
            for attr, val in zip(("x1", "y1", "x2", "y2"), nv):
                out = re.sub(rf'(\b{attr}\s*=\s*["\'])[-\d.eE]+',
                             lambda mm, v=val: f"{mm.group(1)}{v:.1f}",
                             out, count=1)
            return out

        svg = re.sub(r"<line\b([^>]*)>", _line_repl, svg)

        def _path_repl(m: "re.Match[str]") -> str:
            pm = re.match(
                r"\s*M\s*([-\d.eE]+)[ ,]+([-\d.eE]+)\s*"
                r"L\s*([-\d.eE]+)[ ,]+([-\d.eE]+)\s*$", m.group(2))
            if not pm:
                return m.group(0)
            x1, y1, x2, y2 = (float(g) for g in pm.groups())
            nv = fix(x1, y1, x2, y2)
            if nv == (x1, y1, x2, y2):
                return m.group(0)
            return (f'{m.group(1)}M {nv[0]:.1f} {nv[1]:.1f} '
                    f'L {nv[2]:.1f} {nv[3]:.1f}{m.group(3)}')

        svg = re.sub(r'(<path\b[^>]*\bd\s*=\s*["\'])([^"\']*)(["\'])',
                     _path_repl, svg)

        # <polyline points="x,y x,y …"> — retract endpoints (first and
        # last point) that sit inside a node.  Internal points untouched
        # so curved / multi-segment connectors keep their shape.
        def _polyline_repl(m: "re.Match[str]") -> str:
            attrs = m.group(0)
            pm = re.search(r'\bpoints\s*=\s*["\']([^"\']+)["\']', attrs)
            if not pm:
                return attrs
            raw = pm.group(1)
            pts = re.findall(r'(-?\d+\.?\d*(?:[eE][-+]?\d+)?)[, ]+'
                             r'(-?\d+\.?\d*(?:[eE][-+]?\d+)?)', raw)
            if len(pts) < 2:
                return attrs
            pts_f = [(float(x), float(y)) for x, y in pts]
            n_first, d_first = nearest(*pts_f[0])
            n_last, d_last = nearest(*pts_f[-1])
            changed = False
            if (n_first is not None and d_first < n_first[2] - 1.0):
                pts_f[0] = perim(n_first, *pts_f[1])
                changed = True
            if (n_last is not None and d_last < n_last[2] - 1.0):
                pts_f[-1] = perim(n_last, *pts_f[-2])
                changed = True
            if not changed:
                return attrs
            new_points = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts_f)
            return re.sub(r'(\bpoints\s*=\s*["\'])[^"\']+(["\'])',
                          lambda mm: f"{mm.group(1)}{new_points}"
                                     f"{mm.group(2)}", attrs, count=1)

        svg = re.sub(r"<polyline\b[^>]*>", _polyline_repl, svg)
        return svg
    except Exception:  # noqa: BLE001
        return svg


def fit_viewbox_to_content(svg: str) -> str:
    """Recompute the viewBox to tightly bound the figure's content.

    Removes large empty margins — the model often draws everything in
    the lower part of the canvas, leaving a big empty band at the top —
    AND includes content that overflowed the original viewBox so it is
    not clipped.  The root width/height are rewritten to match so the
    aspect ratio stays correct.  Quote-agnostic, inclusive over every
    visible element type, generous 24-px margin.  Idempotent; fails
    open.
    """
    try:
        root = re.search(r"<svg\b[^>]*>", svg)
        if not root:
            return svg
        tag = root.group(0)
        vbm = re.search(r'viewBox\s*=\s*["\']([-\d.\seE]+)["\']', tag)
        if not vbm:
            return svg
        parts = vbm.group(1).split()
        if len(parts) != 4:
            return svg
        ox, oy, ow, oh = (float(p) for p in parts)
        if ow <= 0 or oh <= 0:
            return svg

        def af(attrs: str, name: str):
            mm = re.search(rf'\b{name}\s*=\s*["\']([-\d.eE]+)', attrs)
            try:
                return float(mm.group(1)) if mm else None
            except ValueError:
                return None

        xs: list[float] = []
        ys: list[float] = []
        for m in re.finditer(r"<text\b([^>]*)>([^<]*)", svg):
            a = m.group(1)
            x, y = af(a, "x"), af(a, "y")
            if x is None or y is None:
                continue
            fs = af(a, "font-size") or 16.0
            am = re.search(r'text-anchor\s*=\s*["\'](\w+)', a)
            anchor = (am.group(1) if am else "start").lower()
            w = len(m.group(2).strip()) * fs * 0.62
            xl = (x - w / 2 if anchor == "middle"
                  else x - w if anchor == "end" else x)
            xs += [xl, xl + w]
            ys += [y - fs, y + fs * 0.4]
        for m in re.finditer(r"<rect\b([^>]*)>", svg):
            a = m.group(1)
            x, y, w, h = (af(a, "x"), af(a, "y"),
                          af(a, "width"), af(a, "height"))
            if None not in (x, y, w, h):
                xs += [x, x + w]
                ys += [y, y + h]
        for m in re.finditer(r"<circle\b([^>]*)>", svg):
            a = m.group(1)
            cx, cy, r = af(a, "cx"), af(a, "cy"), af(a, "r")
            if None not in (cx, cy, r):
                xs += [cx - r, cx + r]
                ys += [cy - r, cy + r]
        for m in re.finditer(r"<ellipse\b([^>]*)>", svg):
            a = m.group(1)
            cx, cy = af(a, "cx"), af(a, "cy")
            rx, ry = af(a, "rx"), af(a, "ry")
            if None not in (cx, cy, rx, ry):
                xs += [cx - rx, cx + rx]
                ys += [cy - ry, cy + ry]
        for m in re.finditer(r"<line\b([^>]*)>", svg):
            a = m.group(1)
            for xn, yn in (("x1", "y1"), ("x2", "y2")):
                x, y = af(a, xn), af(a, yn)
                if x is not None:
                    xs.append(x)
                if y is not None:
                    ys.append(y)
        for m in re.finditer(
                r'<(?:polygon|polyline)\b[^>]*\bpoints\s*=\s*["\']'
                r'([^"\']*)', svg):
            nums = re.findall(r"-?\d[\d.eE]*", m.group(1))
            for i in range(0, len(nums) - 1, 2):
                try:
                    xs.append(float(nums[i]))
                    ys.append(float(nums[i + 1]))
                except ValueError:
                    pass
        for m in re.finditer(
                r'<path\b[^>]*\bd\s*=\s*["\']([^"\']*)', svg):
            nums = re.findall(r"-?\d[\d.eE]*", m.group(1))
            for i in range(0, len(nums) - 1, 2):
                try:
                    xs.append(float(nums[i]))
                    ys.append(float(nums[i + 1]))
                except ValueError:
                    pass
        if len(xs) < 2 or len(ys) < 2:
            return svg
        margin = 24.0
        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)
        nx, ny = minx - margin, miny - margin
        nw = max(maxx - minx + 2 * margin, 200.0)
        nh = max(maxy - miny + 2 * margin, 150.0)
        if (abs(nx - ox) < 8 and abs(ny - oy) < 8
                and abs(nw - ow) < 8 and abs(nh - oh) < 8):
            return svg
        tag2 = re.sub(r'viewBox\s*=\s*["\'][^"\']*["\']',
                      f'viewBox="{nx:.0f} {ny:.0f} '
                      f'{nw:.0f} {nh:.0f}"', tag)
        tag2 = re.sub(r'\bwidth\s*=\s*["\']\d[^"\']*["\']',
                      f'width="{nw:.0f}"', tag2)
        tag2 = re.sub(r'\bheight\s*=\s*["\']\d[^"\']*["\']',
                      f'height="{nh:.0f}"', tag2)
        return svg[:root.start()] + tag2 + svg[root.end():]
    except Exception:  # noqa: BLE001
        return svg


def strip_latex_in_svg_text(svg: str) -> str:
    """Walk every <text> / <tspan> body in the SVG and remove any
    LaTeX syntax the LLM leaked through.  Idempotent.

    Fails open: any regex error returns the input unchanged.
    """
    try:
        return _TEXT_BODY_RE.sub(
            lambda m: m.group(1) + _scrub_latex(m.group(2)) + m.group(3),
            svg,
        )
    except Exception:
        return svg


def normalize_matrix_layout(svg: str) -> str:
    """Detect text elements that look like matrix cells (`a₁₁ = 4`,
    `a_{1,2} = 3`, etc.) and re-layout them on a clean N×N grid.

    The LLM frequently produces a 4×4 matrix as 3 columns × 4 rows
    plus the 4th column stacked separately, because it doesn't
    actually compute (x, y) for each cell — it just picks an x for
    each row and increments y.  This pass:

      1. Scans every top-level <text> for content matching the
         cell pattern `<name>(_<i>_<j>|_{i,j}|<unicode_subs>)
         (= <value>)?`.
      2. Groups cells by their letter name.  Each name with N*M
         matching cells (where N = max_i, M = max_j) is treated
         as one matrix.
      3. Computes a regular lattice: cell_w = max-text-est-width +
         20 px, cell_h = max-font-size * 1.8.
      4. Rewrites each cell text's x and y attributes to land on
         the correct grid position; the matrix anchors at the
         minimum (x, y) of the original cells so we preserve the
         model's intended placement on the canvas.

    Idempotent — a cell already on the lattice gets re-set to
    almost-the-same coords (rounded), no visible change.
    """
    import re

    attr_re = re.compile(
        r'\b([A-Za-z_-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')'
    )

    def _attrs(tag: str) -> dict[str, str]:
        return {
            m.group(1): (m.group(2) if m.group(2) is not None else m.group(3))
            for m in attr_re.finditer(tag)
        }

    # Map Unicode subscript digits back to ASCII.
    UNI_SUB = "₀₁₂₃₄₅₆₇₈₉"
    def _strip_subs(s: str) -> tuple[str, str]:
        """Pull the leading letter + subscript digits.  Returns
        (letter, digits) or ("", "") if not a cell-shaped token."""
        m = re.match(rf"([A-Za-z])([{UNI_SUB}]+)", s)
        if m:
            digits = "".join(str(UNI_SUB.index(c)) for c in m.group(2))
            return m.group(1), digits
        # ASCII form: a_11, a_{1,2}, a_1_2
        m = re.match(r"([A-Za-z])_\{(\d+),?(\d+)?\}", s)
        if m:
            return m.group(1), m.group(2) + (m.group(3) or "")
        m = re.match(r"([A-Za-z])_(\d)_(\d)", s)
        if m:
            return m.group(1), m.group(2) + m.group(3)
        m = re.match(r"([A-Za-z])_(\d{2})", s)
        if m:
            return m.group(1), m.group(2)
        return "", ""

    # Find all <text> elements (top-level or inside <g>).  Cells can
    # live anywhere; we don't filter by parent.
    text_iter = list(re.finditer(
        r'<text\b([^>]*)>([^<]+)</text>', svg, re.S,
    ))
    if not text_iter:
        return svg

    # Collect candidate cells: (start, end, x, y, fs, letter, i, j,
    # value_repr, original_tag).
    cells: list[dict] = []
    for m in text_iter:
        head = m.group(0).split('>', 1)[0] + '>'
        a = _attrs(head)
        try:
            x = float(a.get("x", "")); y = float(a.get("y", ""))
        except ValueError:
            continue
        try:
            fs = float(a.get("font-size", "16").rstrip("pxptem"))
        except ValueError:
            fs = 16.0
        content = m.group(2).strip()
        # Try to parse as "letter+sub" optionally followed by "= value".
        token = content.split("=")[0].strip()
        letter, digits = _strip_subs(token)
        if not letter or len(digits) != 2:
            continue
        i, j = int(digits[0]), int(digits[1])
        if i < 1 or j < 1 or i > 9 or j > 9:
            continue
        cells.append({
            "start": m.start(), "end": m.end(),
            "x": x, "y": y, "fs": fs,
            "letter": letter, "i": i, "j": j,
            "content": content,
        })

    if not cells:
        return svg

    # Group cells by letter.
    by_letter: dict[str, list[dict]] = {}
    for c in cells:
        by_letter.setdefault(c["letter"], []).append(c)

    edits: list[tuple[int, int, str]] = []
    for letter, group in by_letter.items():
        max_i = max(c["i"] for c in group)
        max_j = max(c["j"] for c in group)
        # Require a COMPLETE matrix: every (i, j) in 1..max_i × 1..max_j
        # present exactly once.  Partial matrices stay as the model
        # placed them.
        if len(group) != max_i * max_j:
            continue
        ij_set = {(c["i"], c["j"]) for c in group}
        if len(ij_set) != max_i * max_j:
            continue
        # Compute lattice geometry.  cell_w from longest content,
        # cell_h from largest font size, both inflated.
        max_w = 0.0
        max_fs = 0.0
        for c in group:
            est = len(c["content"]) * c["fs"] * 0.6
            max_w = max(max_w, est)
            max_fs = max(max_fs, c["fs"])
        cell_w = max_w + 24.0
        cell_h = max_fs * 1.9
        # Anchor at the (min x, min y) of the original cell positions.
        # This preserves the model's intended placement on the canvas
        # while fixing the internal layout.
        base_x = min(c["x"] for c in group)
        base_y = min(c["y"] for c in group)
        for c in group:
            new_x = base_x + (c["j"] - 1) * cell_w
            new_y = base_y + (c["i"] - 1) * cell_h
            if abs(new_x - c["x"]) < 0.5 and abs(new_y - c["y"]) < 0.5:
                continue
            head = svg[c["start"]:c["end"]].split('>', 1)[0] + '>'
            # Replace x= and y= in the head.
            def _set(attr: str, val: float, tag: str) -> str:
                new = f'{attr}="{val:.0f}"'
                pat_dq = re.compile(rf'\b{attr}\s*=\s*"[^"]*"')
                pat_sq = re.compile(rf"\b{attr}\s*=\s*'[^']*'")
                if pat_dq.search(tag):
                    return pat_dq.sub(new, tag, count=1)
                if pat_sq.search(tag):
                    return pat_sq.sub(new, tag, count=1)
                return re.sub(r'>$', f' {new}>', tag, count=1)
            new_head = _set("x", new_x, head)
            new_head = _set("y", new_y, new_head)
            edits.append((c["start"], c["start"] + len(head), new_head))

    if not edits:
        return svg
    edits.sort(key=lambda t: -t[0])
    out = svg
    for s, e, repl in edits:
        out = out[:s] + repl + out[e:]
    return out


def clamp_group_transforms(svg: str) -> str:
    """Adjust `<g transform="translate(dx dy)">` so the group's
    children don't poke above the viewBox top edge.

    The model frequently writes `transform="translate(150 0)"` —
    intending the group to be in a horizontal row — but child texts
    use local y=0 (which renders at the very top of the canvas and
    gets clipped because glyphs extend ABOVE the baseline).

    For each top-level `<g>` with a translate transform:
      1. Parse current dx, dy.
      2. Find the minimum text-baseline y of any direct text child.
      3. If `dy + min_y - font_size < TOP_MARGIN`, raise dy so the
         topmost glyph clears TOP_MARGIN.

    Idempotent — already-clear groups pass through unchanged.
    """
    import re

    vb_match = re.search(
        r'<svg\b[^>]*?\bviewBox\s*=\s*(?:"([^"]*)"|\'([^\']*)\')',
        svg, re.S,
    )
    if not vb_match:
        return svg
    vb_raw = vb_match.group(1) if vb_match.group(1) is not None else vb_match.group(2)
    try:
        parts = vb_raw.replace(",", " ").split()
        vb_x, vb_y = float(parts[0]), float(parts[1])
    except (ValueError, IndexError):
        return svg

    TOP_MARGIN = 20.0
    attr_re = re.compile(
        r'\b([A-Za-z_-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')'
    )

    def _attrs(tag: str) -> dict[str, str]:
        return {
            m.group(1): (m.group(2) if m.group(2) is not None else m.group(3))
            for m in attr_re.finditer(tag)
        }

    group_re = re.compile(r'<g\b[^>]*?>.*?</g>', re.S)
    translate_re = re.compile(
        r'translate\s*\(\s*([\-0-9.]+)[,\s]+([\-0-9.]+)\s*\)'
    )

    edits: list[tuple[int, int, str]] = []
    for gm in group_re.finditer(svg):
        body = gm.group(0)
        head_end = body.find('>')
        if head_end < 0:
            continue
        head = body[:head_end + 1]
        # Pull current transform translate (if any).
        tr = translate_re.search(head)
        if not tr:
            continue
        try:
            dx = float(tr.group(1))
            dy = float(tr.group(2))
        except ValueError:
            continue
        # Compute the min top edge of any CHILD bbox in local
        # coordinates.  Considers text (y - font_size for the glyph
        # cap), rect (y), and circle/ellipse (cy - r).  Without this
        # an "empty matrix" group (only <rect> children, no text)
        # would skip clamping and leave the boxes at y=0 — they'd
        # render above/over the canvas title.
        min_top = None
        for tm in re.finditer(r'<text\b[^>]*>', body):
            a = _attrs(tm.group(0))
            try:
                y = float(a.get("y", ""))
            except ValueError:
                continue
            try:
                fs = float(a.get("font-size", "16").rstrip("pxptem"))
            except ValueError:
                fs = 16.0
            top = y - fs * 0.9
            if min_top is None or top < min_top:
                min_top = top
        for rm in re.finditer(r'<rect\b[^>]*?/?>', body):
            a = _attrs(rm.group(0))
            try:
                ry = float(a.get("y", ""))
            except ValueError:
                continue
            if min_top is None or ry < min_top:
                min_top = ry
        for cm in re.finditer(r'<(?:circle|ellipse)\b[^>]*?/?>', body):
            a = _attrs(cm.group(0))
            try:
                cy = float(a.get("cy", "")); r = float(a.get("r", a.get("ry", "")))
            except ValueError:
                continue
            top = cy - r
            if min_top is None or top < min_top:
                min_top = top
        if min_top is None:
            continue
        # If absolute top of glyph (dy + min_top) is above TOP_MARGIN,
        # raise dy.
        if dy + min_top >= vb_y + TOP_MARGIN:
            continue
        new_dy = (vb_y + TOP_MARGIN) - min_top
        new_head = translate_re.sub(
            f"translate({dx:.0f} {new_dy:.0f})", head, count=1,
        )
        if new_head == head:
            continue
        start = gm.start()
        edits.append((start, start + len(head), new_head))

    if not edits:
        return svg
    edits.sort(key=lambda t: -t[0])
    out = svg
    for s, e, repl in edits:
        out = out[:s] + repl + out[e:]
    return out


def wrap_overlong_text(svg: str) -> str:
    """Split any `<text>` whose estimated rendered width exceeds the
    available horizontal space into multiple stacked `<text>`
    elements (word-broken).

    Estimated width per char = 0.6 × font-size (em).  Available width
    = viewBox width − x (anchor position) − safe margin (20 px).  If
    width exceeds available, find the WORD boundary closest to but
    not over the available width, emit a new <text> with the prefix,
    move the remaining suffix to a new <text> at the same x, y + 1.2
    × font-size.  Repeats until the suffix fits.

    Only operates on TOP-LEVEL text elements (skips inside <g>) so
    matrix cells and labelled diagram elements aren't mangled.
    Preserves id, font-size, font-family attributes on every produced
    text fragment (id is replaced with `<id>_wrap_<n>` for fragments
    2+ to keep ids unique).
    """
    import re

    vb_match = re.search(
        r'<svg\b[^>]*?\bviewBox\s*=\s*(?:"([^"]*)"|\'([^\']*)\')',
        svg, re.S,
    )
    if not vb_match:
        return svg
    vb_raw = vb_match.group(1) if vb_match.group(1) is not None else vb_match.group(2)
    try:
        parts = vb_raw.replace(",", " ").split()
        vb_x, vb_y = float(parts[0]), float(parts[1])
        vb_w, vb_h = float(parts[2]), float(parts[3])
    except (ValueError, IndexError):
        return svg

    RIGHT_MARGIN = 20.0
    MAX_LINES = 4
    WIDTH_FACTOR = 0.6

    attr_re = re.compile(
        r'\b([A-Za-z_-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')'
    )

    def _attrs(tag: str) -> dict[str, str]:
        return {
            m.group(1): (m.group(2) if m.group(2) is not None else m.group(3))
            for m in attr_re.finditer(tag)
        }

    # Track <g> ranges so we skip group-internal text.
    g_ranges: list[tuple[int, int]] = []
    depth = 0
    start = -1
    for m in re.finditer(r'<g\b[^>]*>|</g>', svg, re.S):
        if m.group(0).startswith("</"):
            depth -= 1
            if depth == 0 and start >= 0:
                g_ranges.append((start, m.end()))
                start = -1
        else:
            if depth == 0:
                start = m.start()
            depth += 1

    def _in_group(pos: int) -> bool:
        for s, e in g_ranges:
            if s <= pos < e:
                return True
        return False

    edits: list[tuple[int, int, str]] = []
    text_re = re.compile(r'<text\b[^>]*?>([^<]+)</text>', re.S)
    for m in text_re.finditer(svg):
        # Skip text inside <g> ONLY when the content is short
        # (likely a matrix cell or labelled token — shouldn't wrap).
        # Long prose inside semantic groups (a conclusion paragraph
        # under <g id="discussion">, a step inside a <g id="step_3">)
        # MUST still be wrapped when too wide.
        is_inside_group = _in_group(m.start())
        if is_inside_group and len(m.group(1).strip()) < 30:
            continue
        full_tag = m.group(0)
        head = full_tag.split('>', 1)[0] + '>'
        a = _attrs(head)
        try:
            x = float(a.get("x", ""))
            y = float(a.get("y", ""))
        except ValueError:
            continue
        try:
            fs = float(a.get("font-size", "16").rstrip("pxptem"))
        except ValueError:
            fs = 16.0
        content = m.group(1).strip()
        if not content:
            continue
        anchor = (a.get("text-anchor") or "start").lower()
        est_w = len(content) * fs * WIDTH_FACTOR
        # Available width depends on anchor: for start, the text
        # extends right from x; for middle, both ways; for end, left.
        # Wrap is only meaningful when text starts at the left, so
        # we limit this pass to text-anchor=start (the default).
        if anchor != "start":
            continue
        avail = vb_x + vb_w - RIGHT_MARGIN - x
        if est_w <= avail or avail < 80:
            continue
        # Need to wrap.  Find word-boundary breakpoints.
        words = content.split()
        if len(words) <= 1:
            continue  # one-word too-wide text can't be wrapped sanely
        line_w_chars = max(1, int(avail / (fs * WIDTH_FACTOR)))
        lines: list[str] = []
        cur: list[str] = []
        cur_len = 0
        for w in words:
            wlen = len(w) + (1 if cur else 0)
            if cur_len + wlen > line_w_chars and cur:
                lines.append(" ".join(cur))
                cur = [w]
                cur_len = len(w)
            else:
                cur.append(w)
                cur_len += wlen
        if cur:
            lines.append(" ".join(cur))
        # Cap at MAX_LINES; if more would be needed, ellipsise the
        # last fragment.
        if len(lines) > MAX_LINES:
            lines = lines[:MAX_LINES - 1] + [lines[MAX_LINES - 1] + " …"]
        if len(lines) <= 1:
            continue  # nothing to do
        # Emit one <text> per line, stepping y by 1.25*fs.
        line_dy = fs * 1.25
        attrs_serialized = head[len("<text"):-1]  # everything inside <text...>
        # Strip the existing x, y, and id (we'll re-insert per line).
        attrs_no_xyid = re.sub(
            r'\b(?:x|y|id)\s*=\s*(?:"[^"]*"|\'[^\']*\')\s*',
            '',
            attrs_serialized,
        ).strip()
        # The text-anchor and font-size etc remain.
        base_id = a.get("id", "")
        new_lines = []
        for i, line in enumerate(lines):
            ly = y + i * line_dy
            line_id = base_id if i == 0 else f"{base_id}_wrap_{i}"
            id_attr = f' id="{line_id}"' if base_id else ""
            new_lines.append(
                f'<text x="{x:.0f}" y="{ly:.0f}"{id_attr} {attrs_no_xyid}>{line}</text>'
            )
        # Replace the entire original <text>...</text> match.
        edits.append((m.start(), m.end(), "\n".join(new_lines)))

    if not edits:
        return svg
    edits.sort(key=lambda t: -t[0])
    out = svg
    for s, e, repl in edits:
        out = out[:s] + repl + out[e:]
    return out


def clamp_text_to_viewbox(svg: str) -> str:
    """Pull every <text> back inside the SVG's viewBox.  The model
    occasionally places section headers at y = -36 or y = -10 hoping
    the SVG will clip them — instead the negative-y texts all stack
    visually at the same place (or above the visible canvas), which
    surfaces in the user audit as the "Clause Gadgets / Variable
    Gadgets / Vertex Cover all overlap 100%" failure.

    For each text whose anchor (x, y) lies outside the viewBox by
    more than a small tolerance, snap the offending axis back to the
    margin (20 px from each edge).  Idempotent for already-inside
    text.  The subsequent reflow_overlapping_text pass then resolves
    any same-y collisions the clamping introduces.
    """
    import re

    vb_match = re.search(
        r'<svg\b[^>]*?\bviewBox\s*=\s*(?:"([^"]*)"|\'([^\']*)\')',
        svg, re.S,
    )
    if not vb_match:
        return svg
    vb_raw = vb_match.group(1) if vb_match.group(1) is not None else vb_match.group(2)
    try:
        parts = vb_raw.replace(",", " ").split()
        vb_x, vb_y, vb_w, vb_h = (float(parts[0]), float(parts[1]),
                                   float(parts[2]), float(parts[3]))
    except (ValueError, IndexError):
        return svg

    TOP_MARGIN = 20.0
    LEFT_MARGIN = 20.0

    attr_re = re.compile(
        r'\b([A-Za-z_-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')'
    )

    def _attrs(tag: str) -> dict[str, str]:
        return {
            m.group(1): (m.group(2) if m.group(2) is not None else m.group(3))
            for m in attr_re.finditer(tag)
        }

    def _set_attr(tag: str, name: str, val: float) -> str:
        new = f'{name}="{val:.0f}"'
        pat_dq = re.compile(rf'\b{name}\s*=\s*"[^"]*"')
        pat_sq = re.compile(rf"\b{name}\s*=\s*'[^']*'")
        if pat_dq.search(tag):
            return pat_dq.sub(new, tag, count=1)
        if pat_sq.search(tag):
            return pat_sq.sub(new, tag, count=1)
        return re.sub(r'>$', f' {new}>', tag, count=1)

    edits: list[tuple[int, int, str]] = []
    for m in re.finditer(r'<text\b[^>]*>([^<]*)</text>', svg, re.S):
        head = m.group(0).split('>', 1)[0] + '>'
        a = _attrs(head)
        try:
            x = float(a.get("x", ""))
            y = float(a.get("y", ""))
        except ValueError:
            continue
        new_x = x
        new_y = y
        if y < vb_y + TOP_MARGIN:
            new_y = vb_y + TOP_MARGIN
        if x < vb_x + LEFT_MARGIN:
            new_x = vb_x + LEFT_MARGIN
        if abs(new_x - x) < 0.5 and abs(new_y - y) < 0.5:
            continue
        new_head = head
        if abs(new_x - x) >= 0.5:
            new_head = _set_attr(new_head, "x", new_x)
        if abs(new_y - y) >= 0.5:
            new_head = _set_attr(new_head, "y", new_y)
        start = m.start()
        edits.append((start, start + len(head), new_head))

    if not edits:
        return svg
    edits.sort(key=lambda t: -t[0])
    out = svg
    for s, e, repl in edits:
        out = out[:s] + repl + out[e:]
    return out


def fix_html_subsup(svg: str) -> str:
    """SVG renderers don't honour HTML `<sup>` / `<sub>` — they show
    the inner text inline with no baseline shift, so `A<sup>-1</sup>`
    appears as the literal "A-1".  Convert to the proper SVG form
    using <tspan baseline-shift='super|sub' font-size='80%'>.

    Idempotent — text that's already using <tspan> passes through.
    """
    import re
    out = re.sub(
        r'<sup\b[^>]*>([^<]*)</sup>',
        r'<tspan baseline-shift="super" font-size="80%">\1</tspan>',
        svg,
    )
    out = re.sub(
        r'<sub\b[^>]*>([^<]*)</sub>',
        r'<tspan baseline-shift="sub" font-size="80%">\1</tspan>',
        out,
    )
    return out


def autofit_group_rects(svg: str) -> str:
    """Auto-fit the outer <rect> of each <g> group to the bounding
    box of its child elements.

    The LLM often emits a group containing a small outer rect plus
    cell labels that extend WAY past the rect — e.g. a 3×3 matrix
    drawn as <rect width=200 height=200> with cells positioned at
    x=350, y=340 (i.e. needs a 300×300 rect).  The result is a
    little square in the corner with most of the matrix spilling
    out.  This pass walks each <g>, finds the first <rect> child,
    estimates the bbox of every other child (text, line, sub-rect),
    and resizes the rect to cover that bbox with 20-px padding.

    Deterministic, idempotent, no LLM call.  Safe to run on any SVG
    that uses the standard 'outer rect + child labels' pattern; a
    group without a recognisable outer rect is left alone.
    """
    import re

    # Reuse the dual-quote attribute parser from _structural_review.
    attr_re = re.compile(
        r'\b([A-Za-z_-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')'
    )

    def _attrs(tag: str) -> dict[str, str]:
        return {
            m.group(1): (m.group(2) if m.group(2) is not None else m.group(3))
            for m in attr_re.finditer(tag)
        }

    def _text_bbox(tag: str) -> tuple[float, float, float, float] | None:
        a = _attrs(tag)
        try:
            tx = float(a.get("x", "")); ty = float(a.get("y", ""))
        except ValueError:
            return None
        # Pull text content for width estimate.
        content_m = re.search(
            r'<text\b[^>]*>([^<]*)</text>', tag, re.S,
        )
        content = (content_m.group(1).strip() if content_m else "")
        try:
            fs = float(a.get("font-size", "16").rstrip("pxptem"))
        except ValueError:
            fs = 16.0
        # Conservative width estimate — 0.6 catches Unicode subscripts
        # and parentheses better than the previous 0.55.
        anchor = (a.get("text-anchor") or "start").lower()
        est_w = max(len(content), 1) * fs * 0.6
        if anchor == "middle":
            x0 = tx - est_w / 2
        elif anchor == "end":
            x0 = tx - est_w
        else:
            x0 = tx
        # SVG text baseline sits at y; glyphs extend roughly fs above.
        return (x0, ty - fs * 0.9, est_w, fs * 1.1)

    def _rect_bbox(tag: str) -> tuple[float, float, float, float] | None:
        a = _attrs(tag)
        try:
            rx = float(a.get("x", "0")); ry = float(a.get("y", "0"))
            rw = float(a.get("width", "")); rh = float(a.get("height", ""))
        except ValueError:
            return None
        return (rx, ry, rw, rh)

    def _circle_bbox(tag: str) -> tuple[float, float, float, float] | None:
        a = _attrs(tag)
        try:
            cx = float(a.get("cx", "")); cy = float(a.get("cy", ""))
            r = float(a.get("r", ""))
        except ValueError:
            return None
        return (cx - r, cy - r, 2 * r, 2 * r)

    def _line_bbox(tag: str) -> tuple[float, float, float, float] | None:
        a = _attrs(tag)
        try:
            x1 = float(a.get("x1", "")); y1 = float(a.get("y1", ""))
            x2 = float(a.get("x2", "")); y2 = float(a.get("y2", ""))
        except ValueError:
            return None
        return (min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))

    def _bbox(tag: str) -> tuple[float, float, float, float] | None:
        s = tag.lstrip("<").split()[0] if tag.startswith("<") else ""
        if s.startswith("text"):
            return _text_bbox(tag)
        if s.startswith("rect"):
            return _rect_bbox(tag)
        if s.startswith("circle") or s.startswith("ellipse"):
            return _circle_bbox(tag)
        if s.startswith("line"):
            return _line_bbox(tag)
        return None

    # Walk each <g ...> ... </g> block, find its first <rect>, compute
    # the bbox of all NON-rect (well, non-first-rect) children, and
    # resize the rect if it's smaller than that bbox.
    # PAD reduced from 20 → 4 after user feedback: "the borders of the
    # matrices are too big.  The squares around the matrices should be
    # calculated carefully to be just around the matrix tightly."  Cells
    # already have their own borders, so a thick outer frame just looks
    # like wasted whitespace around the matrix.
    PAD = 4.0

    def _resize_group(group_match: re.Match) -> str:
        inner = group_match.group(0)
        # Find every direct child tag (self-closing or text).
        child_pattern = re.compile(
            r'<(?:rect|text|circle|ellipse|line)\b[^>]*?(?:/>|>(?:[^<]*</'
            r'(?:rect|text|circle|ellipse|line)>)?)',
            re.S,
        )
        children = list(child_pattern.finditer(inner))
        if not children:
            return inner
        # Pick the FIRST <rect> as the container.
        rect_idx = next(
            (i for i, m in enumerate(children) if m.group(0).startswith("<rect")),
            None,
        )
        if rect_idx is None:
            return inner
        rect_match = children[rect_idx]
        rect_box = _rect_bbox(rect_match.group(0))
        if not rect_box:
            return inner
        rx, ry, rw, rh = rect_box
        # Compute the union bbox of all OTHER children.
        x0 = float("inf"); y0 = float("inf")
        x1 = float("-inf"); y1 = float("-inf")
        for i, m in enumerate(children):
            if i == rect_idx:
                continue
            b = _bbox(m.group(0))
            if not b:
                continue
            bx, by, bw, bh = b
            x0 = min(x0, bx); y0 = min(y0, by)
            x1 = max(x1, bx + bw); y1 = max(y1, by + bh)
        if x0 == float("inf"):
            return inner
        # Required rect bounds: WRAP the children with PAD-px margin
        # on each side.  Both shrinks (rect too big) AND expands (rect
        # too small) — previously we only expanded, which left huge
        # empty boxes around small matrices.
        need_x = x0 - PAD
        need_y = y0 - PAD
        need_w = (x1 + PAD) - need_x
        need_h = (y1 + PAD) - need_y
        # Only touch the rect if the difference is meaningful (≥2 px
        # on either axis or ≥10 px on either dimension).  Sub-pixel
        # noise from glyph-width estimates doesn't merit a rewrite.
        if (abs(need_x - rx) < 2 and abs(need_y - ry) < 2
                and abs(need_w - rw) < 10 and abs(need_h - rh) < 10):
            return inner
        # Rewrite the rect's x/y/width/height while preserving every
        # other attribute exactly (stroke, fill, id, etc).
        def _set(attr: str, val: float, tag: str) -> str:
            new = f'{attr}="{val:.0f}"'
            pat_dq = re.compile(rf'\b{attr}\s*=\s*"[^"]*"')
            pat_sq = re.compile(rf"\b{attr}\s*=\s*'[^']*'")
            if pat_dq.search(tag):
                return pat_dq.sub(new, tag, count=1)
            if pat_sq.search(tag):
                return pat_sq.sub(new, tag, count=1)
            # Attribute not present; inject before the closing > or />.
            return re.sub(r'(/?>)$', f' {new}\\1', tag, count=1)
        new_rect = rect_match.group(0)
        new_rect = _set("x", need_x, new_rect)
        new_rect = _set("y", need_y, new_rect)
        new_rect = _set("width", need_w, new_rect)
        new_rect = _set("height", need_h, new_rect)
        return inner.replace(rect_match.group(0), new_rect, 1)

    group_pattern = re.compile(r'<g\b[^>]*>.*?</g>', re.S)
    return group_pattern.sub(_resize_group, svg)


def reflow_overlapping_groups(svg: str) -> str:
    """When two top-level <g> groups have outer rects that overlap
    horizontally, slide the LATER group to the right until clear.
    Handles the "matrix A at x=20-310 and matrix A_inverse at
    x=200-396 sit on top of each other" failure mode.

    Each group's reference rect is its first <rect> child (same
    convention used by autofit_group_rects).  Shift is implemented
    via a `transform="translate(dx 0)"` attribute on the <g> — non-
    destructive (preserves all other attributes) and idempotent
    when no overlap is present.

    Errors are swallowed by the caller.
    """
    import re

    attr_re = re.compile(
        r'\b([A-Za-z_-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')'
    )

    def _attrs(tag: str) -> dict[str, str]:
        return {
            m.group(1): (m.group(2) if m.group(2) is not None else m.group(3))
            for m in attr_re.finditer(tag)
        }

    vb_match = re.search(
        r'<svg\b[^>]*?\bviewBox\s*=\s*(?:"([^"]*)"|\'([^\']*)\')',
        svg, re.S,
    )
    if not vb_match:
        return svg
    vb_raw = vb_match.group(1) if vb_match.group(1) is not None else vb_match.group(2)
    try:
        parts = vb_raw.replace(",", " ").split()
        vb_w = float(parts[2])
    except (ValueError, IndexError):
        return svg

    PAD = 20.0

    def _apply_translate(body: str, dx: float, dy: float) -> str:
        """Write `transform="translate(dx dy)"` onto a <g>'s opening
        tag, replacing any existing transform."""
        open_m = re.match(r'<g\b[^>]*?>', body, re.S)
        if not open_m:
            return body
        open_tag = open_m.group(0)
        new_open = re.sub(
            r'\btransform\s*=\s*(?:"[^"]*"|\'[^\']*\')',
            '',
            open_tag,
        )
        new_open = re.sub(
            r'>$',
            f' transform="translate({dx:.0f} {dy:.0f})">',
            new_open,
            count=1,
        )
        return new_open + body[open_m.end():]

    # For each top-level <g>, pull bbox from its first <rect> child
    # in ABSOLUTE coordinates (compose any existing translate).
    group_re = re.compile(r'<g\b[^>]*?>.*?</g>', re.S)
    translate_re = re.compile(
        r'translate\s*\(\s*([\-0-9.]+)[,\s]+([\-0-9.]+)\s*\)'
    )
    # Each entry: (start, end, body, (x, y, w, h), (orig_dx, orig_dy))
    # where x/y/w/h are ABSOLUTE (local + transform), and orig_dx/dy
    # are the group's CURRENT transform offsets so subsequent
    # _apply_translate calls add to them rather than overwrite.
    groups: list[tuple[int, int, str,
                       tuple[float, float, float, float],
                       tuple[float, float]]] = []
    for m in group_re.finditer(svg):
        body = m.group(0)
        head = body.split('>', 1)[0] + '>'
        tr = translate_re.search(head)
        if tr:
            try:
                orig_dx = float(tr.group(1))
                orig_dy = float(tr.group(2))
            except ValueError:
                orig_dx = orig_dy = 0.0
        else:
            orig_dx = orig_dy = 0.0
        rect_m = re.search(r'<rect\b[^>]*?/?>', body, re.S)
        if not rect_m:
            continue
        a = _attrs(rect_m.group(0))
        try:
            lx = float(a.get("x", "0")); ly = float(a.get("y", "0"))
            w = float(a.get("width", "")); h = float(a.get("height", ""))
        except ValueError:
            continue
        # Absolute bbox after applying the group transform.
        x = lx + orig_dx; y = ly + orig_dy
        groups.append((m.start(), m.end(), body, (x, y, w, h),
                       (orig_dx, orig_dy)))

    # Build TOP-LEVEL text bboxes as obstacles too — a group placed
    # at (180, 0) with no inherent y offset will otherwise sit on
    # top of a title at y=30 because no other GROUP is in its way.
    # Scan top-level <text> (skipping those inside <g>) for absolute
    # bboxes; treat them as immovable when shifting groups.
    g_open_ranges: list[tuple[int, int]] = []
    depth = 0; gstart = -1
    for m in re.finditer(r'<g\b[^>]*>|</g>', svg, re.S):
        if m.group(0).startswith("</"):
            depth -= 1
            if depth == 0 and gstart >= 0:
                g_open_ranges.append((gstart, m.end()))
                gstart = -1
        else:
            if depth == 0:
                gstart = m.start()
            depth += 1

    def _outside_g(pos: int) -> bool:
        for s, e in g_open_ranges:
            if s <= pos < e:
                return False
        return True

    text_obstacles: list[tuple[float, float, float, float]] = []
    for m in re.finditer(r'<text\b[^>]*?>([^<]+)</text>', svg, re.S):
        if not _outside_g(m.start()):
            continue
        head = m.group(0).split('>', 1)[0] + '>'
        a = _attrs(head)
        try:
            tx = float(a.get("x", ""))
            ty = float(a.get("y", ""))
        except ValueError:
            continue
        content = m.group(1).strip()
        if not content:
            continue
        try:
            fs = float(a.get("font-size", "16").rstrip("pxptem"))
        except ValueError:
            fs = 16.0
        est_w = max(len(content), 1) * fs * 0.6
        text_obstacles.append(
            (tx, ty - fs * 0.9, est_w, fs * 1.2),
        )

    if not groups and not text_obstacles:
        return svg

    # Greedy: walk in document order, shift each group past every
    # earlier group AND every top-level text it overlaps.  dx/dy
    # are SHIFTS to add on top of the group's existing transform.
    edits: list[tuple[int, int, str]] = []
    placed: list[tuple[float, float, float, float]] = list(text_obstacles)
    for start, end, body, box, orig in groups:
        x, y, w, h = box
        orig_dx, orig_dy = orig
        dx = 0.0
        dy = 0.0
        max_iters = 20
        while max_iters > 0:
            max_iters -= 1
            overlap = None
            for px, py, pw, ph in placed:
                if (x + dx < px + pw + PAD and x + dx + w + PAD > px
                        and y + dy < py + ph + PAD and y + dy + h + PAD > py):
                    overlap = (px, py, pw, ph)
                    break
            if not overlap:
                break
            shift = (overlap[0] + overlap[2] + PAD) - (x + dx)
            dx += shift
            if x + dx + w > vb_w - 5:
                # No more horizontal room — slide down past the
                # offender's bottom edge instead.
                dx = 0.0
                dy_needed = (overlap[1] + overlap[3] + PAD) - (y + dy)
                if dy_needed > 0:
                    dy += dy_needed
                else:
                    break
        placed.append((x + dx, y + dy, w, h))
        if abs(dx) < 0.5 and abs(dy) < 0.5:
            continue
        # Compose the NEW transform with the group's ORIGINAL one.
        new_dx = orig_dx + dx
        new_dy = orig_dy + dy
        edits.append((start, end, _apply_translate(body, new_dx, new_dy)))

    if not edits:
        return svg
    edits.sort(key=lambda t: -t[0])
    out = svg
    for s, e, repl in edits:
        out = out[:s] + repl + out[e:]
    return out


# Structural-issue classes that are visible-quality complaints, not
# functional / correctness violations.  When the vision reviewer (which
# sees the rendered PNG and represents user perception) says PASS, an
# issue in this set should NOT trigger another retry — retries on
# these often regress the figure (see the 3-SAT case: attempt 0 had 1
# overlap pair, attempt 1 had 5).  Functional issues like
# missing_required_primitive or narration_highlight_id_missing still
# gate retries even when vision passes.
_VISUAL_ONLY_ISSUE_PREFIXES: tuple[str, ...] = (
    "text_text_overlap",
    "oversized_element",
    "caption_overlaps_diagram",
    "bottom_overflow_with_unused_right",
    "label_inside_wrong_vertex",
    "lies_on_violation",
    "duplicate_coords",
    "math_mode_no_coords",
)


def _is_visual_only_issues(issues: list[str]) -> bool:
    """All issues are pixel-level / measurement complaints (not
    functional)?  Used to short-circuit retries when vision passed."""
    if not issues:
        return True
    return all(
        any(s.startswith(p) for p in _VISUAL_ONLY_ISSUE_PREFIXES)
        for s in issues
    )


def _attempt_score(structural_issues: list[str],
                   vision_verdict: str | None) -> int:
    """Quality score for an attempt — lower is better.  Used for
    best-attempt selection when all retries fail review: each
    structural issue counts 1, a vision-FAIL adds 5 (vision-perceived
    problems carry more weight than measurement-level critic
    complaints)."""
    return len(structural_issues) + (5 if vision_verdict is not None else 0)


# (strip_narration_prose_text was removed — it tried to strip <text>
# blocks the LLM emitted as prose, but went too aggressive on proof
# prompts and left near-empty canvases.  Replaced by the deterministic
# text-region layout, which prevents the problem at the source.)


def resolve_text_overlaps(svg: str, max_iterations: int = 60) -> str:
    """Deterministic final-pass that moves overlapping <text> elements
    apart by adjusting their `y` attribute.  Handles <text> ANYWHERE in
    the SVG (including inside <g> groups, which `reflow_overlapping_text`
    skips by design).

    Uses the same bbox estimator as the structural critic
    (0.6 × font-size × char-count for width; 1.2 × font-size for
    height), so any overlap the critic would flag is the same set
    this function tries to resolve.  Iterates up to ``max_iterations``
    times: each pass fixes ONE overlapping pair (greedy by document
    order), re-collects boxes, repeats.  Stops early once no overlaps
    remain.

    Modifies y attribute IN PLACE in the raw SVG markup.  Does NOT
    account for parent transforms — that's OK because the critic's
    detector also ignores them; detection-and-fix agree on the
    coordinate frame.

    Errors are swallowed: layout polish must never block a working
    figure.  Returns the original SVG if anything fails.
    """
    if not svg or "<text" not in svg:
        return svg
    try:
        import re as _re

        attr_re = _re.compile(
            r'(\w[\w-]*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')'
        )

        def _local_attrs(open_tag: str) -> dict[str, str]:
            return {
                m.group(1): (m.group(2) if m.group(2) is not None
                             else m.group(3))
                for m in attr_re.finditer(open_tag)
            }

        # Match `<text ...>content</text>`. Capture the opening tag,
        # the inner content, and the closing tag separately so we can
        # surgically edit only the y attribute of the opening tag.
        text_re = _re.compile(
            r'(<text\b[^>]*>)([^<]*)(</text>)', _re.IGNORECASE
        )
        # Skip <text> inside <g class="text-region-…"> groups: those
        # are deterministically positioned by inject_text_blocks() and
        # must not be moved by the resolver.
        region_g_re = _re.compile(
            r'<g\b[^>]*class\s*=\s*["\'][^"\']*text-region-[^"\']*["\']'
            r'[^>]*>',
        )

        def _region_ranges(s: str) -> list[tuple[int, int]]:
            spans: list[tuple[int, int]] = []
            for gm in region_g_re.finditer(s):
                close_idx = s.find("</g>", gm.end())
                if close_idx >= 0:
                    spans.append((gm.start(), close_idx + 4))
            return spans

        def _collect_boxes(s: str) -> list[dict]:
            items: list[dict] = []
            skip = _region_ranges(s)

            def _in_region(pos: int) -> bool:
                return any(a <= pos < b for a, b in skip)

            for m in text_re.finditer(s):
                if _in_region(m.start()):
                    continue
                open_tag = m.group(1)
                content = m.group(2).strip()
                if not content or len(content) < 2:
                    continue
                attrs = _local_attrs(open_tag)
                try:
                    tx = float(attrs.get("x", ""))
                    ty = float(attrs.get("y", ""))
                except ValueError:
                    continue
                try:
                    fs = float(attrs.get("font-size", "16")
                               .rstrip("pxptem"))
                except ValueError:
                    fs = 16.0
                anchor = (attrs.get("text-anchor") or "start").lower()
                est_w = max(1.0, len(content) * fs * 0.6)
                est_h = fs * 1.2
                x_left = (tx - est_w / 2 if anchor == "middle"
                          else tx - est_w if anchor == "end"
                          else tx)
                items.append({
                    "open_start": m.start(1),
                    "open_end": m.end(1),
                    "tx": tx, "ty": ty, "fs": fs,
                    "anchor": anchor,
                    "x_left": x_left,
                    "width": est_w,
                    "height": est_h,
                    "content": content,
                    "open_tag": open_tag,
                })
            return items

        def _rewrite_y(open_tag: str, new_ty: float) -> str:
            # Replace y="..." (or y='...') ONLY in this opening tag.
            # If no y attribute is present (shouldn't happen — we
            # filtered above), the regex misses and we leave it alone.
            def _replace_one(m: "_re.Match[str]") -> str:
                q_open = m.group(1)   # opening quote char (" or ')
                q_close = m.group(3)  # closing quote char
                return f'y={q_open}{new_ty:.2f}{q_close}'
            return _re.sub(
                r'\by\s*=\s*(["\'])([^"\']*)(["\'])',
                _replace_one,
                open_tag,
                count=1,
            )

        # Match critic's bbox model EXACTLY (line 5556):
        #   y_min = ty - fs ;  height = 1.2 * fs
        # (critic puts a FULL font-size above the baseline, not 0.8 fs).
        def _bbox(it: dict) -> tuple[float, float, float, float]:
            x_left = it["x_left"]
            y_top = it["ty"] - it["fs"]
            return (x_left, y_top, it["width"], 1.2 * it["fs"])

        def _overlap_ratio(a: dict, b: dict) -> float:
            ax, ay, aw, ah = _bbox(a)
            bx, by, bw, bh = _bbox(b)
            ix0 = max(ax, bx); ix1 = min(ax + aw, bx + bw)
            iy0 = max(ay, by); iy1 = min(ay + ah, by + bh)
            if ix1 <= ix0 or iy1 <= iy0:
                return 0.0
            ov = (ix1 - ix0) * (iy1 - iy0)
            smaller = max(1.0, min(aw * ah, bw * bh))
            return ov / smaller

        def _clear_ty(item: dict, items: list[dict], skip_idx: int) -> float:
            """Find a baseline `ty` for `item` such that its bbox at
            that ty does NOT collide (>= 20% of smaller area) with any
            other item's bbox.  Greedy: start at the item's current ty,
            push down past every conflict's bottom + gap.  Caps push
            distance at the viewBox-ish range; if we'd land below y=2000
            give up (return current ty unchanged)."""
            ty = item["ty"]
            for _safety in range(50):
                trial = dict(item, ty=ty)
                bx, by, bw, bh = _bbox(trial)
                conflict_bottom = None
                for k, other in enumerate(items):
                    if k == skip_idx:
                        continue
                    if _overlap_ratio(trial, other) < 0.20:
                        continue
                    ox, oy, ow, oh = _bbox(other)
                    o_bottom = oy + oh
                    if conflict_bottom is None or o_bottom > conflict_bottom:
                        conflict_bottom = o_bottom
                if conflict_bottom is None:
                    return ty
                # Place item so its TOP is 4 units below the lowest
                # conflict's bottom.  Convert back to baseline:
                # baseline = top + 1.0 * fs (mirrors _bbox's y_top).
                new_ty = conflict_bottom + 4 + item["fs"]
                if new_ty <= ty + 0.5:
                    # No progress (e.g., infeasible) — give up.
                    return ty
                ty = new_ty
                if ty > 2000:
                    return item["ty"]
            return ty

        for _it in range(max_iterations):
            items = _collect_boxes(svg)
            if len(items) < 2:
                return svg
            moved = False
            # Greedy: find the first overlapping pair in document order
            # and resolve it by placing the LATER item below ALL of its
            # current conflicts.  Re-collect on next iteration since
            # byte offsets shift after every edit.
            for i in range(len(items)):
                a = items[i]
                for j in range(i + 1, len(items)):
                    b = items[j]
                    if _overlap_ratio(a, b) < 0.20:
                        continue
                    # Resolve by moving B clear of every conflict.
                    new_ty_b = _clear_ty(b, items, skip_idx=j)
                    if abs(new_ty_b - b["ty"]) < 1.0:
                        # Couldn't find a clear y — skip this pair
                        # rather than loop forever.
                        continue
                    new_open = _rewrite_y(b["open_tag"], new_ty_b)
                    if new_open == b["open_tag"]:
                        continue
                    svg = (svg[:b["open_start"]]
                           + new_open
                           + svg[b["open_end"]:])
                    moved = True
                    break
                if moved:
                    break
            if not moved:
                return svg
        return svg
    except Exception:
        return svg


def reflow_overlapping_text(svg: str) -> str:
    """Greedy 2-D layout pass that nudges top-level <text> elements
    apart when their bounding boxes overlap.

    The LLM frequently places multiple text elements at the SAME y
    in a row, not realising the leftmost one is long enough to run
    UNDER the others (e.g. a 60-character formula at x=20,y=290
    overlapping three short formulas at x=300/450/600,y=290).  This
    pass:

      1. Parses every top-level <text> (those NOT inside a <g>) with
         estimated bbox (anchor-aware, font-size-aware, 0.6em width).
      2. Walks them in document order.  For each, if its bbox
         intersects ANY previously-placed text's bbox, shifts it
         down (y += dy) by just enough to clear, in 4-px increments.
      3. If the shift would push the text past viewBox_height-20,
         instead REWRITES x to start a new column (x += 480) and
         resets y back to the topmost unused row in that column.

    Idempotent — already-clean layouts pass through unchanged.
    Tolerates single- AND double-quoted SVG attributes.  Errors are
    swallowed by the caller so a layout bug never blocks a figure.
    """
    import re

    # Reuse the dual-quote parser.
    attr_re = re.compile(
        r'\b([A-Za-z_-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')'
    )

    def _attrs(tag: str) -> dict[str, str]:
        return {
            m.group(1): (m.group(2) if m.group(2) is not None else m.group(3))
            for m in attr_re.finditer(tag)
        }

    # Pull viewBox so we know the canvas bounds.
    vb_match = re.search(
        r'<svg\b[^>]*?\bviewBox\s*=\s*(?:"([^"]*)"|\'([^\']*)\')',
        svg, re.S,
    )
    if not vb_match:
        return svg
    vb_raw = vb_match.group(1) if vb_match.group(1) is not None else vb_match.group(2)
    try:
        parts = vb_raw.replace(",", " ").split()
        vb_x, vb_y, vb_w, vb_h = (float(parts[0]), float(parts[1]),
                                   float(parts[2]), float(parts[3]))
    except (ValueError, IndexError):
        return svg

    # Identify which character ranges in svg are inside <g>…</g> so we
    # skip those — autofit_group_rects already handles them and they
    # carry their own logical layout (e.g. matrix cells inside a
    # bordered grid).  We only reflow TOP-LEVEL text.
    g_ranges: list[tuple[int, int]] = []
    depth = 0
    for m in re.finditer(r'<g\b[^>]*>|</g>', svg, re.S):
        if m.group(0).startswith("</"):
            depth -= 1
            if depth == 0:
                # close range started at start
                start = g_ranges[-1][0] if g_ranges and g_ranges[-1][1] == -1 else None
                if start is not None:
                    g_ranges[-1] = (start, m.end())
        else:
            if depth == 0:
                g_ranges.append((m.start(), -1))
            depth += 1
    g_ranges = [r for r in g_ranges if r[1] != -1]

    def _in_group(pos: int) -> bool:
        for s, e in g_ranges:
            if s <= pos < e:
                return True
        return False

    def _enclosing_group_translate(svg_text: str, pos: int) -> tuple[float, float]:
        """If ``pos`` is inside a <g transform="translate(dx dy)">, return
        (dx, dy); otherwise (0, 0).  Used when adding a group-internal
        text element to the obstacle list so its REAL screen position
        accounts for any earlier reflow_overlapping_groups shift."""
        for s, e in g_ranges:
            if not (s <= pos < e):
                continue
            head_end = svg_text.find('>', s)
            if head_end < 0:
                continue
            head = svg_text[s:head_end + 1]
            m = re.search(
                r'transform\s*=\s*(?:"|\')\s*translate\(\s*([\-0-9.]+)[\s,]+([\-0-9.]+)\s*\)',
                head,
            )
            if m:
                try:
                    return float(m.group(1)), float(m.group(2))
                except ValueError:
                    return 0.0, 0.0
            return 0.0, 0.0
        return 0.0, 0.0

    # 10 px PAD (was 4) — a 4-px gap is too tight when the rendered
    # font is 16-18 px and may include diacritics / subscripts that
    # extend the visual bbox.  10 px ≈ 0.6 line-height; gives a
    # comfortable visual gap even for the busiest figures.
    PAD = 10.0      # min separation between text bboxes
    STEP = 4.0      # shift increment
    COL_DX = 480.0  # second-column x offset
    TOP_MARGIN = 30.0
    BOT_MARGIN = 20.0

    # Walk every <text> tag.  Skip those inside <g> groups.
    text_pattern = re.compile(
        r'<text\b[^>]*>([^<]*)</text>', re.S,
    )
    matches = list(text_pattern.finditer(svg))
    placed: list[tuple[float, float, float, float]] = []  # (x, y, w, h) in bbox form
    edits: list[tuple[int, int, str]] = []  # (start, end, new_tag)

    # Pre-populate `placed` with the bboxes of EVERY <text> that lives
    # inside a <g> group.  We DON'T move those (matrix cells, group-
    # internal labels — autofit_group_rects + reflow_overlapping_groups
    # handle their containers).  But they DO act as obstacles that the
    # top-level text must avoid; without this step a top-level
    # paragraph could end up sitting right on top of a group's
    # formula, which was the user's most recent visible bug.
    for m in matches:
        if not _in_group(m.start()):
            continue
        head = m.group(0).split('>', 1)[0] + '>'
        a = _attrs(head)
        try:
            tx = float(a.get("x", "")); ty = float(a.get("y", ""))
        except ValueError:
            continue
        content = m.group(1).strip()
        if not content:
            continue
        try:
            fs = float(a.get("font-size", "16").rstrip("pxptem"))
        except ValueError:
            fs = 16.0
        anchor = (a.get("text-anchor") or "start").lower()
        est_w = max(len(content), 1) * fs * 0.6
        if anchor == "middle":
            x0 = tx - est_w / 2
        elif anchor == "end":
            x0 = tx - est_w
        else:
            x0 = tx
        # Account for any parent <g transform="translate(dx dy)"> by
        # looking at the immediately enclosing group's opening tag.
        # The group itself may have been shifted by an earlier pass
        # (reflow_overlapping_groups), so the obstacle's REAL screen
        # position is the cell coords PLUS the group's translate.
        gdx, gdy = _enclosing_group_translate(svg, m.start())
        placed.append((x0 + gdx, ty - fs * 0.9 + gdy, est_w, fs * 1.2))

    for m in matches:
        if _in_group(m.start()):
            continue
        head = m.group(0).split('>', 1)[0] + '>'
        a = _attrs(head)
        try:
            tx = float(a.get("x", ""))
            ty = float(a.get("y", ""))
        except ValueError:
            continue
        content = m.group(1).strip()
        if not content:
            continue
        try:
            fs = float(a.get("font-size", "16").rstrip("pxptem"))
        except ValueError:
            fs = 16.0
        anchor = (a.get("text-anchor") or "start").lower()
        est_w = max(len(content), 1) * fs * 0.6
        # Anchor-aware bbox.
        if anchor == "middle":
            x0 = tx - est_w / 2
        elif anchor == "end":
            x0 = tx - est_w
        else:
            x0 = tx
        h = fs * 1.2
        y0 = ty - fs * 0.9

        # Greedy shift to avoid overlap.
        cur_x, cur_y = tx, ty
        new_x0, new_y0 = x0, y0
        max_iters = 400
        for _ in range(max_iters):
            overlap = False
            for px, py, pw, ph in placed:
                # Reject if bboxes overlap with PAD margin.
                if (new_x0 < px + pw + PAD and new_x0 + est_w + PAD > px
                        and new_y0 < py + ph + PAD and new_y0 + h + PAD > py):
                    overlap = True
                    # Compute minimum y-shift to clear THIS placed box.
                    dy = (py + ph + PAD) - new_y0
                    new_y0 += dy
                    cur_y += dy
                    break
            if not overlap:
                break
            # If shifting down ran past the viewBox bottom, prefer to
            # jump into a second column when the text can FIT there.
            # When the text is too wide for the second column, KEEP
            # stacking past the bottom — the canvas viewer has
            # overflow:scroll so a tall figure is preferable to
            # overlapping text.
            if new_y0 + h > vb_y + vb_h - BOT_MARGIN:
                if cur_x < vb_x + vb_w * 0.4:
                    # We're still in the left column; try column 2.
                    trial_x = cur_x + COL_DX
                    if anchor == "middle":
                        trial_x0 = trial_x - est_w / 2
                    elif anchor == "end":
                        trial_x0 = trial_x - est_w
                    else:
                        trial_x0 = trial_x
                    if trial_x0 + est_w <= vb_x + vb_w + 5:
                        cur_x = trial_x
                        cur_y = vb_y + TOP_MARGIN + fs * 0.9
                        new_x0 = trial_x0
                        new_y0 = cur_y - fs * 0.9
                        # Loop back; the new position will be re-checked.
                        continue
                # Either we're already in column 2, OR column 2 can't
                # hold this width.  Accept the current y (which may be
                # past the viewBox bottom) — overflow:scroll on the
                # canvas viewer's <main> will let the user scroll.

        placed.append((new_x0, new_y0, est_w, h))
        if abs(cur_x - tx) < 0.5 and abs(cur_y - ty) < 0.5:
            continue
        # Rewrite the x/y attributes of THIS tag.
        new_head = head
        def _set(attr: str, val: float, tag: str) -> str:
            new = f'{attr}="{val:.0f}"'
            pat_dq = re.compile(rf'\b{attr}\s*=\s*"[^"]*"')
            pat_sq = re.compile(rf"\b{attr}\s*=\s*'[^']*'")
            if pat_dq.search(tag):
                return pat_dq.sub(new, tag, count=1)
            if pat_sq.search(tag):
                return pat_sq.sub(new, tag, count=1)
            return re.sub(r'>$', f' {new}>', tag, count=1)
        new_head = _set("x", cur_x, new_head)
        new_head = _set("y", cur_y, new_head)
        edits.append((m.start(), m.start() + len(head), new_head))

    if not edits:
        return svg
    # Apply edits from RIGHT to LEFT so prior offsets stay valid.
    edits.sort(key=lambda t: -t[0])
    out = svg
    for start, end, repl in edits:
        out = out[:start] + repl + out[end:]
    return out


def _verify_arithmetic(svg: str, narration: list[dict[str, Any]]
                       ) -> list[str]:
    """Scan figure text + narration for fully-numeric arithmetic
    claims ``a op b = c`` and flag any that are wrong.

    Deliberately conservative — it only checks claims where BOTH
    operands and the result are plain numbers, skips division (long
    division / remainders look like wrong claims) and skips anything
    near the word "mod" (modular arithmetic is correct-but-different).
    This means it never false-positives on symbolic algebra; it only
    catches the concrete "2 + 3 = 6" class of mistake the vision judge
    keeps reporting.
    """
    import re

    texts: list[str] = re.findall(r'<text\b[^>]*>([^<]*)</text>', svg or "")
    for ph in narration or []:
        if isinstance(ph, dict) and isinstance(ph.get("speak"), str):
            texts.append(ph["speak"])
    blob = " ; ".join(texts)
    blob = (blob.replace("×", "*").replace("·", "*")
                .replace("−", "-").replace("∗", "*"))

    pat = re.compile(
        r'(-?\d+(?:\.\d+)?)\s*([+\-*])\s*'
        r'(-?\d+(?:\.\d+)?)\s*=\s*(-?\d+(?:\.\d+)?)')
    bad: list[str] = []
    for m in pat.finditer(blob):
        lo, hi = max(0, m.start() - 24), m.end() + 24
        if "mod" in blob[lo:hi].lower():
            continue
        try:
            a, b, c = (float(m.group(1)), float(m.group(3)),
                       float(m.group(4)))
        except ValueError:
            continue
        op = m.group(2)
        r = a + b if op == "+" else (a - b if op == "-" else a * b)
        if abs(r - c) > 1e-6 * max(1.0, abs(r)):
            bad.append(f"'{m.group(0).strip()}' (correct: {r:g})")
    if not bad:
        return []
    return [
        "arithmetic_error: the figure/narration states "
        f"{len(bad)} incorrect numeric result(s): "
        + "; ".join(bad[:6])
        + ". Recompute and correct BOTH the figure text and the "
        "narration so every stated number is true."
    ]


def _structural_review(svg: str, narration: list[dict[str, Any]],
                        user_prompt: str = "") -> list[str]:
    """Return a list of structural issues with the (svg, narration) pair.

    Empty list means structural review passes; non-empty list is
    formatted into the critic's checklist for retry.
    """
    import re

    issues: list[str] = []
    if not svg or not isinstance(svg, str):
        return issues

    # All id="..." (and id='...') attributes in the SVG.
    svg_ids: set[str] = set()
    svg_ids.update(re.findall(r'id\s*=\s*"([^"]+)"', svg))
    svg_ids.update(re.findall(r"id\s*=\s*'([^']+)'", svg))

    # 1. Narration highlights must point at real SVG ids.
    unknown_refs: list[tuple[int, str]] = []
    for i, phrase in enumerate(narration or []):
        highlights = phrase.get("highlight") or []
        # Some models emit a single string instead of a list — be
        # forgiving so we don't false-positive on a typing slip.
        if isinstance(highlights, str):
            highlights = [highlights]
        for hid in highlights:
            if not hid or not isinstance(hid, str):
                continue
            if hid not in svg_ids:
                unknown_refs.append((i, hid))

    if unknown_refs:
        sample = ", ".join(
            f"phrase[{i}] -> '{hid}'" for i, hid in unknown_refs[:5]
        )
        more = (f" (and {len(unknown_refs) - 5} more)"
                if len(unknown_refs) > 5 else "")
        issues.append(
            "narration_highlight_id_missing: "
            f"{len(unknown_refs)} narration phrase(s) reference SVG ids that "
            f"do NOT exist in the emitted SVG ({sample}{more}). The viewer's "
            "highlight machinery only fires when document.getElementById of "
            "the highlight value succeeds, so this causes the learner to "
            "see NOTHING flash while the phrase plays. Fix by EITHER giving "
            "the referenced visual element a unique id matching the "
            "highlight string OR removing the bogus id from the highlight "
            "array (use [] for phrases that don't point at a specific "
            "visual element). The svg currently has these ids: "
            f"{sorted(svg_ids)[:30]}"
        )

    # 1b. All-empty highlights: every phrase has highlight=[] (or
    # missing).  Technically legal per the schema, but in practice
    # means the narration plays with no visual cue — exactly the
    # "no item was highlighted" failure the learner reported.  Only
    # flag when there are enough phrases for this to be intentional
    # silence (a 1-phrase explanation might legitimately not target
    # anything).
    if narration and len(narration) >= 4:
        non_empty = 0
        for phrase in narration:
            h = phrase.get("highlight")
            if isinstance(h, str) and h.strip():
                non_empty += 1
            elif isinstance(h, list) and any(x for x in h if isinstance(x, str) and x.strip()):
                non_empty += 1
        if non_empty == 0:
            issues.append(
                "all_highlights_empty: every narration phrase has an "
                "empty highlight array. The viewer cannot spotlight any "
                "element while the narration plays, which leaves the "
                "figure visually inert. For each phrase that names a "
                "specific element ('the vertex v_3', 'this edge', "
                "'the highlighted chord'), populate its highlight "
                "array with the id of that element. At minimum, one or "
                "two phrases should reference a concrete id."
            )

    # 1c. Conclusion-check: the final narration phrase must STATE the
    # result the figure delivers, not merely sign off ("this completes
    # the proof", "we have shown the figure").  Detected by:
    #   (a) a conclusion connector at or near the start ("therefore",
    #       "so", "thus", "hence", "and so", "which gives", "we
    #       conclude", "finally", "in conclusion"), OR
    #   (b) an equals sign / equation in the phrase (a stated value), OR
    #   (c) a definitive verb naming the result ("is", "equals",
    #       "becomes") together with a numeric/symbolic token.
    # Phrases that ONLY recap the technique ("this completes the
    # explanation", "we have now seen the integration formula") are
    # flagged.
    if narration and len(narration) >= 2:
        last = narration[-1] if isinstance(narration[-1], dict) else {}
        last_text = (last.get("speak") or "").strip()
        if last_text:
            lower = last_text.lower()
            conclusion_connectors = (
                "therefore", "so ", "so,", "thus", "hence", "and so",
                "which gives", "we conclude", "finally", "in conclusion",
                "the answer is", "the result is", "the value is",
                "we get", "we have", "this gives",
            )
            recap_phrases = (
                "this completes",
                "this concludes",
                "we have shown the figure",
                "this is how we",
                "and that is how",
                "we have now seen",
                "this illustrates",
                "this explains",
                "as shown in the figure",
                "this completes the proof",
                "this completes the explanation",
                "this completes the walkthrough",
                "this completes our",
            )
            has_connector = any(c in lower for c in conclusion_connectors)
            has_equation = ("=" in last_text or "→" in last_text
                            or "⇒" in last_text)
            looks_like_recap = any(r in lower for r in recap_phrases)
            # Reject as a conclusion if it's pure recap, OR if it lacks
            # both a connector and an equation.
            if looks_like_recap or not (has_connector or has_equation):
                snippet = (last_text[:80] + "…"
                           if len(last_text) > 80 else last_text)
                issues.append(
                    "missing_conclusion: the final narration phrase "
                    "does not state the result the figure delivers — "
                    f"it currently reads {snippet!r}.  Every walkthrough "
                    "MUST end with a phrase that names the concrete "
                    "answer (derivative value, computed sum, "
                    "classification, named result), preferably opened "
                    "by a conclusion connector ('Therefore', 'So', "
                    "'Hence', 'Thus', 'Which gives', 'And so').  Recap "
                    "phrases like 'this completes the proof' or 'we "
                    "have shown the figure' do not count.  Replace the "
                    "last phrase with one that states the answer the "
                    "learner now knows."
                )

    # 2. Graph-completeness heuristic: a figure with many <circle>
    # vertices but very few <text> elements is almost certainly
    # missing vertex labels.  Conservative threshold to avoid
    # false-positive retries on figures that legitimately have few
    # text labels (charts, dashed-line diagrams, etc.).
    circles = re.findall(r'<circle\b[^>]*', svg)
    ellipses = re.findall(r'<ellipse\b[^>]*', svg)
    # Only count circles that look like nodes (carry an id).  Decorative
    # circles in chart backgrounds, etc., usually don't have ids.
    node_like_circles = [c for c in (circles + ellipses) if 'id=' in c]
    texts = re.findall(r'<text\b', svg)
    if len(node_like_circles) >= 4 and len(texts) < len(node_like_circles):
        issues.append(
            "vertex_labels_missing: SVG has "
            f"{len(node_like_circles)} node-like circles/ellipses but only "
            f"{len(texts)} <text> elements. Every vertex in a graph "
            "figure must carry a visible label (vertex name or number) "
            "as an adjacent <text> element. Add one <text> per vertex, "
            "placed just above/right of the circle so it doesn't "
            "overlap the node."
        )

    # 3. Label-inside-wrong-vertex: a short <text> (looks like a vertex
    # name — single letter, two chars, "v_1", etc.) whose centre falls
    # INSIDE a different vertex's circle.  The deterministic check uses
    # the (cx, cy, r) of each circle and the (x, y) of each text node;
    # text whose distance to its OWN circle's centre is > r, but whose
    # distance to ANOTHER circle's centre is <= r * 0.9, is mis-placed.
    # Conservative: we only flag when the wrongly-claimed circle has an
    # id matching the label text — otherwise we'd false-positive on
    # captions sitting inside their target.
    # Pull each <circle.../> | <ellipse.../> tag whole, then extract
    # cx, cy, r, id in any order — attribute ordering varies between
    # generators and a positional regex was missing common shapes.
    tag_re = re.compile(r'<(?:circle|ellipse)\b[^>]*?/?>', re.S)
    text_tag_re = re.compile(r'<text\b[^>]*?>([^<]{1,12})</text>', re.S)
    # IMPORTANT: gpt-4o-mini emits SVG with SINGLE quotes (<text x='100'…>)
    # while the JSON-schema example shows double quotes.  Match BOTH so
    # the structural critic actually sees attribute values on the SVG
    # the live system produces.  A previous version with only the
    # double-quote variant silently returned empty {} for every tag
    # and made out_of_bounds / caption_overlaps_diagram into no-ops.
    attr_re = re.compile(r'\b([A-Za-z_-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\')')

    def _attrs(tag: str) -> dict[str, str]:
        # attr_re has TWO value groups (double-quoted, single-quoted);
        # pick whichever fired.
        return {
            m.group(1): (m.group(2) if m.group(2) is not None else m.group(3))
            for m in attr_re.finditer(tag)
        }

    circles_with_geom: list[tuple[float, float, float, str]] = []
    for m in tag_re.finditer(svg):
        a = _attrs(m.group(0))
        try:
            cx = float(a.get("cx", "")); cy = float(a.get("cy", ""))
            r = float(a.get("r") or a.get("rx") or "")
        except ValueError:
            continue
        cid = (a.get("id") or "").strip()
        circles_with_geom.append((cx, cy, r, cid))

    short_texts: list[tuple[float, float, str]] = []
    for m in text_tag_re.finditer(svg):
        a = _attrs(m.group(0)[: m.group(0).index(">") + 1])
        try:
            tx = float(a.get("x", "")); ty = float(a.get("y", ""))
        except ValueError:
            continue
        inner = m.group(1).strip()
        if inner:
            short_texts.append((tx, ty, inner))
    mis_placed: list[str] = []
    for (tx, ty, label) in short_texts:
        if len(label) > 5:
            continue
        inside: list[tuple[float, str]] = []
        for (cx, cy, r, cid) in circles_with_geom:
            if r <= 0:
                continue
            d2 = (tx - cx) ** 2 + (ty - cy) ** 2
            if d2 <= (r * 0.9) ** 2 and cid:
                inside.append((d2 ** 0.5, cid))
        if not inside:
            continue
        # Pick the closest containing circle's id.
        inside.sort(key=lambda t: t[0])
        host_id = inside[0][1]
        # Heuristic: if the label text is a single letter / short token
        # AND it doesn't match the host circle's id (case-insensitive,
        # ignoring common prefixes like 'v', 'vertex_', 'node_'), it's
        # almost certainly the wrong vertex's label.
        host_norm = host_id.lower().lstrip("v").lstrip("_")
        label_norm = label.lower().lstrip("v").lstrip("_")
        if host_norm != label_norm and host_id.lower() != f"v{label.lower()}":
            mis_placed.append(f"text '{label}' inside circle id='{host_id}'")
    if mis_placed:
        sample = "; ".join(mis_placed[:5])
        more = (f" (and {len(mis_placed) - 5} more)"
                if len(mis_placed) > 5 else "")
        issues.append(
            "label_inside_wrong_vertex: " + str(len(mis_placed)) +
            " short text label(s) sit INSIDE the wrong vertex's "
            "circle: " + sample + more + ". Each vertex label must be "
            "placed adjacent to its own circle (just above-right is "
            "conventional), not inside a neighbouring vertex.  Move "
            "each label so its (x, y) lies at most ~one radius outside "
            "its own circle's centre."
        )

    # 4. Out-of-bounds: every text/rect/circle/line must lie inside
    # the SVG's viewBox.  The model occasionally puts formulas or
    # captions at coordinates past the right edge / below the bottom,
    # which on the rendered canvas means the text is invisible
    # (clipped) or extends off-page in PDF exports.
    # Helper that pulls a single attribute value from the opening <svg
    # tag, tolerant of both single- and double-quoted strings.  Same
    # quoting fix as attr_re above — gpt-4o-mini emits single-quoted
    # SVG which the double-quote-only regex was missing.
    def _svg_attr(name: str) -> str | None:
        pattern = (
            r'<svg\b[^>]*?\b' + re.escape(name)
            + r'\s*=\s*(?:"([^"]*)"|\'([^\']*)\')'
        )
        m = re.search(pattern, svg, re.S)
        if not m:
            return None
        return m.group(1) if m.group(1) is not None else m.group(2)

    vb_raw = _svg_attr("viewBox")
    if vb_raw:
        try:
            parts = vb_raw.replace(",", " ").split()
            vb = [float(p) for p in parts[:4]]
            vb_x, vb_y, vb_w, vb_h = vb[0], vb[1], vb[2], vb[3]
        except (ValueError, IndexError):
            vb_x = vb_y = 0.0; vb_w = vb_h = 0.0
    else:
        # Fall back to width/height when no viewBox is set.
        try:
            w_raw = _svg_attr("width")
            h_raw = _svg_attr("height")
            vb_x = vb_y = 0.0
            vb_w = float(w_raw.rstrip("pxptem%")) if w_raw else 0.0
            vb_h = float(h_raw.rstrip("pxptem%")) if h_raw else 0.0
        except (ValueError, AttributeError):
            vb_x = vb_y = vb_w = vb_h = 0.0

    # 4a. Bottom overflow with unused right column.  Catches the "stack
    # vertical formulas until they fall off the bottom" failure mode
    # the user surfaced as 'text is now out of the page from the
    # bottom.'  When ANY visible element sits below y=viewBox_h-30
    # AND the right half (x > viewBox_w * 0.55) has no visible text,
    # the figure is wasting horizontal space; the model should have
    # spilled into a second column instead.
    if vb_w > 0 and vb_h > 0:
        # Tighter bottom-band: was vb_h-30, now vb_h-80.  In practice a
        # text element at y=580 in a 650-tall viewBox already crowds
        # the bottom edge of the mobile canvas pane; only the right
        # column is guaranteed visible past that point.
        bottom_band = vb_h - 80
        right_half_x = vb_x + vb_w * 0.55
        below_count = 0
        right_count = 0
        for m in re.finditer(r'<text\b[^>]*>([^<]+)</text>', svg, re.S):
            head = m.group(0).split('>', 1)[0] + '>'
            a = _attrs(head)
            try:
                tx = float(a.get("x", "")); ty = float(a.get("y", ""))
            except ValueError:
                continue
            if ty > bottom_band:
                below_count += 1
            if tx > right_half_x:
                right_count += 1
        if below_count >= 1 and right_count == 0:
            issues.append(
                "bottom_overflow_with_unused_right: " + str(below_count) +
                " text element(s) sit below y=" + str(int(bottom_band)) +
                " while the entire right half (x>" + str(int(right_half_x)) +
                f") of the {int(vb_w)}x{int(vb_h)} viewBox is empty.  "
                "Re-flow: keep the diagram in the left column and put "
                "the overflowing formulas in a second column starting "
                f"at x={int(vb_w * 0.55)}, y reset to ~80.  A canvas "
                "always uses its WIDTH before going tall."
            )

    # 4b. LaTeX-source contamination: SVG <text> is NOT MathJax — when
    # a text element contains LaTeX-style subscripts (a_{11}) or
    # commands (\sum, \frac, \theta), it renders as literal source on
    # the canvas instead of as a math glyph.  The model is supposed to
    # use <tspan baseline-shift='sub'> for subscripts and Unicode for
    # operators (Σ ∏ θ).  Flag any <text> whose content contains the
    # tell-tale `_{` / `^{` / `\command` patterns.
    latex_pattern = re.compile(r'(_\{[^}]*\}|\^\{[^}]*\}|\\[A-Za-z]{2,})')
    bad_latex: list[str] = []
    for m in re.finditer(r'<text\b[^>]*>([^<]+)</text>', svg, re.S):
        content = m.group(1).strip()
        if latex_pattern.search(content):
            snippet = content[:50] + ("…" if len(content) > 50 else "")
            bad_latex.append(snippet)
    if bad_latex:
        sample = " | ".join(repr(s) for s in bad_latex[:3])
        more = (f" (and {len(bad_latex) - 3} more)"
                if len(bad_latex) > 3 else "")
        issues.append(
            "latex_source_in_text: " + str(len(bad_latex)) +
            " <text> element(s) contain LaTeX source that will render "
            "as literal characters, not as math: " + sample + more +
            ". Replace each LaTeX subscript like a_{ij} with "
            "<tspan baseline-shift='sub' font-size='80%'>ij</tspan>, "
            "and replace LaTeX commands (\\\\sum, \\\\theta, \\\\frac) "
            "with the corresponding Unicode glyph (Σ, θ) or with the "
            "appropriate SVG markup.  SVG does not interpret LaTeX."
        )

    if vb_w > 0 and vb_h > 0:
        out_of_bounds: list[str] = []
        # Generous tolerance: text gets clipped only when its anchor
        # point is well past the edge.  We flag x > right-edge OR
        # x < left-edge - 5 (text-anchor=end can legitimately sit at
        # the edge); same logic for y.
        tol = 4.0
        # Text elements — check (x, y) and rough text width.
        for m in re.finditer(r'<text\b([^>]*)>([^<]*)', svg):
            attrs = _attrs(m.group(1) + ">")
            try:
                tx = float(attrs.get("x", "")); ty = float(attrs.get("y", ""))
            except ValueError:
                continue
            content = m.group(2).strip()
            # Approximate text width: 0.55em per char @ default font-size
            # (16px) is a reasonable estimate for sans-serif.
            try:
                fs = float(attrs.get("font-size", "16").rstrip("pxptem"))
            except ValueError:
                fs = 16.0
            anchor = (attrs.get("text-anchor") or "start").lower()
            # 0.6 (was 0.55) is a more conservative average glyph width
            # for variable-width fonts with Unicode subscripts and
            # parentheses; 0.55 was letting 50-character formulas pass
            # that actually overflow once rendered.
            est_w = len(content) * fs * 0.6
            x_left = (tx - est_w / 2 if anchor == "middle"
                      else tx - est_w if anchor == "end"
                      else tx)
            x_right = x_left + est_w
            if (x_right > vb_x + vb_w + tol
                    or x_left < vb_x - tol
                    or ty > vb_y + vb_h + tol
                    or ty < vb_y - tol):
                snippet = content[:20] + ("…" if len(content) > 20 else "")
                out_of_bounds.append(
                    f"<text> {snippet!r} at ({tx:.0f},{ty:.0f})"
                )
        if out_of_bounds:
            sample = "; ".join(out_of_bounds[:5])
            more = (f" (and {len(out_of_bounds) - 5} more)"
                    if len(out_of_bounds) > 5 else "")
            issues.append(
                "out_of_bounds: " + str(len(out_of_bounds)) +
                " text element(s) extend beyond the viewBox "
                f"({vb_w:.0f}x{vb_h:.0f}): " + sample + more +
                ". Move each off-canvas element back inside the "
                "viewBox.  Long formulas should be BROKEN across "
                "multiple <text> elements on stacked y values "
                "instead of running past the right edge."
            )

        # 4b. Oversized elements — a single primitive that dominates
        # the canvas and crowds out axes/labels (the SVM-blob failure
        # mode).  A near-full-canvas rect is a legitimate background,
        # so rects are only flagged between 60% and 93% of the area;
        # a circle/ellipse over ~42% is almost never intended.
        import math as _math
        vb_area = vb_w * vb_h
        if vb_area > 0:
            oversized: list[str] = []
            for m in re.finditer(
                    r"<(rect|circle|ellipse)\b([^>]*)>", svg):
                kind = m.group(1)
                a = _attrs(m.group(2) + ">")
                try:
                    if kind == "rect":
                        w = float(a.get("width", "0").rstrip("pxptem%"))
                        h = float(a.get("height", "0").rstrip("pxptem%"))
                        frac = (w * h) / vb_area
                        if 0.60 < frac < 0.93:
                            oversized.append(
                                f"<rect> ~{frac * 100:.0f}% of canvas")
                    elif kind == "circle":
                        r = float(a.get("r", "0").rstrip("pxptem%"))
                        frac = (_math.pi * r * r) / vb_area
                        if frac > 0.42:
                            oversized.append(
                                f"<circle> ~{frac * 100:.0f}% of canvas")
                    else:
                        rx = float(a.get("rx", "0").rstrip("pxptem%"))
                        ry = float(a.get("ry", "0").rstrip("pxptem%"))
                        frac = (_math.pi * rx * ry) / vb_area
                        if frac > 0.42:
                            oversized.append(
                                f"<ellipse> ~{frac * 100:.0f}% of canvas")
                except ValueError:
                    continue
            if oversized:
                issues.append(
                    "oversized_element: " + str(len(oversized)) +
                    " primitive(s) dominate the canvas: " +
                    "; ".join(oversized[:4]) +
                    ". Scale these down so they occupy a sensible "
                    "fraction of the figure and stop crowding out the "
                    "axes, labels and other content."
                )

    # 5. Caption-overlaps-diagram: text whose bounding box intersects
    # the bounding box of a diagram element (rect/circle/path with
    # stroke or fill).  Only flag MAJOR overlaps (>= 50% of the text's
    # area covered by the diagram's box) — incidental touch on a
    # 1-px stroke shouldn't trigger.
    if vb_w > 0 and vb_h > 0:
        # Diagram boxes: rectangles with fill/stroke that aren't
        # background frames (we approximate "background" as a rect
        # covering more than 80% of the viewBox).
        rect_re = re.compile(r'<rect\b[^>]*?/?>', re.S)
        diagram_boxes: list[tuple[float, float, float, float, str]] = []
        for m in rect_re.finditer(svg):
            a = _attrs(m.group(0))
            try:
                rx = float(a.get("x", "0")); ry = float(a.get("y", "0"))
                rw = float(a.get("width", "")); rh = float(a.get("height", ""))
            except ValueError:
                continue
            # Skip background-sized rectangles (frame, viewport bg).
            if rw * rh >= 0.7 * vb_w * vb_h:
                continue
            diagram_boxes.append((rx, ry, rw, rh, (a.get("id") or "").strip()))
        # Add circles as bounding boxes too.
        for cx, cy, r, cid in circles_with_geom:
            diagram_boxes.append((cx - r, cy - r, 2*r, 2*r, cid))

        # Pre-compute byte ranges occupied by <g class="text-region-…">
        # groups.  Text inside these groups is deterministically
        # positioned by inject_text_blocks() and cannot overlap by
        # construction — flagging it here would generate false
        # positives that drive useless retries.
        text_region_ranges: list[tuple[int, int]] = []
        for gm in re.finditer(
            r'<g\b[^>]*class\s*=\s*["\'][^"\']*text-region-[^"\']*["\'][^>]*>',
            svg,
        ):
            # Find the matching </g>.  Groups don't nest in our
            # rendered output (each region is one flat <g>), so a
            # naive forward search to the next </g> is correct.
            close_idx = svg.find("</g>", gm.end())
            if close_idx >= 0:
                text_region_ranges.append((gm.start(), close_idx + 4))

        def _in_text_region(pos: int) -> bool:
            return any(s <= pos < e for s, e in text_region_ranges)

        # Recompute text boxes (similar to out-of-bounds above).
        text_boxes: list[tuple[float, float, float, float, str]] = []
        for m in re.finditer(r'<text\b([^>]*)>([^<]*)', svg):
            if _in_text_region(m.start()):
                # Deterministically-positioned text — skip.
                continue
            attrs = _attrs(m.group(1) + ">")
            try:
                tx = float(attrs.get("x", "")); ty = float(attrs.get("y", ""))
            except ValueError:
                continue
            content = m.group(2).strip()
            if not content or len(content) < 4:
                # Single-char labels (vertex names) live INSIDE
                # diagram elements legitimately; skip them.
                continue
            try:
                fs = float(attrs.get("font-size", "16").rstrip("pxptem"))
            except ValueError:
                fs = 16.0
            anchor = (attrs.get("text-anchor") or "start").lower()
            # 0.6 (was 0.55) is a more conservative average glyph width
            # for variable-width fonts with Unicode subscripts and
            # parentheses; 0.55 was letting 50-character formulas pass
            # that actually overflow once rendered.
            est_w = len(content) * fs * 0.6
            x_left = (tx - est_w / 2 if anchor == "middle"
                      else tx - est_w if anchor == "end"
                      else tx)
            text_boxes.append((x_left, ty - fs, est_w, fs * 1.2, content))

        def _overlap_area(
            a: tuple[float, float, float, float],
            b: tuple[float, float, float, float],
        ) -> float:
            ax, ay, aw, ah = a
            bx, by, bw, bh = b
            ix0 = max(ax, bx); ix1 = min(ax + aw, bx + bw)
            iy0 = max(ay, by); iy1 = min(ay + ah, by + bh)
            if ix1 <= ix0 or iy1 <= iy0:
                return 0.0
            return (ix1 - ix0) * (iy1 - iy0)

        overlaps: list[str] = []
        for tx, ty, tw, th, content in text_boxes:
            text_area = max(1.0, tw * th)
            for dx, dy, dw, dh, did in diagram_boxes:
                ov = _overlap_area((tx, ty, tw, th), (dx, dy, dw, dh))
                # Tightened from 0.5 → 0.25: edge-grazing overlap is
                # still a readability failure for the learner; we
                # caught real bugs (label-on-trapezoid-edge) that the
                # 0.5 threshold let slip through.
                if ov / text_area >= 0.25:
                    snippet = content[:24] + ("…" if len(content) > 24 else "")
                    target = f"box id='{did}'" if did else "an unlabelled box"
                    overlaps.append(f"caption {snippet!r} overlaps {target}")
                    break
        if overlaps:
            sample = "; ".join(overlaps[:5])
            more = (f" (and {len(overlaps) - 5} more)"
                    if len(overlaps) > 5 else "")
            issues.append(
                "caption_overlaps_diagram: " + str(len(overlaps)) +
                " caption(s) overlap a diagram element: " + sample + more +
                ".  Move each offending caption to the figure's margin "
                "(top band y<60, bottom band y>" + str(int(vb_h - 80)) +
                ", or right column x>" + str(int(vb_w * 0.7)) + ") so "
                "the diagram region stays readable."
            )

        # 5a. Curve-through-text-block: a path/polyline whose bounding
        # box covers a BLOCK of text (>= 4 distinct text elements) —
        # the "decorative parabola drawn straight through the
        # explanation" failure.  The >=4 threshold keeps a normal
        # plotted curve with a couple of nearby labels from tripping
        # this; only a genuine text block being crossed is flagged.
        curve_boxes: list[tuple[float, float, float, float]] = []
        for m in re.finditer(
                r'<(?:path|polyline)\b[^>]*?'
                r'\b(?:d|points)\s*=\s*["\']([^"\']*)', svg):
            nums = re.findall(r"-?\d[\d.]*", m.group(1))
            xs = [float(nums[i]) for i in range(0, len(nums) - 1, 2)]
            ys = [float(nums[i]) for i in range(1, len(nums), 2)]
            if len(xs) >= 2 and len(ys) >= 2:
                curve_boxes.append((min(xs), min(ys),
                                    max(xs) - min(xs),
                                    max(ys) - min(ys)))
        for cbx in curve_boxes:
            hit = sum(
                1 for tb in text_boxes
                if _overlap_area((tb[0], tb[1], tb[2], tb[3]), cbx)
                >= 0.25 * max(1.0, tb[2] * tb[3]))
            if hit >= 4:
                issues.append(
                    "text_block_over_curve: a curve/path is drawn "
                    f"across a block of {hit} text elements, so the "
                    "explanation text and the curve overlap and are "
                    "both hard to read.  Put the prose in a clear "
                    "margin column clear of the curve, or drop the "
                    "curve if it is decorative.")
                break

        # 5b. Text-text overlap.  Pairwise check of every <text>'s
        # estimated bbox against every other's — flag the pair when
        # they share >= 20% of the smaller box's area.  Catches the
        # "two labels stacked at the same y" failure that the LLM
        # produces when it sets multiple <text> with the same y in
        # different x-anchored positions and the lengths collide.
        # Pre-existing reflow_overlapping_text resolves this for
        # top-level text but does NOT touch text inside <g>; this
        # critic surfaces residual overlaps so the LLM rewrites them
        # at the source rather than relying on post-processing.
        tt_overlaps: list[str] = []
        for i in range(len(text_boxes)):
            ax, ay, aw, ah, a_content = text_boxes[i]
            for j in range(i + 1, len(text_boxes)):
                bx, by, bw, bh, b_content = text_boxes[j]
                ov = _overlap_area(
                    (ax, ay, aw, ah), (bx, by, bw, bh),
                )
                if ov <= 0:
                    continue
                smaller_area = max(1.0, min(aw * ah, bw * bh))
                if ov / smaller_area >= 0.20:
                    a_snip = a_content[:20] + ("…" if len(a_content) > 20 else "")
                    b_snip = b_content[:20] + ("…" if len(b_content) > 20 else "")
                    tt_overlaps.append(
                        f"{a_snip!r} collides with {b_snip!r}"
                    )
                    break  # one collision per A is enough
            if len(tt_overlaps) >= 10:
                break
        if tt_overlaps:
            sample = "; ".join(tt_overlaps[:6])
            more = (f" (and {len(tt_overlaps) - 6} more)"
                    if len(tt_overlaps) > 6 else "")
            dense = ""
            if len(tt_overlaps) >= 5:
                # Many simultaneous collisions mean the figure is
                # genuinely over-packed: re-stacking inside the same
                # box cannot fix it.  Tell the model to enlarge the
                # canvas and spread elements out.
                dense = (
                    "  This figure is OVER-PACKED — there are too many "
                    "elements for the canvas.  Do NOT just nudge "
                    "labels: ENLARGE the viewBox to at least "
                    + str(int(vb_w * 1.6)) + "×"
                    + str(int(vb_h * 1.6)) +
                    ", give every element its own clear region, and "
                    "increase spacing everywhere.  Prefer a clean grid "
                    "or column layout over free placement."
                )
            issues.append(
                "text_text_overlap: " + str(len(tt_overlaps)) +
                " pair(s) of <text> elements overlap in pixel space: "
                + sample + more +
                ".  Two text strings should never share pixel area — "
                "the learner cannot read either.  Fix by re-stacking "
                "the offending labels on different y values "
                "(min spacing 1.4 × font-size apart), OR by moving "
                "one to a different column (x ≥ "
                + str(int(vb_w * 0.65)) +
                "), OR by removing a redundant duplicate.  Long "
                "formulas must be BROKEN into multiple <text> rows "
                "on stacked y values, not laid out side-by-side at "
                "the same y." + dense
            )

    # 4z. Micro-figure check — when the viewBox is normal-sized (>= 400
    # wide), any primary <circle>/<polygon>/<rect> with a dimension
    # under 14 viewBox units is almost certainly the model literally
    # using the user's prompt numbers as SVG coords ('r=5' rendered as
    # a 5-pixel circle).  Threshold 14 catches r<=12 (the trapezoid
    # bug had r=5) but lets typical vertex-node radii (r=15-25) pass.
    # Skip entirely when no viewBox is set (test fixtures, mini SVGs).
    # Strip <text> blocks so we don't count text content as visual
    # below.  Always computed, used by both micro-figure and no-shape
    # checks.
    svg_no_text = re.sub(r'<text\b[^>]*>.*?</text>', '', svg, flags=re.S)
    svg_no_text = re.sub(r'<tspan\b[^>]*>.*?</tspan>', '', svg_no_text, flags=re.S)
    micro_shapes: list[str] = []
    if vb_w >= 400 and vb_h >= 300:
        primary_shape_re = re.compile(
            r'<(?P<tag>circle|ellipse|polygon|rect|path)\b'
            r'([^>]*\bid\s*=\s*["\'][^"\']+["\'][^>]*)/?>',
            re.S,
        )
        for m in primary_shape_re.finditer(svg_no_text):
            tag = m.group("tag")
            attrs = m.group(2)
            if tag == "circle":
                rm = re.search(r"\br\s*=\s*['\"]([0-9.]+)['\"]", attrs)
                if rm and float(rm.group(1)) < 14:
                    micro_shapes.append(
                        f"<circle r='{rm.group(1)}'> — well under "
                        "readable size"
                    )
            elif tag == "ellipse":
                rxm = re.search(r"\brx\s*=\s*['\"]([0-9.]+)['\"]", attrs)
                rym = re.search(r"\bry\s*=\s*['\"]([0-9.]+)['\"]", attrs)
                rx = float(rxm.group(1)) if rxm else 9999
                ry = float(rym.group(1)) if rym else 9999
                if rx < 14 or ry < 14:
                    micro_shapes.append(
                        f"<ellipse rx='{rx}' ry='{ry}'> — under 14 vb units"
                    )
            elif tag == "rect":
                wm = re.search(r"\bwidth\s*=\s*['\"]([0-9.]+)['\"]", attrs)
                hm = re.search(r"\bheight\s*=\s*['\"]([0-9.]+)['\"]", attrs)
                w = float(wm.group(1)) if wm else 9999
                h = float(hm.group(1)) if hm else 9999
                if w < 14 or h < 14:
                    micro_shapes.append(
                        f"<rect w='{w}' h='{h}'> — under 14 vb units"
                    )
            elif tag == "polygon":
                pts_m = re.search(r"points\s*=\s*['\"]([^'\"]+)['\"]", attrs)
                if pts_m:
                    coords = [float(c) for c in
                              re.findall(r"-?[0-9.]+", pts_m.group(1))]
                    if len(coords) >= 4:
                        xs = coords[0::2]; ys = coords[1::2]
                        w = max(xs) - min(xs); h = max(ys) - min(ys)
                        if w < 30 or h < 30:
                            micro_shapes.append(
                                f"<polygon> bbox {w:.0f}×{h:.0f} — too small"
                            )

    if micro_shapes:
        sample = "; ".join(micro_shapes[:4])
        more = (f" (and {len(micro_shapes) - 4} more)"
                if len(micro_shapes) > 4 else "")
        issues.append(
            "micro_figure: " + str(len(micro_shapes)) +
            " primary geometric primitive(s) are rendered at "
            "near-invisible scale — they are likely using the user's "
            f"prompt numbers as viewBox coordinates: {sample}{more}.  "
            "User-supplied values like 'r = 5', 'base = 8', "
            "'side = 3' are SEMANTIC labels for <text>, NOT viewBox "
            "coords.  Rescale: a primary circle radius should be "
            "150-250 px; a polygon/triangle/trapezoid bbox should be "
            "at least 300x200 px; a rect at least 60x60 px.  The "
            "label retains the original numeric value via a "
            "<text>r = 5</text>."
        )

    # 4y. No-shape check — a figure that has only <text> elements and
    # no geometric primitives is a textbook page, not a diagram.  Only
    # flag on real-sized viewBoxes (>= 400 wide) AND when there are
    # several text labels — a 1-2-label SVG might be a tiny callout or
    # a test fixture, not a real diagram-replacement-by-text failure.
    if vb_w >= 400 and vb_h >= 300:
        shape_re = re.compile(
            r'<(circle|ellipse|polygon|rect|line|path|polyline)\b', re.I)
        shape_count = len(shape_re.findall(svg_no_text))
        text_count = len(re.findall(r'<text\b', svg))
        if shape_count == 0 and text_count >= 3:
            issues.append(
                "no_geometric_primitive: the SVG contains "
                f"{text_count} <text> labels but ZERO geometric "
                "primitives (no <circle>, <polygon>, <line>, <path>, "
                "etc.). A figure for a visual topic must draw the "
                "subject, not just transcribe its formula. Add at "
                "least one shape that depicts the concept (circle "
                "for a circle problem, polygon for area-of-a-polygon "
                "problems, axes + curve for function plots, etc.) "
                "sized to fill 50-80% of the viewBox."
            )

    # 5. Named-quantity-not-shown.  When narration names a geometric
    # quantity with an explicit variable letter ('height h', 'base b₁',
    # 'angle θ'), that variable letter MUST appear as the content of
    # some <text> element in the SVG.  Without this, the learner hears
    # about a measurement that isn't drawn on the page — exactly the
    # 'height mentioned but not shown' failure the user reported.
    _QUANTITY_WORDS = (
        "height|base|radius|side|angle|diameter|hypotenuse|altitude|"
        "apothem|leg|width|depth|chord|arc|segment|sector"
    )
    named_quantity_re = re.compile(
        r"\b(?:the\s+|with\s+|and\s+|its\s+|of\s+)?"
        r"(?P<quantity>" + _QUANTITY_WORDS + r")"
        r"\s+"
        r"(?:(?:is|=|equals?|of\s+length\s+|of\s+measure\s+|"
        r"of\s+magnitude\s+|called|denoted(?:\s+by)?|labeled|labelled)\s+)?"
        # Variable: a single letter (Latin or Greek) optionally followed
        # by a subscript.  Accept Unicode subscript chars, LaTeX-style
        # _digit, or a plain trailing digit (b1, b2).
        r"(?P<var>[a-zA-Zα-ωΑ-Ω])"
        r"(?:[₀-₉]+|_\d+|_\{[^}]*\}|\d+)?"
        r"(?=[\s.,;:!?)(]|$)",
        re.IGNORECASE,
    )
    # Common English words that the regex would otherwise capture as a
    # one-letter variable.  Skip when the word after looks like more
    # English text (i.e. the 'letter' is really a stop-word).
    _ENGLISH_STOPWORDS = {
        "a", "i", "o", "u",  # bare article / pronoun / interjection
        "to", "of", "in", "on", "by", "at", "as", "is", "be",
        "or", "an", "we", "us", "he", "it", "so", "do", "no",
        "if", "up",
    }
    # Build the set of text tokens that exist in the SVG.  We split
    # each <text>/<tspan> content on whitespace and common punctuation
    # so a label like 'h = 4' contributes both 'h' and '4'.
    svg_text_tokens: set[str] = set()
    svg_text_starts: set[str] = set()
    for m in re.finditer(
        r'<(?:text|tspan)\b[^>]*>([^<]*)</(?:text|tspan)>', svg, re.S,
    ):
        content = m.group(1).strip()
        if not content:
            continue
        # Strip Unicode subscript digits and underscores so 'h₁' and
        # 'h_1' both contribute the bare 'h' token too.
        bare = re.sub(r'[₀-₉]+|_\d+|_\{[^}]*\}', '', content)
        for tok in re.split(r'[\s=,()/\[\]:]+', bare):
            if tok:
                svg_text_tokens.add(tok)
        # Track first-character starts so we can also accept content
        # like '4 units' (the leading char might be the value, not the
        # variable — usually still fine because the variable label
        # 'h' lives elsewhere).
        svg_text_starts.add(bare[:1])

    unshown: list[tuple[int, str, str]] = []  # (phrase_idx, quantity, var)
    for i, phrase in enumerate(narration or []):
        speak = (phrase or {}).get("speak", "") or ""
        if not speak:
            continue
        for m in named_quantity_re.finditer(speak):
            var = (m.group("var") or "").strip()
            quantity = (m.group("quantity") or "").lower()
            if not var:
                continue
            # Skip when the captured 'variable' is really an English
            # filler word followed by more English (e.g. 'side a
            # triangle' captures 'a' but it's an article).  We allow
            # 'side a' at end-of-phrase or before punctuation — that
            # is a legitimate math label.
            if var.lower() in _ENGLISH_STOPWORDS:
                tail = speak[m.end():].lstrip()
                if tail and re.match(r"[A-Za-z]{2,}", tail):
                    continue
            # Variable shown in SVG?  Accept exact-token match OR
            # appearance as the first character of any text label
            # (covers 'h = 4' style labels).
            if var in svg_text_tokens or var in svg_text_starts:
                continue
            # Accept the quantity word itself as label (e.g. <text>
            # 'height'</text> with no separate 'h' label).
            if quantity in {t.lower() for t in svg_text_tokens}:
                continue
            unshown.append((i, quantity, var))

    if unshown:
        sample = "; ".join(
            f"phrase[{i}] names '{q} {v}'" for i, q, v in unshown[:5]
        )
        more = (f" (and {len(unshown) - 5} more)"
                if len(unshown) > 5 else "")
        issues.append(
            "named_quantity_not_shown: " + str(len(unshown)) +
            " narration phrase(s) name a geometric quantity with an "
            "explicit variable letter but the SVG has no <text> label "
            f"for that letter: {sample}{more}. Whenever narration says "
            "'height h', 'base b₁', 'radius r', 'angle θ', etc., the "
            "figure MUST draw that quantity (dashed perpendicular for "
            "a height, arc for an angle, line segment for a side) AND "
            "label it with the same variable letter as a <text> "
            "element. Otherwise the learner hears about a measurement "
            "that isn't on the page. Fix: add the missing labelled "
            "element to the SVG, or remove the unsupported mention "
            "from the narration."
        )

    # 7. Topic-keyword required primitive — when the prompt or narration
    # explicitly names a topic that requires a specific visual element
    # (circle, function curve, two overlapping sets, graph edges), the
    # SVG must include that element at readable size.  Catches gpt-4o
    # failures where the figure is built ALMOST right but the central
    # subject is missing: 'unit circle' question with no <circle>,
    # 'derivative as tangent line' with no tangent <line>, 'integral as
    # area under the curve' with no curve <path>, 'overlapping sets A
    # and B' with no Venn ellipses.  Only fire on real-sized viewBoxes
    # so test fixtures aren't flagged.
    if vb_w >= 400 and vb_h >= 300:
        prompt_lower = (user_prompt or "").lower()
        narr_lower = " ".join(
            ((p or {}).get("speak", "") or "") for p in (narration or [])
        ).lower()
        combined = prompt_lower + " || " + narr_lower

        circle_radii_all: list[float] = []
        for m in re.finditer(
            r'<circle\b[^>]*\br\s*=\s*["\']?\s*([0-9.]+)', svg_no_text,
        ):
            try:
                circle_radii_all.append(float(m.group(1)))
            except ValueError:
                continue
        big_circles = [r for r in circle_radii_all if r >= 60]
        big_ellipses = len(re.findall(
            r'<ellipse\b[^>]*\brx\s*=\s*["\']?\s*[6-9][0-9]', svg_no_text,
        )) + len(re.findall(
            r'<ellipse\b[^>]*\brx\s*=\s*["\']?\s*[1-9][0-9]{2,}', svg_no_text,
        ))
        path_decls = re.findall(
            r'<path\b[^>]*\bd\s*=\s*["\']([^"\']+)["\']', svg_no_text,
        )
        # A "curve path" has multiple Bezier or many line-to commands so
        # we don't count a single 'M 0 0 L 100 100' as a function curve.
        curve_paths = [
            d for d in path_decls
            if len(re.findall(r'[CQ]', d)) >= 1
            or len(re.findall(r'[LMlm]', d)) >= 6
        ]
        all_line_count = len(re.findall(r'<line\b', svg_no_text))

        topic_misses: list[str] = []

        # 7a. Circle-required topics.  Match phrases that pin the topic
        # ON the circle as primary subject; exclude 'circumference of
        # a sphere' style false positives via context check.
        circle_topic = bool(re.search(
            r'\bunit\s+circle\b|\bthe\s+circle\b|\ba\s+circle\b|'
            r'\bcircle\s+(?:of|with)\s+radius\b|\bcircle\s+for\b|'
            r'\bsin\s*[θ\w]?\s+and\s+cos\s*[θ\w]?\b|'
            r'\barc\s+length\b|\bsector\b|\bchord\b',
            combined,
        ))
        if circle_topic and not big_circles:
            topic_misses.append(
                "circle_topic_no_big_circle: prompt or narration names a "
                "circle / unit circle / arc / sector / chord as the "
                "subject, but the SVG contains no <circle> with radius "
                ">= 60 vb units. Add a <circle cx=… cy=… r='180'/> at "
                "the diagram centre BEFORE drawing any chords, radii, "
                "or angle markers on it."
            )

        # 7b. Function plot / curve topics.  When the user asks for
        # derivative, integral, area under curve, or names a function
        # f(x)=..., the SVG must contain a curve <path>.
        curve_topic = bool(re.search(
            r'\bderivative\b|\bintegral\b|\barea\s+under\s+the\s+curve\b|'
            r'\bf\s*\(\s*x\s*\)\s*=|\bg\s*\(\s*x\s*\)\s*=|'
            r'\bplot\s+(?:of|the)\b|\bgraph\s+(?:of|the)\s+function\b|'
            r'\bthe\s+parabola\b|\bthe\s+curve\b|\bthe\s+function\b',
            combined,
        ))
        if curve_topic and not curve_paths:
            topic_misses.append(
                "function_plot_no_curve: prompt or narration names a "
                "function / derivative / integral / parabola / curve, "
                "but the SVG has no <path d='…'> that traces a curve "
                "(needs C/Q Bezier commands OR >= 6 polyline points). "
                "Draw the curve as <path d='M x0 y0 L x1 y1 L x2 y2 …'> "
                "with at least 10 samples across the domain, in addition "
                "to axes lines."
            )

        # 7c. Tangent-line topic.  Needs BOTH a curve AND a separate
        # straight <line> for the tangent.  If a curve is present but
        # no tangent line, flag specifically.
        tangent_topic = bool(re.search(
            r'\btangent\s+line\b|\btangent\s+at\s+x\b|'
            r'\bslope\s+of\s+the\s+tangent\b',
            combined,
        ))
        if tangent_topic and curve_paths and all_line_count < 3:
            topic_misses.append(
                "tangent_missing: narration names a tangent line at a "
                "specific point but the SVG has no separate <line> for "
                "the tangent (only axes and the curve are present). Add "
                "<line x1=… y1=… x2=… y2=…/> through the tangent point, "
                "with slope = f'(x₀), spanning ~200-300 vb units."
            )

        # 7d. Overlapping-sets topic.  Set A and Set B (or Venn) need
        # at least two large circular/elliptical shapes.
        set_topic = bool(re.search(
            r'\bset\s+a\b.*\bset\s+b\b|'
            r'\boverlapping\s+sets\b|\bvenn\b|'
            r'\ba\s*[∩∪\\]\s*b\b|'
            r'\bset\s+difference\b|\bset\s+intersection\b|'
            r'\bset\s+union\b',
            combined, re.S,
        ))
        venn_shapes = len([r for r in circle_radii_all if r >= 80]) + big_ellipses
        if set_topic and venn_shapes < 2:
            topic_misses.append(
                "set_topic_no_venn: prompt or narration names two sets "
                "(A and B, set difference, union, intersection, Venn) "
                "but the SVG has fewer than 2 large circular/elliptical "
                "shapes. Draw set A and set B as two overlapping "
                "<ellipse rx='180' ry='130'/> shapes with light fill + "
                "stroke, positioned so they overlap, THEN place element "
                "labels inside their regions."
            )

        # 7e. Graph / tree-construction topics with no edges.  When the
        # prompt asks to 'show', 'construct', 'enumerate', 'reduce' a
        # graph, tree, or gadget structure, the SVG must include
        # multiple <line>/<path> edges.
        graph_topic = bool(re.search(
            r'\bgraph\b|\btree\b|\bgadget\b|\bclause\b.*\bvariable\b|'
            r'\bvertex\s+cover\b|\bspanning\s+tree\b|\bedges?\b',
            combined,
        ))
        graph_cue = bool(re.search(
            r'\bshow\b|\billustrate\b|\bconstruct\b|\benumerate\b|'
            r'\breduce\b|\bvisuali[sz]e\b|\bdraw\b',
            combined,
        ))
        # rough edge count: <line> + curve_path count
        edge_estimate = all_line_count + len(curve_paths)
        if graph_topic and graph_cue and edge_estimate < 3:
            topic_misses.append(
                "graph_topic_no_edges: prompt asks to draw / construct / "
                "reduce a graph or tree, but the SVG has fewer than 3 "
                "<line>/<path> edges. Draw vertices as <circle r='18'/> "
                "and each edge as <line x1=… y1=… x2=… y2=…/>; for a "
                "3SAT → vertex-cover reduction draw each variable "
                "gadget (2 vertices joined) and each clause gadget (3 "
                "vertices in a triangle) AND the cross-edges between "
                "them."
            )

        # 7f. Algorithm trace with no content — Euclidean algorithm,
        # long-division, etc., need step rows with actual numbers, not
        # empty horizontal rules.  Detect by 'algorithm' or 'gcd' +
        # very low text-with-digits count.
        algo_topic = bool(re.search(
            r'\beuclidean\s+algorithm\b|\bgcd\b|\blong\s+division\b|'
            r'\bbisection\b|\bnewton\'?s?\s+method\b',
            combined,
        ))
        if algo_topic:
            digit_texts = len(re.findall(
                r'<text\b[^>]*>[^<]*\d[^<]*</text>', svg,
            ))
            if digit_texts < 3:
                topic_misses.append(
                    "algorithm_trace_no_steps: prompt names an "
                    "iterative algorithm (Euclidean / gcd / long "
                    "division / Newton) but the SVG has fewer than 3 "
                    "<text> labels containing digits. Render each "
                    "iteration as one <text> row showing the equation "
                    "for that step (e.g. for gcd(252,105): '252 = "
                    "2·105 + 42', '105 = 2·42 + 21', '42 = 2·21 + 0', "
                    "'gcd = 21')."
                )

        if topic_misses:
            issues.append(
                "missing_required_primitive: " +
                str(len(topic_misses)) +
                " topic-specific primitive(s) missing — " +
                " | ".join(topic_misses) +
                " The topic-required primitive is the most important "
                "thing on the figure; without it the learner sees a "
                "page that does not match what the narration says."
            )

    issues.extend(_verify_arithmetic(svg, narration))

    # Shape-zone-violation check: count shape primitives whose
    # bounding box extends outside SHAPE_ZONE.  This catches the
    # "LLM drew clause boxes that bled into the right-column text
    # area" failure on proof prompts.  Skips primitives inside any
    # <g class="text-region-*"> group (those are our deterministic
    # text content, not LLM-emitted shapes).
    #
    # GATED: only applies when the figure is using the new zone
    # architecture (at least one text_blocks region was injected).
    # Figures that use the full canvas (deterministic templates,
    # legacy LLM output, simple geometry) are unchanged — only when
    # the LLM has opted into text_blocks does the shape-zone
    # boundary become an enforceable contract.
    try:
        sx0, sy0, sx1, sy1 = SHAPE_ZONE
        # Re-find text-region byte ranges (cheap regex).
        _shape_skip: list[tuple[int, int]] = []
        for gm in re.finditer(
            r'<g\b[^>]*class\s*=\s*["\'][^"\']*text-region-[^"\']*["\'][^>]*>',
            svg,
        ):
            ci = svg.find("</g>", gm.end())
            if ci >= 0:
                _shape_skip.append((gm.start(), ci + 4))


        def _in_shape_skip(p: int) -> bool:
            return any(a <= p < b for a, b in _shape_skip)

        # Gate: only enforce shape-zone boundaries when the figure
        # is using the new zone architecture (text_blocks were
        # injected, producing at least one text-region group).
        # Figures using the full canvas (deterministic templates,
        # legacy LLM output, simple geometry) are exempt.
        violations: list[str] = []
        if not _shape_skip:
            # Skip the rest of the check; `if violations:` below
            # will be False so nothing is appended to `issues`.
            raise _ShapeCheckSkip()

        def _flag(kind: str, x_min: float, y_min: float,
                  x_max: float, y_max: float) -> None:
            # Allow a small margin (5 units) — bbox estimates aren't
            # exact (e.g. stroke-width contributes).
            tol = 5.0
            if (x_max > sx1 + tol or x_min < sx0 - tol
                    or y_max > sy1 + tol or y_min < sy0 - tol):
                violations.append(
                    f"{kind} bbox [{x_min:.0f},{y_min:.0f},"
                    f"{x_max:.0f},{y_max:.0f}] extends past SHAPE_ZONE "
                    f"[{int(sx0)},{int(sy0)},{int(sx1)},{int(sy1)}]"
                )

        # <rect x y width height>
        for m in re.finditer(r'<rect\b[^>]*?/?>', svg):
            if _in_shape_skip(m.start()):
                continue
            a = _attrs(m.group(0))
            try:
                x = float(a.get("x", "0"))
                y = float(a.get("y", "0"))
                w = float(a.get("width", "0"))
                h = float(a.get("height", "0"))
            except ValueError:
                continue
            if w <= 0 or h <= 0:
                continue
            _flag("rect", x, y, x + w, y + h)
        # <circle cx cy r>
        for m in re.finditer(r'<circle\b[^>]*?/?>', svg):
            if _in_shape_skip(m.start()):
                continue
            a = _attrs(m.group(0))
            try:
                cx = float(a.get("cx", "0"))
                cy = float(a.get("cy", "0"))
                r = float(a.get("r", "0"))
            except ValueError:
                continue
            if r <= 0:
                continue
            _flag("circle", cx - r, cy - r, cx + r, cy + r)
        # <ellipse cx cy rx ry>
        for m in re.finditer(r'<ellipse\b[^>]*?/?>', svg):
            if _in_shape_skip(m.start()):
                continue
            a = _attrs(m.group(0))
            try:
                cx = float(a.get("cx", "0"))
                cy = float(a.get("cy", "0"))
                rx = float(a.get("rx", "0"))
                ry = float(a.get("ry", "0"))
            except ValueError:
                continue
            if rx <= 0 or ry <= 0:
                continue
            _flag("ellipse", cx - rx, cy - ry, cx + rx, cy + ry)

        if violations:
            # Cap reported violations at 5 so the critique stays
            # readable.  Most prompts that violate the zone violate
            # it for the same family of shapes (a 3-SAT proof with
            # 8 clause rects, all past the right edge).
            sample = "; ".join(violations[:5])
            more = (f" (and {len(violations) - 5} more)"
                    if len(violations) > 5 else "")
            issues.append(
                "shape_outside_zone: " + str(len(violations)) +
                " shape primitive(s) extend past the SHAPE_ZONE — " +
                sample + more +
                ".  The SHAPE_ZONE is x in [" + str(int(sx0)) + ", " +
                str(int(sx1)) + "], y in [" + str(int(sy0)) + ", " +
                str(int(sy1)) + "].  All shape primitives MUST fit "
                "inside this box; anything outside collides visually "
                "with the text regions (left-column / right-column / "
                "title / bottom-band).  Shrink the offending shapes "
                "or move them; use the text_blocks regions for "
                "content that doesn't fit as shapes."
            )
    except _ShapeCheckSkip:
        # Figure isn't using zone architecture (no text-region groups);
        # no shape-zone violation enforcement.
        pass
    except Exception:  # noqa: BLE001
        # Critic must never raise — silently swallow any unexpected
        # parsing edge case.
        pass

    # ── Crowded iterate markers ──────────────────────────────────
    # If three-or-more <circle> dots are packed into a tiny cluster
    # (typical Newton-method convergence) without the figure visibly
    # zooming into the cluster, none of them will be readable.  Flag
    # so the retry emits a zoomed figure (or the FDL TangentAt path
    # picks up its built-in cluster zoom).
    #
    # User-reported regression on 2026-05-31: x_0=1.5, x_1=1.417,
    # x_2=1.414 rendered as three dots within ~5 pixels of each
    # other; the iterate labels stacked vertically on top of the
    # pile and were unreadable.
    try:
        circle_iter = re.finditer(
            r"<circle\b[^>]*>", svg, flags=re.IGNORECASE
        )
        circles_xy: list[tuple[float, float, float]] = []
        for m in circle_iter:
            a = _attrs(m.group(0))
            try:
                cx = float(a.get("cx", "0"))
                cy = float(a.get("cy", "0"))
                rr = float(a.get("r", "0") or "0")
            except ValueError:
                continue
            # Ignore decoration / arrow heads — only count visible
            # dot-shaped markers.
            if rr <= 0:
                continue
            circles_xy.append((cx, cy, rr))

        # Pairwise close: <= 20 px apart center-to-center.
        if len(circles_xy) >= 3:
            close_pairs = 0
            for i in range(len(circles_xy)):
                for j in range(i + 1, len(circles_xy)):
                    cx1, cy1, _ = circles_xy[i]
                    cx2, cy2, _ = circles_xy[j]
                    d2 = (cx1 - cx2) ** 2 + (cy1 - cy2) ** 2
                    if d2 <= 20.0 * 20.0:
                        close_pairs += 1
            # Three+ dots, two+ close pairs -> at least three dots
            # are packed.
            if close_pairs >= 2:
                issues.append(
                    "crowded_markers: " + str(len(circles_xy)) +
                    " marker dot(s) on the figure, with " +
                    str(close_pairs) + " pair(s) less than 20 px "
                    "apart.  Stacking the iterates on top of each "
                    "other makes them unreadable.  When successive "
                    "iterates (x_0, x_1, x_2, ...) converge to a "
                    "tight cluster, ZOOM the plot window to the "
                    "cluster's range + ~20% margin so each dot is "
                    "drawn at a visibly distinct screen position. "
                    "Re-draw with: xmin = min(iterates) - 0.20*W, "
                    "xmax = max(iterates) + 0.20*W where W is the "
                    "cluster width.  Each iterate label must sit "
                    "next to its dot, not stacked vertically."
                )
    except Exception:  # noqa: BLE001
        pass

    # ── Narration mentions 'tangent' but no concrete function ────
    # When narration says 'tangent line' / 'tangent to the curve'
    # and the user's PROMPT supplies a function (e.g. f(x) = x^2 -
    # 2 with x_0 = 1.5), the deterministic newton_method template
    # OR the FDL TangentAt primitive draws a real SymPy-slope
    # tangent.  When the prompt is vague ('Newton's method
    # approximates roots using tangent lines') and the figure path
    # falls through to LLM-SVG, the model invents tangent-shaped
    # lines that aren't actually tangent — user-reported on
    # 2026-05-31.  Flag this so the retry either pins concrete
    # f / x_0 OR drops the 'tangent' word from narration.
    try:
        narration_text = " ".join(
            (p.get("text") or p.get("speak") or "")
            for p in (narration or [])
        ).lower()
        if "tangent" in narration_text:
            up = (user_prompt or "").lower()
            # Heuristic: a usable function statement includes both
            # an `f(x) = ...` style declaration AND an `x =` /
            # `x_0 =` starting value.  We're permissive — any
            # `f(x)`, `f =`, or explicit polynomial like `x^2 - 2`
            # counts as a function statement.
            has_f = bool(re.search(
                r"f\s*\(\s*x\s*\)\s*=|f\s*=|"
                r"\bx\s*\^\s*\d|\bx\s*\*\*\s*\d|"
                r"\bsqrt\s*\(|\bsin\s*\(|\bcos\s*\(|\bexp\s*\(",
                up,
            ))
            has_x0 = bool(re.search(
                r"\bx[\s_]*0\s*=|\bstart(?:ing)?\b|\binitial\b|"
                r"\bfrom\s+x\s*=|\bguess\b",
                up,
            ))
            if not (has_f and has_x0):
                issues.append(
                    "tangent_without_function_spec: narration "
                    "promises tangent lines but the user's prompt "
                    "did not pin a specific function f and starting "
                    "value x_0.  The figure LLM drew generic lines "
                    "that may not be tangent to the curve.  FIX: "
                    "in the next attempt, pick concrete defaults "
                    "(e.g. f(x) = x^2 - 2, x_0 = 1.5) and DRAW the "
                    "tangent at each iterate x_n as a line through "
                    "(x_n, f(x_n)) with slope f'(x_n).  The tangent "
                    "extends to where it crosses the x-axis at "
                    "x_{n+1} = x_n - f(x_n)/f'(x_n).  Without "
                    "concrete f and x_0 the tangents cannot be "
                    "drawn correctly."
                )
    except Exception:  # noqa: BLE001
        pass

    return issues


_NARROW_EDIT_PATTERNS: tuple[str, ...] = (
    # explicit colour change
    r"\bchange\s+(?:the\s+)?\w+\s+(?:colou?r\s+)?to\s+\w+",
    r"\bcolou?r\s+(?:the\s+|it\s+)\w+\s+\w+",
    r"\bmake\s+(?:the\s+|it\s+)\w+",
    # add / remove / highlight a SINGLE element
    r"\badd\s+(?:a\s+|an\s+|the\s+)?\w+",
    r"\bremove\s+(?:the\s+|that\s+)?\w+",
    r"\bdelete\s+(?:the\s+|that\s+)?\w+",
    r"\bhighlight\s+(?:the\s+|that\s+)?\w+",
    # narrow rename / relabel
    r"\brelabel\s+(?:the\s+)?\w+",
    r"\brename\s+(?:the\s+)?\w+",
    # rotate / move / scale a SINGLE element
    r"\brotate\s+(?:the\s+)?\w+",
    r"\bmove\s+(?:the\s+)?\w+",
    r"\bscale\s+(?:the\s+)?\w+",
)


def is_narrow_targeted_edit(prompt: str) -> bool:
    """True when the user's request reads like a Case A targeted
    edit ('change the curve to red', 'add a label x_3', 'remove the
    green tangent'): a single named element AND a single named
    change.  False for elaboration / complaint / topic-switch and
    anything ambiguous.  Used to gate:
      * whether to skip deterministic templates and run LLM-SVG
        with REFINEMENT MODE byte-for-byte preservation
      * whether to attach the prior SVG XML in _build_user_content
    """
    if not prompt:
        return False
    pl = prompt.lower()
    return any(re.search(p, pl) for p in _NARROW_EDIT_PATTERNS)


_REFINEMENT_CUE_RE = re.compile(
    r"\b("
    r"add|adds|adding|added|"
    r"remove|removes|removing|removed|"
    r"delete|deletes|deleting|deleted|"
    r"change|changes|changing|changed|"
    r"replace|replaces|replacing|replaced|"
    r"highlight|highlights|highlighting|highlighted|"
    r"emphasi[sz]e|emphasi[sz]ed|"
    r"continue|continues|continuing|"
    r"keep\s+going|next\s+step|"
    r"explain|explains|explaining|explained|"
    r"more\s+detail|more\s+on|"
    r"this|that|these|those|"
    r"the\s+figure|the\s+canvas|the\s+diagram|the\s+previous|"
    r"the\s+above|prior|earlier|"
    r"refine|refines|refining|refined|"
    r"clean\s+up|simplify|simplifies|simplifying|"
    r"zoom\s+in|zoom\s+out|"
    r"colour|color|colours|colors|recolou?r|"
    r"label|labels|labelling|labeling|labelled|labeled|relabel|"
    r"annotate|annotates|annotating|"
    r"shrink|shrinks|shrinking|enlarge|enlarges|enlarging|"
    r"move|moves|moving|moved|reposition|"
    r"fix|fixes|fixing|fixed|correct|corrects|correcting|corrected|"
    # complaint / correction phrasings — user is pointing at
    # something in the figure that's wrong.  "These are not tangent
    # lines", "the slope is incorrect", "still wrong", etc.
    r"wrong|incorrect|missing|"
    r"isn'?t|aren'?t|doesn'?t|don'?t|"
    r"should\s+be|should\s+not\s+be|shouldn'?t\s+be|"
    r"still\s+(?:wrong|not|no|missing|doesn'?t)|"
    r"not\s+quite|not\s+right|"
    r"instead\s+of|rather\s+than"
    r")\b",
    re.IGNORECASE,
)


def looks_like_refinement(prompt: str) -> bool:
    """Heuristic: True when the prompt reads like a follow-up that
    refines the previous figure ('add a label', 'highlight C2',
    'change the colour', 'continue'), False when it looks like a
    self-contained new topic ('Compute the integral of f(x) = 2x ...',
    'Apply the Pythagorean theorem ...').

    A multi-turn audit revealed that gpt-4o, when given a prior canvas
    via context_canvases, ALWAYS treats the next turn as refinement and
    overlays the new figure on the old one — even when the user has
    clearly switched topics.  We pre-classify on the server side so an
    unrelated follow-up does NOT trigger REFINEMENT MODE in the prompt.
    """
    text = (prompt or "").strip()
    if not text:
        return False
    # Bare imperative templates ('Compute X', 'Show Y', 'Apply Z',
    # 'Illustrate W') with no anaphora and no refinement cue are
    # almost always topic switches.
    if _REFINEMENT_CUE_RE.search(text):
        return True
    return False


def _build_user_content(
    user_prompt: str,
    context_canvases: list[dict[str, Any]],
) -> Any:
    """Multi-modal user message: the new request, plus PNG snapshots,
    SVG XML, and metadata for every prior canvas the user is
    referencing.

    Critically the SVG is included as RAW XML TEXT (not just as a
    screenshot) so the model can copy-paste unchanged sections verbatim
    when the user asks for a targeted refinement ('change the formula',
    'highlight C2', 'add an arrow from x1 to C3').  Without the XML,
    the model regenerates the figure from scratch every turn.

    When ``context_canvases`` is empty, returns a plain string (cheaper
    and works on text-only models).
    """
    if not context_canvases:
        return user_prompt
    blocks: list[dict[str, Any]] = []
    blocks.append({"type": "text", "text": (
        f"=== REFINEMENT MODE ===\n"
        f"{len(context_canvases)} prior figure(s) are attached below.  "
        f"For each, you'll see (a) the SVG XML, (b) the rendered PNG, "
        f"(c) the prompt that produced it, (d) its narration script.\n\n"
        f"FIRST, CLASSIFY THE NEW REQUEST.  Three cases:\n\n"
        f"  CASE A — NARROW targeted edit.  ONLY when the user names "
        f"  a specific visible element AND a specific change to it: "
        f"  'change the curve colour to red', 'add a label x_3 next "
        f"  to that dot', 'remove the green tangent', 'highlight the "
        f"  intersection in yellow'.  The prior figure is essentially "
        f"  correct; the user wants ONE localised pixel-level tweak. "
        f"  Start from the prior SVG and apply ONLY the requested "
        f"  change.  Preserve every unchanged element BYTE-FOR-BYTE "
        f"  — same ids, coordinates, text.  Do NOT regenerate the "
        f"  layout.\n\n"
        f"  CASE B — Complaint that the prior figure is WRONG "
        f"  ('these are not tangent lines', 'the slope is incorrect', "
        f"  'that's wrong', 'still not right', 'the points overlap', "
        f"  'doesn't look right', 'the labels are crowded', 'the "
        f"  formula is missing').  The prior figure has a math error "
        f"  or layout failure.  DO NOT preserve it byte-for-byte — "
        f"  copying broken pixels keeps them broken.  Treat the "
        f"  prior figure ONLY as context to understand WHAT the user "
        f"  is pointing at; then DRAW A FRESH figure of the SAME "
        f"  CONCEPT, fixing the specific defect the user named. "
        f"  Pick concrete numbers if the original prompt was vague. "
        f"  Recompute every coordinate, every slope, every label "
        f"  position from scratch.\n\n"
        f"  CASE C — Elaboration / 'show more' / 'explain visually' "
        f"  / 'with proper formulas' / 'in more detail' / 'step by "
        f"  step' / 'add a worked example' / 'expand on this' / "
        f"  'more carefully'.  The user wants the SAME CONCEPT shown "
        f"  more completely.  Like Case B: DRAW A FRESH figure, do "
        f"  NOT preserve the prior layout.  The prior canvas is "
        f"  context for topic continuity only.  Recompute every "
        f"  coordinate; add the extra detail the user asked for "
        f"  (more iterates, more labels, an annotated formula box, "
        f"  step captions); do NOT keep both old and new captions "
        f"  stacked — re-emit a single clean layout.\n\n"
        f"DEFAULT TO CASE C when uncertain.  Case A is the narrowest: "
        f"only apply it when the user's request CLEARLY names ONE "
        f"specific change to ONE specific element.  Most follow-up "
        f"requests are Case B or Case C — when in doubt, redraw "
        f"fresh.  Byte-for-byte preservation is correct only for "
        f"single-attribute tweaks ('make it red'); it produces "
        f"overlapping captions and stacked titles for anything else.\n\n"
        f"NARRATION RULE — this is the part most models get wrong, "
        f"please re-read carefully:\n"
        f"  CASE A: the ``narration`` field MUST contain ONLY THE "
        f"  NEW PHRASES that describe THIS turn's change.  Do NOT "
        f"  re-emit any prior phrase verbatim and do NOT prepend the "
        f"  prior narration.  Concretely: if prior narration had 5 "
        f"  phrases and the user asks 'continue with the next step', "
        f"  the new narration must be e.g. 2-4 phrases — only the "
        f"  new step's narration — NOT the original 5 + 3 new = 8.\n"
        f"  CASE B: emit a SHORT new narration (1-3 phrases) that "
        f"  briefly acknowledges the corrected figure ('here is the "
        f"  same construction with the correct tangent slopes', "
        f"  'redrawn with the iterates spread out so they're "
        f"  readable').  Do NOT repeat the whole concept explanation.\n"
        f"  CASE C: emit a FULL narration (5-7 phrases) walking "
        f"  through the elaborated figure — one phrase per step.  "
        f"  The user asked for more detail; deliver it.\n\n"
        f"Only generate a fully new figure (and a fully new narration) "
        f"when the new request is UNRELATED to any attached prior "
        f"figure (e.g. user pivots: 'now show me matrix multiplication')."
    )})
    is_narrow_edit = is_narrow_targeted_edit(user_prompt)

    for i, ctx in enumerate(context_canvases, start=1):
        cid = ctx.get("id", "?")
        prior_prompt = ctx.get("prompt") or "(unknown prompt)"
        prior_svg = ctx.get("svg") or ""
        if is_narrow_edit:
            blocks.append({"type": "text", "text": (
                f"\n—— PRIOR FIGURE {i} (canvas id={cid}) ——\n"
                f"Original prompt: {prior_prompt!r}\n\n"
                f"Its current SVG XML (modify this in place when the "
                f"user is refining it):\n```xml\n{prior_svg}\n```"
            )})
        else:
            # CASE B / CASE C: don't hand the model the SVG XML — it
            # WILL copy text from it byte-for-byte and produce a new
            # figure with the old captions still stacked on top of
            # the new ones.  Show the rendered PNG only; the model
            # understands the prior figure visually and redraws fresh.
            blocks.append({"type": "text", "text": (
                f"\n—— PRIOR FIGURE {i} (canvas id={cid}) ——\n"
                f"Original prompt: {prior_prompt!r}\n\n"
                f"Rendered preview attached below.  The SVG XML is "
                f"INTENTIONALLY WITHHELD because your request looks "
                f"like elaboration / correction (Case B or C above), "
                f"not a single-element targeted edit (Case A).  "
                f"Redraw fresh; do not copy text or coordinates from "
                f"the prior figure — recompute everything."
            )})
        try:
            png = _svg_to_png(ctx["svg"])
            b64 = base64.b64encode(png).decode("ascii")
            blocks.append({"type": "image_url",
                           "image_url": {"url": f"data:image/png;base64,{b64}"}})
        except Exception as exc:  # noqa: BLE001
            blocks.append({"type": "text", "text": f"(could not render preview: {exc})"})
        narration = ctx.get("narration") or []
        if narration:
            # The prior narration is shown for CONTEXT only — so the
            # model knows what the user has already heard and which
            # SVG-element ids prior phrases targeted.  The new turn's
            # ``narration`` field must NOT include any of these
            # phrases.  Only emit phrases that describe the new
            # change.
            blocks.append({"type": "text", "text": (
                "Its prior narration script (FOR REFERENCE ONLY — the "
                "user has already heard this; do NOT include any of "
                "these phrases in your new ``narration`` output):\n"
                + json.dumps(narration, indent=2)
            )})
    blocks.append({"type": "text", "text": (
        f"\n=== NEW REQUEST ===\n{user_prompt}\n\n"
        f"Reminder if this is a refinement: SVG keeps every prior "
        f"element + adds the requested change; ``narration`` contains "
        f"ONLY the NEW phrases for this turn (the user has already "
        f"heard the prior audio).  If this is a brand-new request "
        f"unrelated to the prior figure, ignore the priors and start "
        f"fresh with a complete narration."
    )})
    return blocks
