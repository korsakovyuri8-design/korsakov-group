"""Atlas demo v1: five scenes, computed end to end by the real pipeline,
offline and deterministic.

    booking documents -> Journey Graph -> timeline -> free window
                      -> demo World -> Discovery -> what actually fits

Nothing a scene shows is typed in here: every time, place, issue and
suggestion comes from the fixtures (data/trips/fixtures), the trip policies
and the SYNTHETIC demo world (data/trips/demo_world.yaml). This module only
words the results. Wording keeps planned location apart from observed
location: "your next confirmed place is ...", never "you are at ...".

    python -m app.trip.atlas_demo            # the scenes as text
    python -m app.trip.atlas_demo --json     # the scenes as data
    python -m app.trip.atlas_demo --html demo/atlas_demo.html   # refresh the data the demo page plays back
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import TripItem, TripSourceRecord
from app.db.session import create_schema, make_engine
from app.journey import ingest, policies, timeinfo, timeline
from app.trip import journey_bridge
from app.world.records import PlaceRecord, Provenance, RegionRecord
from app.world.sources import FixtureSource
from app.world.store import sync

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "data" / "trips" / "fixtures"
WORLD = ROOT / "data" / "trips" / "demo_world.yaml"
RECEIVED = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 18, 11, 0, tzinfo=timezone.utc)          # the demo "now": landed in Podgorica
DAY = "2026-10-18"
BRAND = {"name": "KORSAKOV ATLAS", "tagline": "Your entire trip, understood.",
         "cta": "Forward your bookings. Atlas handles the rest."}
DOCS = {"F1": ("airline_email", "F1_airline_lgw_tgd.eml"), "F2": ("hotel_email", "F2_hotel_kotor.eml"),
        "F3": ("restaurant_email", "F3_restaurant_kotor.eml"), "F13": ("transfer_email", "F13_transfer_tgd_kotor.eml"),
        "F1c": ("airline_email", "F1c_airline_lgw_tgd_change.eml")}
DOC_LABELS = {"F1": "Demo Air booking confirmation", "F2": "Hotel Demo Kotor booking confirmation",
              "F3": "DemoTable reservation", "F13": "Demo Transfers confirmation",
              "F1c": "Demo Air schedule change"}
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


# ------------------------------------------------------------------ the world and the trip
def _world(session: Session) -> dict[str, Any]:
    spec = yaml.safe_load(WORLD.read_text(encoding="utf-8"))
    r = spec["region"]
    verified = datetime(2026, 10, 10, tzinfo=timezone.utc)
    places = []
    for p in spec["places"]:
        hours = {}
        if p.get("hours"):
            opens, closes = p["hours"].split("-")
            hours = {"weekly": {d: [[opens, closes]] for d in DAYS}}
        places.append(PlaceRecord(
            slug=p["slug"], name=p["name"], subcategory=p["subcategory"], latitude=p["at"][0], longitude=p["at"][1],
            timezone=r["timezone"], hours=hours, tags=list(p.get("tags") or []),
            attributes={"typical_visit_minutes": p["visit"]} if p.get("visit") else {},
            provenance=Provenance(source_id="fixture:atlas-demo", source_type="fixture", source_record_id=p["slug"],
                                  label="Atlas demo world (synthetic)", last_verified_at=verified, confidence=1.0,
                                  provider_owned=False, is_synthetic=True)))
    sync(session, FixtureSource(RegionRecord(r["slug"], r["name"], r["timezone"], tuple(r["center"])), places, [],
                                source_id="fixture:atlas-demo"), now=verified)
    return spec


def _import(session: Session, trip_id: str, *names: str) -> None:
    for name in names:
        kind, file = DOCS[name]
        ingest.import_document(session, trip_id, kind=kind, raw=(FIX / file).read_text(encoding="utf-8"),
                               source_id=name, received_at=RECEIVED)


def _build(session: Session, trip_id: str) -> dict[str, Any]:
    return timeline.build(timeline.snapshot(session, trip_id), policies.load())


def _count(session: Session, model: Any, trip_id: str) -> int:
    return len(session.scalars(select(model.id).where(model.trip_id == trip_id)).all())


def _items(session: Session, trip_id: str) -> dict[str, TripItem]:
    return {i.identity: i for i in session.scalars(select(TripItem).where(TripItem.trip_id == trip_id))}


# ------------------------------------------------------------------ wording
def _hm(local: str | None) -> str:
    return (local or "")[11:16]


def _span(start: str, end: str) -> str:
    return _hm(start) if start == end else f"{_hm(start)}–{_hm(end)}"


def _city(code: str) -> str:
    return timeinfo.airport_city(code) or code


def _name(item: TripItem) -> str:
    if item.kind == "restaurant":
        return item.details.get("venue") or item.provider
    return (item.provider or item.kind).title()


def _entry(out: dict[str, Any], identity: str, role: str) -> dict[str, Any]:
    return next(e for e in out["entries"] if e["identity"] == identity and e["role"] == role)


def _day_windows(out: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"time": _span(w["start"], w["end"]), "minutes": w["minutes"]}
            for w in out["windows"] if w["start"].startswith(DAY)]


def _issue_text(issue: dict[str, Any], out: dict[str, Any], items: dict[str, TripItem]) -> str:
    if issue["kind"] == "missing_transfer":
        return f"No transport from {issue['from']['locality']} to {issue['to']['locality']}."
    if issue["kind"] == "impossible_connection":
        return (f"{issue['gap_minutes']} min between landing and the transfer; "
                f"about {issue['required_minutes']} min are needed.")
    return issue["kind"].replace("_", " ")


def _issues(out: dict[str, Any], items: dict[str, TripItem]) -> list[dict[str, Any]]:
    return [{"kind": i["kind"], "severity": i["severity"], "text": _issue_text(i, out, items)} for i in out["issues"]]


def _bookings(items: dict[str, TripItem]) -> list[dict[str, Any]]:
    order = {"flight": 0, "accommodation": 1, "transfer": 2, "restaurant": 3}
    rows = []
    for item in sorted(items.values(), key=lambda i: (order.get(i.kind, 9), i.identity)):
        d, t = item.details, item.times
        if item.kind == "flight":
            title = f"Flight {d['flight_number']} {_city(d['origin'])} → {_city(d['destination'])}"
            when = f"{t['departs']['local'][:10]} {_hm(t['departs']['local'])} → {_hm(t['arrives']['local'])}"
        elif item.kind == "accommodation":
            title, when = _name(item), f"{t['check_in']['local']} → {t['check_out']['local']}"
        elif item.kind == "transfer":
            title = f"Transfer {d['pickup']} → {d['dropoff'].split(',')[1].strip()}"
            when = f"{t['pickup_at']['local'][:10]} {_span(t['pickup_at']['local'], t['dropoff_at']['local'])}"
        else:
            title = f"Dinner at {_name(item)}"
            when = f"{t['starts']['local'][:10]} {_hm(t['starts']['local'])}"
        rows.append({"kind": item.kind, "title": title, "when": when, "status": item.status,
                     "provider": item.provider, "reference": item.booking_reference})
    return rows


# ------------------------------------------------------------------ scenes
def _scene_bookings(out: dict[str, Any], items: dict[str, TripItem]) -> dict[str, Any]:
    flight = next(i for i in items.values() if i.kind == "flight")
    stay = next(i for i in items.values() if i.kind == "accommodation")
    hotel_town = stay.details["address"].split(",")[-2].strip()
    first = min(e["local_start"] for e in out["entries"])[:10]
    last = stay.times["check_out"]["local"]
    month = datetime.fromisoformat(first).strftime("%B")
    return {"id": "bookings", "title": "Here are my bookings",
            "route": [_city(flight.details["origin"]), _city(flight.details["destination"]), hotel_town],
            "dates": f"{int(first[8:])}–{int(last[8:])} {month}", "bookings": _bookings(items)}


def _scene_problem(out: dict[str, Any], items: dict[str, TripItem]) -> dict[str, Any]:
    gap = next(i for i in out["issues"] if i["kind"] == "missing_transfer")
    before, after = _entry(out, gap["from"]["identity"], gap["from"]["role"]), \
        _entry(out, gap["to"]["identity"], gap["to"]["role"])
    nxt = items[gap["to"]["identity"]]
    what = f"dinner at {_name(nxt)}" if nxt.kind == "restaurant" else _name(nxt)
    return {"id": "problem", "title": "Atlas notices a problem",
            "lines": [f"You land in {gap['from']['locality']} at {_hm(before['local_end'])}.",
                      f"Your next confirmed place is {what} in {gap['to']['locality']} at "
                      f"{_hm(after['local_start'])}.",
                      "There is no transport between them."],
            "action": "Arrange transfer", "issues": _issues(out, items), "free_time": _day_windows(out)}


def _itinerary(out: dict[str, Any], items: dict[str, TripItem]) -> list[dict[str, Any]]:
    rows: list[tuple[str, int, dict[str, Any]]] = []
    for e in out["entries"]:
        if not e["local_start"] or not e["local_start"].startswith(DAY) or not e["occupies"]:
            continue
        item = items[e["identity"]]
        if e["role"] == "flight":
            d = item.details
            label = (f"Flight {d['flight_number']} {_city(d['origin'])} ({d['origin']}) → "
                     f"{_city(d['destination'])} ({d['destination']})")
            air = int((datetime.fromisoformat(e["end"]) - datetime.fromisoformat(e["start"])).total_seconds() // 60)
            detail = (f"{_hm(e['local_start'])} {_city(d['origin'])} time · {_hm(e['local_end'])} "
                      f"{_city(d['destination'])} time · {air // 60} h {air % 60} min in the air")
            rows.append((e["start"], 0, {"time": _span(e["local_start"], e["local_end"]), "label": label,
                                         "kind": "flight", "detail": detail}))
        elif e["role"] == "transfer":
            rows.append((e["start"], 0, {"time": _span(e["local_start"], e["local_end"]),
                                         "label": f"Transfer from {item.details['pickup']}", "kind": "transfer"}))
        elif e["role"] == "check_in":
            stay = _name(item)
            rows.append((e["start"], 1, {"time": _hm(e["local_start"]), "label": f"Hotel arrival — {stay}",
                                         "kind": "arrival"}))
            rows.append((e["start"], 2, {"time": _span(e["local_start"], e["local_end"]), "label": "Check-in",
                                         "kind": "check_in", "assumed": True}))
        elif e["role"] == "reservation":
            rows.append((e["start"], 0, {"time": _hm(e["local_start"]), "label": f"Dinner at {_name(item)}",
                                         "kind": "restaurant"}))
    for w in out["windows"]:
        if w["start"].startswith(DAY):
            rows.append((w["start_utc"], 0, {"time": _span(w["start"], w["end"]), "label": "Free time",
                                             "kind": "free", "minutes": w["minutes"]}))
    return [r for _, _, r in sorted(rows, key=lambda x: (x[0], x[1]))]


def _scene_rebuilt(out: dict[str, Any], items: dict[str, TripItem]) -> dict[str, Any]:
    return {"id": "rebuilt", "title": "Atlas rebuilds the day", "day": "Sunday 18 October",
            "itinerary": _itinerary(out, items), "issues": _issues(out, items)}


def _scene_suggestions(session: Session, out: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    window = next(w for w in out["windows"] if w["start"].startswith(DAY))
    region = spec["region"]["slug"]
    res = journey_bridge.suggest(session, window, journey_bridge.world_locator(session, spec["links"], region=region),
                                 region=region, now_utc=NOW, categories=("CULTURE", "NATURE"), limit=3)
    hours = window["minutes"] // 60
    picks = []
    for c in res.candidates:
        start, end = c.visit_slot                           # naive local times at the place
        leave = start - timedelta(minutes=c.walk_minutes or 0)
        arrive = end + timedelta(minutes=c.walk_end_minutes or 0)
        picks.append({"name": c.place.name, "kind": c.place.subcategory.replace("_", " "),
                      "walk_there": c.walk_minutes, "walk_on": c.walk_end_minutes,
                      "leave_hotel": leave.strftime("%H:%M"), "visit_from": start.strftime("%H:%M"),
                      "visit_to": end.strftime("%H:%M"), "arrive_dinner": arrive.strftime("%H:%M"),
                      "visit_minutes": int((end - start).total_seconds() // 60)})
    return {"id": "suggestions", "title": "Atlas uses the World",
            "headline": f"You have {hours} hours before dinner. Here are {len(picks)} things that actually fit.",
            "window": {"time": _span(window["start"], window["end"]), "from": window["start_anchor"]["place"],
                       "to": window["end_anchor"]["place"], "assumptions": window["assumptions"]},
            "suggestions": picks, "considered": res.retrieved, "left_out": sum(res.rejected.values()),
            "left_out_reasons": dict(sorted(res.rejected.items()))}


def _scene_change(out: dict[str, Any], items: dict[str, TripItem]) -> dict[str, Any]:
    flight = next(i for i in items.values() if i.kind == "flight")
    revs = sorted(flight.revisions, key=lambda r: (r["issued_at"] or "", r["revision"]))
    before = {}
    for r in revs[:-1]:
        before.update(r["times"])
    now = flight.times
    lines = [f"Departure moved from {_hm(before['departs']['local'])} to {_hm(now['departs']['local'])}.",
             f"You now land in {_city(flight.details['destination'])} at {_hm(now['arrives']['local'])}."]
    clash = next((i for i in out["issues"] if i["kind"] == "impossible_connection"), None)
    if clash:
        transfer = _entry(out, clash["before"]["identity"], clash["before"]["role"])
        lines += [f"Your transfer from {clash['before']['place']} leaves at {_hm(transfer['local_start'])} — "
                  f"{clash['gap_minutes']} min after landing; getting out of the airport takes about "
                  f"{clash['required_minutes']}.",
                  "Your existing transfer may no longer fit."]
    dinner = next((i for i in items.values() if i.kind == "restaurant" and i.status == "confirmed"), None)
    plan = (f"By your plan you are still due at {_name(dinner)} at {_hm(dinner.times['starts']['local'])}; "
            "the free time before it is on hold until the transfer is sorted.") if dinner else None
    return {"id": "change", "title": "A flight change arrives", "headline": f"{flight.provider.title()} changed your flight.",
            "lines": lines, "plan": plan, "status": "Transfer needs rebooking" if clash else None,
            "action": f"Contact {next(i for i in items.values() if i.kind == 'transfer').provider.title()}",
            "issues": _issues(out, items), "free_time": _day_windows(out), "itinerary": _itinerary(out, items)}


def run() -> dict[str, Any]:
    eng = make_engine("sqlite://")
    create_schema(eng)
    try:
        with sessionmaker(eng)() as s:
            spec = _world(s)
            trip = ingest.create_trip(s, title="London - Kotor")
            _import(s, trip.id, "F1", "F2", "F3")
            a, items = _build(s, trip.id), _items(s, trip.id)
            imported = {"documents": _count(s, TripSourceRecord, trip.id), "bookings": len(items)}
            scenes = [{**_scene_bookings(a, items), "documents": [DOC_LABELS[d] for d in ("F1", "F2", "F3")],
                       "imported": imported},
                      _scene_problem(a, items)]
            _import(s, trip.id, "F13")
            b, items = _build(s, trip.id), _items(s, trip.id)
            scenes += [{**_scene_rebuilt(b, items), "documents": [DOC_LABELS["F13"]]},
                       _scene_suggestions(s, b, spec)]
            _import(s, trip.id, "F1c")
            c, items = _build(s, trip.id), _items(s, trip.id)
            scenes.append({**_scene_change(c, items), "documents": [DOC_LABELS["F1c"]]})
            summary = {"documents": _count(s, TripSourceRecord, trip.id),
                       "bookings": len([i for i in items.values() if i.kind != "statement"]),
                       "missing_connections": sum(i["kind"] == "missing_transfer" for i in a["issues"]),
                       "activities_that_fit": len(scenes[3]["suggestions"]),
                       "disruptions": sum(i["kind"] == "impossible_connection" for i in c["issues"])}
            return {"brand": BRAND, "summary": summary, "policy_version": b["policy_version"], "registry": b["registry"],
                    "world": "synthetic demo world (data/trips/demo_world.yaml)", "scenes": scenes}
    finally:
        eng.dispose()


def _text(demo: dict[str, Any]) -> str:
    out = [demo["brand"]["name"], demo["brand"]["tagline"], ""]
    for n, s in enumerate(demo["scenes"], 1):
        out.append(f"Scene {n} — {s['title']}")
        if s["id"] == "bookings":
            out.append("  " + " → ".join(s["route"]) + f"   {s['dates']}")
            out += [f"  · {b['title']}  {b['when']}  [{b['status']}]" for b in s["bookings"]]
        elif s["id"] in ("problem", "change"):
            out += [f"  ⚠ {s['headline']}"] if s.get("headline") else []
            out += [f"  {line}" for line in s["lines"]]
            out += [f"  {s['plan']}"] if s.get("plan") else []
            out.append(f"  [ {s['action']} ]")
        elif s["id"] == "rebuilt":
            out += [f"  {i['time']:<12} {i['label']}" for i in s["itinerary"]]
        elif s["id"] == "suggestions":
            out.append(f"  {s['headline']}")
            out += [f"  · {x['name']} ({x['kind']}): {x['walk_there']} min walk, visit {x['visit_from']}–{x['visit_to']}, "
                    f"{x['walk_on']} min walk to dinner" for x in s["suggestions"]]
            out.append(f"  ({s['considered']} considered, {s['left_out']} don't fit your time)")
        out.append("")
    out.append(demo["brand"]["cta"])
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Atlas demo v1 (offline)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--html", metavar="PAGE", help="refresh the scene data embedded in the demo page")
    args = parser.parse_args()
    demo = run()
    if args.html:
        page = Path(args.html)
        text = page.read_text(encoding="utf-8")
        start, end = "<!--ATLAS-DATA-->", "<!--/ATLAS-DATA-->"
        a, b = text.index(start) + len(start), text.index(end)
        data = json.dumps(demo, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
        page.write_text(f'{text[:a]}<script type="application/json" id="atlas-data">{data}</script>{text[b:]}',
                        encoding="utf-8")
        print(f"updated {page}")
        return
    print(json.dumps(demo, ensure_ascii=False, indent=2, sort_keys=True) if args.json else _text(demo))


if __name__ == "__main__":
    main()
