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
from typing import Any

import httpx


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
    "LAYOUT — match canonical textbook form:\n"
    "  • Matrix multiplication: 2-D grids for A (m×n), B (n×p), C (m×p) "
    "with '·' and '=' between them; row-i of A and column-j of B both "
    "highlighted; the worked sum-of-products written as a separate "
    "caption.\n"
    "  • 3SAT→clique: 3 columns of literals with cross-cluster edges; "
    "highlight one valid k-clique.\n"
    "  • Sets: overlapping circles with example elements drawn inside.\n"
    "  • Derivative / integral: function curve + tangent line / shaded "
    "area + the actual computed value.\n"
    "\n"
    "STYLE:\n"
    "  • Notation conventional: a_{ij} via <tspan baseline-shift='sub'>, "
    "Greek letters as Unicode (α β θ φ), operators as Unicode "
    "(∑ ∏ ∈ ∀ ∨ ∧ ¬ · ≤ ≥ ≠).  Never ASCII substitutes.\n"
    "  • Every visually distinct element has a unique SVG id "
    "(matrix_a_label, cell_a_1_2, sum_step_1, formula_general).\n"
    "  • viewBox sized to fit comfortably (typical: 0 0 900 650).\n"
    "  • No overlapping text.  Margins for captions.  Use colour "
    "purposefully (highlight the active row/column in a contrasting hue).\n"
    "  • Narration is spoken by piper TTS — write spoken words, not "
    "symbols (say 'a sub i j' not 'a_{ij}'; 'sigma from k equals 1 to n' "
    "not '∑').  Each phrase highlights ONE element.  Walk through the "
    "computation step by step (typically 15-30 phrases for a non-trivial "
    "concept).\n"
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
    "conversation, your default behaviour is to copy that SVG and "
    "narration verbatim, then apply ONLY the user's specific edit.  "
    "Same node ids, same coordinates, same captions for everything "
    "except the explicitly-changed elements.  The user expects "
    "visual continuity — they are iterating on a figure, not asking "
    "for an unrelated new one each turn.\n"
    "\n"
    "If the user's request can't reasonably be drawn, emit a small SVG "
    "saying so plus a one-phrase narration explaining why."
)


# ── Vision review (used between retries) ─────────────────────────────────

_REVIEW_SYSTEM = (
    "You are a pragmatic reviewer of mathematical figures.  Default to "
    "PASS.  PASS any figure that is functional and broadly correct, "
    "even if it could be better — partial figures, mid-quality "
    "labelling, and missing-but-non-essential captions are all PASS.  "
    "FAIL only when the figure is genuinely BROKEN:\n"
    "  • orphan leader lines pointing to empty canvas\n"
    "  • notation mismatches the user's request (wrong dimensions, "
    "wrong concept)\n"
    "  • main content missing entirely (e.g. 'matrix multiplication' "
    "with no matrices visible at all)\n"
    "  • text overlapping text such that nothing is readable\n"
    "  • wrong topology (3SAT-clique drawn as a tree, etc.)\n"
    "When you FAIL, list concrete actionable fixes — specific elements "
    "to add/change/remove with values and positions.  Pedagogical "
    "perfection (e.g. 'no concrete worked example shown') is NOT a "
    "FAIL condition; the user can ask for that as a follow-up."
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


def _review_user_prompt(user_prompt: str) -> str:
    return (
        f"User asked: {user_prompt!r}\n\n"
        "Review this figure pragmatically.  PASS unless the figure is "
        "genuinely broken or wildly off-topic.  Specifically PASS when "
        "the main content matches the request (matrices for matrix "
        "mult, clauses-and-edges for 3SAT-clique, etc.), even if some "
        "polish is missing — the user can request refinements as "
        "follow-up turns.\n\n"
        "FAIL only on these objective problems:\n"
        "  • Main content missing entirely (e.g. matrix-mult request "
        "    but no matrices visible).\n"
        "  • Wrong topology (3SAT-clique shown as a tree, "
        "    matrix-mult shown as a single column of indices).\n"
        "  • Orphan leader lines pointing to empty canvas.\n"
        "  • Dimensions don't match the user's request "
        "    (asked for 3×5, drawn as 2×4).\n"
        "  • Text completely overlapping text such that nothing is "
        "    readable.\n\n"
        "Set verdict='PASS' when the figure shows the right concept "
        "with reasonable layout, even if it lacks worked examples or "
        "explanatory captions.  Reserve verdict='FAIL' for the "
        "objective problems above.\n\n"
        "If FAIL, populate fixes[] with concrete actions: specific "
        "elements to add/change/remove with positions and values."
    )


# ── Pipeline entry point ──────────────────────────────────────────────────

async def express_figure(
    user_prompt: str,
    base_url: str,
    model: str,
    api_key: str | None,
    max_retries: int = 1,
    context_canvases: list[dict[str, Any]] | None = None,
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

    # Build the user message — multi-modal when prior context exists.
    user_content = _build_user_content(user_prompt, context_canvases or [])
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _EXPRESS_SYSTEM},
        {"role": "user", "content": user_content},
    ]

    review_history: list[str] = []
    result: dict[str, Any] = {}

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
        async with httpx.AsyncClient(timeout=180) as client:
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

        # 2. Render SVG → PNG and run vision review.
        verdict = await _vision_review(
            user_prompt=user_prompt,
            svg=result["svg"],
            base_url=base_url,
            model=model,
            api_key=api_key,
        )
        if verdict is None:  # PASS or unable to review
            return {
                "svg": result["svg"],
                "narration": result.get("narration") or [],
                "title": result.get("title") or "",
                "review_history": review_history,
                "retries_used": attempt,
            }
        review_history.append(verdict)

        # 3. Inject critique + image, ask for a corrected response.
        if attempt >= max_retries:
            break
        png = _svg_to_png(result["svg"])
        b64 = base64.b64encode(png).decode("ascii")
        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": [
            {"type": "text", "text": (
                "Your previous figure failed review.  Below is the "
                "rendered PNG and a structured list of specific fixes.  "
                "APPLY EVERY LISTED FIX — do not just regenerate a "
                "near-identical SVG.  Each fix names a concrete action, "
                "the element it applies to, where it goes, and the "
                "exact content/values to use.\n\n"
                + verdict +
                "\n\nNow re-emit the corrected svg + narration in the "
                "same JSON schema, with every numbered fix above "
                "actually applied to the SVG."
            )},
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]})

    # Loop exited with a still-failing figure — return last attempt anyway.
    return {
        "svg": result.get("svg", ""),
        "narration": result.get("narration") or [],
        "title": result.get("title") or "",
        "review_history": review_history,
        "retries_used": max_retries,
    }


