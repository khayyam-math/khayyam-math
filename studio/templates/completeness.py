"""Completeness — classifier, rubrics, and critic for pedagogical depth.

The figure-correctness gates (vision review, structural critic, math
verifier) cover whether an answer is RIGHT.  This module covers
whether it is COMPLETE — whether its shape matches the pedagogical
contract implicit in the question.

A complete answer = (cognitive level × structural depth ×
representational forms) match.  All three axes are detectable from
the question text + conversation history without an explicit "level"
field.

Three exports:

  classify_question(prompt, history=None) -> ArchetypeKey
        regex-first classifier; falls back to a deterministic default
        ("concept_with_intuition") for unrecognised prompts.

  completeness_review(archetype, primer, narration, chat_text) -> list[str]
        runs the rubric for the chosen archetype against the produced
        text; returns a list of critique strings the existing retry
        loop can act on.  Empty list means complete.

  rubric_brief_for_llm(archetype) -> str
        the system-prompt clause to brief the figure / chat LLM on
        what "complete" looks like for this question.  Plugs into
        _EXPRESS_SYSTEM and SYSTEM_PROMPT.

Gated by SEVIM_COMPLETENESS_CRITIC env var.  Default: on.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Iterable, Literal


__all__ = [
    "ArchetypeKey",
    "classify_question",
    "completeness_review",
    "rubric_brief_for_llm",
    "COMPLETENESS_RUBRICS",
    "is_enabled",
]


# ---------------------------------------------------------------------------
# Archetype catalog
# ---------------------------------------------------------------------------
# Nine archetypes that cover the bulk of math-tutoring questions.  Each
# carries a set of required text components, a narration-phrase range,
# and a primer word range.  The class names are deliberately descriptive
# (not Bloom-level codes) because they show up in retry critiques the
# figure LLM reads.

ArchetypeKey = Literal[
    "quick_fact",
    "concept_definition",
    "concept_with_intuition",
    "apply_worked_example",
    "step_by_step",
    "causal_explanation",
    "comparison",
    "proof",
    "construction",
]


@dataclass(frozen=True)
class Rubric:
    """What "complete" looks like for one archetype.

    ``required``         element names; each maps to a detector in
                         ``_DETECTORS`` below.
    ``narration_range``  inclusive (lo, hi) for the number of narration
                         phrases the figure should ship with.
    ``primer_range``     inclusive (lo, hi) for primer word count.
    ``description``      one-line description used in the rubric brief.
    """
    required: tuple[str, ...]
    narration_range: tuple[int, int]
    primer_range: tuple[int, int]
    description: str


COMPLETENESS_RUBRICS: dict[ArchetypeKey, Rubric] = {
    "quick_fact": Rubric(
        required=("statement",),
        narration_range=(1, 2),
        primer_range=(15, 60),
        description=(
            "A direct factual question.  Give the answer plainly; "
            "do not over-explain."
        ),
    ),
    "concept_definition": Rubric(
        required=("statement", "paraphrase"),
        narration_range=(2, 4),
        primer_range=(60, 140),
        description=(
            "A definition request.  Give the precise statement, then "
            "a plain-language paraphrase that a learner who hasn't "
            "met the term before can hold on to."
        ),
    ),
    "concept_with_intuition": Rubric(
        required=("statement", "intuition", "takeaway"),
        narration_range=(3, 5),
        primer_range=(100, 200),
        description=(
            "A 'what is X' or 'help me understand X' question.  Give "
            "the statement, the geometric / visual / physical intuition "
            "for WHY it is what it is, and a one-line takeaway."
        ),
    ),
    "apply_worked_example": Rubric(
        required=("statement", "worked_example_with_numbers", "takeaway"),
        narration_range=(4, 6),
        primer_range=(120, 220),
        description=(
            "A 'show me with an example' / 'how do I use X' question.  "
            "Give the rule / formula, then walk through a concrete "
            "numeric example end-to-end, then a takeaway sentence."
        ),
    ),
    "step_by_step": Rubric(
        required=(
            "statement",
            "sequence_of_steps",
            "worked_example_with_numbers",
            "takeaway",
        ),
        narration_range=(6, 9),
        primer_range=(150, 280),
        description=(
            "A 'step by step', 'walk me through', 'explain in detail' "
            "request.  Give one phrase per logical step, with concrete "
            "numbers, and a closing takeaway.  Aim for 6-9 narration "
            "phrases, one per step plus an intro and a close."
        ),
    ),
    "causal_explanation": Rubric(
        required=(
            "statement",
            "causal_chain",
            "link_to_prior",
            "takeaway",
        ),
        narration_range=(5, 8),
        primer_range=(150, 260),
        description=(
            "A 'why' / 'what's the intuition' / 'how come X is true' "
            "question.  Connect the fact back to a more basic "
            "principle by an explicit causal chain (because / since "
            "/ therefore).  Reference a prior concept the learner "
            "already knows.  Do NOT just restate the fact."
        ),
    ),
    "comparison": Rubric(
        required=("criteria", "tabulation", "takeaway"),
        narration_range=(4, 7),
        primer_range=(70, 200),
        description=(
            "A 'compare X and Y' / 'what is the difference' question.  "
            "Lay out the criteria of comparison explicitly, then "
            "tabulate (markdown table or repeated structured "
            "comparisons), then close with which one wins on which "
            "criterion."
        ),
    ),
    "proof": Rubric(
        required=(
            "statement",
            "full_deduction",
            "qed_remark",
        ),
        narration_range=(5, 9),
        primer_range=(160, 320),
        description=(
            "A 'prove X' question.  Give the statement to be proved, "
            "then a full deduction with each step justified (every "
            "'therefore' / 'since' / 'by lemma' explicit), then a "
            "closing QED mark.  Do NOT skip arithmetic steps; show "
            "the manipulation."
        ),
    ),
    "construction": Rubric(
        required=("construction_steps", "verification"),
        narration_range=(5, 8),
        primer_range=(140, 260),
        description=(
            "A 'construct' / 'design' / 'find an X such that Y' "
            "question.  Enumerate the construction steps in order, "
            "then verify the result satisfies the requested property."
        ),
    ),
}


# Default archetype used when no rule matches.  Chosen to be the
# safest mid-depth answer shape — three required components, 3-5
# narration phrases, 100-200 word primer.
DEFAULT_ARCHETYPE: ArchetypeKey = "concept_with_intuition"


# ---------------------------------------------------------------------------
# Classifier — regex first, no LLM round-trip
# ---------------------------------------------------------------------------

# Patterns ordered by SPECIFICITY (most specific first); first match wins.
# Each pattern returns the archetype it claims.  When nothing matches the
# DEFAULT_ARCHETYPE is returned.
_CLASSIFIER_RULES: tuple[tuple[re.Pattern[str], ArchetypeKey], ...] = (
    # Proof requests
    (re.compile(
        r"\b(prove|proof|show that|demonstrate that|derive|verify that)\b",
        re.IGNORECASE,
    ), "proof"),

    # Construction / design
    (re.compile(
        r"\b(construct|design|find (?:an?|the) \w+ such that|build|exhibit)\b",
        re.IGNORECASE,
    ), "construction"),

    # Comparison
    (re.compile(
        r"\b(compare|contrast|what(?:'s| is) the difference|"
        r"differences? between|how does .{1,40}? (?:differ|compare))\b",
        re.IGNORECASE,
    ), "comparison"),

    # Causal / why
    (re.compile(
        r"\b(why|how come|what(?:'s| is) the (?:intuition|reason)|"
        r"explain why|reason\s+for)\b",
        re.IGNORECASE,
    ), "causal_explanation"),

    # Step-by-step
    (re.compile(
        r"\b(step[- ]by[- ]step|walk me through|in detail|"
        r"explain in detail|each step|every step)\b",
        re.IGNORECASE,
    ), "step_by_step"),

    # Worked example
    (re.compile(
        r"\b(with (?:an?|a worked) example|show (?:me )?(?:with )?(?:an?|the) example|"
        r"apply\b|how do i use|how to use|"
        r"use .{1,30}? to (?:solve|differentiate|integrate|compute|find|evaluate|prove))\b",
        re.IGNORECASE,
    ), "apply_worked_example"),

    # Concept WITH intuition cue (visualize / understand)
    (re.compile(
        r"\b(intuit\w*|understand|explain visually|visualize|"
        r"visualise|what'?s going on|make sense)\b",
        re.IGNORECASE,
    ), "concept_with_intuition"),

    # Definition request
    (re.compile(
        r"\b(define|definition of|what (?:is|are)|what does .{1,30}? mean)\b",
        re.IGNORECASE,
    ), "concept_definition"),

    # Quick fact — small calculation, single value, "evaluate"
    (re.compile(
        r"\b(evaluate|compute|calculate|simplify|what (?:is|equals?) "
        r"\d+|what'?s \d)\b",
        re.IGNORECASE,
    ), "quick_fact"),

    # "Show me X" / "draw" / "plot" without "example" cue —
    # graph-shaped requests want a worked example by default.
    (re.compile(
        r"\b(show me|draw|plot|sketch|illustrate|graph)\b",
        re.IGNORECASE,
    ), "apply_worked_example"),

    # "Explain" without any further qualifier — concept with intuition.
    (re.compile(
        r"\bexplain\b",
        re.IGNORECASE,
    ), "concept_with_intuition"),
)


def classify_question(
    prompt: str,
    history: Iterable[str] | None = None,
) -> ArchetypeKey:
    """Pick the archetype a question lands in.

    Conversation history is used as a tiebreaker — when the user
    asked the same concept in the prior turn and is now asking a
    follow-up, the SOLO level shifts one step up (Unistructural
    definition → Multistructural apply → Relational why).
    """
    text = (prompt or "").strip()
    if not text:
        return DEFAULT_ARCHETYPE

    for pat, key in _CLASSIFIER_RULES:
        if pat.search(text):
            return key

    # Follow-up shift: if the prior assistant turn already gave a
    # definition (history's tail mentions "is defined" / "is the"),
    # promote a bare follow-up to causal_explanation.
    if history:
        prior = " ".join(str(h) for h in history)[-2000:].lower()
        if any(cue in prior for cue in ("is defined", "we defined", "the definition")):
            return "causal_explanation"

    return DEFAULT_ARCHETYPE


# ---------------------------------------------------------------------------
# Component detectors — pure regex on the produced text
# ---------------------------------------------------------------------------
# Each detector returns True if the named component is present in the
# combined (primer + narration speak strings + chat reply) text.  The
# detectors are intentionally LENIENT — false positives are cheaper than
# false negatives (a false positive ships a complete answer; a false
# negative triggers a retry that may regress quality).

_STATEMENT_RE = re.compile(
    # An assertion of equality / definition.  Either an English form
    # ("is defined as / is the / equals") OR a math-equation form
    # ("a = b", ":=", "x_n = ...").  The `=\s*[^=\s]` alternative
    # has no \b anchor so it fires after non-word chars like "}".
    r"(?i)("
    r"\bis defined as\b|\bis the\b|\bis an?\b|\bare the\b|\bequals?\b|"
    r"\bdenotes?\b|"
    # math equation: an `=` (not `==`) with a non-space, non-`=` char on each side
    r"[^\s=]\s*=\s*[^=\s]|"
    # `:=` definition
    r":="
    r")",
)
_PARAPHRASE_RE = re.compile(
    r"\b(in other words|that is|put differently|or equivalently|"
    r"informally|in plain|simply put|essentially)\b",
    re.IGNORECASE,
)
_INTUITION_RE = re.compile(
    r"\b(intuitively|geometrically|think of (?:it|this) as|"
    r"represents?|picture|imagine|the idea is|the key insight|"
    r"what's happening|behind the scenes)\b",
    re.IGNORECASE,
)
_NUMERIC_LINE_RE = re.compile(
    r"-?\d+(?:\.\d+)?(?:\s*[-+*/=≈≠<>]\s*-?\d+(?:\.\d+)?){1,}"
)
_STEP_MARKERS_RE = re.compile(
    r"\b(step\s+\d|first[,.]|second[,.]|next[,.]|then[,.]|"
    r"finally|after that|^\d+[.)]\s)",
    re.IGNORECASE | re.MULTILINE,
)
_CAUSAL_RE = re.compile(
    r"\b(because|since|therefore|thus|hence|leads? to|results? in|"
    r"so that|which means|implies)\b",
    re.IGNORECASE,
)
_LINK_TO_PRIOR_RE = re.compile(
    r"\b(recall(?:\s+that)?|as we saw|earlier we|previously|"
    r"this is the same as|compare to|just like|analogous to|"
    r"by analogy)\b",
    re.IGNORECASE,
)
_TAKEAWAY_RE = re.compile(
    r"\b(in short|the takeaway|the key (?:point|idea)|"
    r"to summari[sz]e|in summary|bottom line|the upshot|"
    r"in conclusion|key fact)\b",
    re.IGNORECASE,
)
_CRITERIA_RE = re.compile(
    r"\b(criteria|criterion|axis|axes|along (?:which|the))\b|"
    r"(?:^|\n)\s*[-*]\s|\b\d+[.)]\s",
    re.IGNORECASE | re.MULTILINE,
)
_TABULATION_RE = re.compile(r"\|.*?\|.*?\|", re.MULTILINE)
_DEDUCTION_RE = re.compile(
    r"\b(let\b|assume|suppose|we have|note that|observe that|"
    r"by (?:the )?(?:lemma|theorem|definition|hypothesis)|"
    r"applying|substituting)\b",
    re.IGNORECASE,
)
_QED_RE = re.compile(
    r"(qed\b|∎|q\.e\.d\.|this completes the proof|"
    r"the proof is complete|as required|as desired|"
    r"as claimed)",
    re.IGNORECASE,
)
_VERIFICATION_RE = re.compile(
    r"\b(verify|check that|this satisfies|substituting back|"
    r"plugging in|confirm)\b",
    re.IGNORECASE,
)


def _has_worked_example_with_numbers(text: str) -> bool:
    """At least two distinct numeric tokens AND an equation-style
    line connecting them.  Catches '3*2 = 6', 'x = 1.5, f(x) = 2.375'
    etc.  Misses pure-symbolic proofs (handled separately by the
    proof archetype)."""
    # Distinct numbers
    nums = re.findall(r"-?\d+(?:\.\d+)?", text)
    if len(set(nums)) < 2:
        return False
    return bool(_NUMERIC_LINE_RE.search(text))


def _has_sequence_of_steps(text: str) -> bool:
    """At least 3 step markers OR 3 sentences starting with a step
    cue.  More demanding than the bare causal regex."""
    matches = _STEP_MARKERS_RE.findall(text)
    return len(matches) >= 3


def _has_causal_chain(text: str) -> bool:
    """At least 2 causal connectives in the same passage — a single
    'therefore' isn't a chain."""
    return len(_CAUSAL_RE.findall(text)) >= 2


