"""The deterministic trip timeline (docs/ITERATION_7_DESIGN.md §18).

build(snapshot, policy) is a PURE function of item snapshots and the policy
file: the same items in any order give a byte-identical result, and it
never sees a database id (snapshot() leaves ids and evidence ids out).

* CONFIRMED / BOOKED items are authoritative and occupy time; CANCELLED
  ones stay in `history` and occupy nothing; STATED / UNKNOWN ones are
  `tentative`; an item with a time that has no UTC is `floating` - off the
  absolute timeline.
* Accommodation is a BASE location, not an occupied interval: check-in
  handling (only when a booked drop-off proves arrival at its address) and
  check-out handling occupy time; every night it covers adds a sleep and a
  wake point, located, occupying nothing.
* Issues are derived, never fixed: missing_transfer (consecutive located
  points in different localities, nothing booked between them),
  impossible_connection, overlapping_bookings.
* A free window exists only between two PROVEN points in one locality and
  one local day, clipped to waking hours; it records both anchors, the zone,
  the policy version and every assumed buffer it applied.

Order: (start UTC, end UTC, item identity, role) - identity is the semantic
key, never a UUID."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import TripItem
from app.journey import timeinfo

AUTHORITATIVE = ("confirmed", "booked")
_REQUIRED_TIMES = {"flight": ("departs", "arrives"), "transfer": ("pickup_at", "dropoff_at"),
                   "restaurant": ("starts",), "accommodation": ("check_in", "check_out")}


def snapshot(session: Session, trip_id: str) -> list[dict[str, Any]]:
    """The trip's items as plain data, without database or evidence ids."""
    items = session.scalars(select(TripItem).where(TripItem.trip_id == trip_id))
    return sorted(({"kind": i.kind, "identity": i.identity, "status": i.status, "details": dict(i.details or {}),
                    "times": {k: {f: v for f, v in rec.items() if f != "evidence"}
                              for k, rec in (i.times or {}).items()}} for i in items),
                  key=lambda x: x["identity"])


# ------------------------------------------------------------------ places
def _place(name: str | None, kind: str) -> dict[str, Any] | None:
    """{place, locality, tz}: an airport's locality is the registered city it
    serves; an address's is its town (the part before the country)."""
    if not name:
        return None
    if kind == "airport":
        return {"place": name, "locality": timeinfo.airport_city(name), "tz": timeinfo.zone_for(name, "airport")}
    parts = [p.strip() for p in name.split(",")]
    return {"place": name, "locality": parts[-2] if len(parts) >= 3 else None,
            "tz": timeinfo.zone_for(parts[-1], "country")}


