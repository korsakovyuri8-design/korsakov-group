"""Lightweight language detection for guest messages.

Supported reply languages are English ("en") and Montenegrin ("cnr",
ISO 639-3). Serbian, Croatian and Bosnian input is mutually intelligible
with Montenegrin and is answered in Montenegrin. Other detected languages
fall back to the conversation's language (or English).

Signals, strongest first: Montenegrin Cyrillic letters / Latin diacritics,
then function-word votes on diacritic-folded text. Russian/Ukrainian Cyrillic
is recognised so it is not mistaken for Montenegrin Cyrillic.

The detector is deliberately dependency-free; adding a language means adding
a word list (or swapping in a statistical detector behind `detect_language`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.text import tokens

SUPPORTED_LANGUAGES = ("en", "cnr")
DEFAULT_LANGUAGE = "en"

_CNR_DIACRITICS = re.compile(r"[čćšžđśź]", re.IGNORECASE)
_SR_CYRILLIC = re.compile(r"[јљњћђџ]", re.IGNORECASE)   # letters absent from Russian
_RU_CYRILLIC = re.compile(r"[ыэъьяюёйщ]", re.IGNORECASE)  # letters absent from Montenegrin
_ANY_CYRILLIC = re.compile(r"[Ѐ-ӿ]")

_WORDS = {
    "en": set(
        """the is are was what when where how can could would does you your we our my
        an and or for with please thanks thank hello hi there have has need want room
        breakfast time check this that it of in at be will help""".split()
    ),
    "cnr": set(
        """je su sam smo ste da li sta sto kad kada gdje kako koliko mogu moze mozemo mozete
        imate ima imamo molim hvala zdravo dobar dan jutro vece veceras sobu soba sobe
        dorucak za od sa na iz ali ili jos vec treba trebam zelim hocu nam vam mi vi
        biti bice hotelu recepcija pomoc ne nije nijesu""".split()
    ),
}
# Words such as "a", "i", "do", "to", "hotel" exist in both languages and are
# intentionally left out of both lists.


@dataclass(frozen=True)
class LanguageGuess:
    language: str | None   # None = no reliable signal
    confidence: float
    detected: str | None = None  # raw detection, may be unsupported (e.g. "ru")


def detect_language(text: str) -> LanguageGuess:
    if _SR_CYRILLIC.search(text):
        return LanguageGuess("cnr", 0.95, "cnr")
    if _RU_CYRILLIC.search(text):
        return LanguageGuess(None, 0.8, "ru")
    if _ANY_CYRILLIC.search(text):
        return LanguageGuess("cnr", 0.7, "cnr")

    words = tokens(text)
    votes = {lang: sum(1 for w in words if w in vocab) for lang, vocab in _WORDS.items()}
    if _CNR_DIACRITICS.search(text):
        votes["cnr"] += 2
    total = sum(votes.values())
    if total == 0:
        return LanguageGuess(None, 0.0)
    best = max(votes, key=votes.get)  # type: ignore[arg-type]
    confidence = votes[best] / total
    if confidence <= 0.5:
        return LanguageGuess(None, confidence)
    return LanguageGuess(best, confidence, best)


def resolve_reply_language(guess: LanguageGuess, conversation_language: str | None) -> str:
    """Language to reply in: a confident detection wins; otherwise keep the
    conversation's language (so "ok" or "👍" does not flip it)."""
    if guess.language in SUPPORTED_LANGUAGES and (guess.confidence >= 0.6 or not conversation_language):
        return guess.language
    return conversation_language or guess.language or DEFAULT_LANGUAGE
