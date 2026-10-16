"""Atlas Public View API v2: a richer, still read-only view-model of the trip.

Added inside `trip` (every v1 field stays byte-identical, see the golden file):
  context     - the demo clock ("now"), its timezone, and where the PLAN puts
                the traveller at that moment (basis is always "planned")
  next_item   - the next booked thing after now, chosen by the backend
  items       - detail views built from already-resolved TripItem data
  disruption  - after a provider change: which free windows were provable
                before it and are not any more, and why
No Journey Graph rule changes; no raw evidence, source documents, record ids
or database ids in any response; structured data, not presentation text."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.atlas_web.app import create_app
from app.atlas_web.sessions import SessionStore
from tests.conftest import ROOT

GOLDEN = json.loads((Path(__file__).parent / "golden" / "atlas_web_v1.json").read_text(encoding="utf-8"))
FIX = ROOT / "data" / "trips" / "fixtures"
FLIGHT = "flight|demo air|DMX7Q2|DM1432|LGW-TGD|2026-10-18"
TRANSFER = "transfer|demo transfers|TR551"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _run() -> dict:
    """The sample path, as the frontend drives it."""
    with TestClient(create_app(store=SessionStore())) as c:
        out = {}
        d = c.get("/demo/sample").json()
        sid = d.pop("session")
        out["sample"] = d
        for step in ("transfer", "flight_change"):
            r = c.post("/demo/import", json={"session": sid, "sample": step}).json()
            r.pop("session")
            out[step] = r
        r = c.get(f"/demo/trip/{sid}").json()
        r.pop("session")
        out["trip"] = r
        body = (FIX / "F1_airline_lgw_tgd.eml").read_text(encoding="utf-8").split("\n\n", 1)[1]
        r = c.post("/demo/import", json={"text": body}).json()
        r.pop("session")
        out["paste_f1"] = r
        out["unreadable"] = c.post("/demo/import", json={"text": "not a booking"}).json()
        return out


@pytest.fixture(scope="module")
def run() -> dict:
    return _run()


def _v1(value, golden):  # noqa: ANN001, ANN202
    """`value` restricted to the keys the v1 golden response had (recursively)."""
    if isinstance(golden, dict) and isinstance(value, dict):
        return {k: _v1(value[k], golden[k]) for k in golden if k in value}
    if isinstance(golden, list) and isinstance(value, list):
        return [_v1(v, g) for v, g in zip(value, golden)] + value[len(golden):]
    return value


# ------------------------------------------------------------------ compatibility
def test_every_v1_field_is_unchanged(run):
    for step, golden in GOLDEN.items():
        assert _v1(run[step], golden) == golden, step


def test_v2_adds_fields_only_inside_trip(run):
    for step, golden in GOLDEN.items():
        assert set(run[step]) == set(golden), step
        if run[step].get("trip"):
            assert set(run[step]["trip"]) - set(golden["trip"]) == {"context", "next_item", "items", "disruption"}


def test_the_view_is_deterministic(run):
    assert _run() == run


# ------------------------------------------------------------------ context
def test_context_states_the_demo_clock_and_where_the_plan_puts_you(run):
    ctx = run["sample"]["trip"]["context"]
    assert ctx["now"] == {"utc": "2026-10-18T11:00:00+00:00", "local": "2026-10-18T13:00",
                          "timezone": "Europe/Podgorica", "clock": "demo"}
    assert ctx["planned_location"] == {
        "basis": "planned", "status": "arrived", "place": "TGD", "locality": "Podgorica",
        "timezone": "Europe/Podgorica", "since": {"local": "2026-10-18T12:55", "utc": "2026-10-18T10:55:00+00:00"},
        "by": {"key": FLIGHT, "role": "flight"}}


def test_during_a_flight_the_plan_says_in_transit(run):
    loc = run["flight_change"]["trip"]["context"]["planned_location"]
    assert loc["basis"] == "planned" and loc["status"] == "in_transit"
    assert (loc["from"], loc["to"], loc["locality"]) == ("LGW", "TGD", None)
    assert loc["until"] == {"local": "2026-10-18T14:55", "utc": "2026-10-18T12:55:00+00:00"}


def test_the_planned_location_is_never_called_observed(run):
    text = json.dumps(run)
    assert '"observed"' not in text and '"basis": "planned"' in text


# ------------------------------------------------------------------ next item
def test_the_next_item_is_chosen_by_the_backend(run):
    nxt = run["sample"]["trip"]["next_item"]
    assert nxt == {"key": "restaurant|demotable|R4417", "kind": "restaurant", "role": "reservation",
                   "starts": {"local": "2026-10-18T20:00", "utc": "2026-10-18T18:00:00+00:00",
                              "timezone": "Europe/Podgorica"},
                   "place": "Stari grad 210, Kotor, Montenegro", "locality": "Kotor", "minutes_from_now": 420,
                   "issues": ["missing_transfer"]}
    assert run["transfer"]["trip"]["next_item"]["key"] == TRANSFER
    assert run["transfer"]["trip"]["next_item"]["minutes_from_now"] == 120 and \
        run["transfer"]["trip"]["next_item"]["issues"] == []


def test_a_next_item_at_risk_says_so(run):
    nxt = run["flight_change"]["trip"]["next_item"]
    assert nxt["key"] == TRANSFER and nxt["issues"] == ["impossible_connection"]


# ------------------------------------------------------------------ item detail views
def test_item_details_come_from_resolved_trip_items(run):
    items = {i["key"]: i for i in run["transfer"]["trip"]["items"]}
    assert list(items) == [FLIGHT, "accommodation|hotel demo kotor|HK20931", TRANSFER, "restaurant|demotable|R4417"]
    f = items[FLIGHT]
    assert f["kind"] == "flight" and f["provider"] == "demo air" and f["reference"] == "DMX7Q2"
    assert f["status"] == "confirmed" and f["revision"] == {"current": 1, "count": 1, "changed_fields": []}
    assert f["detail"] == {
        "carrier": "DM", "flight_number": "DM1432", "cabin": "Economy", "passengers": 2, "service_date": "2026-10-18",
        "origin": {"code": "LGW", "city": "London", "timezone": "Europe/London"},
        "destination": {"code": "TGD", "city": "Podgorica", "timezone": "Europe/Podgorica"},
        "departs": {"local": "2026-10-18T09:15", "utc": "2026-10-18T08:15:00+00:00", "timezone": "Europe/London"},
        "arrives": {"local": "2026-10-18T12:55", "utc": "2026-10-18T10:55:00+00:00", "timezone": "Europe/Podgorica"},
        "duration_minutes": 160}
    h = items["accommodation|hotel demo kotor|HK20931"]["detail"]
    assert h == {"address": "Stari grad 1, Kotor, Montenegro", "locality": "Kotor", "room": "Double room, sea view",
                 "guests": 2, "check_in": {"date": "2026-10-18", "from": "14:00", "timezone": "Europe/Podgorica"},
                 "check_out": {"date": "2026-10-21", "until": "11:00", "timezone": "Europe/Podgorica"}, "nights": 3}
    t = items[TRANSFER]["detail"]
    assert t["pickup"] == {"place": "TGD", "kind": "airport", "locality": "Podgorica"}
    assert t["dropoff"] == {"place": "Stari grad 1, Kotor, Montenegro", "kind": "address", "locality": "Kotor"}
    assert t["pickup_at"]["local"] == "2026-10-18T15:00" and t["passengers"] == 2
    r = items["restaurant|demotable|R4417"]["detail"]
    assert r == {"venue": "Konoba Demo", "address": "Stari grad 210, Kotor, Montenegro", "locality": "Kotor",
                 "party": 2, "starts": {"local": "2026-10-18T20:00", "utc": "2026-10-18T18:00:00+00:00",
                                        "timezone": "Europe/Podgorica"}}


def test_a_revised_item_shows_its_revision_not_its_documents(run):
    f = next(i for i in run["flight_change"]["trip"]["items"] if i["key"] == FLIGHT)
    assert f["revision"] == {"current": 2, "count": 2, "changed_fields": ["arrives", "departs"]}
    assert f["detail"]["departs"]["local"] == "2026-10-18T11:15"


# ------------------------------------------------------------------ disruption impact
def test_no_disruption_before_any_provider_change(run):
    assert run["sample"]["trip"]["disruption"] is None and run["transfer"]["trip"]["disruption"] is None


def test_the_flight_change_lists_the_window_that_is_no_longer_provable(run):
    d = run["flight_change"]["trip"]["disruption"]
    assert d["cause"] == {"key": FLIGHT, "kind": "flight", "revision": 2, "changed_fields": ["arrives", "departs"],
                          "previous": {"departs": "2026-10-18T09:15", "arrives": "2026-10-18T12:55"},
                          "current": {"departs": "2026-10-18T11:15", "arrives": "2026-10-18T14:55"}}
    assert d["issues_added"] == ["impossible_connection"]
    lost = d["windows_no_longer_provable"]
    assert lost == [{"start": "2026-10-18T16:30", "end": "2026-10-18T19:30", "timezone": "Europe/Podgorica",
                     "minutes": 180,
                     "start_anchor": {"identity": "accommodation|hotel demo kotor|HK20931", "role": "check_in",
                                      "place": "Stari grad 1, Kotor, Montenegro"},
                     "end_anchor": {"identity": "restaurant|demotable|R4417", "role": "reservation",
                                    "place": "Stari grad 210, Kotor, Montenegro"},
                     "reason": "start_anchor_not_proven"}]
    assert d["windows_still_provable"] == 3 and d["windows_changed"] == []


# ------------------------------------------------------------------ nothing raw
def test_no_raw_evidence_documents_or_ids_are_exposed(run):
    text = json.dumps(run, ensure_ascii=False)
    for key in ('"evidence"', '"raw"', '"record_ids"', '"revisions"', '"observations"', '"issued_at"'):
        assert key not in text, key
    for line in ("Your booking is confirmed.", "Booking reference:", "Issued:", "Ana Example", "Marko Example"):
        assert line not in text, line
    assert not UUID.search(text)
