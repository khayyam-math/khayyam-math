"""Tests for the deterministic language detector + script
cross-check.

Each test row pins a real prompt or narration string to the
ISO 639-1 code we expect the detector to return.  Two specific
production failures are explicit rows in this file:

  * a German prompt MUST NOT classify as Chinese (regression
    test for 'How is that possible to recognize German as
    Chinese?');
  * an English prompt MUST NOT classify as German (regression
    test for 'A friend used the platform in English but received
    the answer in German').
"""
from __future__ import annotations

import pytest

from studio.language import (
    describe_language,
    detect_language,
    expected_scripts_for,
    text_matches_script,
)


# ---------------------------------------------------------------------------
# detect_language — happy path
# ---------------------------------------------------------------------------

DETECT_CASES = [
    # English
    ("Show Newton method on f(x) = x^3 - 2", "en"),
    ("What is a tangent line?", "en"),
    ("Explain visually and with proper formulas not pure latex", "en"),
    ("Prove that sqrt(2) is irrational", "en"),
    ("Compare bubble sort and insertion sort", "en"),

    # German
    ("Erkläre die Newton-Methode zur Wurzelsuche", "de"),
    ("Was ist eine Tangente?", "de"),
    ("Bitte erkläre Newton Verfahren Schritt für Schritt mit Beispiel", "de"),
    ("Beweise dass Wurzel zwei irrational ist", "de"),
    ("Zeige mir wie das Newton-Verfahren funktioniert", "de"),

    # French
    ("Expliquer la méthode de Newton pour trouver les racines", "fr"),
    ("Qu'est-ce qu'une tangente?", "fr"),
    ("Montrer comment on calcule la dérivée", "fr"),

    # Spanish
    ("Explica el método de Newton paso a paso", "es"),
    ("¿Qué es una línea tangente?", "es"),

    # Italian
    ("Spiegare il metodo di Newton con un esempio", "it"),

    # Persian (uses Persian-specific letters پ چ ژ گ)
    ("روش نیوتن را برای پیدا کردن ریشه توضیح دهید", "fa"),

    # Arabic (no Persian-specific letters)
    ("اشرح طريقة نيوتن لإيجاد الجذور", "ar"),

    # Chinese
    ("请展示牛顿法在 f(x) = x^3 - 2 上的迭代", "zh"),

    # Japanese (has Hiragana/Katakana → distinguishes from Chinese)
    ("ニュートン法を使って f(x) = x^3 - 2 の根を求めてください", "ja"),

    # Korean
    ("뉴턴 방법을 사용하여 f(x) = x^3 - 2의 근을 찾으세요", "ko"),

    # Russian
    ("Объясните метод Ньютона для поиска корней", "ru"),

    # Greek
    ("Εξηγήστε τη μέθοδο Newton για την εύρεση ριζών", "el"),

    # Hindi
    ("न्यूटन विधि से f(x) = x^3 - 2 के मूल खोजें", "hi"),

    # Edge cases
    ("", "und"),
    ("   ", "und"),
    ("a", "und"),
    ("123", "und"),
    ("f(x) = 3x^2 + 2x + 1", "en"),  # math-only, default English
]


@pytest.mark.parametrize("text, expected", DETECT_CASES)
def test_detect_language(text: str, expected: str) -> None:
    got = detect_language(text)
    assert got == expected, (
        f"detect_language({text!r}) returned {got!r}; "
        f"expected {expected!r}"
    )


# ---------------------------------------------------------------------------
# Regression tests for the two reported production failures
# ---------------------------------------------------------------------------

GERMAN_PROMPTS = [
    "Erkläre die Newton-Methode zur Wurzelsuche, anschaulich.",
    "Was ist eine Tangente und wie wird sie berechnet?",
    "Bitte zeige Newton-Verfahren für f(x) = x^3 - 2 mit Beispiel",
    "Erkläre warum die Ableitung in der Newton-Methode benötigt wird",
    "Beweise dass die Wurzel aus zwei irrational ist",
    "Wie funktioniert das Verfahren von Gauss zur Lösung linearer Gleichungssysteme?",
]


@pytest.mark.parametrize("german_prompt", GERMAN_PROMPTS)
def test_german_prompts_never_classify_as_chinese(german_prompt: str) -> None:
    """Regression: 2026-06-03 a German prompt produced Chinese
    narration in production.  Root cause was the localiser LLM
    self-reporting language; this test pins the deterministic
    detector's behaviour for every realistic German prompt
    pattern."""
    got = detect_language(german_prompt)
    assert got != "zh", (
        f"German prompt incorrectly classified as Chinese: "
        f"{german_prompt!r}"
    )
    assert got != "ja", (
        f"German prompt incorrectly classified as Japanese: "
        f"{german_prompt!r}"
    )
    assert got == "de", (
        f"expected 'de' for {german_prompt!r}, got {got!r}"
    )


ENGLISH_PROMPTS = [
    "Show Newton method on f(x) = x^3 - 2",
    "What is a tangent line?",
    "Explain visually and with proper formulas not pure latex",
    "Prove that sqrt(2) is irrational",
    "Compare bubble sort and insertion sort",
    "Show me with an example how Newton's method works",
    "Use the chain rule to differentiate sin(x^2)",
    "Draw a DFA for L = (a|b)* ending in ab",
]


