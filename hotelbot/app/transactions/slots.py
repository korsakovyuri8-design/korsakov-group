"""Deterministic extraction of service details from guest messages.

Extraction is per field *kind*, so a new service with datetime/count fields
needs no new code. Values are only taken from what the guest wrote; the few
contextual inferences (e.g. "I'm arriving at Podgorica airport" -> drop-off
at the property) are flagged as inferred and always shown back to the guest
in the quote they must explicitly accept.

Supported: English, Montenegrin (Latin/Cyrillic), Russian.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.agent.memory import extract_dates
from app.text import fold
from app.transactions.catalog import ServiceSpec

PROPERTY = "@property"  # placeholder for "the property the guest is staying at"

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

_AIRPORT = r"(?i:airport|aerodrom\w*|аэропорт\w*|аеродром\w*)"
_NAME = r"[A-ZČĆŠŽĐА-ЯЁЂЈЉЊЋЏ][\w'-]+"
_AIRPORT_NAMED_BEFORE = re.compile(rf"({_NAME})\s+{_AIRPORT}", re.UNICODE)
_AIRPORT_NAMED_AFTER = re.compile(rf"{_AIRPORT}\s+({_NAME})", re.UNICODE)
_AIRPORT_ANY = re.compile(_AIRPORT)
_TO_WORDS = {"to", "do", "na", "v", "vo", "u", "k", "until"}
_FROM_WORDS = {"from", "iz", "sa", "s", "od", "ot", "at"}
_ARRIVAL = re.compile(r"\b(arriv\w*|land\w*|fly in|flying in|prilet\w*|slijec\w*|sleti\w*|stizem\w*|stizemo|"
                      r"pristizem\w*|priletayu|prilecu|prizeml\w*|pribyva\w*)")
_PROPERTY_WORDS = r"(?:the\s+)?(?:hotel\w*|property|apartment\w*|apartman\w*|here|ovdje|odavde|otel\w*|gostinic\w*|smjestaj\w*)"
_FROM_PROPERTY = re.compile(rf"\b(?:from|iz|sa|od|ot|iz)\s+{_PROPERTY_WORDS}\b")
_TO_PROPERTY = re.compile(rf"\b(?:to|do|u|v|na)\s+{_PROPERTY_WORDS}\b")
_BARE_PROPERTY = re.compile(rf"^\s*(?:at\s+|from\s+|iz\s+|ot\s+|ispred\s+)?{_PROPERTY_WORDS}\s*$")
_NAMED_DEST = re.compile(rf"\b(?:to|do|в|до)\s+({_NAME}(?:\s+{_NAME})*)", re.UNICODE)
_NAMED_FROM = re.compile(rf"\b(?:from|iz|из|от)\s+({_NAME}(?:\s+{_NAME})*)", re.UNICODE)


LANGUAGE_PHRASES = {
    "ru": ["russian", "russian-speaking", "in russian", "ruski", "ruskom", "na ruskom", "russk*", "na russkom",
           "русск*", "русскоговорящ*", "на русском"],
    "en": ["english", "english-speaking", "in english", "engleski", "engleskom", "na engleskom", "anglijsk*",
           "английск*", "англоговорящ*"],
    "cnr": ["montenegrin", "serbian", "local language", "crnogorsk*", "srpsk*", "na nasem", "черногорск*", "сербск*"],
    "de": ["german", "german-speaking", "njemack*", "nemack*", "немецк*"],
}


def parse_language(folded: str) -> str | None:
    from app.text import contains_phrase

    for code, phrases in LANGUAGE_PHRASES.items():
        if any(contains_phrase(folded, p) for p in phrases):
            return code
    return None


@dataclass
class WhenParts:
    day: date | None = None
    time: time | None = None


@dataclass
class Extraction:
    values: dict[str, object] = field(default_factory=dict)
    inferred: set[str] = field(default_factory=set)
    when: dict[str, WhenParts] = field(default_factory=dict)


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
                break
    return parts


def _direction(folded_before: str) -> str | None:
    words = folded_before.split()
    for w in reversed(words[-2:]):
        if w in _TO_WORDS:
            return "destination"
        if w in _FROM_WORDS:
            return "pickup"
    return None


def parse_places(text: str) -> tuple[dict[str, str], set[str]]:
    """pickup / destination from transport requests."""
    folded = fold(text)
    places: dict[str, str] = {}
    inferred: set[str] = set()
    if _FROM_PROPERTY.search(folded):
        places["pickup"] = PROPERTY
    if _TO_PROPERTY.search(folded):
        places["destination"] = PROPERTY

    m = _AIRPORT_NAMED_BEFORE.search(text) or _AIRPORT_NAMED_AFTER.search(text)
    airport_match = m or _AIRPORT_ANY.search(text)
    if airport_match:
        airport = text[airport_match.start():airport_match.end()].strip()
        arriving = bool(_ARRIVAL.search(folded))
        # Arriving guests are picked up AT the airport, whatever the preposition
        # ("прилетаю в аэропорт" literally reads "to the airport").
        if arriving and places.get("pickup") != PROPERTY:
            role = "pickup"
        else:
            role = _direction(fold(text[:airport_match.start()])) or "destination"
        places.setdefault(role, airport)
        if arriving and role == "pickup" and "destination" not in places:
            places["destination"] = PROPERTY
            inferred.add("destination")
    for pattern, role in ((_NAMED_DEST, "destination"), (_NAMED_FROM, "pickup")):
        if role not in places and (nm := pattern.search(text)):
            name = nm.group(1).strip()
            if not _AIRPORT_ANY.search(name):
                places[role] = name
    return places, inferred


def extract(text: str, spec: ServiceSpec, today: date) -> Extraction:
    folded = fold(text)
    out = Extraction()
    kinds = {f.kind for f in spec.fields}
    if "count" in kinds and (n := parse_count(folded)) is not None:
        for f in spec.fields:
            if f.kind == "count":
                out.values[f.key] = n
    if "datetime" in kinds:
        parts = parse_when(folded, today)
        if parts.day or parts.time:
            for f in spec.fields:
                if f.kind == "datetime":
                    out.when[f.key] = parts
    if "language" in kinds and (lang := parse_language(folded)):
        for f in spec.fields:
            if f.kind == "language":
                out.values[f.key] = lang
    if "place" in kinds:
        places, inferred = parse_places(text)
        for f in spec.fields:
            if f.kind == "place" and f.key in places:
                out.values[f.key] = places[f.key]
        out.inferred |= inferred
    return out


def free_text_answer(text: str, kind: str) -> str | None:
    """A short reply to a question about a place/text field ("Hotel lobby",
    "Kotor old town") is taken as the answer."""
    stripped = text.strip().strip(".!")
    if kind == "place" and _BARE_PROPERTY.match(fold(stripped)):
        return PROPERTY
    if kind in ("place", "text") and 0 < len(stripped.split()) <= 8 and "?" not in stripped:
        return re.sub(r"^(from|to|at|iz|do|sa|из|от|до|в)\s+", "", stripped, flags=re.IGNORECASE)
    return None


def resolve_datetime(parts: WhenParts, previous: datetime | None, now_local: datetime,
                     partial_day: date | None = None) -> tuple[datetime | None, date | None]:
    """Combine extracted parts with an earlier value (for modifications such
    as "what about 6:30?") and the current time. Returns (datetime, pending
    day) - a day without a time stays pending until the time is known."""
    day = parts.day or partial_day or (previous.date() if previous else None)
    tm = parts.time or (previous.timetz().replace(tzinfo=None) if previous and parts.day else None)
    if tm is None:
        return None, day
    if day is None:
        candidate = now_local.replace(hour=tm.hour, minute=tm.minute, second=0, microsecond=0)
        if candidate <= now_local:
            candidate += timedelta(days=1)
        return candidate, None
    return datetime.combine(day, tm, tzinfo=now_local.tzinfo), None


def local_now(now_utc: datetime, tz: str) -> datetime:
    return now_utc.astimezone(ZoneInfo(tz))
