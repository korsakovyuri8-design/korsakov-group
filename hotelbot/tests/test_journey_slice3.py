"""Iteration 7, slice 3 (docs/ITERATION_7_DESIGN.md §18): the deterministic
timeline, its derived issues and the free windows between proven anchors.

E2E-A: flight + hotel + restaurant, no transfer -> one missing_transfer and
no window on the arrival day. E2E-B: + F13 transfer -> check-in handling and
the 16:30-19:30 window, hotel -> Konoba Demo."""

from __future__ import annotations

import itertools
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db.models import TripItem
from app.db.session import create_schema, make_engine
from app.journey import ingest, policies, timeline
from tests.conftest import ROOT

FIX = ROOT / "data" / "trips" / "fixtures"
RECEIVED = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
KIND = {"F1": "airline_email", "F2": "hotel_email", "F3": "restaurant_email", "F13": "transfer_email",
        "F6": "guest_statement"}
FILES = {"F1": "F1_airline_lgw_tgd.eml", "F2": "F2_hotel_kotor.eml", "F3": "F3_restaurant_kotor.eml",
         "F3b": "F3b_restaurant_cancel.eml", "F13": "F13_transfer_tgd_kotor.eml", "F6": "F6_guest_statement.json"}
E2E_A = ["F1", "F2", "F3"]
E2E_B = ["F1", "F2", "F3", "F13"]


def _raw(name: str) -> str:
    return (FIX / FILES[name]).read_text(encoding="utf-8")


def _session():  # noqa: ANN202
    eng = make_engine("sqlite://")
    create_schema(eng)
    return sessionmaker(eng)()


def _trip(docs, raws: dict[str, str] | None = None):  # noqa: ANN001, ANN202
    """A fresh database with `docs` imported in this order -> (session, trip id)."""
    s = _session()
    trip = ingest.create_trip(s, title="London - Kotor")
    for name in docs:
        raw = (raws or {}).get(name) or _raw(name)
        ingest.import_document(s, trip.id, kind=KIND[name.rstrip("bcd")],
                               raw=raw, source_id=name, received_at=RECEIVED)
    return s, trip.id


def _build(docs, raws=None) -> dict:  # noqa: ANN001
    s, trip_id = _trip(docs, raws)
    try:
        return timeline.build(timeline.snapshot(s, trip_id), policies.load())
    finally:
        s.close()


def _day(out: dict, day: str) -> list[tuple[str, str]]:
    return [(w["start"][11:], w["end"][11:]) for w in out["windows"] if w["start"].startswith(day)]


def _issues(out: dict, kind: str) -> list[dict]:
    return [i for i in out["issues"] if i["kind"] == kind]


# ================================================================== 1. timeline core
def test_the_transfer_document_is_a_confirmed_item_with_provenance():
    s, trip_id = _trip(["F13"])
    item = s.scalar(select(TripItem).where(TripItem.trip_id == trip_id))
    assert item.kind == "transfer" and item.status == "confirmed" and item.booking_reference == "TR551"
    assert item.details == {"pickup": "TGD", "pickup_place_kind": "airport",
                            "dropoff": "Stari grad 1, Kotor, Montenegro", "dropoff_place_kind": "address",
                            "passengers": 2}
    assert item.times["pickup_at"]["utc"] == "2026-10-18T13:00:00+00:00"
    assert item.times["dropoff_at"]["tz"] == "Europe/Podgorica" and item.times["dropoff_at"]["local"] == "2026-10-18T16:00"
    assert ingest.provenance_violations(s, item) == []


def test_any_import_order_gives_a_byte_identical_timeline_and_windows():
    outs = {json.dumps(_build(list(order)), sort_keys=True) for order in itertools.permutations(E2E_B)}
    assert len(outs) == 1


def test_database_uuids_have_no_effect():
    s, trip_id = _trip(E2E_B)
    snap = timeline.snapshot(s, trip_id)
    ids = {i.id for i in s.scalars(select(TripItem))} | {trip_id}
    text = json.dumps(timeline.build(snap, policies.load()), sort_keys=True) + json.dumps(snap, sort_keys=True)
    assert not any(i in text for i in ids)                  # neither the snapshot nor the timeline carries an id
    assert all("evidence" not in rec for item in snap for rec in item["times"].values())