async def _vision_review(
    user_prompt: str,
    svg: str,
    base_url: str,
    model: str,
    api_key: str | None,
) -> str | None:
    """Render SVG to PNG, ask the LLM to review it via the structured
    REVIEW_SCHEMA.  Returns ``None`` on PASS (or if the review call
    itself fails); returns a formatted critique string on FAIL that
    lists each fix as 'ACTION: what — where — details'.

    The structured schema forces the reviewer to produce concrete,
    actionable fixes (with specific values and positions) rather than
    vague prose.  The retry prompt formats them as a numbered checklist
    so the generator has a literal diff to apply.
    """
    import sys as _sys
    def _log(msg: str) -> None:
        print(f"[express:review] {msg}", flush=True, file=_sys.stderr)

    try:
        png = _svg_to_png(svg)
        _log(f"rendered SVG ({len(svg)} chars) → PNG ({len(png)} bytes)")
    except Exception as exc:  # noqa: BLE001
        _log(f"PNG render FAILED: {type(exc).__name__}: {exc} — skipping review")
        return None
    b64 = base64.b64encode(png).decode("ascii")

    headers = {"content-type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "max_tokens": 1200,
        "temperature": 0.0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "vision_review",
                "schema": REVIEW_SCHEMA,
                "strict": True,
            },
        },
        "messages": [
            {"role": "system", "content": _REVIEW_SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": _review_user_prompt(user_prompt)},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]},
        ],
    }
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
        f"RULE: when the user's NEW REQUEST asks for a targeted change "
        f"to a prior figure ('change X', 'add Y', 'remove Z', "
        f"'highlight W'), you MUST start from that prior SVG and apply "
        f"ONLY the requested change.  Preserve every unchanged element "
        f"BYTE-FOR-BYTE — same ids, same coordinates, same text.  Do "
        f"NOT regenerate the layout from scratch; the user expects to "
        f"see continuity.  Same applies to narration: keep unchanged "
        f"phrases verbatim, modify or insert only what the request "
        f"affects.\n\n"
        f"Only generate a fully new figure when the new request is "
        f"unrelated to any attached prior figure (e.g. user says "
        f"'now show me a different concept')."
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
            # Include the FULL narration script with highlight ids — the
            # model needs to know which phrases targeted which elements
            # so it can preserve unchanged phrases verbatim and only
            # modify the ones the refinement affects.
            blocks.append({"type": "text", "text": (
                "Its current narration script "
                "(modify in place; preserve unchanged phrases byte-for-byte):\n"
                + json.dumps(narration, indent=2)
            )})
    blocks.append({"type": "text", "text": (
        f"\n=== NEW REQUEST ===\n{user_prompt}\n\n"
        f"Reminder: if this is a refinement of a prior figure, return "
        f"the prior SVG with ONLY the requested edits applied.  If it "
        f"is a brand-new figure, ignore the priors and start fresh."
    )})
    return blocks