def _has_full_deduction(text: str) -> bool:
    """Multiple deduction markers — a proof needs >= 3 distinct
    'let/assume/observe/therefore/by lemma' steps."""
    return len(_DEDUCTION_RE.findall(text)) >= 3


_DETECTORS = {
    "statement": lambda t: bool(_STATEMENT_RE.search(t)),
    "paraphrase": lambda t: bool(_PARAPHRASE_RE.search(t)),
    "intuition": lambda t: bool(_INTUITION_RE.search(t)),
    "worked_example_with_numbers": _has_worked_example_with_numbers,
    "sequence_of_steps": _has_sequence_of_steps,
    "causal_chain": _has_causal_chain,
    "link_to_prior": lambda t: bool(_LINK_TO_PRIOR_RE.search(t)),
    "takeaway": lambda t: bool(_TAKEAWAY_RE.search(t)),
    "criteria": lambda t: bool(_CRITERIA_RE.search(t)),
    "tabulation": lambda t: bool(_TABULATION_RE.search(t)),
    "full_deduction": _has_full_deduction,
    "qed_remark": lambda t: bool(_QED_RE.search(t)),
    "construction_steps": _has_sequence_of_steps,  # alias
    "verification": lambda t: bool(_VERIFICATION_RE.search(t)),
}


