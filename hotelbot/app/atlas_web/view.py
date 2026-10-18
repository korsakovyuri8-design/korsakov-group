"""The trip as a visitor sees it: no JSON internals, no ids, no raw text.

picture()    - route, dates, bookings, what needs attention, the day plans,
               activities that fit the first free window the demo world
               covers, and the sample documents that move the story on.
understood() - the card for one imported document ("Atlas understood this
               booking"), with what changed when it updates a booking.

All wording comes from app/trip/atlas_demo.py, so the live trip and the
90-second playback say the same things. Planned location is never worded
as observed location."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db.models import TripItem
from app.atlas_web import view_v2
from app.journey import policies, timeline
from app.journey.ingest import ImportReport
from app.trip.atlas_demo import (NOW, booking_rows, city, display_name, entry_of, fitting_activities,
                                 hm, items_of, itinerary, span)

SAMPLE_FLIGHT = "flight|demo air|DMX7Q2|DM1432|LGW-TGD|2026-10-18"
SAMPLE_TRANSFER = "transfer|demo transfers|TR551"


def _day(local: str) -> str:
    return datetime.fromisoformat(local[:10]).strftime("%-d %b")


def _town(address: str | None) -> str:
    parts = [p.strip() for p in (address or "").split(",")]
    return parts[-2] if len(parts) >= 3 else (address or "")


def _dates(out: dict[str, Any]) -> str | None:
    days = sorted({e["local_start"][:10] for e in out["entries"] if e.get("local_start")} |
                  {e["local_end"][:10] for e in out["entries"] if e.get("local_end")})
    if not days:
        return None
    a, b = datetime.fromisoformat(days[0]), datetime.fromisoformat(days[-1])
    if a == b:
        return a.strftime("%-d %B")
    if a.month == b.month:
        return f"{a.day}–{b.day} {a.strftime('%B')}"
    return f"{a.strftime('%-d %b')} – {b.strftime('%-d %b')}"


def _route(out: dict[str, Any]) -> list[str]:
    route: list[str] = []
    for e in out["entries"]:
        for side in ("from", "to"):
            loc = (e.get(side) or {}).get("locality")
            if loc and (not route or route[-1] != loc):
                route.append(loc)
    return route


def _what(item: TripItem, role: str) -> str:
    if item.kind == "restaurant":
        return f"dinner at {display_name(item)}"
    if item.kind == "accommodation":
        return f"your hotel, {display_name(item)}"
    if item.kind == "flight":
        return f"flight {item.details.get('flight_number')}"
    return display_name(item)


def _attention(out: dict[str, Any], items: dict[str, TripItem]) -> list[dict[str, Any]]:
    found = []
    for issue in out["issues"]:
        if issue["kind"] == "missing_transfer":
            a, b = issue["from"], issue["to"]
            before, after = entry_of(out, a["identity"], a["role"]), entry_of(out, b["identity"], b["role"])
            first = (f"You land in {a['locality']} at {hm(before['local_end'])}." if a["role"] == "flight" else
                     f"By your plan you are in {a['locality']} until {hm(before['local_end'])}.")
            nxt = items[b["identity"]]
            at = "" if b["role"] in ("sleep", "wake") else f" at {hm(after['local_start'])}"
            found.append({"kind": "missing_transfer", "severity": "warning", "headline": "Missing transport",
                          "lines": [first, f"Your next confirmed place is {_what(nxt, b['role'])} in "
                                           f"{b['locality']}{at}.", "There is no transport between them."]})
        elif issue["kind"] == "impossible_connection":
            a, b = issue["after"], issue["before"]
            landed, leaves = entry_of(out, a["identity"], a["role"]), entry_of(out, b["identity"], b["role"])
            what = "transfer" if b["role"] == "transfer" else "connecting flight"
            lines = [f"You land in {city(a['place'])} at {hm(landed['local_end'])}.",
                     f"Your {what} from {b['place']} leaves at {hm(leaves['local_start'])} — {issue['gap_minutes']} "
                     f"min after landing; you need about {issue['required_minutes']}.",
                     f"Your existing {what} may no longer fit."]
            dinner = next((i for i in sorted(items.values(), key=lambda x: x.identity)
                           if i.kind == "restaurant" and i.status == "confirmed"), None)
            found.append({"kind": "impossible_connection", "severity": "error",
                          "headline": "A connection may no longer work", "lines": lines,
                          "status": f"{what.capitalize()} needs rebooking",
                          "plan": (f"By your plan you are still due at {display_name(dinner)} at "
                                   f"{hm(dinner.times['starts']['local'])}.") if dinner else None})
        elif issue["kind"] == "overlapping_bookings":
            a, b = items[issue["a"]["identity"]], items[issue["b"]["identity"]]
            found.append({"kind": "overlapping_bookings", "severity": "warning", "headline": "Two bookings overlap",
                          "lines": [f"{display_name(a)} and {display_name(b)} overlap by {issue['overlap_minutes']} min.",
                                    "Atlas keeps both; you decide which one stays."]})
    return found


def _days(out: dict[str, Any], items: dict[str, TripItem]) -> list[dict[str, Any]]:
    dates = sorted({e["local_start"][:10] for e in out["entries"] if e["occupies"] and e.get("local_start")} |
                   {w["start"][:10] for w in out["windows"]})
    days = []
    for d in dates:
        rows = itinerary(out, items, d)
        if rows:
            days.append({"date": d, "label": datetime.fromisoformat(d).strftime("%a %-d %b"), "itinerary": rows})
    return days


def _activities(db, out: dict[str, Any], items: dict[str, TripItem], world: dict[str, Any],
                now_utc: datetime = NOW) -> dict[str, Any] | None:  # noqa: ANN001
    for w in out["windows"]:
        found = fitting_activities(db, w, world, now_utc=now_utc)
        if found is None:
            continue
        n, minutes = len(found["suggestions"]), w["minutes"]
        length = f"{minutes // 60} hours" if minutes % 60 == 0 else f"{minutes // 60} h {minutes % 60} min"
        end = items.get(w["end_anchor"]["identity"])
        if end is not None and end.kind == "restaurant":
            lead = f"You have {length} before dinner."
        else:
            lead = f"You have free time on {_day(w['start'])}, {span(w['start'], w['end'])}."
        tail = f"Here are {n} things that actually fit." if n else "Nothing in the demo world fits this window."
        return {**found, "day": _day(w["start"]), "headline": f"{lead} {tail}"}
    return None


def _next(out: dict[str, Any], items: dict[str, TripItem]) -> list[dict[str, str]]:
    """Sample documents that move the story on (demo only, clearly labelled)."""
    kinds = {i["kind"] for i in out["issues"]}
    nxt = []
    if SAMPLE_TRANSFER not in items and any(i["kind"] == "missing_transfer" and i["from"]["place"] == "TGD"
                                            for i in out["issues"]):
        nxt.append({"id": "transfer", "label": "Arrange transfer",
                    "hint": "Adds the sample Demo Transfers confirmation: 15:00 from Podgorica airport."})
    flight = items.get(SAMPLE_FLIGHT)
    if flight and flight.current_revision == 1 and SAMPLE_TRANSFER in items and not kinds:
        nxt.append({"id": "flight_change", "label": "Simulate a flight change",
                    "hint": "Adds the sample Demo Air schedule change for DM1432."})
    return nxt


def picture(db, trip_id: str, world: dict[str, Any], *, now_utc: datetime = NOW, clock: str = "demo",
            fallback_timezone: str | None = None) -> dict[str, Any]:  # noqa: ANN001
    snapshot = timeline.snapshot(db, trip_id)
    out = timeline.build(snapshot, policies.load())
    all_items = items_of(db, trip_id)
    items = {k: v for k, v in all_items.items() if v.kind in ("flight", "accommodation", "transfer", "restaurant")}
    public_items = {k: v for k, v in all_items.items() if v.kind != "statement"}
    return {"route": _route(out), "dates": _dates(out), "bookings": booking_rows(items),
            "attention": _attention(out, items), "days": _days(out, items),
            "activities": _activities(db, out, items, world, now_utc), "next": _next(out, items),
            "off_timeline": [{"kind": f["kind"], "reason": "Atlas can't place this in time yet (unknown time zone)."}
                             for f in out["floating"]],
            "policy_version": out["policy_version"],
            # v2 (view_v2.py): structured, additive; every v1 field above is unchanged
            "context": view_v2.context(out, now_utc, clock=clock, fallback_timezone=fallback_timezone),
            "next_item": view_v2.next_item(out, now_utc),
            "items": view_v2.items(public_items, now_utc=now_utc), "disruption": view_v2.disruption(snapshot, out, items)}


# ------------------------------------------------------------------ the import card
def _card_basics(item: TripItem) -> dict[str, str]:
    d, t = item.details, item.times
    if item.kind == "flight":
        return {"title": f"Flight {d['flight_number']}", "route": f"{city(d['origin'])} → {city(d['destination'])}",
                "when": _day(t["departs"]["local"])}
    if item.kind == "accommodation":
        a, b = datetime.fromisoformat(t["check_in"]["local"]), datetime.fromisoformat(t["check_out"]["local"])
        when = f"{a.day}–{b.day} {a.strftime('%b')}" if a.month == b.month else f"{a:%-d %b} – {b:%-d %b}"
        return {"title": display_name(item), "route": _town(d.get("address")), "when": when}
    if item.kind == "plan":
        return {"title": d.get("event") or "Stated plan",
                "route": d.get("place") or "",
                "when": d.get("date_text") or d.get("timing_text") or d.get("duration_text") or "Dates not set"}
    if item.kind == "transfer":
        to = d["dropoff"] if d.get("dropoff_place_kind") == "airport" else _town(d.get("dropoff"))
        return {"title": f"Transfer {d['pickup']} → {to}", "route": f"{d['pickup']} → {to}",
                "when": f"{_day(t['pickup_at']['local'])} · {hm(t['pickup_at']['local'])}"}
    return {"title": f"Dinner at {display_name(item)}", "route": _town(d.get("address")),
            "when": f"{_day(t['starts']['local'])} · {hm(t['starts']['local'])}"}


_FIELD = {"departs": "Departure", "arrives": "Arrival", "starts": "Time", "pickup_at": "Pickup",
          "dropoff_at": "Drop-off", "check_in": "Check-in", "check_out": "Check-out"}


def understood(item: TripItem | None, report: ImportReport, before: tuple[str, int] | None) -> dict[str, Any]:
    """`before` = (status, revision) of the item before this import, None if it was new."""
    if item is None:
        return {"outcome": "ambiguous", "message": "Atlas read this document but can't tell which booking it changes.",
                "detail": "No trip data was changed."}
    changes = []
    if before is not None:
        old_status, _ = before
        if report.new_revision:
            revs = sorted(item.revisions, key=lambda r: (r["issued_at"] or "", r["revision"]))
            prior: dict[str, Any] = {}
            for r in revs[:-1]:
                prior.update(r["times"])
            order = list(_FIELD)
            for f in sorted(revs[-1]["changed_fields"], key=lambda x: (order.index(x) if x in order else 99, x)):
                if f in prior and f in item.times:
                    changes.append(f"{_FIELD.get(f, f.title())} moved from {hm(prior[f]['local'])} to "
                                   f"{hm(item.times[f]['local'])}.")
        if old_status != item.status:
            changes.append(f"Status changed from {old_status.title()} to {item.status.title()}.")
    if not report.record_new or (before is not None and not changes):
        outcome = "already_known"
    else:
        outcome = "new" if before is None else "updated"
    return {"outcome": outcome, "kind": item.kind, "provider": (item.provider or "").title(), **_card_basics(item),
            "status": item.status.title(), "changes": changes}
