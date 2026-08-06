"""Deterministic "the user explicitly asked for a visual" detector.

Field report (2026-08-05): a user asked for something visual on
khayyammath.com and got a text-only reply — no figure at all.  Root
cause is structural, not a one-off: ``/studio/chat`` runs the chat-LLM
with ``tool_choice="auto"`` and relies on the SYSTEM_PROMPT's DECISION
RULE to make it call ``sevim_express``.  That rule is advisory.  When
the model decides to elaborate in prose instead, the canvas stays
empty and there is nothing downstream to catch it — a soft prompt
cannot be a hard guarantee.

This module is the hard guarantee.  It answers one narrow question
with regexes only (no LLM round-trip, no latency):

    Did the user EXPLICITLY ask to see something, on a subject the
    canvas can actually draw?

When the answer is yes, the caller pins ``tool_choice`` to
``sevim_express`` for that turn, so the figure is not optional.  When
the answer is no, nothing changes and the model keeps its normal
freedom to clarify, acknowledge, and explain in chat.

Three gates, in order — any one of the first two vetoes the force:

  1. OPT-OUT.  "just tell me", "no diagram", "in words" — an explicit
     request for prose beats an incidental visual verb.
  2. NOT-DRAWABLE.  "show me the code", "show me your prompt",
     "show me my account" — the visual verb points at the *system*,
     not at math.  Forcing a figure here draws nonsense; worse, the
     honest "I can't draw that" reply becomes unreachable because a
     pinned tool_choice removes the model's option to decline.
  3. ASK.  A visual verb/noun in any language Studio serves.

Kept deliberately conservative: a missed force degrades to today's
behaviour (the model probably draws anyway), while a wrong force
produces a figure nobody wanted and burns a TTS synthesis.
"""
from __future__ import annotations

import os
import re


def is_enabled() -> bool:
    """Force-visual routing is ON by default; set to 0 to disable."""
    return (os.environ.get("SEVIM_FORCE_VISUAL_ROUTE", "1")
            .strip().lower() not in ("0", "false", "no", "off"))


# ── 1. Opt-out: the user explicitly wants prose ──────────────────────
#
# These beat every visual cue below.  A user who writes "explain the
# chain rule in words, no diagram" has used the word "diagram" and
# must NOT get one.
_OPT_OUT = re.compile(
    r"""
      \b(?:no|without|skip|don'?t\s+(?:draw|show|plot|make)|
            do\s+not\s+(?:draw|show|plot|make))\b
        [^.?!]{0,20}
      \b(?:figure|diagram|picture|image|graph|plot|drawing|visual|
            animation|canvas)\b
    | \b(?:text|words|writing)[-\s]?only\b
    | \bonly\s+(?:in\s+)?(?:text|words)\b
    | \b(?:in|with)\s+(?:plain\s+)?(?:text|words)\b
    | \bjust\s+(?:tell|explain\s+to)\s+me\b
    | \bjust\s+(?:explain|answer|say)\s+(?:it\s+)?(?:in\s+)?
        (?:words|text)?\b
    | \bnur\s+text\b | \bohne\s+(?:bild|grafik|diagramm|abbildung)\b
    | \bsolo\s+texto\b | \bsin\s+(?:imagen|gr[áa]fico|diagrama)\b
    | \bsans\s+(?:image|sch[ée]ma|graphique|figure)\b
    | 只(?:要|用)?文字 | 不要(?:图|圖|画|畫)
    | بدون\s*(?:شکل|نمودار|تصویر)
    """,
    re.IGNORECASE | re.VERBOSE,
)