def test_equal_times_are_ordered_by_semantic_identity_not_input_order():
    def flight(identity: str) -> dict:
        t = lambda local, utc, tz, place: {"local": local, "utc": utc, "tz": tz, "place": place}  # noqa: E731
        return {"kind": "flight", "identity": identity, "status": "confirmed",
                "details": {"origin": "LGW", "destination": "TGD"},
                "times": {"departs": t("2026-10-18T09:15", "2026-10-18T08:15:00+00:00", "Europe/London", "LGW"),
                          "arrives": t("2026-10-18T12:55", "2026-10-18T10:55:00+00:00", "Europe/Podgorica", "TGD")}}
    a, b = flight("flight|x|A"), flight("flight|x|B")
    one, two = timeline.build([b, a], policies.load()), timeline.build([a, b], policies.load())
    assert one == two and [e["identity"] for e in one["entries"]] == ["flight|x|A", "flight|x|B"]


def test_confirmed_items_occupy_time_cancelled_stay_in_history():
    out = _build(["F1", "F2", "F3", "F3b", "F13"])
    assert [e["role"] for e in out["entries"] if e["occupies"]] == ["flight", "transfer", "check_in", "check_out"]
    assert [h["kind"] for h in out["history"]] == ["restaurant"] and out["history"][0]["status"] == "cancelled"


def test_a_stated_item_is_tentative_and_never_on_the_timeline():
    out = _build(E2E_B + ["F6"])
    assert [t["kind"] for t in out["tentative"]] == ["statement"] and out["tentative"][0]["status"] == "stated"
    assert all(e["kind"] != "statement" for e in out["entries"])
    without = _build(E2E_B)
    assert out["windows"] == without["windows"] and out["issues"] == without["issues"]


def test_an_unknown_timezone_stays_off_the_absolute_timeline():
    raws = {"F3": _raw("F3").replace("Kotor, Montenegro", "Kotor, Atlantis")}
    out = _build(E2E_B, raws)
    assert [f["kind"] for f in out["floating"]] == ["restaurant"]
    assert all(e["kind"] != "restaurant" for e in out["entries"])


def test_accommodation_is_a_base_location_not_an_occupied_interval():
    out = _build(E2E_B)
    stay = [e for e in out["entries"] if e["kind"] == "accommodation"]
    occupying = {e["role"]: (e["local_start"], e["local_end"]) for e in stay if e["occupies"]}
    assert occupying == {"check_in": ("2026-10-18T16:00", "2026-10-18T16:30"),
                         "check_out": ("2026-10-21T10:45", "2026-10-21T11:00")}
    assert {e["role"] for e in stay if not e["occupies"]} == {"sleep", "wake"}


# ================================================================== 2. issues
def test_e2e_a_no_transfer_is_exactly_one_missing_transfer_warning():
    out = _build(E2E_A)
    missing = _issues(out, "missing_transfer")
    assert len(missing) == 1 and len(out["issues"]) == 1
    assert missing[0]["from"]["place"] == "TGD" and missing[0]["from"]["locality"] == "Podgorica"
    assert missing[0]["to"]["locality"] == "Kotor" and missing[0]["severity"] == "warning"
    assert not any(e["role"] == "check_in" for e in out["entries"])       # arrival at the hotel is not proven


def test_e2e_b_the_transfer_removes_the_warning():
    assert _build(E2E_B)["issues"] == []


def test_an_overlap_keeps_both_bookings():
    second = (_raw("F3").replace("R4417", "R5550").replace("Time: 20:00", "Time: 21:00")
              .replace("<r4417-1@", "<r5550-1@"))
    s, trip_id = _trip(E2E_B)
    ingest.import_document(s, trip_id, kind="restaurant_email", raw=second, source_id="second", received_at=RECEIVED)
    out = timeline.build(timeline.snapshot(s, trip_id), policies.load())
    overlap = _issues(out, "overlapping_bookings")
    assert len(overlap) == 1 and {overlap[0]["a"]["identity"], overlap[0]["b"]["identity"]} == \
        {"restaurant|demotable|R4417", "restaurant|demotable|R5550"}
    assert len([e for e in out["entries"] if e["kind"] == "restaurant"]) == 2       # both kept


