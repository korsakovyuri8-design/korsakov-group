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
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.text import fold
from app.nlp.temporal import (  # noqa: F401  (re-exported for callers)
    _COUNT_NOUNS,
    _NUM,
    DAY_PARTS,
    WhenParts,
    _to_int,
    parse_count,
    parse_when,
)
from app.transactions.catalog import ServiceSpec

PROPERTY = "@property"  # placeholder for "the property the guest is staying at"

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
_NAMED_DEST = re.compile(rf"\b(?:to|do|в|до)\s+(?:the\s+)?({_NAME}(?:\s+{_NAME})*)", re.UNICODE)
# Capitalised words that follow a place name but are not part of it
# ("from Podgorica Friday evening").
_NOT_PLACE = re.compile(r"(\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Today|Tomorrow|Tonight|"
                        r"January|February|March|April|May|June|July|August|September|October|November|December|"
                        r"Ponedjeljak|Utorak|Srijeda|Četvrtak|Petak|Subota|Nedjelja|Sutra|Danas|"
                        r"Понедельник|Вторник|Среда|Четверг|Пятница|Суббота|Воскресенье|Завтра|Сегодня)\b.*)$")
_NAMED_FROM = re.compile(rf"\b(?:from|iz|из|от)\s+(?:the\s+)?({_NAME}(?:\s+{_NAME})*)", re.UNICODE)


LANGUAGE_PHRASES = {
    "ru": ["russian", "russian-speaking", "in russian", "ruski", "ruskom", "na ruskom", "russk*", "na russkom",
           "русск*", "русскоговорящ*", "на русском"],
    "en": ["english", "english-speaking", "in english", "engleski", "engleskom", "na engleskom", "anglijsk*",
           "английск*", "англоговорящ*"],
    "cnr": ["montenegrin", "serbian", "local language", "crnogorsk*", "srpsk*", "na nasem", "черногорск*", "сербск*"],
    "de": ["german", "german-speaking", "njemack*", "nemack*", "немецк*"],
    "it": ["italian", "italian-speaking", "italijansk*", "итальянск*"],
    "fr": ["french", "french-speaking", "francusk*", "французск*"],
}


def parse_language(folded: str) -> str | None:
    from app.text import contains_phrase

    for code, phrases in LANGUAGE_PHRASES.items():
        if any(contains_phrase(folded, p) for p in phrases):
            return code
    return None


@dataclass
class Extraction:
    values: dict[str, object] = field(default_factory=dict)
    inferred: set[str] = field(default_factory=set)
    when: dict[str, WhenParts] = field(default_factory=dict)


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
            name = _NOT_PLACE.sub("", nm.group(1)).strip()
            if not name:
                continue
            if not _AIRPORT_ANY.search(name):
                places[role] = name
    return places, inferred


# ----------------------------------------------------------- marketplace kinds
_N = _NUM
_ITEM_UNITS = r"(?:pairs?|sets?|pieces?|units?|parova|para|kompleta?|komad\w*|par|комплект\w*|пар\w*|штук\w*)"
_QTY_RES = [
    re.compile(rf"\b{_N}\s+{_ITEM_UNITS}\b"),
    re.compile(rf"\b{_N}\s+(?:\w+\s+)?(?:skis?|snowboards?|bikes?|bicycles?|e-bikes?|ebikes?|cars?|scooters?|"
               rf"tents?|skij\w*|bicikl\w*|automobil\w*|lyz\w*|velosiped\w*|elektrovelosiped\w*|palatk\w*|"
               rf"сноуборд\w*|snoubord\w*)\b"),
]
_ONE_ITEM = re.compile(r"\b(?:a|an|one|jedan|jednu|jedno|odin|odnu|odno)\s+(?:\w+\s+)?(?:car|bike|bicycle|e-bike|"
                       r"ebike|scooter|tent|snowboard|automobil|bicikl|skuter|snoubord)\b")
_LUGGAGE = re.compile(rf"\b{_N}\s+(?:big\s+|large\s+|small\s+)?(?:bags?|suitcases?|pieces of luggage|luggage|"
                      rf"kofer\w*|torb\w*|kofera|kofere|cemodan\w*|сумк\w*|чемодан\w*|bagaz\w*)\b")
_CHILD_SEAT = re.compile(r"(child|baby|infant|kid'?s?|toddler)\s*(car\s*)?seats?|booster|djecj\w*\s+sjedist\w*|"
                         r"auto\s*sjedist\w*|detsk\w*\s+kresl\w*|детск\w*\s+кресл\w*|бустер\w*")
