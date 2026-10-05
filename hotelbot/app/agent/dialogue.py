"""Deterministic dialogue helpers for knowledge questions.

* `split_clauses` - "What time is breakfast and do you have a sauna?" is two
  questions; each is grounded (or not) on its own, so the bot can answer one
  part and say it cannot confirm the other.
* `is_follow_up` - "Is it free?" after "Do you have parking?" refers to the
  previous question; retrieval then uses both, instead of matching "free"
  against whatever item mentions it first.
"""

from __future__ import annotations

import re

from app.text import fold, tokens

# Conjunctions are language-specific: the English pronoun "I" must not be
# read as the Montenegrin conjunction "i".
_CONJUNCTIONS = {
    "en": r"and|also",
    "cnr": r"i|и|a takođe|takođe|takodje|а такође|такође",
    "ru": r"и|а также",
}
_ANAPHORA = {fold(w) for w in "it its that there they them ono tamo njega nju это он она оно там его её ее".split()}
_CONTINUATION_START = {
    "en": {"and", "also", "what", "how"},          # "and for dogs?", "what about parking?"
    "cnr": {fold(w) for w in "i a и а".split()},   # "A parking?", "I za pse?"
    "ru": {fold(w) for w in "и а".split()},        # "А парковка?"
}
FOLLOW_UP_MAX_TOKENS = 7


def split_clauses(text: str, locale: str = "en") -> list[str]:
    """Independent questions in one message; a single clause if splitting
    would produce fragments (fewer than 2 words)."""
    conj = _CONJUNCTIONS.get(locale.split("-")[0], _CONJUNCTIONS["en"])
    flags = re.IGNORECASE if locale.startswith("en") else 0  # "I" vs "i" matters in cnr
    parts = [p.strip(" ,.;!") for p in re.split(rf"\?+|\s+(?:{conj})\s+", text, flags=flags)]
    parts = [p for p in parts if p]
    if len(parts) < 2 or any(len(p.split()) < 2 for p in parts):
        return [text]
    return parts


def is_follow_up(text: str, locale: str = "en") -> bool:
    words = tokens(text)
    if not words or len(words) > FOLLOW_UP_MAX_TOKENS:
        return False
    starts = _CONTINUATION_START.get(locale.split("-")[0], _CONTINUATION_START["en"])
    return bool(set(words) & _ANAPHORA) or words[0] in starts
