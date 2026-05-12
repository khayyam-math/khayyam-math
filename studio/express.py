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

import base64
import json
import os
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
                # Mid-\uXXXX escape; just skip the hex chars.  We
                # already emitted '?' as a placeholder when the \u
                # was first seen.  Final JSON parse at the end of
                # streaming recovers the real character.
                self._unicode_left -= 1
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
                    # 4-hex-digit unicode escape.  Emit a placeholder
                    # and skip the 4 hex chars.
                    self._svg.append("?")
                    self._unicode_left = 4
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
                "or computation, end with the conclusion.  10-25 phrases "
                "for a non-trivial figure."
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
    },
    "required": ["svg", "narration", "title"],
}


_EXPRESS_SYSTEM = (
    "You are a math TEACHER illustrating a concept.  The figure must "
    "TEACH the operation, not merely label it.  A reader who has never "
    "seen this concept should be able to learn it from the figure + "
    "narration alone.  Aim for the depth of a Khan Academy / 3Blue1Brown "
    "explainer, rendered as a static SVG.\n"
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
    "saying so plus a one-phrase narration explaining why."
)


# ── Vision review (used between retries) ─────────────────────────────────

_REVIEW_SYSTEM = (
    "You are a pragmatic reviewer of mathematical figures AND the "
    "narration that explains them.  You are given the rendered figure "
    "(as a PNG) and the spoken narration script (as text).  Default to "
    "PASS for visual polish — partial figures, mid-quality labelling, "
    "and missing-but-non-essential captions are PASS.\n"
    "\n"
    "FAIL on these BROKEN-FIGURE problems:\n"
    "  • orphan leader lines pointing to empty canvas\n"
    "  • notation mismatches the user's request (wrong dimensions, "
    "wrong concept)\n"
    "  • main content missing entirely (e.g. 'matrix multiplication' "
    "with no matrices visible at all)\n"
    "  • text overlapping text such that nothing is readable\n"
    "  • wrong topology (3SAT-clique drawn as a tree, etc.)\n"
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
    return (
        f"User asked: {user_prompt!r}\n"
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
    "in parallel.  Your job: write a short, spoken-style PRIMER (3 to 5 "
    "sentences, MAXIMUM 80 words) that introduces the concept and "
    "states the key formula(s) the learner needs to follow the upcoming "
    "figure.\n\n"
    "RULES:\n"
    "  * Plain prose, no headings, no bullet lists, no markdown.\n"
    "  * Write so it can be SPOKEN aloud at natural pace.\n"
    "  * Inline math in LaTeX: $\\theta$, $\\sin^2 + \\cos^2 = 1$.  Use "
    "    $$...$$ only when the formula is the centerpiece of the answer.\n"
    "  * Do NOT describe the figure (you cannot see it).  Do NOT say "
    "    'as shown below' or 'in the diagram.'  Speak only the theory.\n"
    "  * Stop after the formula(s).  Do NOT add a closing summary.\n"
    "  * NEVER mention that another component is generating a figure."
)


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
        "max_tokens": 220,
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
    return out


# ── Pipeline entry point ──────────────────────────────────────────────────

async def express_figure(
    user_prompt: str,
    base_url: str,
    model: str,
    api_key: str | None,
    max_retries: int = 1,
    context_canvases: list[dict[str, Any]] | None = None,
    on_svg_chunk: Callable[[str], Awaitable[None]] | None = None,
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
    user_content = _build_user_content(user_prompt, context_canvases or [])

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

    import sys as _sys
    def _log(msg: str) -> None:
        print(f"[express] {msg}", flush=True, file=_sys.stderr)

    _log(f"start prompt={user_prompt[:60]!r} model={model}")

    for attempt in range(max_retries + 1):
        _log(f"attempt={attempt} sending main request")
        # 1. Ask LLM for {svg, narration, title} in structured form.
        payload = {
            "model": model,
            "max_tokens": 8192,
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
        _log(f"got content length={len(content)}")
        result = json.loads(content)
        _log(f"parsed: svg_len={len(result.get('svg',''))} phrases={len(result.get('narration',[]))}")
        # Deterministic layout pass — auto-fit every <g>'s outer
        # rectangle to its child elements so a 3×3 matrix drawn with
        # a 200×200 rect but cells extending to (350, 340) gets the
        # rect expanded to wrap everything.  Idempotent: a correctly-
        # sized group passes through unchanged.  Errors are swallowed
        # because layout polish must never block a working figure.
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
        # Second layout pass — reflow top-level <text> elements whose
        # bounding boxes overlap.  Walks every text in document order,
        # shifts later ones down (then over to a new column if
        # needed) until they don't collide with earlier ones.  Handles
        # the "long formula at x=20,y=290 covers three short formulas
        # at x=300/450/600,y=290" failure mode the model keeps emitting.
        try:
            reflowed = reflow_overlapping_text(result["svg"])
            if reflowed != result["svg"]:
                _log(
                    f"reflow_overlapping_text: rewrote {len(result['svg'])} -> "
                    f"{len(reflowed)} chars"
                )
                result["svg"] = reflowed
        except Exception as exc:  # noqa: BLE001
            _log(f"reflow_overlapping_text FAILED: {type(exc).__name__}: {exc}")

        # 2a. Cheap deterministic structural review BEFORE the vision
        # call.  Catches failures the vision LLM can't reliably detect
        # from a rendered PNG alone — most importantly, narration
        # phrases that reference SVG ids that don't exist (the
        # "highlights don't fire" symptom the learner sees as
        # "the artifact under attention was not highlighted").
        structural_issues = _structural_review(
            result.get("svg", ""), result.get("narration") or [],
        )
        if structural_issues:
            _log(f"structural review: {len(structural_issues)} issue(s)")

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
        )

        # Merge structural issues into the verdict so a single retry
        # covers both classes.  If vision passed but structural failed,
        # we still need to retry; if vision failed too, the critic
        # checklist gets both kinds of fixes.
        if structural_issues:
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

        # 3. Inject critique + image, ask for a corrected response.
        if attempt >= max_retries:
            break
        retry_text = (
            "Your previous figure failed review.  Below is the "
            "rendered PNG and a structured list of specific fixes.  "
            "APPLY EVERY LISTED FIX --- do not just regenerate a "
            "near-identical SVG.  Each fix names a concrete action, "
            "the element it applies to, where it goes, and the "
            "exact content/values to use.\n\n"
            + verdict +
            "\n\nNow re-emit the corrected svg + narration in the "
            "same JSON schema, with every numbered fix above "
            "actually applied to the SVG."
        )
        messages.append({"role": "assistant", "content": content})
        # Text-only backends can't see the PNG; send the prior SVG
        # (which they can read literally) + the structured critique.
        if model in text_only_models:
            messages.append({
                "role": "user",
                "content": (
                    retry_text
                    + "\n\nFor reference, your previous SVG output was:\n"
                    + "```svg\n" + result.get("svg", "")[:8000] + "\n```"
                ),
            })
        else:
            png = _svg_to_png(result["svg"])
            b64 = base64.b64encode(png).decode("ascii")
            messages.append({"role": "user", "content": [
                {"type": "text", "text": retry_text},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]})

    # Loop exited with a still-failing figure — return last attempt anyway.
    # No repair pair recorded: nothing was actually corrected.
    #
    # Salvage step: if the retry over-corrected the narration (e.g.,
    # collapsed it from 6 phrases to 1 in an attempt to fix factual
    # errors flagged by the reviewer), prefer the earlier attempt's
    # narration so the spoken explanation stays substantial.  The SVG
    # itself is always the latest attempt — only the narration is
    # swapped.  This is what fixes the "I heard just the end of the
    # narrative" failure mode: the retry's 1-phrase narration plays
    # for 5 seconds and is gone before the learner registered audio.
    last_narration = result.get("narration") or []
    if prev_fail is not None and len(last_narration) < 3:
        _, prev_narration, _ = prev_fail
        if len(prev_narration) >= 3 and len(prev_narration) > len(last_narration):
            _log(
                f"narration salvage: last attempt had "
                f"{len(last_narration)} phrase(s); falling back to the "
                f"previous attempt's {len(prev_narration)}-phrase narration"
            )
            last_narration = prev_narration
    return {
        "svg": result.get("svg", ""),
        "narration": last_narration,
        "title": result.get("title") or "",
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

    Mode is one of 'text', 'vision', 'off'.  Defaults match the
    "GPT-4o-mini reviews SVG-as-text" recommendation; flip via env.
    """
    mode = (os.environ.get("SEVIM_REVIEW_MODE") or "text").lower().strip()
    if mode not in ("text", "vision", "off"):
        mode = "text"
    model = os.environ.get("SEVIM_REVIEW_MODEL") or "gpt-4o-mini"
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
        user_msg = _review_user_prompt(user_prompt, narration, svg_text=svg)
        messages = [
            {"role": "system", "content": _REVIEW_SYSTEM},
            {"role": "user", "content": user_msg},
        ]
    else:
        # Vision mode: rasterise to PNG and ship as image_url.  Use
        # only when the reviewer model supports image input (gpt-4o,
        # gpt-4o-mini, gpt-4-vision, etc.).
        try:
            png = _svg_to_png(svg)
            _log(f"rendered SVG ({len(svg)} chars) -> PNG ({len(png)} bytes)")
        except Exception as exc:  # noqa: BLE001
            _log(f"PNG render FAILED: {type(exc).__name__}: {exc} -- skipping review")
            return None
        b64 = base64.b64encode(png).decode("ascii")
        messages = [
            {"role": "system", "content": _REVIEW_SYSTEM},
            {"role": "user", "content": [
                {"type": "text",
                 "text": _review_user_prompt(user_prompt, narration)},
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


def _svg_to_png(svg: str, width: int = 1200) -> bytes:
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
    PAD = 20.0

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
        # Required rect bounds.
        need_x = min(rx, x0 - PAD)
        need_y = min(ry, y0 - PAD)
        need_w = max(rx + rw, x1 + PAD) - need_x
        need_h = max(ry + rh, y1 + PAD) - need_y
        # Only touch the rect if children actually overflow it.
        if (need_x >= rx and need_y >= ry
                and need_w <= rw + 0.5 and need_h <= rh + 0.5):
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

    PAD = 4.0       # min separation between text bboxes
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


def _structural_review(svg: str, narration: list[dict[str, Any]]) -> list[str]:
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

        # Recompute text boxes (similar to out-of-bounds above).
        text_boxes: list[tuple[float, float, float, float, str]] = []
        for m in re.finditer(r'<text\b([^>]*)>([^<]*)', svg):
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
                if ov / text_area >= 0.5:
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

    return issues


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
        f"SVG RULE: when the user's NEW REQUEST asks for a targeted "
        f"change ('add X', 'change Y', 'remove Z', 'highlight W', "
        f"'continue', 'explain step 3'), start from the prior SVG and "
        f"apply ONLY the requested change.  Preserve every unchanged "
        f"element BYTE-FOR-BYTE — same ids, same coordinates, same "
        f"text.  Do NOT regenerate the layout; the user expects "
        f"visual continuity.\n\n"
        f"NARRATION RULE — this is the part most models get wrong, "
        f"please re-read carefully: the ``narration`` field MUST "
        f"contain ONLY THE NEW PHRASES that describe THIS turn's "
        f"change.  Do NOT re-emit any prior phrase verbatim and do "
        f"NOT prepend the prior narration to the new content.  The "
        f"user has ALREADY heard the prior audio in their browser; "
        f"playing it again from the top is exactly what the user is "
        f"trying to avoid.\n\n"
        f"Concretely: if prior narration had 5 phrases and the user "
        f"asks 'continue with the next step', the new narration must "
        f"be e.g. 2-4 phrases — only the new step's narration — NOT "
        f"the original 5 + 3 new = 8.  If unsure whether a phrase is "
        f"genuinely new vs a restatement of the prior turn, drop it.\n\n"
        f"Only generate a fully new figure (and a fully new narration) "
        f"when the new request is UNRELATED to any attached prior "
        f"figure (e.g. user pivots: 'now show me matrix multiplication')."
    )})
    for i, ctx in enumerate(context_canvases, start=1):
        cid = ctx.get("id", "?")
        prior_prompt = ctx.get("prompt") or "(unknown prompt)"
        prior_svg = ctx.get("svg") or ""
        blocks.append({"type": "text", "text": (
            f"\n—— PRIOR FIGURE {i} (canvas id={cid}) ——\n"
            f"Original prompt: {prior_prompt!r}\n\n"
            f"Its current SVG XML (modify this in place when the user is "
            f"refining it):\n```xml\n{prior_svg}\n```"
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