_CHILD_SEAT_N = re.compile(rf"\b{_N}\s+(?:child|baby|infant|kids?'?|toddler|djecj\w*|detsk\w*|детск\w*)")
_HOURS = re.compile(rf"\b(?:for\s+)?{_N}[\s-]+(?:hours?|hrs?|sata|sati|casa|chas\w*|час\w*)\b")
_DAYS = re.compile(rf"\b(?:for\s+|na\s+|на\s+)?{_N}\s+(?:days?|dana|dan|dnya|dney|дн\w*|сут\w*)\b")
_WEEK = re.compile(r"\b(a|one|for a)\s+week\b|\bnedjelju dana\b|\bнеделю\b")
_HEIGHT_CM = re.compile(r"\b(1[2-9]\d|20\d|21\d)\s*(?:cm|cms|centimet\w*|см)?\b")
_HEIGHT_M = re.compile(r"\b(1)[.,]([5-9]\d)\s*(?:m|metr\w*|м)?\b")
_SKILL = [
    ("beginner", r"beginner\w*|first time|novice|pocetni\w*|новичк\w*|начинающ\w*"),
    ("intermediate", r"intermediate|average|srednj\w*|средн\w*"),
    ("advanced", r"advanced|expert|good skier\w*|naprednj?\w*|odlic\w*|продвинут\w*|опытн\w*"),
]
_PRIVATE = re.compile(r"\b(private|just us|only us|privat\w*|individual\w*|приватн\w*|частн\w*|индивидуальн\w*)")
_GROUP = re.compile(r"\b(group tour|join a group|shared|grupn\w*|grupi|групп\w*|сборн\w*)")
_AGE = re.compile(r"\b(?:i am|i'm|driver is|aged?|age)\s+(\d{2})\b|\b(\d{2})\s+(?:years old|godina|лет|год)")


def parse_quantity(folded: str) -> int | None:
    for pattern in _QTY_RES:
        if (m := pattern.search(folded)) and (n := _to_int(m.group(1))):
            return n
    if _ONE_ITEM.search(folded):
        return 1
    return None


def parse_luggage(folded: str) -> int | None:
    m = _LUGGAGE.search(folded)
    return _to_int(m.group(1)) if m else None


def parse_child_seats(folded: str) -> int | None:
    if not _CHILD_SEAT.search(folded):
        return None
    m = _CHILD_SEAT_N.search(folded)
    return (_to_int(m.group(1)) or 1) if m else 1


def parse_hours(folded: str) -> int | None:
    m = _HOURS.search(folded)
    return _to_int(m.group(1)) if m else None


def parse_days(folded: str) -> int | None:
    if _WEEK.search(folded):
        return 7
    if re.search(r"\b(weekend|vikend|выходны\w*)\b", folded) and not re.search(r"\bthis weekend\b", folded):
        return None
    m = _DAYS.search(folded)
    return _to_int(m.group(1)) if m else None


def parse_heights(folded: str) -> list[int]:
    out = [int(a) * 100 + int(b) for a, b in _HEIGHT_M.findall(folded)]
    rest = _HEIGHT_M.sub(" ", folded)
    for m in _HEIGHT_CM.finditer(rest):
        if m.group(0).strip().isdigit() and not re.search(r"\d\s*(cm|см)|tall|visok|rost|рост|height|visin",
                                                          folded):
            continue   # a bare 3-digit number is only a height in a heights context
        out.append(int(m.group(1)))
    return out


def parse_skill(folded: str) -> str | None:
    for level, pattern in _SKILL:
        if re.search(rf"\b({pattern})", folded):
            return level
    return None


def parse_activity(folded: str) -> str | None:
    from app.text import contains_phrase
    from app.transactions.catalog import GUIDE_ACTIVITIES

    for key, words in GUIDE_ACTIVITIES.items():
        if any(contains_phrase(folded, w) for w in words):
            return key
    return None


def parse_format(folded: str) -> str | None:
    if _PRIVATE.search(folded):
        return "private"
    if _GROUP.search(folded):
        return "group"
    return None


def parse_age(folded: str) -> int | None:
    m = _AGE.search(folded)
    return int(m.group(1) or m.group(2)) if m else None


_KIND_PARSERS = {
    "quantity": parse_quantity, "luggage": parse_luggage, "child_seats": parse_child_seats, "hours": parse_hours,
    "days": parse_days, "skill": parse_skill, "activity": parse_activity, "format": parse_format, "age": parse_age,
}


def extract(text: str, spec: ServiceSpec, today: date) -> Extraction:
    from app.transactions.catalog import rental_category

    folded = fold(text)
    out = Extraction()
    kinds = {f.kind for f in spec.fields}
    if "count" in kinds and (n := parse_count(folded)) is not None:
        for f in spec.fields:
            if f.kind == "count":
                out.values[f.key] = n
    for f in spec.fields:
        parser = _KIND_PARSERS.get(f.kind)
        if parser is not None and (v := parser(folded)) is not None:
            out.values[f.key] = v
        elif f.kind == "rental_category" and (cat := rental_category(folded)):
            out.values[f.key] = cat
        elif f.kind == "heights" and (hs := parse_heights(folded)):
            out.values[f.key] = hs
    if "quantity" in kinds and "quantity" not in out.values and (n := parse_count(folded)) is not None:
        out.values["quantity"] = n            # "skis for 4 people" -> 4 sets
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


def kind_answer(text: str, kind: str) -> object | None:
    """A reply to a question about a field of this kind ("180 and 165",
    "beginner", "3", "private")."""
    folded = fold(text)
    if kind == "heights":
        hs = parse_heights(folded) or [int(x) for x in re.findall(r"\b(1[2-9]\d|20\d)\b", folded)]
        return hs or None
    if kind in ("quantity", "luggage", "child_seats", "hours", "days", "age") and \
            (m := re.fullmatch(rf"\s*{_NUM}\s*\w*\s*", folded)):
        return _to_int(m.group(1))
    if kind == "rental_category":
        from app.transactions.catalog import rental_category

        return rental_category(folded)
    parser = _KIND_PARSERS.get(kind)
    return parser(folded) if parser else None


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
