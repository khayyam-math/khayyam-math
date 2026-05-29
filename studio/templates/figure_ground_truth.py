"""Independent figure ground-truth synthesis for the vision audit.

The math_verifier (Tier 2) validates the equation-level claims the
figure LLM emits alongside its figure ("the derivative is 3x^2", "the
area is 8/3").  That catches incorrect math STATEMENTS.  It does not
catch a figure whose dots, lines, and labels are in the wrong place:
the LLM never asserted a positional claim, so the math_verifier had
nothing to check.

This module closes that gap.  Given the user's prompt, it runs an
INDEPENDENT pass — an LLM proposer plus a SymPy validator — to produce
a list of positional / relational / value claims that any CORRECT
illustration must satisfy.  Those validated claims are then injected
into the vision-audit prompt under a separate "INDEPENDENT GROUND
TRUTH" heading so the auditor compares the rendered figure against
ground truth derived WITHOUT the figure-generating LLM in the loop.

Each proposed claim is required to come with a small SymPy-parseable
derivation that the validator can evaluate and compare against the
claimed numeric.  A claim the validator can't reproduce (parse error,
numeric mismatch, eval exception) is DROPPED.  Empty result is a
valid outcome: the existing audit continues without ground truth and
the pipeline is unchanged for prompts that have no figure-level
ground truth.

Used by ``studio.express`` from the vision-audit branch.

Env switches:

    SEVIM_FIGURE_GROUND_TRUTH    "0" disables this module entirely
                                  (default "1" — on).
    SEVIM_GROUND_TRUTH_MODEL     proposer model, default gpt-4o-mini.
    SEVIM_GROUND_TRUTH_URL       proposer base URL; falls back to
                                  SEVIM_REVIEW_URL, then OpenAI.
    SEVIM_GROUND_TRUTH_KEY       proposer API key; falls back to
                                  OPENAI_API_KEY.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Literal


# ---------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------

ClaimKind = Literal["position", "value", "slope", "relation", "presence"]


@dataclass(frozen=True)
class FigureClaim:
    """One thing a correct figure MUST visibly satisfy.

    label
        Human-readable handle, used both in the vision-audit prompt
        and in the structured-fixes critique fed back to the figure
        LLM on retry.  e.g. "x_1 on x-axis", "tangent slope at x_0",
        "area under f from 0 to 2".
    kind
        position : a labelled point on a specific axis (axis="x"|"y", value=float)
        value    : a labelled scalar somewhere on the figure (e.g., "Area = 8/3")
        slope    : slope of a named line / tangent
        relation : a relational fact between two named items
                   (relation="less_than", lhs/rhs values from SymPy)
        presence : an element that must appear at all (no numeric)
    value
        Ground-truth numeric value for position/value/slope kinds.
        None for relation/presence.
    axis
        "x" or "y" for position kind; otherwise None.
    tolerance
        Relative tolerance for numeric comparison.  5% by default,
        forgiving enough for hand-drawn figures but tight enough to
        flag actual inversions (1.5 vs +Inf, 1.5 vs 2.5, 1.5 vs -1.5).
    relation
        For kind="relation", a string the validator and the vision
        auditor both understand: less_than, greater_than, left_of,
        right_of, above, below, equal, approx.
    source
        Provenance of the validation result.  Used in logs and the
        rendered reviewer block to indicate confidence.
    explanation
        One-sentence rationale.  Helps the figure LLM understand WHY
        the retry critique flagged a fix when a claim is violated.
    """
    label: str
    kind: ClaimKind
    value: float | None = None
    axis: str | None = None
    tolerance: float = 0.05
    relation: str | None = None
    source: Literal["sympy", "llm-only"] = "sympy"
    explanation: str = ""


@dataclass
class FigureGroundTruth:
    """Result of an extraction pass: validated claims + a tiny debug
    record of what was proposed-but-dropped (useful for tuning the
    proposer prompt over time).
    """
    claims: list[FigureClaim] = field(default_factory=list)
    proposed: int = 0
    validated: int = 0
    dropped_reasons: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.claims)


# ---------------------------------------------------------------------
# Proposer: separate LLM call that emits structured claims
# ---------------------------------------------------------------------

_PROPOSER_SYSTEM = (
    "You are the helper to a mathematical-figure auditor.  Given a "
    "math prompt from a student, you propose a SHORT LIST of specific "
    "facts that a correct illustration of the prompt MUST visibly "
    "satisfy.  You do NOT see the figure itself; you reason only from "
    "the prompt.\n"
    "\n"
    "Each fact must be:\n"
    "  - independently verifiable: you compute it yourself, you do "
    "    NOT trust the figure or any external solver to be right.\n"
    "  - numeric (a coordinate, a value, a slope) or relational "
    "    (one labelled item is strictly less than / left of / above "
    "    another).\n"
    "  - paired with a SymPy-parseable derivation expression that "
    "    recomputes the value, so a downstream validator can confirm "
    "    it numerically.\n"
    "\n"
    "Prefer ROOT-CAUSE claims that figures tend to get wrong:\n"
    "  - iteration direction (does x_{n+1} land left or right of x_n?),\n"
    "  - sign and steepness of a slope (positive vs negative, ~12 vs ~0.5),\n"
    "  - on-which-side-of relations (is the max above or below the axis?),\n"
    "  - exact intercept positions (where exactly does a tangent cross 0?),\n"
    "  - convergence targets (what value do iterates approach?).\n"
    "\n"
    "If the prompt is non-mathematical, ambiguous, or has no figure-"
    "level ground truth (e.g., 'draw something pretty', 'show a Venn "
    "diagram of A and B'), return an EMPTY list.  Empty is correct "
    "and expected.  Never fabricate values.\n"
    "\n"
    "Keep the list short: 2-6 high-value claims is the sweet spot.  "
    "Each claim must be checkable, not vague.\n"
    "\n"
    "SymPy expression language: standard Python math, plus diff, "
    "integrate, limit, Sum, Product, Matrix, hessian, sin/cos/tan, "
    "exp/log/sqrt, factorial, pi, E, I, oo.  Use Rational(p, q) for "
    "exact fractions.  Implicit multiplication is allowed (2x means "
    "2*x).  Variables: x y z t a b c d n k m p q r u v w "
    "alpha beta gamma theta phi (use lambda_ for the eigenvalue "
    "symbol).\n"
    "\n"
    "Return JSON conforming to the supplied schema."
)


_PROPOSER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "label": {
                        "type": "string",
                        "description": (
                            "Short handle for the claim, used in "
                            "audit fixes.  e.g. 'x_1 on x-axis', "
                            "'tangent slope at x_0', 'area under f'."
                        ),
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["position", "value", "slope",
                                 "relation", "presence"],
                    },
                    "axis": {
                        "type": ["string", "null"],
                        "enum": ["x", "y", None],
                        "description": (
                            "For kind=position only.  Otherwise null."
                        ),
                    },
                    "value_expr": {
                        "type": ["string", "null"],
                        "description": (
                            "SymPy-parseable derivation expression "
                            "that evaluates to the numeric value of "
                            "this claim.  Required for kinds "
                            "position / value / slope.  Null for "
                            "kinds relation / presence."
                        ),
                    },
                    "lhs_expr": {
                        "type": ["string", "null"],
                        "description": (
                            "For kind=relation only: SymPy expression "
                            "for the left-hand side."
                        ),
                    },
                    "rhs_expr": {
                        "type": ["string", "null"],
                        "description": (
                            "For kind=relation only: SymPy expression "
                            "for the right-hand side."
                        ),
                    },
                    "relation": {
                        "type": ["string", "null"],
                        "enum": [
                            None,
                            "less_than", "greater_than",
                            "left_of", "right_of",
                            "above", "below",
                            "approx", "equal",
                        ],
                    },
                    "tolerance": {
                        "type": "number",
                        "description": (
                            "Relative tolerance, default 0.05.  Use "
                            "tighter (0.01) for exact integer answers, "
                            "looser (0.1) for hand-drawn slopes."
                        ),
                    },
                    "explanation": {
                        "type": "string",
                        "description": (
                            "One sentence explaining why this claim "
                            "must hold.  Used in retry critique."
                        ),
                    },
                },
                "required": [
                    "label", "kind", "axis", "value_expr",
                    "lhs_expr", "rhs_expr", "relation",
                    "tolerance", "explanation",
                ],
            },
            "maxItems": 8,
        },
    },
    "required": ["claims"],
}


async def _propose_claims(
    user_prompt: str,
    base_url: str,
    api_key: str | None,
    model: str,
) -> tuple[list[dict], str]:
    """Send the prompt to the proposer LLM and return the list of
    proposed claims (raw dicts from JSON).  Second tuple slot is a
    short status string for logging.  Never raises.
    """
    import httpx

    payload = {
        "model": model,
        "max_tokens": 800,
        "temperature": 0.0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "figure_ground_truth_proposal",
                "schema": _PROPOSER_SCHEMA,
                "strict": True,
            },
        },
        "messages": [
            {"role": "system", "content": _PROPOSER_SYSTEM},
            {"role": "user",
             "content": (
                 f"Math prompt from the student:\n{user_prompt!r}\n\n"
                 "Propose figure-level ground-truth claims as JSON."
             )},
        ],
    }
    headers = {"content-type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers=headers, json=payload,
            )
    except Exception as exc:  # noqa: BLE001
        return [], f"proposer request failed: {type(exc).__name__}: {exc}"
    if r.status_code != 200:
        body = (await r.aread()).decode(errors="replace") if hasattr(r, "aread") else r.text
        return [], f"proposer HTTP {r.status_code}: {body[:120]}"
    try:
        content = r.json()["choices"][0]["message"]["content"]
        obj = json.loads(content)
    except Exception as exc:  # noqa: BLE001
        return [], f"proposer JSON parse failed: {type(exc).__name__}"
    return (obj.get("claims") or []), "ok"


# ---------------------------------------------------------------------
# Validator: SymPy evaluates each proposed claim
# ---------------------------------------------------------------------


_RELATION_OPS = {
    # Each op returns True if the LHS satisfies <op> RHS within tolerance.
    # tolerance is the relative tolerance scaled by max(1, |rhs|).
    "less_than":    lambda u, v, tol: u < v - tol * max(1.0, abs(v)),
    "greater_than": lambda u, v, tol: u > v + tol * max(1.0, abs(v)),
    "left_of":      lambda u, v, tol: u < v - tol * max(1.0, abs(v)),
    "right_of":     lambda u, v, tol: u > v + tol * max(1.0, abs(v)),
    "above":        lambda u, v, tol: u > v + tol * max(1.0, abs(v)),
    "below":        lambda u, v, tol: u < v - tol * max(1.0, abs(v)),
    "equal":        lambda u, v, tol: abs(u - v) <= tol * max(1.0, abs(v)),
    "approx":       lambda u, v, tol: abs(u - v) <= tol * max(1.0, abs(v)),
}


def _validate(claim: dict) -> tuple[FigureClaim | None, str | None]:
    """Run SymPy on the claim's derivation; return either a validated
    FigureClaim or (None, reason).  Never raises.
    """
    # Reuse the math_verifier's SymPy environment (knows diff, integrate,
    # Matrix, trig, implicit multiplication, the usual variable names).
    try:
        from studio.templates.math_verifier import _make_env  # type: ignore
        sp, parse = _make_env()
    except Exception as exc:  # noqa: BLE001
        return None, f"sympy env unavailable: {type(exc).__name__}"

    kind = claim.get("kind")
    label = (claim.get("label") or "").strip()
    if not label:
        return None, "missing label"
    if kind not in ("position", "value", "slope", "relation", "presence"):
        return None, f"unknown kind {kind!r}"
    explanation = (claim.get("explanation") or "").strip()
    try:
        tol = float(claim.get("tolerance") or 0.05)
    except (TypeError, ValueError):
        tol = 0.05
    # Clamp to a sane range so the LLM can't disable validation by
    # returning a 1.0 tolerance.
    tol = max(0.001, min(tol, 0.25))

    # ── presence ─────────────────────────────────────────────────
    if kind == "presence":
        if len(label) > 80:
            return None, "presence label too long"
        return FigureClaim(
            label=label, kind="presence",
            source="llm-only", explanation=explanation,
        ), None

    # ── relation ─────────────────────────────────────────────────
    if kind == "relation":
        lhs_expr = claim.get("lhs_expr")
        rhs_expr = claim.get("rhs_expr")
        relation = claim.get("relation")
        if not (lhs_expr and rhs_expr and relation):
            return None, "relation missing lhs/rhs/op"
        a = parse(lhs_expr)
        b = parse(rhs_expr)
        if a is None or b is None:
            return None, (f"sympy parse failed: lhs={lhs_expr!r} "
                          f"rhs={rhs_expr!r}")
        try:
            an = float(sp.N(a))
            bn = float(sp.N(b))
        except Exception as exc:  # noqa: BLE001
            return None, f"numeric eval failed: {type(exc).__name__}"
        op = _RELATION_OPS.get(relation)
        if op is None:
            return None, f"unknown relation op {relation!r}"
        if not op(an, bn, tol):
            return None, (f"sympy refutes the proposed relation "
                          f"({an:g} {relation} {bn:g} is false within "
                          f"tol={tol})")
        return FigureClaim(
            label=label, kind="relation",
            relation=relation, tolerance=tol,
            source="sympy", explanation=explanation,
        ), None

    # ── position / value / slope ─────────────────────────────────
    expr = claim.get("value_expr")
    if not expr:
        return None, f"kind={kind} missing value_expr"
    e = parse(expr)
    if e is None:
        return None, f"sympy parse failed: {expr!r}"
    try:
        v = float(sp.N(e))
    except Exception as exc:  # noqa: BLE001
        return None, f"numeric eval failed for {expr!r}: {type(exc).__name__}"
    if not (v == v):  # NaN guard
        return None, f"value_expr {expr!r} evaluated to NaN"

    axis = claim.get("axis") if kind == "position" else None
    if kind == "position" and axis not in ("x", "y"):
        return None, "position kind requires axis 'x' or 'y'"

    return FigureClaim(
        label=label, kind=kind,
        value=v, axis=axis, tolerance=tol,
        source="sympy", explanation=explanation,
    ), None


# ---------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------


def _proposer_config() -> tuple[str, str, str | None]:
    """Resolve (model, base_url, api_key) for the proposer call.
    Defaults to gpt-4o-mini on the reviewer's endpoint."""
    model = os.environ.get("SEVIM_GROUND_TRUTH_MODEL", "gpt-4o-mini")
    url = os.environ.get(
        "SEVIM_GROUND_TRUTH_URL",
        os.environ.get("SEVIM_REVIEW_URL", "https://api.openai.com/v1"),
    )
    key = os.environ.get(
        "SEVIM_GROUND_TRUTH_KEY",
        os.environ.get("OPENAI_API_KEY"),
    )
    return model, url, key


