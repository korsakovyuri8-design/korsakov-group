"""Deterministic parser for a traveller's own stated trip plan.

A plan is guest evidence, never a booking.  It accepts common travel wording
and keeps partial plans partial: missing dates stay missing, relative timing
stays relative, and notes remain literal evidence.  It does not invent a
provider, confirmation, year, clock time, coordinates, place role, overnight
stay, origin, route, or transaction state.
"""

from __future__ import annotations

import re

from app.journey.parsers import common
from app.journey.parsers.airline import Parsed

_MONTH = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?"
)
_DATE = re.compile(
    rf"\b(?P<a>\d{{1,2}})(?:\s*(?:-|–|—|to)\s*(?P<b>\d{{1,2}}))?"
    rf"\s+(?:of\s+)?(?P<m>{_MONTH})(?:\s+(?P<y>\d{{4}}))?\b",
    re.I,
)
_RELATIVE = re.compile(
    r"\b(?:this|next)\s+(?:weekend|week|month)\b|"
    r"\b(?:tomorrow|tonight|soon)\b|"
    r"\bon\s+the\s+\d{1,2}(?:st|nd|rd|th)\b",
    re.I,
)
_DURATION = re.compile(
    r"\bfor\s+(?P<v>(?:a|one|two|three|four|five|six|seven|\d+)\s+"
    r"(?:weekend|day|days|week|weeks)|a\s+weekend)\b",
    re.I,
)
_NOTE = re.compile(
    r"\b(?P<v>without\s+.+?|no\s+hotel\s+booked(?:\s+yet)?|"
    r"hotel\s+not\s+booked(?:\s+yet)?)\s*[.!?]?\s*$",
    re.I,
)
_PLACE_LINK = re.compile(
    r"\b(?:return(?:s|ing)?\s+to|staying\s+(?:at|in)|stay\s+(?:at|in)|back\s+to)\s+",
    re.I,
)
_EVENT_NOUN = re.compile(
    r"\b(?:conference|summit|forum|expo|exhibition|festival|meeting|congress|"
    r"convention|grand prix|wedding)\b",
    re.I,
)
_PURPOSE = re.compile(
    r"\s+for\s+(?:the\s+)?(?P<event>(?:investment\s+)?(?:conference|summit|forum|expo|"
    r"exhibition|festival|meeting|congress|convention|grand prix|wedding))\s*$",
    re.I,
)
_PREFIXES = (
    re.compile(r"\bi\s+want\s+to\s+(?:go\s+to|visit|stay\s+(?:at|in))\s+", re.I),
    re.compile(r"\bi(?:'d| would)\s+like\s+to\s+(?:go\s+to|visit|stay\s+(?:at|in))\s+", re.I),
    re.compile(r"\bi(?:'m| am)\s+(?:going\s+to|planning\s+to\s+(?:go\s+to|visit))\s+", re.I),
    re.compile(r"\bi\s+plan\s+to\s+(?:go\s+to|visit|stay\s+(?:at|in))\s+", re.I),
    re.compile(r"\bthinking\s+(?:of|about)\s+(?:going\s+to|visiting)\s+", re.I),
    re.compile(r"\bi(?:'ll| will)\s+be\s+(?:at|in)\s+", re.I),
    re.compile(r"\bi\s+need\s+to\s+be\s+(?:at|in)\s+", re.I),
    re.compile(r"\bi(?:'m| am)\s+staying\s+(?:at|in)\s+", re.I),
    re.compile(r"\bgoing\s+to\s+", re.I),
)
_SPEND = re.compile(
    r"\bi\s+(?:want|would\s+like|'d\s+like)\s+to\s+spend\s+"
    r"(?P<duration>(?:a|one|two|three|four|five|six|seven|\d+)\s+"
    r"(?:weekend|day|days|week|weeks)|a\s+weekend)\s+in\s+(?P<rest>.+)$",
    re.I,
)
_MAYBE = re.compile(r"^\s*maybe\s+(?P<rest>.+)$", re.I)