# ---------------------------------------------------------------------------
# Critic
# ---------------------------------------------------------------------------

_FIX_GUIDANCE = {
    "statement": (
        "State the answer or claim explicitly using 'is defined as' / "
        "'is the …' / an explicit equation.  The reader should be able "
        "to point to the one sentence that IS the answer."
    ),
    "paraphrase": (
        "After the precise statement, add a plain-language paraphrase "
        "starting with 'in other words' / 'that is' / 'informally' so "
        "a learner unfamiliar with the technical term still gets it."
    ),
    "intuition": (
        "Add an intuition sentence using 'intuitively' / 'geometrically' "
        "/ 'think of it as' / 'the key insight is'.  Explain WHY the "
        "fact is true, not just THAT it is."
    ),
    "worked_example_with_numbers": (
        "Add a concrete worked example with REAL numbers and an "
        "equation chain that the reader can verify (e.g. 'x_1 = 2 - "
        "6/12 = 1.5').  Symbolic-only is not a worked example."
    ),
    "sequence_of_steps": (
        "Lay out the procedure as numbered or 'first / then / next / "
        "finally' steps.  At least three explicit step markers."
    ),
    "causal_chain": (
        "Build a causal chain back to a more basic principle.  Use "
        "two or more 'because' / 'since' / 'therefore' connectives "
        "so the reader sees the logical scaffold, not just the "
        "conclusion."
    ),
    "link_to_prior": (
        "Reference a concept the learner already knows with 'recall' / "
        "'as we saw' / 'this is the same as' / 'analogous to'.  "
        "Scaffolding lives in the connection back."
    ),
    "takeaway": (
        "Close with a one-line takeaway: 'in short …' / 'the key "
        "point is …' / 'bottom line: …'.  Without it the reader "
        "leaves without an anchor."
    ),
    "criteria": (
        "Enumerate the criteria of comparison explicitly (numbered "
        "or bulleted), before comparing on each criterion."
    ),
    "tabulation": (
        "Tabulate the comparison as a markdown table with one row "
        "per criterion.  Side-by-side prose is harder to read."
    ),
    "full_deduction": (
        "Show each deduction step.  Use 'let' / 'assume' / 'note "
        "that' / 'observe that' / 'by Lemma X' explicitly; do not "
        "skip arithmetic.  At least three deduction markers."
    ),
    "qed_remark": (
        "Close the proof with QED / ∎ / 'this completes the proof' "
        "/ 'as required'."
    ),
    "construction_steps": (
        "Enumerate the construction steps in order, then perform "
        "them concretely."
    ),
    "verification": (
        "Verify the constructed object satisfies the required "
        "property: substitute it back / check the conditions."
    ),
}


