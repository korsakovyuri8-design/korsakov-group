"""Language-agnostic text normalisation shared by retrieval, language
detection and intent classification.

Montenegrin is written in both Latin and Cyrillic, and guests frequently
type without diacritics ("dorucak" for "doručak"), so everything is folded to
ASCII Latin before matching.
"""

from __future__ import annotations

import re
import unicodedata

_CYRILLIC_TO_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "ђ": "dj", "е": "e", "ж": "z",
    "з": "z", "и": "i", "ј": "j", "к": "k", "л": "l", "љ": "lj", "м": "m", "н": "n",
    "њ": "nj", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "ћ": "c", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "c", "џ": "dz", "ш": "s",
    # Montenegrin-specific letters
    "ś": "s", "ź": "z",
}
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def fold(text: str) -> str:
    """Lowercase, transliterate Cyrillic, strip diacritics (đ -> dj)."""
    text = text.lower()
    text = "".join(_CYRILLIC_TO_LATIN.get(ch, ch) for ch in text)
    text = text.replace("đ", "dj")
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(fold(text))


def stem(token: str) -> str:
    """Crude prefix stemmer that works acceptably for both English and the
    heavily inflected Montenegrin (doručak / doručka / doručku -> "doruc")."""
    if len(token) > 5:
        return token[:5]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def stems(text: str, stopwords: frozenset[str] = frozenset()) -> list[str]:
    return [stem(t) for t in tokens(text) if t not in stopwords]


def contains_phrase(folded_text: str, phrase: str) -> bool:
    """Whole-word phrase match on already-folded text. A phrase ending in `*`
    matches any word continuation (e.g. "recepcij*")."""
    p = fold(phrase).strip()
    if p.endswith("*"):
        pattern = r"\b" + re.escape(p[:-1])
    else:
        pattern = r"\b" + re.escape(p) + r"\b"
    return re.search(pattern, folded_text) is not None