def test_an_impossible_connection_is_surfaced_not_fixed():
    early = _raw("F13").replace("18 Oct 2026 15:00", "18 Oct 2026 13:15").replace("18 Oct 2026 16:00",
                                                                                  "18 Oct 2026 14:15")
    out = _build(E2E_B, {"F13": early})
    bad = _issues(out, "impossible_connection")
    assert len(bad) == 1 and bad[0]["gap_minutes"] == 20 and bad[0]["required_minutes"] == 45
    transfer = next(e for e in out["entries"] if e["role"] == "transfer")
    assert transfer["local_start"] == "2026-10-18T13:15"                          # the booking is not moved
    assert not any(e["role"] == "check_in" for e in out["entries"])   # a missed transfer proves no arrival
    assert _day(out, "2026-10-18") == []


# ================================================================== 3. free windows
def test_e2e_a_no_false_kotor_window_on_the_arrival_day():
    assert _day(_build(E2E_A), "2026-10-18") == []


def test_e2e_b_the_window_is_16_30_to_19_30_hotel_to_restaurant():
    out = _build(E2E_B)
    assert _day(out, "2026-10-18") == [("16:30", "19:30")]
    w = next(w for w in out["windows"] if w["start"].startswith("2026-10-18"))
    assert w["timezone"] == "Europe/Podgorica" and w["start_utc"] == "2026-10-18T14:30:00+00:00"
    assert w["start_anchor"] == {"identity": "accommodation|hotel demo kotor|HK20931", "role": "check_in",
                                 "place": "Stari grad 1, Kotor, Montenegro"}
    assert w["end_anchor"] == {"identity": "restaurant|demotable|R4417", "role": "reservation",
                               "place": "Stari grad 210, Kotor, Montenegro"}
    assert w["policy_version"] == policies.load()["version"] and w["minutes"] == 180
    assert {"policy": "reservation_buffer_minutes", "minutes": 30, "at": "end"} in w["assumptions"]


def test_full_days_and_the_check_out_morning():
    out = _build(E2E_B)
    assert _day(out, "2026-10-19") == [("08:00", "23:00")] and _day(out, "2026-10-20") == [("08:00", "23:00")]
    assert _day(out, "2026-10-21") == [("08:00", "10:45")]       # after check-out the location is not proven


def test_a_cancelled_restaurant_occupies_no_window():
    out = _build(["F1", "F2", "F3", "F3b", "F13"])
    assert _day(out, "2026-10-18") == [("16:30", "23:00")]


def test_windows_never_come_from_tentative_or_floating_items():
    raws = {"F3": _raw("F3").replace("Kotor, Montenegro", "Kotor, Atlantis")}
    out = _build(E2E_B + ["F6"], raws)
    assert _day(out, "2026-10-18") == [("16:30", "23:00")]       # the floating table cuts nothing either


# ================================================================== 4. Discovery bridge + fixture world
from app.discovery.engine import DiscoveryQuery, run  # noqa: E402
from app.shared.geo import Point, StraightLineEstimator  # noqa: E402
from app.trip import journey_bridge  # noqa: E402
from app.world.records import PlaceRecord, Provenance, RegionRecord  # noqa: E402
from app.world.sources import FixtureSource  # noqa: E402
from app.world.store import sync  # noqa: E402

REGION, TZ = "kotor-trip-demo", "Europe/Podgorica"
HOTEL, KONOBA = (43.4247, 18.7712), (43.4500, 18.7712)          # ~2.8 km apart, ~50 min on foot
OPEN = {"weekly": {d: [["09:00", "21:00"]] for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}}
LINKS = {"accommodation|hotel demo kotor|HK20931": "hotel-demo-kotor", "restaurant|demotable|R4417": "konoba-demo"}


def _world(s) -> None:  # noqa: ANN001
    def place(slug, sub, at, **kw):  # noqa: ANN001, ANN202
        prov = Provenance(source_id="fixture:trip", source_type="fixture", source_record_id=slug, label="trip E2E",
                          last_verified_at=datetime(2026, 10, 10, tzinfo=timezone.utc), confidence=1.0,
                          provider_owned=False, is_synthetic=True)
        return PlaceRecord(slug=slug, name=slug.replace("-", " ").title(), subcategory=sub, provenance=prov,
                           latitude=at[0], longitude=at[1], timezone=TZ, **kw)
    mid = ((HOTEL[0] + KONOBA[0]) / 2, HOTEL[1])
    places = [place("hotel-demo-kotor", "hotel", HOTEL), place("konoba-demo", "konoba", KONOBA, hours=OPEN),
              place("museum-by-the-hotel", "museum", (HOTEL[0], HOTEL[1] + 0.001), hours=OPEN,
                    attributes={"typical_visit_minutes": 150}),
              place("museum-on-the-way", "museum", mid, hours=OPEN, attributes={"typical_visit_minutes": 60}),
              place("museum-far-away", "museum", (43.30, 18.95), hours=OPEN, attributes={"typical_visit_minutes": 30})]
    sync(s, FixtureSource(RegionRecord(REGION, "Kotor trip demo", TZ, HOTEL), places, [], source_id="fixture:trip"),
         now=datetime(2026, 10, 10, tzinfo=timezone.utc))


