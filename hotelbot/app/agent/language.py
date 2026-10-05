"""Lightweight language detection for guest messages.

Reply *locales* (language + script):

    en        English
    cnr       Montenegrin, Latin script (ISO 639-3 "cnr")
    cnr-Cyrl  Montenegrin, Cyrillic script - used when the guest writes Cyrillic
    ru        Russian - routing, safety/handoff and operational templates

Serbian, Croatian and Bosnian input is mutually intelligible with Montenegrin
and is answered in Montenegrin (in the script the guest used).

Signals, strongest first: script-specific letters (ј љ њ ћ ђ џ vs ы э ъ ь я
ю ё й щ), Latin diacritics, then function-word votes on folded text.

The detector is deliberately dependency-free; adding a language means adding
a word list (or swapping in a statistical detector behind `detect_language`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.text import fold, tokens

SUPPORTED_LANGUAGES = ("en", "cnr", "ru")
SUPPORTED_LOCALES = ("en", "cnr", "cnr-Cyrl", "ru")
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

# Russian vs Montenegrin function words, folded (used only for Cyrillic text
# that has no script-specific letters, e.g. "Где парковка?").
# Only words that do NOT exist in Montenegrin/Serbian: "у" (in), "где" (ekavian
# "where"), "да", "кад" are shared and must not vote.
_RU_WORDS = {fold(w) for w in "что как когда есть вас меня мне нас вы мы это можно пожалуйста спасибо здравствуйте номер завтрак сколько во".split()}
_CNR_CYR_WORDS = {fold(w) for w in "шта је ли гдје када имате може хвала здраво доручак соба собу соби помозите молим".split()}


@dataclass(frozen=True)
class LanguageGuess:
    language: str | None   # None = no reliable signal
    confidence: float
    detected: str | None = None  # raw detection
    script: str = "Latn"         # "Latn" | "Cyrl"

    @property
    def locale(self) -> str | None:
        if self.language == "cnr" and self.script == "Cyrl":
            return "cnr-Cyrl"
        return self.language


def detect_language(text: str) -> LanguageGuess:
    if _ANY_CYRILLIC.search(text):
        return _detect_cyrillic(text)

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


def _detect_cyrillic(text: str) -> LanguageGuess:
    sr, ru = len(_SR_CYRILLIC.findall(text)), len(_RU_CYRILLIC.findall(text))
    if sr and sr >= ru:
        return LanguageGuess("cnr", 0.95, "cnr", "Cyrl")
    if ru:
        return LanguageGuess("ru", 0.9, "ru", "Cyrl")
    words = set(tokens(text))
    ru_votes, cnr_votes = len(words & _RU_WORDS), len(words & _CNR_CYR_WORDS)
    if ru_votes > cnr_votes:
        return LanguageGuess("ru", 0.7, "ru", "Cyrl")
    # No distinguishing signal: Montenegrin Cyrillic, but with low confidence so
    # an established conversation language is kept.
    return LanguageGuess("cnr", 0.55 if cnr_votes == 0 else 0.7, "cnr", "Cyrl")


def resolve_reply_language(guess: LanguageGuess, conversation_language: str | None) -> str:
    """Locale to reply in: a confident detection wins; otherwise keep the
    conversation's locale (so "ok" or "👍" does not flip it)."""
    if guess.language in SUPPORTED_LANGUAGES and (guess.confidence >= 0.6 or not conversation_language):
        return guess.locale  # type: ignore[return-value]
    return conversation_language or guess.locale or DEFAULT_LANGUAGE


def base_language(locale: str | None) -> str:
    """"cnr-Cyrl" -> "cnr"."""
    return (locale or DEFAULT_LANGUAGE).split("-")[0]
