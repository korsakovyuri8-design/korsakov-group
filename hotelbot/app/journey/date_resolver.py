"""Conservative deterministic resolution of a stated date/date range.

The user's original text remains authoritative evidence. This module only
adds a derived calendar interpretation.

If the user did not state a year, Atlas chooses the nearest upcoming
occurrence relative to the injected reference time. The inference is always
labelled and is never presented as user-stated fact.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

RX = re.compile(
    r"^\s*(?P<a>\d{1,2})"
    r"(?:\s*(?:-|–|—|to)\s*(?P<b>\d{1,2}))?"
    r"\s+(?P<m>[A-Za-z]+)"
    r"(?:\s+(?P<y>\d{4}))?\s*$",
    re.I,
)


def resolve_date_range(
    text: str | None,
    *,
    now_utc: datetime,
    timezone: str,
) -> dict[str, Any] | None:
    if not text:
        return None

    m = RX.match(text)
    if not m:
        return None

    month = MONTHS.get(m.group("m").casefold())
    if month is None:
        return None

    start_day = int(m.group("a"))
    end_day = int(m.group("b") or m.group("a"))
    explicit_year = int(m.group("y")) if m.group("y") else None

    local_now = now_utc.astimezone(ZoneInfo(timezone))
    reference_date = local_now.date()

    if explicit_year is not None:
        year = explicit_year
        basis = "stated_year"
    else:
        year = reference_date.year
        try:
            candidate = date(year, month, start_day)
        except ValueError:
            return None
        if candidate < reference_date:
            year += 1
        basis = "inferred_upcoming_date"

    try:
        start = date(year, month, start_day)
        end = date(year, month, end_day)
    except ValueError:
        return None

    if end < start:
        return None

    return {
        "status": "resolved",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "timezone": timezone,
        "basis": basis,
        "reference_date": reference_date.isoformat(),
        "year_stated": explicit_year is not None,
    }