@pytest.fixture
def e2e():  # noqa: ANN201
    s, trip_id = _trip(E2E_B)
    _world(s)
    out = timeline.build(timeline.snapshot(s, trip_id), policies.load())
    window = next(w for w in out["windows"] if w["start"].startswith("2026-10-18"))
    yield s, trip_id, window
    s.close()


NOW = datetime(2026, 10, 18, 10, 0, tzinfo=timezone.utc)


def test_without_end_near_the_old_fit_check_is_unchanged(e2e):
    s, _, window = e2e
    q = DiscoveryQuery(region=REGION, categories={"CULTURE"}, at=datetime(2026, 10, 18, 16, 30),
                       window_end=datetime(2026, 10, 18, 19, 30), open_at=True, visit_minutes=180,
                       near=Point(*HOTEL), limit=5)
    assert q.end_near is None
    slugs = [c.place.slug for c in run(s, q, NOW, TZ).candidates]
    assert "museum-by-the-hotel" in slugs                         # 150 + 2 x 1 min: fits "there and back"


def test_start_candidate_end_really_fits_the_window(e2e):
    s, _, window = e2e
    res = journey_bridge.suggest(s, window, journey_bridge.world_locator(s, LINKS, region=REGION), region=REGION,
                                 now_utc=NOW, limit=5)
    assert res.query.near == Point(*HOTEL) and res.query.end_near == Point(*KONOBA)
    slugs = [c.place.slug for c in res.candidates]
    assert slugs == ["museum-on-the-way"]
    assert res.rejected["does_not_fit_window"] == 2               # by-the-hotel (150 + 1 + ~50) and far-away
    est = StraightLineEstimator()
    for c in res.candidates:
        p = Point(c.place.latitude, c.place.longitude)
        need = est.walking_minutes(Point(*HOTEL), p) + c.place.attributes["typical_visit_minutes"] + \
            est.walking_minutes(p, Point(*KONOBA))
        assert need <= window["minutes"]


def test_an_unlocated_start_asks_discovery_nothing(e2e):
    s, _, window = e2e
    assert journey_bridge.suggest(s, window, journey_bridge.world_locator(s, {}), region=REGION, now_utc=NOW) is None


def test_changing_a_world_link_does_not_change_booking_truth(e2e):
    s, trip_id, window = e2e
    before = (timeline.snapshot(s, trip_id), timeline.build(timeline.snapshot(s, trip_id), policies.load()))
    moved = {**LINKS, "restaurant|demotable|R4417": "museum-by-the-hotel"}
    res = journey_bridge.suggest(s, window, journey_bridge.world_locator(s, moved, region=REGION), region=REGION,
                                 now_utc=NOW, limit=5)
    assert res.query.end_near != Point(*KONOBA)                   # the link moved the measurement only
    after = (timeline.snapshot(s, trip_id), timeline.build(timeline.snapshot(s, trip_id), policies.load()))
    assert before == after
    assert all(i.world_entity_id is None for i in s.scalars(select(TripItem)))


def test_the_journey_layer_imports_neither_discovery_nor_the_trip_layer():
    import ast

    for f in (ROOT / "app" / "journey").rglob("*.py"):
        tree = ast.parse(f.read_text())
        mods = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module] + \
               [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        assert not [m for m in mods if m.startswith(("app.discovery", "app.trip", "app.world"))], f
    bridge = ast.parse((ROOT / "app" / "trip" / "journey_bridge.py").read_text())
    assert not [n for n in ast.walk(bridge) if isinstance(n, ast.Attribute) and n.attr in ("status", "times", "details")
                and isinstance(n.ctx, ast.Store)]                  # the bridge writes no booking fact
