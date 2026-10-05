"""Structured opening hours -> OPEN NOW / OPEN LATER / CLOSED / UNKNOWN.

Never inferred from descriptive text and never assumed: no hours data means
UNKNOWN.

    hours:
      weekly:   {mon: [["12:00", "23:00"]], fri: [["18:00", "02:00"]], sun: []}   # end < start = past midnight
      kitchen:  {mon: [["12:00", "22:00"]], ...}       # optional, food service window
      last_entry: "01:30"                               # optional
      seasonal: [{from: "12-15", to: "04-15", weekly: {...}}]   # MM-DD, inclusive, may wrap the year
      special:  {"2026-12-25": [], "2026-12-31": [["18:00", "03:00"]]}   # holidays override everything
      closed:   [{from: "2026-10-01", to: "2026-10-20", reason: "renovation"}]   # temporary closure
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class OpenState(str, enum.Enum):
    OPEN = "open"
    OPEN_LATER = "open_later"     # closed now, opens later today
    CLOSED = "closed"             # closed for the rest of today
    UNKNOWN = "unknown"           # no structured hours


@dataclass(frozen=True)
class HoursStatus:
    state: OpenState
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    reason: str | None = None     # temporary closure reason


def _t(value: str) -> time:
    h, m = value.split(":")
    return time(int(h) % 24, int(m))


def _in_season(day: date, frm: str, to: str) -> bool:
    md = (day.month, day.day)
    start, end = tuple(map(int, frm.split("-"))), tuple(map(int, to.split("-")))
    return start <= md <= end if start <= end else (md >= start or md <= end)


def _closed_reason(hours: dict[str, Any], day: date) -> str | None:
    for c in hours.get("closed") or []:
        if date.fromisoformat(c["from"]) <= day <= date.fromisoformat(c["to"]):
            return c.get("reason") or "temporarily closed"
    return None


def ranges_for(hours: dict[str, Any], day: date, key: str = "weekly") -> list[tuple[datetime, datetime]] | None:
    """Concrete opening intervals that START on `day` (naive local datetimes).
    None = no data for that kind of hours."""
    if _closed_reason(hours, day):
        return []
    special = (hours.get("special") or {}).get(day.isoformat()) if key == "weekly" else None
    if special is not None:
        spec = special
    else:
        weekly = hours.get(key)
        if key == "weekly":
            for season in hours.get("seasonal") or []:
                if _in_season(day, season["from"], season["to"]):
                    weekly = season["weekly"]
                    break
        if weekly is None:
            return None
        spec = weekly.get(DAYS[day.weekday()], [])
    out = []
    for start, end in spec:
        s = datetime.combine(day, _t(start))
        e = datetime.combine(day, _t(end))
        if e <= s:
            e += timedelta(days=1)   # past midnight
        out.append((s, e))
    return out


def status_at(hours: dict[str, Any] | None, at: datetime, key: str = "weekly") -> HoursStatus:
    """`at` is naive local time at the place."""
    if not hours or (key not in hours and not (key == "weekly" and hours.get("special"))):
        return HoursStatus(OpenState.UNKNOWN)
    reason = _closed_reason(hours, at.date())
    # Yesterday's range may still be running after midnight.
    for day in (at.date() - timedelta(days=1), at.date()):
        for s, e in ranges_for(hours, day, key) or []:
            if s <= at < e:
                if key == "weekly" and hours.get("last_entry"):
                    # last entry applies only if it falls inside this interval
                    le = _t(hours["last_entry"])
                    last = datetime.combine(s.date() if le >= s.time() else s.date() + timedelta(days=1), le)
                    if s < last <= e and at > last:
                        continue
                return HoursStatus(OpenState.OPEN, closes_at=e)
    later = [s for s, _ in ranges_for(hours, at.date(), key) or [] if s > at]
    if later:
        return HoursStatus(OpenState.OPEN_LATER, opens_at=min(later), reason=reason)
    return HoursStatus(OpenState.CLOSED, reason=reason)


def open_during(hours: dict[str, Any] | None, start: datetime, end: datetime | None = None, key: str = "weekly") -> bool | None:
    """Is the place open at `start` (and still open at `end`, if given)?
    None when there is no data."""
    st = status_at(hours, start, key)
    if st.state == OpenState.UNKNOWN:
        return None
    if st.state != OpenState.OPEN:
        return False
    return end is None or (st.closes_at is not None and st.closes_at >= end)
