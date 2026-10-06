"""Local travel layer: hours, geo, taxonomy/NLU, region ingest, availability,
discovery filtering/ranking and the itinerary."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.clock import FrozenClock, as_utc
from app.db.models import AvailabilitySlot, Event, ItemStatus, Place
from app.discovery.engine import DiscoveryQuery, discover_events, discover_places
from app.discovery.nlu import parse_discovery
from app.marketplace.inventory import find_slot
from app.places.geo import Point, distance_km, walking_minutes
from app.places.hours import OpenState, open_during, status_at
from app.places.taxonomy import SUBCATEGORIES, Category, category_of, label
from app.trip import itinerary
from tests.conftest import ROOT

TZ = "Europe/Podgorica"
MON = datetime(2026, 10, 5, 14, 0)            # local, naive (Monday)
HOTEL = Point(43.1544, 19.1225)

HOURS = {
    "weekly": {"mon": [], "tue": [["12:00", "23:00"]], "fri": [["19:00", "02:00"]], "sat": [["19:00", "02:00"]]},
    "kitchen": {"tue": [["12:00", "22:00"]]},
    "special": {"2026-10-08": [["10:00", "12:00"]]},
    "closed": [{"from": "2026-10-20", "to": "2026-10-22", "reason": "renovation"}],
}


# ------------------------------------------------------------------ hours
def test_hours_open_closed_open_later():
    assert status_at(HOURS, MON).state == OpenState.CLOSED                      # Monday: closed all day
    tue_morning = status_at(HOURS, datetime(2026, 10, 6, 10, 0))
    assert tue_morning.state == OpenState.OPEN_LATER and tue_morning.opens_at.hour == 12
    tue = status_at(HOURS, datetime(2026, 10, 6, 21, 0))
    assert tue.state == OpenState.OPEN and tue.closes_at == datetime(2026, 10, 6, 23, 0)


def test_hours_kitchen_closes_before_venue():
    at = datetime(2026, 10, 6, 22, 30)
    assert status_at(HOURS, at).state == OpenState.OPEN
    assert status_at(HOURS, at, key="kitchen").state != OpenState.OPEN


def test_hours_past_midnight_belongs_to_previous_day():
    sat_1am = datetime(2026, 10, 10, 1, 0)          # Friday's 19:00-02:00 range
    st = status_at(HOURS, sat_1am)
    assert st.state == OpenState.OPEN and st.closes_at == datetime(2026, 10, 10, 2, 0)
    assert open_during(HOURS, datetime(2026, 10, 9, 23, 0), datetime(2026, 10, 10, 1, 30)) is True
    assert open_during(HOURS, datetime(2026, 10, 9, 23, 0), datetime(2026, 10, 10, 2, 30)) is False


def test_hours_special_day_and_temporary_closure():
    assert status_at(HOURS, datetime(2026, 10, 8, 11, 0)).state == OpenState.OPEN       # special opening
    closed = status_at(HOURS, datetime(2026, 10, 20, 13, 0))                            # Tuesday, renovation
    assert closed.state == OpenState.CLOSED and closed.reason == "renovation"


def test_hours_last_entry_and_seasonal_and_unknown():
    club = {"weekly": {"fri": [["23:00", "04:00"]]}, "last_entry": "03:00"}
    assert status_at(club, datetime(2026, 10, 10, 2, 30)).state == OpenState.OPEN
    assert status_at(club, datetime(2026, 10, 10, 3, 30)).state != OpenState.OPEN       # after last entry
    ski = {"seasonal": [{"from": "12-15", "to": "04-10", "weekly": {"mon": [["08:00", "16:00"]]}}], "weekly": {}}
    assert status_at(ski, datetime(2027, 1, 11, 9, 0)).state == OpenState.OPEN
    assert status_at(ski, datetime(2026, 10, 5, 9, 0)).state == OpenState.CLOSED        # out of season
    assert status_at(None, MON).state == OpenState.UNKNOWN
    assert open_during(None, MON) is None


# -------------------------------------------------------------- geo / taxonomy
def test_geo_distance_and_walking():
    assert distance_km(HOTEL, HOTEL) == pytest.approx(0.0)
    d = distance_km(HOTEL, Point(43.1544, 19.1350))       # ~1 km east
    assert 0.9 < d < 1.1
    assert walking_minutes(1.0) >= 12


def test_taxonomy_has_all_top_level_categories_and_localized_labels():
    assert len(Category) == 19
    assert {s.category for s in SUBCATEGORIES.values()} <= set(Category)
    assert category_of("pharmacy") == "HEALTH"
    assert label("konoba", "ru") == "коноба" and label("pharmacy", "en") == "pharmacy"


# ---------------------------------------------------------------------- NLU
@pytest.mark.parametrize("text, subs, cats", [
    ("Where is the nearest pharmacy?", {"pharmacy"}, set()),
    ("Gdje je najbliži bankomat?", {"atm"}, set()),
    ("Где купить сим-карту?", {"sim_shop"}, set()),
    ("Where can I buy groceries?", {"supermarket"}, set()),
    ("Suggest somewhere lively for drinks tonight", set(), {"NIGHTLIFE"}),
])
def test_nlu_categories(text, subs, cats):
    req = parse_discovery(text, MON.replace(tzinfo=timezone.utc))
    assert req is not None
    assert subs <= req.query.subcategories
    assert cats <= req.query.categories


def test_nlu_constraints_and_time():
    req = parse_discovery("Find a vegan restaurant open now near the hotel", MON)
    q = req.query
    assert q.required["vegan_options"] is True and q.open_at and q.at == MON and q.max_km == 2.0
    late = parse_discovery("Where can we get a drink after midnight?", MON).query
    assert late.open_until == datetime(2026, 10, 6, 0, 30) and not late.open_at
    local = parse_discovery("Find somewhere local for dinner", MON).query
    assert local.any_of == {"cuisine": ["montenegrin"]}
    jazz = parse_discovery("Is there any live jazz this week?", MON)
    assert jazz.events and "jazz" in jazz.query.tags_preferred


def test_nlu_leaves_property_questions_alone():
    assert parse_discovery("What time is breakfast?", MON) is None
    assert parse_discovery("Can I get extra towels?", MON) is None


def test_nlu_exclusions():
    q = parse_discovery("Find a bar nearby, but not a nightclub", MON).query
    assert "nightclub" in q.exclude_subcategories and "nightclub" not in q.subcategories


# ------------------------------------------------- region ingest / discovery
@pytest.fixture
def region(container):
    with container.session_factory() as s:
        yield s


def test_region_ingest_is_synthetic_and_utc(region):
    place = region.scalar(select(Place).where(Place.slug == "apoteka-demo-centar"))
    assert place.is_synthetic and place.category == "HEALTH" and place.source == "synthetic-dev-dataset"
    ev = region.scalar(select(Event).where(Event.slug == "dj-night-oct"))
    # 2026-10-09 23:00 local (UTC+2 in October) is stored as 21:00 UTC
    assert as_utc(ev.start_at) == datetime(2026, 10, 9, 21, 0, tzinfo=timezone.utc)


def test_region_reingest_is_idempotent(container):
    from app.places.pack import ingest_region, load_region_pack

    pack = load_region_pack(str(ROOT / "data" / "regions" / "zabljak_demo.yaml"))
    with container.session_factory() as s:
        before = s.query(AvailabilitySlot).count()
        ingest_region(s, pack)
        s.commit()
        assert s.query(AvailabilitySlot).count() == before
        assert s.query(Place).filter(Place.slug == "pub-demo").count() == 1


def _q(**kw) -> DiscoveryQuery:
    return DiscoveryQuery(region="zabljak-demo", near=HOTEL, **kw)


def test_discovery_open_now_is_a_hard_filter(region):
    now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    names = [c.place.name for c in discover_places(region, _q(subcategories={"konoba", "restaurant"}, at=MON,
                                                              open_at=True, limit=10), now, TZ)]
    assert "Restoran Demo Ponoć" in names and "Konoba Demo Durmitor" not in names   # Durmitor: closed Mondays


def test_discovery_dietary_and_distance_constraints(region):
    now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    vegan = discover_places(region, _q(subcategories={"konoba", "restaurant", "fast_food"},
                                       required={"vegan_options": True}, limit=10), now, TZ)
    assert vegan and all(c.place.attributes["vegan_options"] for c in vegan)
    near = discover_places(region, _q(subcategories={"konoba", "restaurant"}, max_km=1.0, limit=10), now, TZ)
    assert all(c.distance_km <= 1.0 for c in near)
    assert "Restoran Demo Jezero" not in [c.place.name for c in near]               # 2.3 km away


def test_discovery_soft_preference_never_returns_the_opposite(region):
    now = datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc)
    res = discover_places(region, _q(categories={"NIGHTLIFE"}, preferred={"noise_level": "lively"},
                                     tags_preferred={"lively"}, limit=10), now, TZ)
    names = [c.place.name for c in res]
    assert names[0] == "Bar Demo Koktel" and "Pub Demo" not in names                # pub is recorded as quiet


def test_discovery_explanations_come_from_fields(region):
    now = datetime(2027, 1, 11, 9, 0, tzinfo=timezone.utc)
    res = discover_places(region, _q(subcategories={"nightclub"}, limit=1), now, TZ)
    c = res[0]
    assert "age:21" in c.caveats and "cover:10" in c.caveats
    assert "data:stale" in c.caveats                                                # verified 2026-09-25


def test_events_never_show_past_ones(region):
    now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    evs = discover_events(region, "zabljak-demo", now, now - timedelta(days=90), now + timedelta(days=7))
    slugs = [e.event.slug for e in evs]
    assert "rafting-fest-past" not in slugs and "dj-night-oct" in slugs


# -------------------------------------------------------------- availability
# (inventory holds and the marketplace are covered in tests/test_marketplace.py)
def test_availability_none_when_no_inventory_modelled(region):
    assert find_slot(region, [], datetime(2027, 1, 16, 9, 0, tzinfo=timezone.utc), 2) is None


# ----------------------------------------------------------------- itinerary
def test_itinerary_status_comes_from_source(container, chat):
    reply = chat("I need a taxi tomorrow at 9am from the hotel to the airport, we are 2")
    assert any(a.kind == "quote" for a in reply.actions)
    with container.session_factory() as s:
        stay_id = reply.stay_id
        itinerary.add(s, stay_id=stay_id, kind="FOOD", title="Konoba", status=ItemStatus.SAVED,
                             starts_at=datetime(2026, 10, 6, 20, 0, tzinfo=timezone(timedelta(hours=2))))
        s.commit()
        plan = itinerary.plan(s, stay_id)
        statuses = {e.item.title: e.status for e in plan}
        assert statuses["Konoba"] == "saved"
        assert "offered" in statuses.values()                     # the quote, read from the Quote row
        konoba = next(e for e in plan if e.item.title == "Konoba")
        assert konoba.starts_at == datetime(2026, 10, 6, 18, 0, tzinfo=timezone.utc)   # stored as UTC


def test_frozen_clock_is_used_for_region_freshness():
    clock = FrozenClock(datetime(2027, 1, 11, 9, 0, tzinfo=timezone.utc))
    assert clock.now().year == 2027


def test_staff_can_read_a_guest_plan(client, chat, staff_headers):
    reply = chat("What events are happening this week?")
    chat("save 1")
    rows = client.get(f"/api/staff/stays/{reply.stay_id}/plan", headers=staff_headers).json()
    assert [(r["title"], r["status"]) for r in rows] == [("DJ night", "saved")]
