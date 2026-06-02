"""Deterministic language detection + script cross-check.

The earlier language pipeline trusted a single LLM-emitted signal
(gpt-4o-mini's `language` field in localise_narration) at every
step.  Field reports:

  - 2026-06-03: a German prompt got Chinese narration shipped
    (LLM hallucinated `language=zh` for an unambiguously German
    prompt; downstream code applied the Chinese translation
    without cross-checking).
  - 2026-06-03: an English prompt got German narration shipped
    (the chat-LLM paraphrased into German for the tool call, the
    figure-LLM's LANGUAGE RULE made the narration German, the
    localiser's ASCII fast-path passed it through because the
    user's literal was ASCII English — no verification that the
    narration was actually English).

This module is the deterministic backbone behind a three-layer
fix:

  1. detect_language(text)        — stdlib-only classifier.
  2. expected_scripts_for(lang)   — what Unicode scripts the
                                    output narration should
                                    consist of, given the target
                                    language.
  3. text_matches_script(text, scripts)
                                  — cross-check: does the
                                    produced narration look like
                                    it was written in the target
                                    script?

No external dependencies — only `unicodedata` + `re`.  Cost: a
single pass over the prompt text, no LLM round-trip.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Final

__all__ = [
    "detect_language",
    "expected_scripts_for",
    "text_matches_script",
    "describe_language",
]


# ---------------------------------------------------------------------------
# Unicode script classification
# ---------------------------------------------------------------------------
# `unicodedata.name(ch)` returns names like "LATIN CAPITAL LETTER A",
# "ARABIC LETTER ALEF", "CJK UNIFIED IDEOGRAPH-4E2D".  We pick the
# script tag from the first word(s) of the name.  Pre-computed for
# the few hundred most common code points is faster, but a one-time
# uncached `name()` call is ~1 µs — fast enough for prompt-length
# text.

_SCRIPT_PREFIX_TO_TAG: Final[dict[str, str]] = {
    "LATIN":      "Latin",
    "CYRILLIC":   "Cyrillic",
    "GREEK":      "Greek",
    "ARABIC":     "Arabic",
    "HEBREW":     "Hebrew",
    "DEVANAGARI": "Devanagari",
    "BENGALI":    "Bengali",
    "TAMIL":      "Tamil",
    "THAI":       "Thai",
    "HIRAGANA":   "Hiragana",
    "KATAKANA":   "Katakana",
    "HANGUL":     "Hangul",
    "CJK":        "Han",      # CJK UNIFIED IDEOGRAPH ... -> Han
    "ARMENIAN":   "Armenian",
    "GEORGIAN":   "Georgian",
    "ETHIOPIC":   "Ethiopic",
}


def _script_of(ch: str) -> str | None:
    """Return the script name for a single character, or None if
    the character is not an alphabetic letter (digits, punctuation,
    spaces, symbols → None)."""
    if not ch.isalpha():
        return None
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return None
    # The script is the first word for most scripts;
    # CJK is multi-word ("CJK UNIFIED IDEOGRAPH-…").
    first = name.split(" ", 1)[0]
    return _SCRIPT_PREFIX_TO_TAG.get(first)


# ---------------------------------------------------------------------------
# Stopword markers — small, curated, biased toward unique markers
# ---------------------------------------------------------------------------
# These are the words used to disambiguate Latin-script languages.
# Keep them SHORT (so multi-word prompts hit at least one) and
# DISTINCTIVE (so the same word doesn't match three languages).
# `de` and `la` deliberately excluded from non-en sets because they
# clash with French / Spanish / Italian / Portuguese.

_STOPWORDS_EN: Final[frozenset[str]] = frozenset((
    "the", "and", "what", "how", "why", "show", "explain", "is",
    "are", "with", "this", "that", "for", "from", "have", "has",
    "does", "do", "you", "your", "we", "us", "us", "our", "their",
    "them", "they", "but", "not", "or", "if", "so", "than", "then",
    "when", "where", "while", "which", "who", "would", "could",
    "should", "will", "shall", "can", "must", "about", "between",
    "after", "before", "above", "below", "during", "through",
    "show", "draw", "plot", "prove", "find",
))
_STOPWORDS_DE: Final[frozenset[str]] = frozenset((
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einer",
    "und", "ist", "sind", "war", "waren", "wird", "werden",
    "mit", "auf", "für", "über", "unter", "nicht", "kein", "keine",
    "wie", "was", "warum", "wann", "wo", "wer", "weil", "dass",
    "erkläre", "erklären", "zeige", "zeigen", "ich", "du", "wir",
    "ihr", "sie", "es", "wenn", "dann", "noch", "schon", "auch",
    "bei", "vom", "zum", "zur", "aber", "doch", "sehr", "nur",
))
_STOPWORDS_FR: Final[frozenset[str]] = frozenset((
    "le", "les", "des", "une", "un", "est", "sont", "était", "étaient",
    "que", "qui", "pour", "avec", "sur", "sous", "dans", "par",
    "pas", "ne", "ni", "ou", "et", "comment", "pourquoi", "quand",
    "où", "quoi", "expliquer", "montrer", "afficher", "tracer",
    "dessiner", "calculer", "résoudre", "trouver", "vous", "nous",
    "ils", "elles", "ceci", "cela", "celui", "celle",
))
_STOPWORDS_ES: Final[frozenset[str]] = frozenset((
    "el", "los", "las", "una", "es", "son", "era", "eran", "será",
    "que", "quien", "para", "con", "por", "sobre", "bajo", "entre",
    "qué", "cómo", "por qué", "cuándo", "dónde", "quién",
    "no", "ni", "o", "y", "muestra", "mostrar", "explicar",
    "calcular", "resolver", "encontrar", "dibujar", "trazar",
    "nosotros", "vosotros", "ellos", "ellas", "este", "esta",
))
_STOPWORDS_IT: Final[frozenset[str]] = frozenset((
    "il", "lo", "gli", "una", "è", "sono", "era", "erano", "sarà",
    "che", "chi", "per", "con", "su", "tra", "fra", "sotto",
    "non", "né", "o", "e", "come", "perché", "quando", "dove",
    "mostrare", "spiegare", "calcolare", "risolvere", "trovare",
    "noi", "voi", "loro", "questo", "questa",
))
_STOPWORDS_PT: Final[frozenset[str]] = frozenset((
    "os", "as", "uma", "é", "são", "era", "eram", "será",
    "que", "quem", "para", "com", "por", "sobre", "entre", "sob",
    "não", "nem", "ou", "e", "como", "porquê", "porque", "quando",
    "onde", "mostrar", "explicar", "calcular", "resolver",
    "encontrar", "desenhar", "nós", "vós", "eles", "elas", "este",
    "esta",
))

_STOPWORDS: Final[dict[str, frozenset[str]]] = {
    "en": _STOPWORDS_EN,
    "de": _STOPWORDS_DE,
    "fr": _STOPWORDS_FR,
    "es": _STOPWORDS_ES,
    "it": _STOPWORDS_IT,
    "pt": _STOPWORDS_PT,
}


# ---------------------------------------------------------------------------
# Distinctive diacritic / letter sets — last-resort fallback for
# Latin-script prompts that had no stopword hits.
# ---------------------------------------------------------------------------
_DIACRITIC_HINTS: Final[tuple[tuple[str, str], ...]] = (
    # Order matters: most specific letter first.  ß is exclusively
    # German; ñ exclusively Spanish; etc.
    ("de", "ß"),
    ("es", "ñ¿¡"),
    ("fr", "œçàâéèêëîïôûùüÿ"),  # not all are unique to French; checked after Spanish/Portuguese
    ("pt", "ãõçáâàéêíóôú"),
    ("it", "àèéìòù"),
    ("de", "äöü"),  # late: ä/ö/ü also occur in Swedish but unlikely
)


# ---------------------------------------------------------------------------
# Persian / Arabic disambiguation.
# Persian and Arabic share most of the Arabic script, but a few
# codepoints are exclusively (or almost exclusively) Persian:
#
#   پ U+067E pe          ژ U+0698 jeh
#   چ U+0686 cheh        گ U+06AF gaf
#   ی U+06CC farsi yeh   (vs Arabic ي U+064A)
#   ک U+06A9 keheh       (vs Arabic ك U+0643)
#
# Plus the Persian digit set ۰-۹ (U+06F0..U+06F9) vs Arabic-Indic
# ٠-٩ (U+0660..U+0669).  Persian text very commonly contains the
# Persian Yeh / Keheh even when it doesn't contain pe/cheh/jeh/gaf,
# so adding them to the check is what saves "روش نیوتن" (Persian
# 'Newton method') from being classified as Arabic.
# ---------------------------------------------------------------------------
_PERSIAN_LETTERS: Final[str] = "پچژگیک"
_PERSIAN_DIGITS: Final[str] = "۰۱۲۳۴۵۶۷۸۹"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_language(text: str) -> str:
    """Return an ISO 639-1 language code for the input text.

    Returns ``"en"``, ``"de"``, ``"fr"``, ``"es"``, ``"it"``,
    ``"pt"``, ``"fa"``, ``"ar"``, ``"zh"``, ``"ja"``, ``"ko"``,
    ``"hi"``, ``"ru"``, ``"el"``, ``"he"``, ``"th"`` or
    ``"und"`` (undetermined).

    Empty / whitespace-only / sub-2-character input returns
    ``"und"``.  Pure-ASCII text with no detectable stopwords
    defaults to ``"en"`` (English is the platform default and the
    Latin-only fallback is "looks like English").

    Single-pass over the text; no external dependencies; no LLM.
    """
    if not text:
        return "und"
    t = text.strip()
    if len(t) < 2:
        return "und"

    # Pass 1: count letters by script.
    script_counts: dict[str, int] = {}
    total_letters = 0
    for ch in t:
        s = _script_of(ch)
        if s is None:
            continue
        total_letters += 1
        script_counts[s] = script_counts.get(s, 0) + 1

    if total_letters == 0:
        return "und"

    dominant = max(script_counts, key=script_counts.get)
    dominant_share = script_counts[dominant] / total_letters

    # If the dominant script is non-Latin and clearly dominant
    # (≥40% of letters), trust the script.  Math equations often
    # mix scripts (Greek π, Latin x) so we don't demand 100%.
    if dominant != "Latin" and dominant_share >= 0.40:
        if dominant == "Han":
            # Could be Chinese or Japanese; if Hiragana/Katakana
            # also present, it's Japanese.
            if script_counts.get("Hiragana", 0) + script_counts.get("Katakana", 0) > 0:
                return "ja"
            return "zh"
        if dominant in ("Hiragana", "Katakana"):
            return "ja"
        if dominant == "Hangul":
            return "ko"
        if dominant == "Devanagari":
            return "hi"
        if dominant == "Cyrillic":
            # Could be Russian / Ukrainian / Bulgarian; default ru.
            return "ru"
        if dominant == "Greek":
            return "el"
        if dominant == "Hebrew":
            return "he"
        if dominant == "Thai":
            return "th"
        if dominant == "Arabic":
            # Disambiguate Arabic vs Persian.  The Persian Yeh
            # (ی, U+06CC) and Keheh (ک, U+06A9) are different
            # codepoints from their Arabic look-alikes (ي
            # U+064A, ك U+0643) and they appear in essentially
            # every Persian sentence — so checking for them
            # catches Persian prompts that don't contain the
            # rarer pe / cheh / jeh / gaf consonants.
            if any(ch in _PERSIAN_LETTERS for ch in t):
                return "fa"
            if any(ch in _PERSIAN_DIGITS for ch in t):
                return "fa"
            return "ar"
        # Other less-common scripts — pass through.
        return "und"

    # Latin-script branch.  Disambiguate via stopwords.
    # Tokenise lowercased, letters-only words (incl. common Latin
    # diacritics).
    words = re.findall(r"[a-zA-ZÀ-ſ]+", t.lower())
    hits: dict[str, int] = {}
    for lang, sws in _STOPWORDS.items():
        hits[lang] = sum(1 for w in words if w in sws)

    # Pick the language with the most stopword hits, with a
    # bias toward English on ties.
    en_hits = hits["en"]
    best_non_en = max(
        (lang for lang in hits if lang != "en"),
        key=lambda l: hits[l],
    )
    best_non_en_hits = hits[best_non_en]

    # Strong non-English signal: 2+ hits AND beats English.
    if best_non_en_hits >= 2 and best_non_en_hits > en_hits:
        return best_non_en

    # Weak non-English signal: at least one hit and English had
    # zero.  Tiebreak among non-English candidates by hit count.
    if best_non_en_hits >= 1 and en_hits == 0:
        return best_non_en

    # English has any hit -> English.
    if en_hits >= 1:
        return "en"

    # No stopword hits at all — check distinctive diacritics.
    for lang, letters in _DIACRITIC_HINTS:
        if any(ch in letters for ch in t):
            return lang

    # Pure Latin, no markers, no stopwords → default to English.
    return "en"


_EXPECTED_SCRIPTS: Final[dict[str, frozenset[str]]] = {
    # Latin-script languages all share the same script set.
    "en": frozenset({"Latin"}),
    "de": frozenset({"Latin"}),
    "fr": frozenset({"Latin"}),
    "es": frozenset({"Latin"}),
    "it": frozenset({"Latin"}),
    "pt": frozenset({"Latin"}),
    # Non-Latin scripts.
    "fa": frozenset({"Arabic"}),
    "ar": frozenset({"Arabic"}),
    "zh": frozenset({"Han"}),
    "ja": frozenset({"Han", "Hiragana", "Katakana"}),
    "ko": frozenset({"Hangul", "Han"}),
    "hi": frozenset({"Devanagari"}),
    "ru": frozenset({"Cyrillic"}),
    "el": frozenset({"Greek"}),
    "he": frozenset({"Hebrew"}),
    "th": frozenset({"Thai"}),
}


def expected_scripts_for(lang: str) -> frozenset[str]:
    """Return the set of Unicode scripts a string in ``lang`` is
    expected to consist of.  Empty set if the language is
    undetermined or unsupported."""
    return _EXPECTED_SCRIPTS.get(lang, frozenset())


def text_matches_script(text: str, scripts: frozenset[str]) -> bool:
    """Return True if ≥60% of the text's letters fall into the
    expected script set (or `scripts` is empty, in which case we
    have no opinion and pass through).

    The 60% threshold tolerates loanwords, English LaTeX commands
    embedded in a Persian primer, math symbols, etc., while
    catching the German-prompt-but-Chinese-narration class of
    failure (Han characters where Latin is expected).
    """
    if not scripts:
        return True
    if not text:
        return True
    expected = scripts
    matched = 0
    total = 0
    for ch in text:
        s = _script_of(ch)
        if s is None:
            continue
        total += 1
        if s in expected:
            matched += 1
    if total == 0:
        return True
    return matched / total >= 0.60


_LANGUAGE_NAMES: Final[dict[str, str]] = {
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "fa": "Persian (Farsi)",
    "ar": "Arabic",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "hi": "Hindi",
    "ru": "Russian",
    "el": "Greek",
    "he": "Hebrew",
    "th": "Thai",
    "und": "(undetermined)",
}


def describe_language(lang: str) -> str:
    """Human-readable language name for use in LLM prompts.
    'de' -> 'German', 'fa' -> 'Persian (Farsi)', etc."""
    return _LANGUAGE_NAMES.get(lang, lang.upper())
