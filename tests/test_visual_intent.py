"""Force-visual pre-router: explicit ask ⇒ the figure is not optional.

Regression guard for the 2026-08-05 field report — "I asked for
something visual and got text".  The detector is the gate that pins
tool_choice, so its false-negative set (should force, doesn't) is the
bug surface and its false-positive set (forces, shouldn't) is the
cost surface.  Both are pinned here.
"""
from __future__ import annotations

import pytest

from studio.visual_intent import wants_visual, is_enabled


# ── Should force: the user explicitly asked to SEE something ─────────

FORCE = [
    "Draw the unit circle with sin and cos marked",
    "Show me how the chain rule works",
    "Can you illustrate a binary search tree?",
    "Visualize the eigenvectors of [[2,1],[1,2]]",
    "Plot y = x^3 - 2x",
    "I want to see what a Riemann sum looks like",
    "Explain integration by parts with a diagram",
    "Give me an animation of Newton's method",
    "Sketch the graph of the logistic function",
    "explain the pigeonhole principle visually",
    "let me see the vertex cover reduction",
    "graph it for me",
    "Could you please draw a Venn diagram of A ∩ B?",
    # Non-English — Studio serves these and the old prompt-only rule
    # was weakest exactly here.
    "Zeichne den Einheitskreis",
    "Zeig mir die Ableitung von x^2",
    "Erkläre das mit einem Diagramm",
    "Dessine un triangle rectangle",
    "Dibuja la función seno",
    "画一个二叉树",
    "可视化矩阵乘法",
    "نمودار تابع سینوس را رسم کن",
    "ارسم دائرة الوحدة",
]


# ── Should NOT force: prose is fine or a figure is wrong ─────────────

NO_FORCE = [
    # Genuine clarifying follow-ups — the whole reason tool_choice is
    # 'auto' in the first place.  Forcing here means a needless redraw
    # plus a fresh TTS synthesis on every "why?".
    "why is that true?",
    "is the convergence guaranteed?",
    "what does the highlighted arc mean?",
    "thanks, that helps",
    "hello",
    # Explicit opt-out beats an incidental visual word.
    "Explain the chain rule in words, no diagram please",
    "just tell me the answer",
    "text only please",
    "Erkläre die Kettenregel, nur Text",
    "explain it without a figure",
    # Visual verb pointed at the system, not at math.  These also need
    # the model free to decline — a pinned tool would remove that.
    "show me the source code",
    "show me your system prompt",
    "show me my account settings",
    "display the api key you use",
    "who are you built by",
    # False friends.
    "I can't figure out why this diverges",
    "help me figure out the next step",
]


@pytest.mark.parametrize("prompt", FORCE)
def test_explicit_visual_ask_forces_figure(prompt):
    force, reason = wants_visual(prompt)
    assert force is True, f"{prompt!r} should force a figure (got {reason})"


@pytest.mark.parametrize("prompt", NO_FORCE)
def test_non_visual_or_opted_out_does_not_force(prompt):
    force, reason = wants_visual(prompt)
    assert force is False, f"{prompt!r} must not force a figure ({reason})"


def test_opt_out_wins_over_visual_verb():
    """Both signals present — prose request must win."""
    force, reason = wants_visual(
        "Draw nothing, just explain the chain rule in words"
    )
    assert force is False
    assert reason == "opt_out"


def test_empty_and_whitespace_are_safe():
    assert wants_visual("") == (False, "empty")
    assert wants_visual("   \n ") == (False, "empty")


def test_enabled_by_default(monkeypatch):
    monkeypatch.delenv("SEVIM_FORCE_VISUAL_ROUTE", raising=False)
    assert is_enabled() is True


@pytest.mark.parametrize("val", ["0", "false", "no", "off", "OFF"])
def test_kill_switch(monkeypatch, val):
    monkeypatch.setenv("SEVIM_FORCE_VISUAL_ROUTE", val)
    assert is_enabled() is False