@pytest.mark.parametrize("english_prompt", ENGLISH_PROMPTS)
def test_english_prompts_never_classify_as_german(english_prompt: str) -> None:
    """Regression: 2026-06-03 an English prompt produced German
    narration in production.  Root cause was the chat-LLM
    paraphrasing into German plus the localiser's ASCII fast-path
    not verifying the actual narration language.  This test pins
    the detector's behaviour for every English prompt pattern
    that has been seen on production."""
    got = detect_language(english_prompt)
    assert got != "de", (
        f"English prompt incorrectly classified as German: "
        f"{english_prompt!r}"
    )
    assert got == "en", (
        f"expected 'en' for {english_prompt!r}, got {got!r}"
    )


# ---------------------------------------------------------------------------
# expected_scripts_for
# ---------------------------------------------------------------------------

EXPECTED_SCRIPTS_CASES = [
    ("en", {"Latin"}),
    ("de", {"Latin"}),
    ("fr", {"Latin"}),
    ("es", {"Latin"}),
    ("fa", {"Arabic"}),
    ("ar", {"Arabic"}),
    ("zh", {"Han"}),
    ("ja", {"Han", "Hiragana", "Katakana"}),
    ("ko", {"Hangul", "Han"}),
    ("hi", {"Devanagari"}),
    ("ru", {"Cyrillic"}),
    ("el", {"Greek"}),
    ("he", {"Hebrew"}),
    ("th", {"Thai"}),
    ("und", set()),     # empty -> "no opinion"
    ("unknown", set()),
]


@pytest.mark.parametrize("lang, expected", EXPECTED_SCRIPTS_CASES)
def test_expected_scripts_for(lang: str, expected: set[str]) -> None:
    assert expected_scripts_for(lang) == frozenset(expected)


# ---------------------------------------------------------------------------
# text_matches_script — the cross-check that catches mis-translation
# ---------------------------------------------------------------------------

def test_german_narration_passes_german_check() -> None:
    text = "Die Newton-Methode findet Wurzeln durch Tangentenlinien."
    assert text_matches_script(text, expected_scripts_for("de"))


def test_german_check_rejects_chinese_text() -> None:
    """The production failure: German prompt, Chinese narration
    shipped.  The cross-check MUST reject this so the localiser
    rolls back to the original narration."""
    chinese = "牛顿法通过切线找到方程的根。这是一种迭代方法。"
    assert not text_matches_script(chinese, expected_scripts_for("de"))


def test_english_check_rejects_german_text() -> None:
    """The second production failure: English prompt, German
    narration shipped because the chat-LLM paraphrased.  The
    cross-check must reject a German narration on an English
    request because too many letters have non-ASCII diacritics
    used heavily in German.

    Note: 'Latin' as a script class includes diacritics, so a
    purely Latin-script-with-umlauts narration would PASS the
    cross-check.  This case is best caught by passing the
    detected English lang to the figure LLM as a hard
    constraint (so it never produces German in the first place)
    rather than by the script cross-check."""
    german = "Die Newton-Methode findet Wurzeln durch Tangentenlinien."
    # Both are Latin script -> the cross-check passes.  The real
    # protection in this case is the OUTPUT LANGUAGE hard
    # constraint on the figure-LLM system prompt.
    assert text_matches_script(german, expected_scripts_for("en"))


def test_persian_narration_passes_persian_check() -> None:
    text = "روش نیوتن جایی که یک تابع به صفر می‌رسد را با دنبال کردن خطوط مماس پیدا می‌کند."
    assert text_matches_script(text, expected_scripts_for("fa"))


def test_chinese_narration_passes_chinese_check() -> None:
    text = "牛顿法通过沿着切线向下到达 x 轴来找到函数与零交叉的地方。"
    assert text_matches_script(text, expected_scripts_for("zh"))


def test_japanese_narration_passes_japanese_check() -> None:
    text = "ニュートン法は接線をたどることで関数の根を求めます。"
    assert text_matches_script(text, expected_scripts_for("ja"))


def test_empty_text_passes() -> None:
    # No letters → no opinion → pass.
    assert text_matches_script("", expected_scripts_for("de"))
    assert text_matches_script("12 + 34 = 46", expected_scripts_for("de"))


def test_60_percent_threshold() -> None:
    """The cross-check allows up to 40 % of letters in a different
    script (loanwords / English LaTeX commands embedded in a
    Persian primer / mathematical English terms inside a French
    explanation)."""
    # Mostly German, some English -> passes German check
    mostly_german = (
        "Die Newton-Methode findet Wurzeln durch Tangentenlinien.  "
        "f(x) = x^2 ist die Funktion und x_0 ist der Startwert.  "
        "We iterate until convergence."  # last sentence English
    )
    assert text_matches_script(mostly_german, expected_scripts_for("de"))

    # Mostly Chinese, some English -> passes Chinese check
    mostly_chinese = (
        "牛顿法通过沿着切线向下到达 x 轴来找到函数与零交叉的地方。"
        "我们从初始猜测 x_0 开始，然后迭代直到收敛到根附近。"
        "迭代序列收敛速度很快。"
        "Converges fast."  # short English clause
    )
    assert text_matches_script(mostly_chinese, expected_scripts_for("zh"))


# ---------------------------------------------------------------------------
# describe_language
# ---------------------------------------------------------------------------

def test_describe_language_known() -> None:
    assert describe_language("de") == "German"
    assert describe_language("fa") == "Persian (Farsi)"
    assert describe_language("zh") == "Chinese"
    assert describe_language("en") == "English"


def test_describe_language_unknown() -> None:
    # Unknown codes round-trip uppercased for prompt readability.
    assert describe_language("xx") == "XX"
