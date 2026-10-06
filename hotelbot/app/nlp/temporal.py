"""Language-neutral parsing of quantities, days and times (en / cnr / ru,
folded text). Shared by discovery ("open at 22:30 on Friday") and
transactions ("pickup tomorrow at 6") - it belongs to neither world."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, time, timedelta

from app.agent.memory import extract_dates

_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "jedan": 1, "jedna": 1, "dva": 2, "dvije": 2, "dvoje": 2, "tri": 3, "troje": 3, "cetiri": 4, "cetvoro": 4,
    "pet": 5, "petoro": 5, "sest": 6,
    "odin": 1, "odna": 1, "dvoe": 2, "troe": 3, "cetvero": 4, "cetyre": 4, "pyatero": 5, "pyat": 5,
}
_NUM = r"(\d{1,2}|" + "|".join(_NUM_WORDS) + r")"
_COUNT_NOUNS = (r"people|persons?|guests?|adults?|passengers?|pax|of us|osob\w*|gost\w*|putnik\w*|odrasl\w*|"
                r"celovek\w*|passazir\w*|vzrosl\w*")
_COUNT_RES = [
    re.compile(rf"\b{_NUM}\s+(?:{_COUNT_NOUNS})\b"),
    re.compile(rf"\b(?:we are|we're|there are|there will be|nas je|bice nas|nas)\s+{_NUM}\b"),
    re.compile(rf"\bfor\s+{_NUM}\s*(?:{_COUNT_NOUNS})?\s*(?:,|\.|$)"),
]
_ALONE = re.compile(r"\b(just me|only me|i am alone|i'm alone|sam sam|sama sam|odin|odna|ya odin|ya odna)\b")

_DAY_WORDS = [
    (re.compile(r"\b(day after tomorrow|prekosutra|poslezavtra)\b"), 2),
    (re.compile(r"\b(tomorrow|sutra|zavtra)\b"), 1),
    (re.compile(r"\b(today|tonight|danas|veceras|segodnya)\b"), 0),
]
# Weekdays (en / cnr incl. inflections / ru folded) -> 0=Monday
_WEEKDAYS = [
    (0, r"monday|ponedjelj\w*|ponedelj\w*|ponedelnik\w*"),
    (1, r"tuesday|utor\w*|vtornik\w*"),
    (2, r"wednesday|srijed\w*|sred\w*"),
    (3, r"thursday|cetvrt\w*|cetverg\w*"),
    (4, r"friday|petak|petk\w*|pyatnic\w*"),
    (5, r"saturday|subot\w*|subbot\w*"),
    (6, r"sunday|nedjelj\w*|nedelj\w*|voskresen\w*"),
]
_WEEKDAY_RES = [(n, re.compile(rf"\b(?:{p})\b")) for n, p in _WEEKDAYS]
# Vague day parts -> a concrete time that is SHOWN to the guest for confirmation.
DAY_PARTS = [
    (re.compile(r"\b(morning|ujutru|ujutro|prije podne|utrom|s utra)\b"), time(9, 0)),
    (re.compile(r"\b(noon|midday|podne|v polden|v obed)\b"), time(12, 0)),
    (re.compile(r"\b(afternoon|popodne|posle obeda|dnem)\b"), time(15, 0)),
    (re.compile(r"\b(evening|uvece|navece|vecerom)\b"), time(19, 0)),
    (re.compile(r"\b(night|tonight|nocu|veceras|nochyu|noch)\b"), time(22, 0)),
]

_AM = re.compile(r"\b(am|a\.m\.|morning|ujutru|ujutro|izjutra|utra|utrom)\b")
_PM = re.compile(r"\b(pm|p\.m\.|evening|tonight|uvece|navece|vecera|vecerom|popodne|dnya|afternoon)\b")
_TIME_RES = [
    re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)"),      # 6am, 6:30 pm
    re.compile(r"\b(\d{1,2}):(\d{2})\b"),                                  # 6:30, 22:30
    re.compile(r"\b(\d{1,2})(?:[.:](\d{2}))?\s*(?:h|sati|casova|cas|casov)\b"),   # 6h, 22.30h, 6 sati
    re.compile(r"\b(\d{1,2})\s+(?:in the morning|in the evening|utra|vecera|dnya|noci|ujutru|ujutro|uvece|navece)\b"),
    re.compile(r"\b(?:at|u|v|vo|oko|around|by|k)\s+(\d{1,2})(?:[.](\d{2}))?\b(?!\s*(?:" + _COUNT_NOUNS + r"))"),
]


@dataclass
class WhenParts:
    day: date | None = None
    time: time | None = None
    # True when the time came from a vague day part ("evening"): fine for a
    # day rental, never good enough for a driver's pickup time.
    approximate: bool = False


def _to_int(token: str) -> int | None:
    return int(token) if token.isdigit() else _NUM_WORDS.get(token)


def parse_count(folded: str) -> int | None:
    if _ALONE.search(folded):
        return 1
    for pattern in _COUNT_RES:
        m = pattern.search(folded)
        if m and (n := _to_int(m.group(1))) and 0 < n <= 60:
            return n
    return None


def parse_when(folded: str, today: date) -> WhenParts:
    parts = WhenParts()
    for pattern, offset in _DAY_WORDS:
        if pattern.search(folded):
            parts.day = today + timedelta(days=offset)
            break
    if parts.day is None:
        for weekday, pattern in _WEEKDAY_RES:
            if pattern.search(folded):
                parts.day = today + timedelta(days=(weekday - today.weekday()) % 7)
                break
    if parts.day is None:
        dates = extract_dates(folded, today)
        if dates:
            parts.day = dates[0][1]
    for pattern in _TIME_RES:
        for m in pattern.finditer(folded):
            hour = int(m.group(1))
            minute = int(m.group(2)) if m.lastindex and m.lastindex >= 2 and m.group(2) else 0
            matched = m.group(0)
            # "20.12." is a date, not a time (only relevant for "." separators)
            if "." in matched.strip(".") and folded[m.end():m.end() + 1] == ".":
                continue
            if hour > 24 or minute > 59:
                continue
            meridiem = m.group(3) if pattern is _TIME_RES[0] else None
            window = folded[m.start():m.end() + 12]
            if meridiem:
                if meridiem.startswith("p") and hour < 12:
                    hour += 12
                elif meridiem.startswith("a") and hour == 12:
                    hour = 0
            elif (_PM.search(window) or _PM.search(folded)) and hour < 12 and not _AM.search(window):
                hour += 12
            if hour == 24:
                hour = 0
            parts.time = time(hour, minute)
            break
        if parts.time:
            break
    if parts.time is None:
        for pattern, t in DAY_PARTS:
            if pattern.search(folded):
                parts.time = t
                parts.day = parts.day or today   # "this evening"
                parts.approximate = True
                break
    return parts