async def extract_figure_ground_truth(
    user_prompt: str,
) -> FigureGroundTruth:
    """Top-level entry point used by ``studio.express``.

    Returns a FigureGroundTruth that may be empty.  Never raises;
    extraction failure must not break the express pipeline.
    """
    if os.environ.get("SEVIM_FIGURE_GROUND_TRUTH", "1") == "0":
        return FigureGroundTruth()
    if not user_prompt or len(user_prompt) > 4000:
        return FigureGroundTruth()

    model, url, key = _proposer_config()
    try:
        proposed, status = await _propose_claims(user_prompt, url, key, model)
    except Exception as exc:  # noqa: BLE001
        return FigureGroundTruth(
            dropped_reasons=[f"propose failed: {type(exc).__name__}: {exc}"]
        )

    if not proposed:
        return FigureGroundTruth(
            proposed=0, validated=0,
            dropped_reasons=[f"proposer empty: {status}"]
            if status != "ok" else [],
        )

    validated: list[FigureClaim] = []
    dropped: list[str] = []
    for c in proposed:
        try:
            claim, reason = _validate(c)
        except Exception as exc:  # noqa: BLE001
            claim, reason = None, f"validator exception: {type(exc).__name__}"
        if claim is not None:
            validated.append(claim)
        elif reason:
            label = (c.get("label") or "?")[:40] if isinstance(c, dict) else "?"
            dropped.append(f"[{label}] {reason}")

    return FigureGroundTruth(
        claims=validated,
        proposed=len(proposed),
        validated=len(validated),
        dropped_reasons=dropped,
    )


