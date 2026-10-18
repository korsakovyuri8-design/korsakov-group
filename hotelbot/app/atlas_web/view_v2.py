"""Atlas Public View API v2: context, next item, item details, disruption impact.

Read-only view-model fields added to the trip picture. They are structured
data (ISO times, zones, keys, reason codes) for the frontend to present; the
wording stays in the frontend. Nothing here changes a Journey Graph rule:
everything is read from resolved TripItems and from the pure timeline.

* context.now           the demo clock, in the zone of the planned location
* planned_location      where the PLAN puts the traveller at `now` - always
                        basis "planned", never an observed position
* next_item             the next booked, time-occupying entry after `now`
* items                 per-item detail views (no evidence, documents, record
                        ids or passenger names - passengers are a count)
* disruption            after the latest provider revision: the trip is
                        rebuilt with that item one revision back (the same
                        pure timeline), and windows that were provable then
                        but are not now are listed with a reason code
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.db.models import TripItem
from app.journey import date_resolver, place_resolver, planning, policies, timeinfo, timeline

KIND_ORDER = {"flight": 0, "accommodation": 1, "transfer": 2, "restaurant": 3, "plan": 4}
MOVES = ("flight", "transfer")


def _local(utc: str, tz: str | None) -> str | None:
    return datetime.fromisoformat(utc).astimezone(ZoneInfo(tz)).strftime("%Y-%m-%dT%H:%M") if tz else None


def _time(rec: dict[str, Any] | None) -> dict[str, Any] | None:
    if not rec:
        return None
    out = {"local": rec.get("local"), "utc": rec.get("utc"), "timezone": rec.get("tz")}
    for flag in ("date_only", "approximate"):
        if rec.get(flag):
            out[flag] = True
    return out


def _locality(place: str | None, kind: str) -> str | None:
    if not place:
        return None
    if kind == "airport":
        return timeinfo.airport_city(place)
    parts = [p.strip() for p in place.split(",")]
    return parts[-2] if len(parts) >= 3 else None


def _issues_by_item(out: dict[str, Any]) -> dict[str, list[str]]:
    by: dict[str, set[str]] = {}
    refs = {"missing_transfer": ("to",), "impossible_connection": ("before",), "overlapping_bookings": ("a", "b")}
    for issue in out["issues"]:
        for side in refs.get(issue["kind"], ()):
            by.setdefault(issue[side]["identity"], set()).add(issue["kind"])
    return {k: sorted(v) for k, v in by.items()}


# ------------------------------------------------------------------ context
def context(out: dict[str, Any], now_utc: datetime, *, clock: str = "demo",
            fallback_timezone: str | None = None) -> dict[str, Any]:
    now = now_utc.astimezone(timezone.utc)
    started = [e for e in out["entries"] if datetime.fromisoformat(e["start"]) <= now]
    ongoing = [e for e in started if e["occupies"] and now < datetime.fromisoformat(e["end"])]
    loc: dict[str, Any]
    if ongoing:
        e = ongoing[-1]
        by = {"key": e["identity"], "role": e["role"]}
        until = {"local": e["local_end"], "utc": e["end"]}
        if e["role"] in MOVES:
            loc = {"basis": "planned", "status": "in_transit", "from": (e["from"] or {}).get("place"),
                   "to": (e["to"] or {}).get("place"), "locality": None, "timezone": (e["to"] or {}).get("tz"),
                   "until": until, "by": by}
        else:
            p = e["to"] or {}
            loc = {"basis": "planned", "status": "at", "place": p.get("place"), "locality": p.get("locality"),
                   "timezone": p.get("tz"), "until": until, "by": by}
    elif started:
        e = max(started, key=lambda x: (x["end"], x["start"], x["identity"], x["role"]))
        p = e["to"] or {}
        if not e.get("proven", True):
            loc = {"basis": "planned", "status": "unknown", "reason": "connection_not_proven",
                   "by": {"key": e["identity"], "role": e["role"]}}
        else:
            loc = {"basis": "planned", "status": "arrived" if e["role"] in MOVES else "at", "place": p.get("place"),
                   "locality": p.get("locality"), "timezone": p.get("tz"),
                   "since": {"local": e["local_end"], "utc": e["end"]}, "by": {"key": e["identity"], "role": e["role"]}}
    else:
        loc = {"basis": "planned", "status": "unknown", "reason": "trip_not_started"}
    tz = loc.get("timezone") or next(((e["from"] or {}).get("tz") for e in out["entries"]
                                      if (e["from"] or {}).get("tz")), None) or fallback_timezone or "UTC"
    return {"now": {"utc": now.isoformat(), "local": _local(now.isoformat(), tz), "timezone": tz, "clock": clock},
            "planned_location": loc}


# ------------------------------------------------------------------ next item
def next_item(out: dict[str, Any], now_utc: datetime) -> dict[str, Any] | None:
    now = now_utc.astimezone(timezone.utc)
    ahead = [e for e in out["entries"] if e["occupies"] and datetime.fromisoformat(e["start"]) > now]
    if not ahead:
        return None
    e = ahead[0]                                           # timeline order: (start, end, identity, role)
    p = e["from"] or {}
    return {"key": e["identity"], "kind": e["kind"], "role": e["role"],
            "starts": {"local": e["local_start"], "utc": e["start"], "timezone": p.get("tz")},
            "place": p.get("place"), "locality": p.get("locality"),
            "minutes_from_now": int((datetime.fromisoformat(e["start"]) - now).total_seconds() // 60),
            "issues": _issues_by_item(out).get(e["identity"], [])}


# ------------------------------------------------------------------ item details
def _ordered_revisions(item: TripItem) -> list[dict[str, Any]]:
    return sorted(item.revisions or [], key=lambda r: (r.get("issued_at") or "", r["revision"]))


def _detail(item: TripItem, *, now_utc: datetime) -> dict[str, Any]:
    d, t = item.details or {}, item.times or {}
    if item.kind == "flight":
        dep, arr = t.get("departs") or {}, t.get("arrives") or {}
        minutes = None
        if dep.get("utc") and arr.get("utc"):
            minutes = int((datetime.fromisoformat(arr["utc"]) - datetime.fromisoformat(dep["utc"])).total_seconds() // 60)
        return {"carrier": d.get("carrier"), "flight_number": d.get("flight_number"), "cabin": d.get("cabin"),
                "passengers": len(d.get("passengers") or []), "service_date": d.get("service_date"),
                "origin": {"code": d.get("origin"), "city": timeinfo.airport_city(d.get("origin")),
                           "timezone": timeinfo.zone_for(d.get("origin"), "airport")},
                "destination": {"code": d.get("destination"), "city": timeinfo.airport_city(d.get("destination")),
                                "timezone": timeinfo.zone_for(d.get("destination"), "airport")},
                "departs": _time(dep), "arrives": _time(arr), "duration_minutes": minutes}
    if item.kind == "accommodation":
        cin, cout = t.get("check_in") or {}, t.get("check_out") or {}
        nights = None
        if cin.get("local") and cout.get("local"):
            nights = (date.fromisoformat(cout["local"][:10]) - date.fromisoformat(cin["local"][:10])).days
        return {"address": d.get("address"), "locality": _locality(d.get("address"), "address"), "room": d.get("room"),
                "guests": d.get("guests"),
                "check_in": {"date": cin.get("local"), "from": d.get("check_in_from"), "timezone": cin.get("tz")},
                "check_out": {"date": cout.get("local"), "until": d.get("check_out_until"), "timezone": cout.get("tz")},
                "nights": nights}
    if item.kind == "transfer":
        def end(name: str) -> dict[str, Any]:
            kind = d.get(f"{name}_place_kind") or "address"
            return {"place": d.get(name), "kind": kind, "locality": _locality(d.get(name), kind)}
        return {"pickup": end("pickup"), "dropoff": end("dropoff"), "pickup_at": _time(t.get("pickup_at")),
                "dropoff_at": _time(t.get("dropoff_at")), "passengers": d.get("passengers")}
    if item.kind == "restaurant":
        return {"venue": d.get("venue"), "address": d.get("address"), "locality": _locality(d.get("address"), "address"),
                "party": d.get("party"), "starts": _time(t.get("starts"))}
    if item.kind == "plan":
        resolved_place = place_resolver.resolve_place(d.get("place"))
        timezone_name = (
            (resolved_place or {}).get("timezone")
            or "UTC"
        )

        internal_transport = d.get("transport_transaction")
        confirmed_transport = None

        if (
            isinstance(internal_transport, dict)
            and internal_transport.get("status")
            in {"accepted", "in_progress", "completed"}
        ):
            confirmed_transport = {
                "status": internal_transport.get("status"),
                "provider": internal_transport.get("provider"),
                "provider_reference":
                    internal_transport.get("provider_reference"),
                "pickup": internal_transport.get("pickup"),
                "destination":
                    internal_transport.get("destination"),
                "pickup_time":
                    internal_transport.get("pickup_time"),
                "party_size":
                    internal_transport.get("party_size"),
                "amount":
                    internal_transport.get("amount"),
                "currency":
                    internal_transport.get("currency"),
                "sandbox":
                    internal_transport.get("sandbox") is True,
            }

        return {
            "event": d.get("event"),
            "place": d.get("place"),
            "date_text": d.get("date_text"),
            "basis": "guest",
            "place_role": d.get("place_role"),
            "overnight_stay": d.get("overnight_stay"),
            "origin_text": d.get("origin_text"),
            "arrival_time": d.get("arrival_time"),
            "party_size": d.get("party_size"),
            "route_plan": d.get("route_plan"),
            "confirmed_transport": confirmed_transport,
            "resolved_place": resolved_place,
            "resolved_date": date_resolver.resolve_date_range(
                d.get("date_text"),
                now_utc=now_utc,
                timezone=timezone_name,
            ),
        }
    return {}


def items(trip_items: dict[str, TripItem], *, now_utc: datetime) -> list[dict[str, Any]]:
    rows = []
    for item in sorted(trip_items.values(), key=lambda i: (KIND_ORDER.get(i.kind, 9), i.identity)):
        revs = _ordered_revisions(item)
        detail = _detail(item, now_utc=now_utc)
        if item.kind == "plan":
            stated = item.details or {}
            for key in ("timing_text", "duration_text", "note"):
                if stated.get(key):
                    detail[key] = stated[key]
            # Provider-backed transaction evidence is needed by the
            # deterministic planning engine, but it is an internal domain
            # record and must not be exposed wholesale through the public
            # Trip API.
            planning_detail = {
                **detail,
                "transport_transaction":
                    (item.details or {}).get(
                        "transport_transaction"
                    ),
            }

            detail["planning"] = planning.needs_for_plan(
                planning_detail,
                trip_items.values(),
            )
        rows.append({"key": item.identity, "kind": item.kind, "provider": item.provider,
                     "reference": item.booking_reference, "status": item.status,
                     "revision": {"current": item.current_revision, "count": len(revs),
                                  "changed_fields": list(revs[-1]["changed_fields"]) if revs else []},
                     "detail": detail})
    return rows


# ------------------------------------------------------------------ disruption impact
def _strip(times: dict[str, Any]) -> dict[str, Any]:
    return {k: {f: v for f, v in rec.items() if f != "evidence"} for k, rec in times.items()}


def disruption(snapshot: list[dict[str, Any]], out: dict[str, Any],
               trip_items: dict[str, TripItem]) -> dict[str, Any] | None:
    revised = [i for i in trip_items.values() if len(i.revisions or []) >= 2]
    if not revised:
        return None
    cause = max(revised, key=lambda i: (_ordered_revisions(i)[-1].get("issued_at") or "", i.identity))
    revs = _ordered_revisions(cause)
    prev_d: dict[str, Any] = {}
    prev_t: dict[str, Any] = {}
    for r in revs[:-1]:                                    # the item as it stood before its latest revision
        prev_d.update(r["details"])
        prev_t.update(r["times"])
    before_snap = [{**s, "details": {**s["details"], **prev_d}, "times": {**s["times"], **_strip(prev_t)}}
                   if s["identity"] == cause.identity else s for s in snapshot]
    before = timeline.build(before_snap, policies.load())

    def key(w: dict[str, Any]) -> tuple:          # the same two anchors recur daily (wake -> sleep): the day too
        return (w["start"][:10], w["start_anchor"]["identity"], w["start_anchor"]["role"],
                w["end_anchor"]["identity"], w["end_anchor"]["role"])

    now_by = {key(w): w for w in out["windows"]}
    proven = {(e["identity"], e["role"]) for e in out["entries"] if e.get("proven", True)}
    lost, changed, still = [], [], 0
    for w in before["windows"]:
        cur = now_by.get(key(w))
        if cur is None:
            if (w["start_anchor"]["identity"], w["start_anchor"]["role"]) not in proven:
                reason = "start_anchor_not_proven"
            elif (w["end_anchor"]["identity"], w["end_anchor"]["role"]) not in proven:
                reason = "end_anchor_not_proven"
            else:
                reason = "gap_no_longer_provable"
            lost.append({"start": w["start"], "end": w["end"], "timezone": w["timezone"], "minutes": w["minutes"],
                         "start_anchor": w["start_anchor"], "end_anchor": w["end_anchor"], "reason": reason})
        elif (cur["start"], cur["end"]) != (w["start"], w["end"]):
            changed.append({"before": {"start": w["start"], "end": w["end"]},
                            "after": {"start": cur["start"], "end": cur["end"]}, "timezone": cur["timezone"],
                            "start_anchor": cur["start_anchor"], "end_anchor": cur["end_anchor"]})
        else:
            still += 1
    changed_fields = list(revs[-1]["changed_fields"])

    def values(times: dict[str, Any], details: dict[str, Any]) -> dict[str, Any]:
        return {f: (times[f].get("local") if f in times else details.get(f)) for f in changed_fields}

    return {"cause": {"key": cause.identity, "kind": cause.kind, "revision": cause.current_revision,
                      "changed_fields": changed_fields, "previous": values(prev_t, prev_d),
                      "current": values(cause.times or {}, cause.details or {})},
            "issues_added": sorted({i["kind"] for i in out["issues"]} - {i["kind"] for i in before["issues"]}),
            "windows_no_longer_provable": lost, "windows_changed": changed, "windows_still_provable": still}