def is_enabled() -> bool:
    """SEVIM_COMPLETENESS_CRITIC env gate.  Default: on."""
    return os.environ.get(
        "SEVIM_COMPLETENESS_CRITIC", "on"
    ).lower() not in ("off", "0", "false", "no")


def _combined_text(
    primer: str,
    narration: list[dict] | None,
    chat_text: str,
) -> str:
    parts = [primer or ""]
    for ph in (narration or []):
        sp = ph.get("speak") or ph.get("text") or ""
        if sp:
            parts.append(sp)
    parts.append(chat_text or "")
    return "\n\n".join(p for p in parts if p)


def _format_issue(missing: str, archetype: ArchetypeKey) -> str:
    guidance = _FIX_GUIDANCE.get(missing, "Add this component.")
    return (
        f"completeness_missing_{missing}: this question was classified "
        f"as '{archetype}'.  A complete answer for that class is "
        f"missing the '{missing}' component.  {guidance}"
    )


def completeness_review(
    archetype: ArchetypeKey,
    primer: str = "",
    narration: list[dict] | None = None,
    chat_text: str = "",
) -> list[str]:
    """Return a list of completeness issues for the produced (primer,
    narration, chat reply) tuple against the archetype's rubric.
    Empty list = complete.

    Each issue is a self-contained critique string the existing retry
    loop in ``express_figure`` can act on (same shape as the
    structural critic's output).
    """
    if not is_enabled():
        return []

    rubric = COMPLETENESS_RUBRICS.get(archetype)
    if rubric is None:
        return []

    text = _combined_text(primer, narration, chat_text)
    if not text.strip():
        return []

    issues: list[str] = []

    # Component check
    for component in rubric.required:
        detector = _DETECTORS.get(component)
        if detector is None:
            continue
        try:
            if not detector(text):
                issues.append(_format_issue(component, archetype))
        except Exception:  # noqa: BLE001
            # Detector must never raise; on internal error, treat as
            # present (false-positive bias).
            continue

    # Narration phrase count
    lo, hi = rubric.narration_range
    n = len(narration or [])
    if n and n < lo:
        issues.append(
            f"completeness_narration_too_short: archetype "
            f"'{archetype}' expects {lo}-{hi} narration phrases; got "
            f"{n}.  Add more phrases — one per logical step or "
            f"sub-concept — so the learner can follow the figure as "
            f"the audio plays."
        )

    # Primer word count
    plo, phi = rubric.primer_range
    pwords = len((primer or "").split())
    if pwords and pwords < plo:
        issues.append(
            f"completeness_primer_too_short: archetype "
            f"'{archetype}' expects a {plo}-{phi} word primer; got "
            f"{pwords} words.  Expand: add intuition / a small "
            f"concrete example / the relevant formula.  Aim for "
            f"depth-of-explanation, not padding."
        )

    return issues