_FROM_TO = re.compile(
    r"\b(?:"
    r"i(?:'m| am)\s+(?:going|travell?ing)|"
    r"i\s+(?:want|plan)\s+to\s+(?:go|travel)|"
    r"i(?:'d| would)\s+like\s+to\s+(?:go|travel)|"
    r"going|travell?ing"
    r")\s+from\s+(?P<origin>.+?)\s+to\s+(?P<rest>.+)$",
    re.I,
)


def _date_text(m: re.Match[str]) -> str:
    month = m.group("m")
    month = month.title() if len(month) > 3 else month[:3].title()
    days = m.group("a") if not m.group("b") else f"{m.group('a')}–{m.group('b')}"
    return f"{days} {month}" + (f" {m.group('y')}" if m.group("y") else "")


def _rest_after_intent(text: str) -> tuple[str | None, str | None]:
    if m := _SPEND.search(text):
        return m.group("rest").strip(), m.group("duration").strip()
    for pattern in _PREFIXES:
        if m := pattern.search(text):
            return text[m.end():].strip(), None
    if m := _MAYBE.match(text):
        rest = m.group("rest").strip()
        if _DATE.search(rest) or _RELATIVE.search(rest) or _DURATION.search(rest):
            return rest, None
    return None, None


def parse(raw: str) -> Parsed | None:
    body = common.body(raw)
    if not body:
        return None
    text = " ".join(t for t, _ in body).strip()

    origin = None

    if directional := _FROM_TO.search(text):
        origin = directional.group("origin").strip(" ,.;:-")
        rest = directional.group("rest").strip()
        duration = None
    else:
        rest, duration = _rest_after_intent(text)

    if not rest:
        return None

    date = _DATE.search(rest)
    relative = _RELATIVE.search(rest)
    duration_match = _DURATION.search(rest)
    note_match = _NOTE.search(rest)
    if duration_match and not duration:
        duration = duration_match.group("v").strip()
    note = note_match.group("v").strip() if note_match else None

    cuts = [m.start() for m in (date, relative, duration_match, note_match) if m]
    core = rest[: min(cuts) if cuts else len(rest)].strip(" ,.;:-")
    core = re.sub(r"\s+\b(?:for|on|from|during)\b\s*$", "", core, flags=re.I).strip(" ,.;:-")
    if not core:
        return None

    event = None
    place = None
    if link := _PLACE_LINK.search(core):
        event = core[:link.start()].strip(" ,.;:-") or None
        place = core[link.end():].strip(" ,.;:-") or None
    elif purpose := _PURPOSE.search(core):
        place = core[:purpose.start()].strip(" ,.;:-") or None
        event = purpose.group("event").strip()
    else:
        preps = list(re.finditer(r"\b(?:at|in|to)\s+", core, re.I))
        if preps:
            last = preps[-1]
            prefix = core[:last.start()].strip(" ,.;:-")
            candidate = core[last.end():].strip(" ,.;:-")
            if prefix and candidate and _EVENT_NOUN.search(prefix):
                event, place = prefix, candidate
            else:
                place = candidate or core
        else:
            place = core

    if place:
        place = re.sub(r"\s+\b(?:for|on|from|during)\b\s*$", "", place, flags=re.I).strip(" ,.;:-") or None
    if not place and not event:
        return None

    evidence = "\n".join(line for _, line in body)
    out = Parsed("plan", doc_type="statement")
    observations: list[tuple[str, object, str]] = [("text", text, evidence)]
    if origin:
        observations.append(("origin_text", origin, origin))
    if event:
        observations.append(("event", event, event))
    if place:
        observations.append(("place", place, place))
    if date:
        observations.append(("date_text", _date_text(date), date.group(0)))
    if relative:
        observations.append(("timing_text", relative.group(0), relative.group(0)))
    if duration:
        observations.append(("duration_text", duration, duration))
    if note:
        observations.append(("note", note, note))
    observations.append(("status_event", "state", evidence))
    out.observations = observations
    return out