# ── 2. Not drawable: the visual verb points at the system, not math ──
#
# "Show me the source code" is an explicit visual ask by pattern and a
# terrible thing to draw.  Equally important: these are exactly the
# prompts where the model needs to be free to say "I can't do that" —
# a pinned tool_choice takes that option away.
_NOT_DRAWABLE = re.compile(
    r"""
      \b(?:show|give|tell|send|display|reveal|print|repeat)\b
        [^.?!]{0,24}
      \b(?:source\s+code|code\s?base|your\s+code|the\s+code|
            system\s+prompt|your\s+(?:prompt|instructions|rules|
                                     configuration|config|settings)|
            api\s+key|api\s+keys|credentials?|password|
            database|logs?|stack\s+trace|
            (?:my|the)\s+(?:account|profile|billing|invoice|
                            subscription|session|history|email))\b
    | \b(?:pricing|price\s+list|how\s+much\s+does\s+it\s+cost)\b
    | \bwho\s+(?:are|made|built|trained)\s+you\b
    | \bwhat\s+(?:model|llm|ai)\s+(?:are|do)\s+you\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# ── 3. The explicit visual ask ───────────────────────────────────────
#
# One pattern per language family Studio serves (see studio/language.py
# for the detector that drives narration).  Latin-script entries are
# word-bounded; CJK / Arabic-script entries are not, because those
# scripts do not delimit words with spaces.
_ASK_LATIN = re.compile(
    r"""
      \b(?:draw|sketch|plot|chart|animate|render)\b
    | \b(?:show|illustrate|visuali[sz]e|display|depict|demonstrate)\b
    | \bshow\s+me\b
    | \b(?:can|could|would)\s+you\s+(?:please\s+)?
        (?:draw|show|plot|sketch|graph|illustrate|visuali[sz]e)\b
    | \b(?:a|an|the|some|with\s+an?)\s+
        (?:diagram|drawing|sketch|picture|illustration|animation|
           visuali[sz]ation|graphic)\b
    | \b(?:diagram|animation|visuali[sz]ation|illustration)\s+
        (?:of|for)\b
    | \bgraph\s+(?:it|this|that|of|the)\b
    | \bplot\s+(?:it|this|that|of|the)\b
    | \b(?:visually|graphically|on\s+the\s+canvas)\b
    | \blet\s+me\s+see\b
    | \bi\s+want\s+to\s+see\b
    # German
    | \b(?:zeichne|zeig|zeige|male|plotte|visualisiere|
            veranschauliche|skizziere)\b
    | \b(?:diagramm|grafik|abbildung|skizze|zeichnung|animation)\b
    # French
    | \b(?:dessine|montre|illustre|visualise|trace)\b
    | \b(?:sch[ée]ma|graphique|figure|dessin|animation)\b
    # Spanish
    | \b(?:dibuja|mu[ée]stra|mu[ée]strame|ilustra|visualiza|grafica)\b
    | \b(?:diagrama|gr[áa]fico|dibujo|animaci[óo]n|ilustraci[óo]n)\b
    # Turkish
    | \b(?:[çc]iz|g[öo]ster|[şs]ekil|grafik)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# CJK + Arabic script: no word boundaries, so match the substring.
_ASK_NON_LATIN = re.compile(
    r"""
      画 | 畫 | 图示 | 圖示 | 图解 | 圖解 | 可视化 | 可視化
    | 动画 | 動畫 | 示意图 | 示意圖 | 给我看 | 給我看 | 展示 | 演示
    | رسم\s*کن | بکش | نشان\s*(?:بده|بدهید) | نمودار | انیمیشن
    | تصویرسازی | ترسیم
    | ارسم | أرني | رسم\s*بياني | مخطط
    """,
    re.IGNORECASE | re.VERBOSE,
)

# "figure out", "show up", "graph theory as a topic name" etc. — visual
# words whose surrounding phrase makes them non-visual.  Stripped from
# the text before the ask patterns run.
_FALSE_FRIENDS = re.compile(
    r"""
      \bfigure\s+out\b
    | \bshows?\s+(?:up|that\b)
    | \bgo\s+figure\b
    | \bplot\s+of\s+(?:the\s+)?(?:story|book|novel|film|movie)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def wants_visual(text: str) -> tuple[bool, str]:
    """Return ``(force_figure, reason)`` for one user message.

    ``force_figure`` True means the caller should pin the chat-LLM's
    ``tool_choice`` to ``sevim_express`` for this turn.  ``reason`` is
    a short server-side log tag — it is never shown to the user.
    """
    raw = (text or "").strip()
    if not raw:
        return False, "empty"
    if _OPT_OUT.search(raw):
        return False, "opt_out"
    if _NOT_DRAWABLE.search(raw):
        return False, "not_drawable"
    scrubbed = _FALSE_FRIENDS.sub(" ", raw)
    if _ASK_LATIN.search(scrubbed):
        return True, "explicit_visual_latin"
    if _ASK_NON_LATIN.search(scrubbed):
        return True, "explicit_visual_non_latin"
    return False, "no_explicit_ask"


# Appended to the system prompt for the turn when the force fires, so
# the model spends its budget writing a good sevim_express prompt
# instead of a chat essay it was never going to be allowed to send
# alone.  Without it the model sometimes emits a long prose answer AND
# the tool call, which reads as the bug the force was meant to fix.
FORCED_VISUAL_BRIEF = (
    "=== THIS TURN: FIGURE IS MANDATORY ===\n"
    "The user explicitly asked to SEE something, and the canvas can "
    "draw it.  You MUST call sevim_express this turn — the server has "
    "pinned the tool and a text-only answer is not reachable.  Put "
    "your effort into the sevim_express prompt: restate the user's "
    "subject in full, keep their exact mathematical concept, and add "
    "any concrete numbers the figure needs.  Do NOT write a long chat "
    "essay first — the canvas narration carries the explanation, so "
    "one short sentence in chat after the tool returns is enough."
)
