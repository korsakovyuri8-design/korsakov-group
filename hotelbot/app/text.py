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
    # Russian-only letters (shared letters use the mapping above)
    "ы": "y", "э": "e", "я": "ya", "ю": "yu", "й": "j", "щ": "sc", "ь": "", "ъ": "", "ё": "e",
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


# --------------------------------------------------------------------------
# Montenegrin/Serbian Latin -> Cyrillic (for replying in the guest's script)
# --------------------------------------------------------------------------

_LAT_DIGRAPHS = {"lj": "љ", "nj": "њ", "dž": "џ", "Lj": "Љ", "LJ": "Љ", "Nj": "Њ", "NJ": "Њ", "Dž": "Џ", "DŽ": "Џ"}
_LAT_TO_CYR = dict(zip(
    "abcčćdđefghijklmnoprsštuvzžśź",
    ["а", "б", "ц", "ч", "ћ", "д", "ђ", "е", "ф", "г", "х", "и", "ј", "к", "л", "м", "н", "о", "п",
     "р", "с", "ш", "т", "у", "в", "з", "ж", "с́", "з́"],
))
_FOREIGN_RE = re.compile(r"[qwxy]", re.IGNORECASE)
# Loanwords and technical terms conventionally kept in Latin inside Cyrillic text.
_KEEP_LATIN = {"check-in", "check-out", "checkin", "checkout", "wi-fi", "wifi", "e-mail", "email", "online"}
_WORD_RE = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*", re.UNICODE)


def _word_to_cyrillic(word: str) -> str:
    if word.lower() in _KEEP_LATIN or _FOREIGN_RE.search(word) or (len(word) > 1 and word.isupper()):
        return word  # EUR, Wi-Fi, check-in ...
    out, i = [], 0
    while i < len(word):
        pair = word[i:i + 2]
        if pair in _LAT_DIGRAPHS or pair.lower() in _LAT_DIGRAPHS:
            cyr = _LAT_DIGRAPHS.get(pair) or _LAT_DIGRAPHS[pair.lower()]
            out.append(cyr.upper() if pair[0].isupper() else cyr)
            i += 2
            continue
        ch = word[i]
        cyr = _LAT_TO_CYR.get(ch.lower())
        out.append(ch if cyr is None else (cyr.upper() if ch.isupper() else cyr))
        i += 1
    return "".join(out)


def latin_to_cyrillic(text: str) -> str:
    """Transliterate Montenegrin/Serbian Latin script to Cyrillic, leaving
    numbers, acronyms and foreign words (Wi-Fi, check-in, EUR) untouched."""
    return _WORD_RE.sub(lambda m: _word_to_cyrillic(m.group(0)), text)