# ---------------------------------------------------------------------
# Renderer: format ground truth as a block for the reviewer prompt
# ---------------------------------------------------------------------


def render_for_reviewer(gt: FigureGroundTruth | None) -> str:
    """Produce the markdown-ish block to inject into the vision-audit
    user message.  Empty string when there is nothing useful to show,
    so a caller can safely append the result unconditionally.
    """
    if not gt or not gt.claims:
        return ""
    lines = [
        "",
        "INDEPENDENT FIGURE GROUND TRUTH (computed from the prompt by a "
        "separate proposer + SymPy validator; NOT taken from the figure "
        "LLM's own narration or math_claims).  A correct figure MUST "
        "visibly satisfy every claim below.  If any claim is missing "
        "from the figure or shown at the wrong position, FAIL the "
        "review and list the violated claim's `label` field verbatim "
        "inside one of the fixes (so the retry knows what to fix).",
    ]
    for c in gt.claims:
        if c.kind == "position":
            lines.append(
                f"  - [{c.label}] (kind=position, axis={c.axis}) should "
                f"appear at coordinate {c.value:.4g} on the {c.axis}-axis "
                f"(±{c.tolerance:.0%} tolerance).  {c.explanation}"
            )
        elif c.kind == "value":
            lines.append(
                f"  - [{c.label}] (kind=value) the figure should display "
                f"the numeric value {c.value:.4g} (±{c.tolerance:.0%}).  "
                f"{c.explanation}"
            )
        elif c.kind == "slope":
            lines.append(
                f"  - [{c.label}] (kind=slope) the named line should "
                f"have slope {c.value:.4g} (±{c.tolerance:.0%}); positive "
                f"slopes go up-right, negative down-right, steep slopes "
                f"are nearly vertical.  {c.explanation}"
            )
        elif c.kind == "relation":
            lines.append(
                f"  - [{c.label}] (kind=relation) MUST hold visually: "
                f"{c.relation}.  {c.explanation}"
            )
        elif c.kind == "presence":
            lines.append(
                f"  - [{c.label}] (kind=presence) this element MUST "
                f"appear somewhere in the figure.  {c.explanation}"
            )
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "FigureClaim",
    "FigureGroundTruth",
    "extract_figure_ground_truth",
    "render_for_reviewer",
]
