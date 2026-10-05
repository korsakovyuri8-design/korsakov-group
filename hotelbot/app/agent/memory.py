"""Conversation (session) memory.

Facts the guest states about their stay are extracted deterministically and
stored on the conversation as *unconfirmed, guest-stated* facts:

    {"arrival_date": {"value": "2026-12-20", "source": "guest_stated",
                      "confirmed": false, "updated_at": "..."}}

Nothing here is ever treated as a booking fact - only staff (or a future PMS
integration) may set `confirmed: true`. The orchestrator also keeps a small
amount of dialogue state under the reserved key "_state" (e.g. a pending
offer awaiting yes/no).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any

from app.text import contains_phrase, fold

MONTHS = {
    # English
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9,
    "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
    # Montenegrin (nominative + genitive)
    "januar": 1, "januara": 1, "februar": 2, "februara": 2, "mart": 3, "marta": 3, "aprila": 4,
    "maj": 5, "maja": 5, "juna": 6, "jula": 7, "avgust": 8, "avgusta": 8, "septembar": 9,
    "septembra": 9, "oktobar": 10, "oktobra": 10, "novembar": 11, "novembra": 11, "decembar": 12,
    "decembra": 12,
}
NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "jedan": 1, "jedna": 1, "dva": 2, "dvije": 2, "dvoje": 2, "tri": 3, "troje": 3, "cetiri": 4,
    "cetvoro": 4, "cetvoje": 4, "pet": 5, "petoro": 5, "sest": 6, "sestoro": 6,
}
ARRIVAL_CUES = ["arriv*", "check in", "check-in", "checkin", "coming", "from", "dolaz*", "stiz*", "stic*", "prijav*", "od"]
DEPARTURE_CUES = ["checking out", "leav*", "depart*", "check out", "check-out", "checkout", "until", "till", "to", "odlaz*", "odjav*", "napust*", "do"]

_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))
_NUM_ALT = "|".join(NUMBER_WORDS)
_DATE_PATTERNS = [
    re.compile(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\.?"),                       # 20.12. / 20/12/2026
    re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\.?\s+(?:of\s+)?({_MONTH_ALT})\b(?:\s+(\d{{4}}))?"),  # 20 December
    re.compile(rf"\b({_MONTH_ALT})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?"),   # December 20
]
_GUESTS_RE = [
    re.compile(rf"\b(\d{{1,2}}|{_NUM_ALT})\s+(?:people|persons|guests|adults|of us|osob[ae]|gost(?:a|iju)|odrasl\w*)\b"),
    re.compile(rf"\b(?:we are|we're|there are|there will be|nas je|bice nas|biće nas)\s+(\d{{1,2}}|{_NUM_ALT})\b"),
]
_ROOM_RE = re.compile(r"\b(?:room|soba|sobi|sobe|sobu)\s*(?:no\.?|number|broj)?\s*#?(\d{1,4})\b")


def _to_int(token: str) -> int | None:
    return int(token) if token.isdigit() else NUMBER_WORDS.get(token)


def _make_date(day: int, month: int, year: int | None, today: date) -> date | None:
    try:
        if year is not None:
            if year < 100:
                year += 2000
            return date(year, month, day)
        candidate = date(today.year, month, day)
        return candidate if candidate >= today else date(today.year + 1, month, day)
    except ValueError:
        return None


def extract_dates(folded: str, today: date) -> list[tuple[int, date]]:
    """All dates in the text as (position, date), in order of appearance."""
    found: list[tuple[int, date]] = []
    for i, pattern in enumerate(_DATE_PATTERNS):
        for m in pattern.finditer(folded):
            g = m.groups()
            if i == 0:
                day, month, year = int(g[0]), int(g[1]), int(g[2]) if g[2] else None
            elif i == 1:
                day, month, year = int(g[0]), MONTHS[g[1]], int(g[2]) if g[2] else None
            else:
                day, month, year = int(g[1]), MONTHS[g[0]], int(g[2]) if g[2] else None
            d = _make_date(day, month, year, today)
            if d and not any(abs(pos - m.start()) < 3 for pos, _ in found):
                found.append((m.start(), d))
    return sorted(found)


def _cue_before(folded: str, pos: int, cues: list[str]) -> bool:
    window = folded[max(0, pos - 30):pos]
    return any(contains_phrase(window, c) for c in cues)


def extract_facts(text: str, today: date | None = None) -> dict[str, Any]:
    """Guest-stated facts in one message. Returns plain values keyed by fact name."""
    today = today or datetime.now(timezone.utc).date()
    f = fold(text)
    facts: dict[str, Any] = {}

    dates = extract_dates(f, today)
    if len(dates) >= 2 and dates[1][1] > dates[0][1]:
        facts["arrival_date"], facts["departure_date"] = dates[0][1].isoformat(), dates[1][1].isoformat()
    elif len(dates) == 1:
        pos, d = dates[0]
        if _cue_before(f, pos, DEPARTURE_CUES) and not _cue_before(f, pos, ["from", "od"]):
            facts["departure_date"] = d.isoformat()
        elif _cue_before(f, pos, ARRIVAL_CUES):
            facts["arrival_date"] = d.isoformat()

    for pattern in _GUESTS_RE:
        m = pattern.search(f)
        if m and (n := _to_int(m.group(1))) and 0 < n <= 30:
            facts["guest_count"] = n
            break

    if m := _ROOM_RE.search(f):
        facts["room_number"] = m.group(1)
    return facts


def remember(memory: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    """Return a new memory dict with facts merged as unconfirmed guest statements."""
    updated = dict(memory)
    now = datetime.now(timezone.utc).isoformat()
    for key, value in facts.items():
        updated[key] = {"value": value, "source": "guest_stated", "confirmed": False, "updated_at": now}
    return updated


def known_facts(memory: dict[str, Any]) -> dict[str, Any]:
    """Facts only (excluding dialogue state), for prompts and handoff packages."""
    return {k: v for k, v in memory.items() if not k.startswith("_")}