# ---------------------------------------------------------------------------
# Brief — system-prompt clause the LLM reads UP FRONT
# ---------------------------------------------------------------------------

def rubric_brief_for_llm(archetype: ArchetypeKey) -> str:
    """A short paragraph the figure / chat LLM should read before
    producing an answer.  Names the archetype, lists required
    components, and gives the narration + primer length range.

    Brief-the-model + critic-on-output is empirically more reliable
    than critic alone — the structural critic chain in express.py
    converged faster on the figure side once the directives went into
    the system prompt.  Same approach here.
    """
    rubric = COMPLETENESS_RUBRICS.get(archetype)
    if rubric is None:
        return ""
    required = ", ".join(rubric.required)
    n_lo, n_hi = rubric.narration_range
    p_lo, p_hi = rubric.primer_range
    return (
        "COMPLETENESS CONTRACT.\n"
        f"This question is classified as '{archetype}'.  "
        f"{rubric.description}\n"
        f"\n"
        f"A complete answer for this class MUST contain the following "
        f"components (each detected by an automatic critic; missing "
        f"components trigger a retry): {required}.\n"
        f"\n"
        f"Length targets:\n"
        f"  • narration: {n_lo}-{n_hi} phrases (one per logical step "
        f"or sub-concept)\n"
        f"  • primer: {p_lo}-{p_hi} words (spoken-style; intuition + "
        f"formula + small example)\n"
        f"\n"
        f"If you are unsure whether to add a component, ADD IT.  The "
        f"cost of one extra paragraph of explanation is much smaller "
        f"than the cost of a half-answer."
    )