def _same_place(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    return bool(a and b) and " ".join(a["place"].lower().split()) == " ".join(b["place"].lower().split())


def _utc(rec: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(rec["utc"]).astimezone(timezone.utc)


def _at(day: str | date, hhmm: str, tz: str) -> datetime:
    d = date.fromisoformat(day) if isinstance(day, str) else day
    return datetime.combine(d, time.fromisoformat(hhmm), ZoneInfo(tz)).astimezone(timezone.utc)


def _entry(item: dict[str, Any], role: str, start: datetime, end: datetime, frm: dict | None, to: dict | None, *,
           occupies: bool = True, assumed: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"identity": item["identity"], "kind": item["kind"], "role": role, "start": start, "end": end,
            "from": frm, "to": to, "occupies": occupies, "assumed": assumed or [], "proven": True}


def _key(e: dict[str, Any]) -> tuple:
    return (e["start"], e["end"], e["identity"], e["role"])


def _ref(e: dict[str, Any], side: str) -> dict[str, Any]:
    p = e["to"] if side == "end" else e["from"]
    return {"identity": e["identity"], "role": e["role"], "place": p["place"] if p else None}


# ------------------------------------------------------------------ entries
def _base_entries(item: dict[str, Any], hard: dict[str, int]) -> list[dict[str, Any]]:
    d, t = item["details"], item["times"]
    if item["kind"] == "flight":
        return [_entry(item, "flight", _utc(t["departs"]), _utc(t["arrives"]), _place(d.get("origin"), "airport"),
                       _place(d.get("destination"), "airport"))]
    if item["kind"] == "transfer":
        return [_entry(item, "transfer", _utc(t["pickup_at"]), _utc(t["dropoff_at"]),
                       _place(d.get("pickup"), d.get("pickup_place_kind", "address")),
                       _place(d.get("dropoff"), d.get("dropoff_place_kind", "address")))]
    if item["kind"] == "restaurant":
        start, minutes = _utc(t["starts"]), hard["restaurant_default_minutes"]
        here = _place(d.get("address"), "address")
        return [_entry(item, "reservation", start, start + timedelta(minutes=minutes), here, here,
                       assumed=[{"policy": "restaurant_default_minutes", "minutes": minutes, "at": "end"}])]
    return []


def _stay_entries(item: dict[str, Any], movements: list[dict[str, Any]], hard: dict[str, int],
                  soft: dict[str, Any]) -> list[dict[str, Any]]:
    d, t = item["details"], item["times"]
    hotel = _place(d.get("address"), "address")
    tz = t["check_in"].get("tz")
    if hotel is None or tz is None:
        return []
    out: list[dict[str, Any]] = []
    first, last = date.fromisoformat(t["check_in"]["local"][:10]), date.fromisoformat(t["check_out"]["local"][:10])
    # check-in handling: only when a proven, booked drop-off at the hotel's address on the check-in day
    arrivals = sorted((m for m in movements if m["proven"] and _same_place(m["to"], hotel)
                       and m["end"].astimezone(ZoneInfo(tz)).date() == first), key=_key)
    if arrivals:
        start = arrivals[0]["end"]
        if d.get("check_in_from"):
            start = max(start, _at(first, d["check_in_from"], tz))
        minutes = hard["check_in_handling_minutes"]
        out.append(_entry(item, "check_in", start, start + timedelta(minutes=minutes), hotel, hotel,
                          assumed=[{"policy": "check_in_handling_minutes", "minutes": minutes, "at": "start"}]))
    if d.get("check_out_until"):
        end, minutes = _at(last, d["check_out_until"], tz), hard["check_out_handling_minutes"]
        out.append(_entry(item, "check_out", end - timedelta(minutes=minutes), end, hotel, hotel,
                          assumed=[{"policy": "check_out_handling_minutes", "minutes": minutes, "at": "end"}]))
    night = first
    while night < last:                                    # the base location for every night it covers
        sleep, wake = _at(night, soft["waking_hours"]["end"], tz), _at(night + timedelta(days=1),
                                                                       soft["waking_hours"]["start"], tz)
        out.append(_entry(item, "sleep", sleep, sleep, hotel, hotel, occupies=False))
        out.append(_entry(item, "wake", wake, wake, hotel, hotel, occupies=False))
        night += timedelta(days=1)
    return out


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    return {"identity": item["identity"], "kind": item["kind"], "status": item["status"],
            "local": {k: rec.get("local") for k, rec in sorted(item["times"].items())}}


def _minutes(delta: timedelta) -> int:
    return int(delta.total_seconds() // 60)


# ------------------------------------------------------------------ build
def build(items: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    hard, soft, version = policy["hard"], policy["soft"], policy["version"]
    history, tentative, floating, authoritative = [], [], [], []
    for item in sorted(items, key=lambda x: x["identity"]):
        if item["status"] == "cancelled":
            history.append(_summary(item))
        elif item["status"] not in AUTHORITATIVE or item["kind"] not in _REQUIRED_TIMES:
            tentative.append(_summary(item))
        elif any(not (item["times"].get(f) or {}).get("utc" if item["kind"] != "accommodation" else "tz")
                 for f in _REQUIRED_TIMES[item["kind"]]):
            floating.append(_summary(item))                # a time without UTC is not on the absolute line
        else:
            authoritative.append(item)

    issues: list[dict[str, Any]] = []
    base = sorted((e for i in authoritative for e in _base_entries(i, hard)), key=_key)
    # impossible connections first: a movement that cannot be made proves no arrival
    for f in (e for e in base if e["role"] == "flight"):
        nxt = [b for b in base if b is not f and b["role"] in ("flight", "transfer") and b["start"] >= f["start"]
               and _same_place(b["from"], f["to"])]
        if not nxt:
            continue
        b = min(nxt, key=_key)
        required = hard["min_connection_minutes"] if b["role"] == "flight" else hard["airport_exit_minutes"]
        gap = _minutes(b["start"] - f["end"])
        if gap < required:
            b["proven"] = False
            issues.append({"kind": "impossible_connection", "severity": "error", "after": _ref(f, "end"),
                           "before": _ref(b, "start"), "gap_minutes": gap, "required_minutes": required,
                           "policy_version": version})
    entries = sorted(base + [e for i in authoritative if i["kind"] == "accommodation"
                             for e in _stay_entries(i, base, hard, soft)], key=_key)

    occupying = [e for e in entries if e["occupies"]]
    for x, a in enumerate(occupying):
        for b in occupying[x + 1:]:
            if b["start"] >= a["end"]:
                break
            if a["identity"] != b["identity"]:
                issues.append({"kind": "overlapping_bookings", "severity": "warning", "a": _ref(a, "start"),
                               "b": _ref(b, "start"), "overlap_minutes": _minutes(min(a["end"], b["end"]) - b["start"])})

    windows: list[dict[str, Any]] = []
    prev: dict[str, Any] | None = None                     # the point with the latest end so far
    for e in entries:
        if prev is not None:
            _between(prev, e, issues, windows, hard, soft, version)
        if prev is None or (e["end"], _key(e)) >= (prev["end"], _key(prev)):
            prev = e

    def iso(e: dict[str, Any]) -> dict[str, Any]:
        tz = (e["to"] or e["from"] or {}).get("tz")
        local = (lambda dt: dt.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%dT%H:%M")) if tz else (lambda dt: None)
        start_tz = (e["from"] or {}).get("tz") or tz
        return {**{k: v for k, v in e.items() if k not in ("start", "end")}, "start": e["start"].isoformat(),
                "end": e["end"].isoformat(),
                "local_start": e["start"].astimezone(ZoneInfo(start_tz)).strftime("%Y-%m-%dT%H:%M") if start_tz else None,
                "local_end": local(e["end"])}

    return {"policy_version": version, "registry": timeinfo.REGISTRY_VERSION, "entries": [iso(e) for e in entries],
            "history": history, "tentative": tentative, "floating": floating,
            "issues": sorted(issues, key=lambda i: (i["kind"], repr(sorted(i.items())))), "windows": windows}


def _between(a: dict[str, Any], b: dict[str, Any], issues: list, windows: list, hard: dict[str, int],
             soft: dict[str, Any], version: str) -> None:
    """The gap from the end of `a` to the start of `b`: an issue, a window or nothing."""
    if a["role"] == "sleep" or not a["proven"]:
        return                                             # the night; or an arrival that cannot be made
    here, there = a["to"], b["from"]
    if not (here and there and here["locality"] and there["locality"]):
        return                                             # an unknown place proves nothing
    if here["locality"] != there["locality"]:
        issues.append({"kind": "missing_transfer", "severity": "warning",
                       "from": {"place": here["place"], "locality": here["locality"], **_ref(a, "end")},
                       "to": {"place": there["place"], "locality": there["locality"], **_ref(b, "start")}})
        return
    tz = here["tz"]
    if tz is None:
        return
    assumptions: list[dict[str, Any]] = []
    start, end = a["end"], b["start"]
    if a["role"] == "flight":
        start += timedelta(minutes=hard["airport_exit_minutes"])
        assumptions.append({"policy": "airport_exit_minutes", "minutes": hard["airport_exit_minutes"], "at": "start"})
    buffer = {"reservation": "reservation_buffer_minutes", "transfer": "reservation_buffer_minutes",
              "flight": "airport_arrival_minutes"}.get(b["role"])
    if buffer:
        end -= timedelta(minutes=hard[buffer])
        assumptions.append({"policy": buffer, "minutes": hard[buffer], "at": "end"})
    zone = ZoneInfo(tz)
    day = start.astimezone(zone).date()
    if end <= start or end.astimezone(zone).date() != day:
        return                                             # one local day only: a night needs a base location
    wake, sleep = _at(day, soft["waking_hours"]["start"], tz), _at(day, soft["waking_hours"]["end"], tz)
    if start < wake or end > sleep:
        assumptions.append({"policy": "waking_hours", "minutes": None, "at": "clip"})
    start, end = max(start, wake), min(end, sleep)
    minutes = _minutes(end - start)
    if minutes < soft["min_window_minutes"]:
        return
    fmt = "%Y-%m-%dT%H:%M"
    windows.append({"start": start.astimezone(zone).strftime(fmt), "end": end.astimezone(zone).strftime(fmt),
                    "start_utc": start.isoformat(), "end_utc": end.isoformat(), "timezone": tz, "minutes": minutes,
                    "locality": here["locality"], "start_anchor": _ref(a, "end"), "end_anchor": _ref(b, "start"),
                    "policy_version": version, "registry": timeinfo.REGISTRY_VERSION, "assumptions": assumptions})
