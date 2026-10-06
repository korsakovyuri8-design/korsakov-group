"""Plan-aware time: day views and free windows, derived from the stay plan.

No optimiser. A free window is the time between "now" (or a stated start)
and the next fixed thing in the plan the guest named ("before dinner"), or
an explicit duration ("two free hours"). Discovery then only returns places
that are open through that window and whose visit fits it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.text import contains_phrase, fold
from app.trip.itinerary import PlanEntry

EVENING = time(17, 0)
_HOURS = re.compile(r"\b(\d{1,2}|an?|one|two|three|four|five|jedan|dva|tri|cetiri|odin|dva|tri|chetyre)\s+"
                    r"(?:free\s+|spare\s+|slobodn\w+\s+|svobodn\w+\s+)?(?:hours?|sata?|sati|chas\w*)\b")
_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "jedan": 1, "dva": 2, "tri": 3,
        "cetiri": 4, "odin": 1, "chetyre": 4}
FREE_WORDS = ["free time", "free hours", "free hour", "spare time", "kill time", "hours to kill", "time before",
              "before dinner", "before lunch", "before the concert", "slobodn* vrijeme", "do vecere", "prije vecere",
              "свободн* врем*", "свободн* час*", "до ужина", "перед ужином"]
_BEFORE = {"dinner": ["dinner", "vecer*", "ужин*"], "lunch": ["lunch", "rucak", "обед*"],
           "concert": ["concert", "koncert*", "концерт*"], "event": ["event", "show", "событи*"]}


@dataclass(frozen=True)
class Window:
    start: datetime          # local, naive
    end: datetime            # local, naive
    anchor: str | None       # the plan item that closes the window, if any
    from_plan: bool


def on_day(entries: list[PlanEntry], day: date, tz: str, *, evening: bool = False) -> list[PlanEntry]:
    z = ZoneInfo(tz)
    out = []
    for e in entries:
        if e.starts_at is None:
            continue
        local = e.starts_at.astimezone(z)
        if local.date() == day and (not evening or local.time() >= EVENING):
            out.append(e)
    return out


_WINDOW_PHRASE = re.compile(r"\b(?:before|until|till|prije|do|перед|до)\s+(?:the\s+|our\s+)?"
                            r"(?:dinner|lunch|concert|event|show|vecer\w*|rucka|koncert\w*|ужин\w*|обед\w*|"
                            r"концерт\w*)\b", re.IGNORECASE)


def strip_window_words(text: str) -> str:
    return _WINDOW_PHRASE.sub(" ", text)


def is_free_window_request(text: str) -> bool:
    folded = fold(text)
    return any(contains_phrase(folded, w) for w in FREE_WORDS) or bool(
        _HOURS.search(folded) and any(contains_phrase(folded, w) for w in ("free", "spare", "before", "until",
                                                                            "slobodn*", "prije", "до", "свободн*")))


def free_window(text: str, entries: list[PlanEntry], now_local: datetime, tz: str) -> Window | None:
    """The window the guest means, from their words and their plan."""
    folded = fold(text)
    hours = None
    if m := _HOURS.search(folded):
        n = m.group(1)
        hours = int(n) if n.isdigit() else _NUM.get(n, 2)
    now = now_local.replace(tzinfo=None, second=0, microsecond=0)
    target = next((k for k, words in _BEFORE.items() if any(contains_phrase(folded, w) for w in words)), None)
    if target is not None:
        z = ZoneInfo(tz)
        upcoming = [e for e in entries if e.starts_at is not None and e.starts_at.astimezone(z).replace(tzinfo=None) > now
                    and e.starts_at.astimezone(z).date() == now.date()
                    and _is(e, target)]
        if upcoming:
            end = upcoming[0].starts_at.astimezone(z).replace(tzinfo=None)
            start = max(now, end - timedelta(hours=hours)) if hours else now
            return Window(start, end, upcoming[0].item.title, True)
    if hours:
        return Window(now, now + timedelta(hours=hours), None, False)
    return None


def _is(entry: PlanEntry, target: str) -> bool:
    title = fold(entry.item.title)
    if target in ("dinner", "lunch"):
        return entry.item.kind == "FOOD" or "restaurant" in title or any(
            contains_phrase(title, w) for w in _BEFORE[target]) or "restaurant_reservation" in title
    if target in ("concert", "event"):
        return entry.item.event_id is not None or any(contains_phrase(title, w) for w in _BEFORE[target])
    return False
