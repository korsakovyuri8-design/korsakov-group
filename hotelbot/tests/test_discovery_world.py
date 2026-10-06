"""Iteration 4: the Local World / Discovery Engine.

World sources and provenance, freshness, food-service state, hard vs soft
constraints, the pipeline's rejections and ranking order (commerce is only
a tie-breaker), preferences, references to results, free windows."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.models import Event, Guest, ItemStatus, Place, TravelerPreference
from app.discovery.engine import DiscoveryQuery, run
from app.discovery.nlu import parse_discovery, understand
from app.places.freshness import Freshness, classify
from app.places.hours import OpenState, food_state
from app.shared.geo import Point
from app.trip import itinerary, preferences, schedule, selection
from app.world.records import EventRecord, PlaceRecord, Provenance, RegionRecord
from app.world.sources import FixtureSource, SyntheticRegionSource
from app.world.store import sync
from tests.conftest import ROOT

TZ = "Europe/Podgorica"
HOTEL = Point(43.1556, 19.1210)
MON = datetime(2026, 10, 5, 14, 0)
MON_UTC = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
FRI_UTC = datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc)      # Fri 20:00 local


@pytest.fixture
def s(container):
    with container.session_factory() as session:
        yield session


def _q(**kw) -> DiscoveryQuery:
    return DiscoveryQuery(region="zabljak-demo", near=HOTEL, **kw)


# =============================================================== world source
def _prov(rid: str, verified: datetime | None = None) -> Provenance:
    return Provenance(source_id="fixture:test", source_type="fixture", source_record_id=rid, label="unit test",
                      last_verified_at=verified, confidence=1.0, provider_owned=False, is_synthetic=True)


def _fixture(places, events=()) -> FixtureSource:
    return FixtureSource(RegionRecord("test-region", "Test", TZ, (43.0, 19.0)), list(places), list(events),
                         source_id="fixture:test")


def _place(slug: str, sub: str = "cafe") -> PlaceRecord:
    return PlaceRecord(slug=slug, name=slug.title(), subcategory=sub, category=None, provenance=_prov(slug),
                       latitude=43.0, longitude=19.0)


def test_synthetic_source_normalises_with_provenance(s):
    src = SyntheticRegionSource.from_path(ROOT / "data" / "regions" / "zabljak_demo.yaml")
    assert src.source_id == "synthetic:zabljak-demo" and src.source_type == "synthetic"
    place = s.scalar(select(Place).where(Place.slug == "bistro-demo-savin"))
    assert (place.source_id, place.source_type, place.source_record_id) == \
        ("synthetic:zabljak-demo", "synthetic", "bistro-demo-savin")
    assert place.price_range == 2 and place.verification.get("hours") is not None
    concert = s.scalar(select(Event).where(Event.slug == "sunday-concert-jan"))
    assert concert.source_record_id == "sunday-concert-jan" and concert.ticket_required


def test_fixture_source_sync_deactivates_never_deletes(s):
    report = sync(s, _fixture([_place("a"), _place("b")]))
    assert report["places"] == 2
    sync(s, _fixture([_place("a")]))
    rows = {p.slug: p for p in s.scalars(select(Place).where(Place.region == "test-region"))}
    assert set(rows) == {"a", "b"} and rows["a"].active and not rows["b"].active
    assert rows["a"].source_id == "fixture:test" and rows["a"].category == "FOOD"


def test_unknown_event_category_becomes_other(s):
    ev = EventRecord(slug="x", title={"en": "X"}, category="rodeo", start=datetime(2027, 1, 1, tzinfo=timezone.utc),
                     provenance=_prov("x"))
    sync(s, _fixture([], [ev]))
    assert s.scalar(select(Event).where(Event.slug == "x", Event.region == "test-region")).category == "other"


def test_ticketing_unknown_is_not_free(s):
    jazz = s.scalar(select(Event).where(Event.slug == "jazz-friday-jan"))
    walk = s.scalar(select(Event).where(Event.slug == "lantern-walk-jan"))
    assert jazz.attributes.get("ticketing") == "unknown"            # the source says nothing
    assert walk.attributes.get("ticketing") is None and not walk.ticket_required


# ================================================================== freshness
def test_freshness_classes_per_fact():
    now = datetime(2027, 1, 11, tzinfo=timezone.utc)
    assert classify("hours", now - timedelta(days=10), now).state == Freshness.FRESH
    assert classify("hours", now - timedelta(days=60), now).state == Freshness.AGING
    assert classify("hours", now - timedelta(days=108), now).state == Freshness.STALE
    assert classify("closure", now - timedelta(days=20), now).state == Freshness.AGING   # closures age faster
    assert classify("static", now - timedelta(days=300), now).state == Freshness.FRESH
    assert classify("hours", None, now).state == Freshness.UNKNOWN
    assert classify("hours", now - timedelta(days=1), now, confidence=0.5).state == Freshness.UNKNOWN
    assert classify("hours", now + timedelta(days=5), now).age_days == 0                 # never negative


# ================================================================= food state
class _P:
    def __init__(self, hours, attributes=None, category="FOOD"):
        self.hours, self.attributes, self.category = hours, attributes or {}, category


WEEK = {d: [["12:00", "23:00"]] for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}


def test_food_state_is_never_inferred_from_venue_hours():
    at = datetime(2026, 10, 5, 22, 30)
    assert food_state(_P({"weekly": WEEK}), at).state == OpenState.UNKNOWN          # venue open, kitchen unknown
    kitchen = {d: [["12:00", "22:00"]] for d in WEEK}
    assert food_state(_P({"weekly": WEEK, "kitchen": kitchen}), at).state != OpenState.OPEN
    follows = _P({"weekly": WEEK}, {"kitchen_follows_opening_hours": True})
    assert food_state(follows, at).state == OpenState.OPEN                           # explicit data says so
    assert food_state(_P({"weekly": WEEK}, {"serves_food": False}), at).state == OpenState.CLOSED
    closed_venue = _P({"weekly": {"mon": []}, "kitchen": kitchen})
    assert food_state(closed_venue, at).state == OpenState.CLOSED                    # venue closed wins


# ========================================================= hard vs soft (NLU)
@pytest.mark.parametrize("text,hard,soft", [
    ("Find a vegan restaurant", {"vegan_options": True}, {}),
    ("Find a restaurant, vegan if possible", {}, {"vegan_options": True}),
    ("We're six and one person is vegan, find a restaurant", {"vegan_options": True}, {}),
    ("A wheelchair accessible restaurant please", {"wheelchair_access": True}, {}),
])
def test_nlu_hard_vs_soft(text, hard, soft):
    q = parse_discovery(text, MON, require_cue=False).query
    assert {k: v for k, v in q.required.items() if k in hard or k in soft} == hard
    assert {k: v for k, v in q.preferred.items() if k in hard or k in soft} == soft


def test_nlu_constraints():
    q = parse_discovery("We're with our 16-year-old, somewhere lively for drinks but not a club", MON).query
    assert q.min_age == 16 and "nightclub" in q.exclude_subcategories and q.preferred["noise_level"] == "lively"
    assert parse_discovery("Find somewhere cheap to eat", MON).query.price_max == 1
    assert parse_discovery("Find a cafe within 500 m", MON).query.max_km == pytest.approx(0.5)
    near = parse_discovery("Find a cafe near the Black Lake", MON)
    assert near.anchor_text == "black lake" and near.query.subcategories == {"cafe"}
    assert parse_discovery("We're six and one person is vegan, find a restaurant", MON).query.party_size == 6
    assert parse_discovery("Do you have a sauna?", MON) is None          # a property question
    assert parse_discovery("Is there parking at the hotel?", MON) is None


def test_nlu_meal_without_time_is_that_meal():
    q = parse_discovery("Find a restaurant for dinner", MON).query
    assert q.at == datetime(2026, 10, 5, 20, 0) and q.serving_at
    assert parse_discovery("Find a restaurant for lunch", datetime(2026, 10, 5, 17, 0)).query.at is None


def test_understand_splits_compound_needs():
    reqs = understand("Find somewhere local for dinner at 20:00 and somewhere lively for drinks afterwards", MON)
    assert [r.label for r in reqs] == ["food", "drinks"]
    assert reqs[1].query.at == reqs[0].query.at + timedelta(hours=2)
    one = understand("Найдите местный ресторан, где ещё будет работать кухня, когда мы приедем", MON)
    assert len(one) == 1                                                 # a relative clause is not a second need


# ================================================================ the pipeline
def test_pipeline_records_why_candidates_were_removed(s):
    res = run(s, _q(subcategories={"pub", "cocktail_bar", "wine_bar"}, at=MON, open_at=True), MON_UTC, TZ)
    assert res.candidates == [] and res.rejected["closed_at_time"] == 3


def test_pipeline_hard_constraints_never_relaxed(s):
    res = run(s, _q(categories={"NIGHTLIFE"}, min_age=16, limit=10), FRI_UTC, TZ)
    names = {c.place.name for c in res.candidates}
    assert not names & {"Bar Demo Koktel", "Demo Rooftop Lounge", "Club Demo"}
    assert res.rejected["age_restricted"] == 3
    big = run(s, _q(subcategories={"restaurant"}, required={"vegan_options": True}, party_size=6, limit=10),
              MON_UTC, TZ)
    assert "Demo Green Kitchen" not in {c.place.name for c in big.candidates}
    assert big.rejected["group_too_large"] == 1


def test_unknown_state_does_not_pad_known_open_results(s):
    at = datetime(2026, 10, 5, 20, 0)
    res = run(s, _q(subcategories={"konoba"}, any_of={"cuisine": ["montenegrin"]}, at=at, serving_at=True),
              MON_UTC, TZ)
    assert [c.place.slug for c in res.candidates] == ["restoran-demo-ponoc"]
    assert res.rejected["state_unknown"] == 1                              # Grill Demo Bobotov
    late = run(s, _q(subcategories={"konoba"}, at=datetime(2026, 10, 5, 23, 30), serving_at=True), MON_UTC, TZ)
    assert [c.place.slug for c in late.candidates] == ["grill-demo-bobotov"]
    assert "hours_unknown" in late.candidates[0].caveats and "kitchen_unknown" in late.candidates[0].caveats


def test_commerce_is_only_a_tie_breaker(s):
    """A commercial signal decides only between otherwise equal candidates;
    it never lifts a closed, farther, less relevant or non-matching place."""
    q = dict(subcategories={"pharmacy"}, essential=True, limit=5)
    plain = [c.place.slug for c in run(s, _q(**q), MON_UTC, TZ).candidates]

    def favour_sjever(place):
        return (0, 99.0) if place.slug == "apoteka-demo-sjever" else (1, 0.0)

    assert [c.place.slug for c in run(s, _q(**q), MON_UTC, TZ, signals=favour_sjever).candidates] == plain

    def favour_konoba(place):
        return (0, 50.0) if place.slug == "konoba-demo-durmitor" else (1, 0.0)

    vegan = run(s, _q(subcategories={"konoba", "restaurant"}, required={"vegan_options": True}), MON_UTC, TZ,
                signals=favour_konoba)
    assert "konoba-demo-durmitor" not in [c.place.slug for c in vegan.candidates]   # hard constraint holds
    open_now = run(s, _q(subcategories={"konoba"}, at=MON, open_at=True), MON_UTC, TZ, signals=favour_konoba)
    assert "konoba-demo-durmitor" not in [c.place.slug for c in open_now.candidates]  # closed on Mondays

    # Two otherwise identical candidates: only now does the signal decide.
    sync(s, _fixture([_place("twin-a"), _place("twin-b")]))
    twin_q = DiscoveryQuery(region="test-region", subcategories={"cafe"}, near=Point(43.0, 19.0))
    assert [c.place.slug for c in run(s, twin_q, MON_UTC, TZ).candidates] == ["twin-a", "twin-b"]   # by name
    favoured = run(s, twin_q, MON_UTC, TZ, signals=lambda p: (0, 0.0) if p.slug == "twin-b" else (1, 0.0))
    assert [c.place.slug for c in favoured.candidates] == ["twin-b", "twin-a"]
    paid = run(s, twin_q, MON_UTC, TZ, signals=lambda p: (1, 15.0) if p.slug == "twin-b" else (1, 0.0))
    assert [c.place.slug for c in paid.candidates] == ["twin-b", "twin-a"]


def test_bridge_signals_follow_explicit_relations_only(s):
    from app.marketplace import bridge

    signals = bridge.ranking_signals(s, property_id=None)
    ponoc = s.scalar(select(Place).where(Place.slug == "restoran-demo-ponoc"))
    pharmacy = s.scalar(select(Place).where(Place.slug == "apoteka-demo-centar"))
    assert signals(pharmacy) == (1, 0.0)                    # not transactable: no commercial signal at all
    assert signals(ponoc)[0] == 1                           # no explicit property relation -> not preferred


def test_geo_anchor_and_radius(s):
    lake = s.scalar(select(Place).where(Place.slug == "crno-jezero-demo"))
    near_lake = run(s, DiscoveryQuery(region="zabljak-demo", subcategories={"cafe"},
                                      near=Point(lake.latitude, lake.longitude)), MON_UTC, TZ)
    assert near_lake.candidates[0].place.slug == "cafe-demo-jezero"
    radius = run(s, _q(subcategories={"cafe"}, max_km=0.5, limit=10), MON_UTC, TZ)
    assert all(c.distance_km <= 0.5 for c in radius.candidates)
    assert all(c.walk_minutes is not None for c in radius.candidates)   # computed, never asked of a model


# ================================================================ preferences
def test_preferences_only_from_general_statements():
    assert [p.key for p in preferences.extract("I generally prefer vegetarian places")] == ["vegetarian_options"]
    assert preferences.extract("Find vegan food tonight") == []
    assert preferences.extract("One of us is vegan") == []
    assert preferences.extract("vegetarian if possible") == []
    assert preferences.extract("We usually avoid loud bars")[0].value == "quiet"


def test_preferences_persist_per_guest(s):
    guest = Guest(channel="demo", external_id="pref-guest")
    s.add(guest)
    s.flush()
    preferences.save(s, guest.id, preferences.extract("I'm vegan"), None)
    preferences.save(s, guest.id, preferences.extract("I'm vegan"), None)     # upsert, not duplicate
    assert preferences.load(s, guest.id) == {"vegan_options": True}
    assert s.query(TravelerPreference).filter_by(guest_id=guest.id).count() == 1


# =========================================================== references / plan
ENTRIES = [
    {"kind": "place", "category": "FOOD", "subcategory": "konoba", "name": "Restoran Demo Ponoć"},
    {"kind": "place", "category": "FOOD", "subcategory": "cafe", "name": "Demo Ski Café Savin"},
    {"kind": "place", "category": "NIGHTLIFE", "subcategory": "cocktail_bar", "name": "Bar Demo Koktel"},
    {"kind": "place", "category": "NIGHTLIFE", "subcategory": "rooftop", "name": "Demo Rooftop Lounge"},
    {"kind": "event", "event_category": "concert", "name": "Sunday chamber concert"},
]


def test_compound_commands_resolve_independently():
    cmds = selection.resolve_all(selection.parse_commands(
        "Book the first restaurant, save the second bar and add the concert to Sunday", date(2027, 1, 11)),
        ENTRIES).commands
    assert [(c.verb, c.entry["name"]) for c in cmds] == [
        ("book", "Restoran Demo Ponoć"), ("save", "Demo Rooftop Lounge"), ("plan", "Sunday chamber concert")]
    assert cmds[2].day == date(2027, 1, 17)


def test_references_never_guess():
    ref = selection.parse_commands("save the bar", date(2027, 1, 11))[0]
    assert selection.resolve(ref.ref, ENTRIES) == (None, "ambiguous")
    assert selection.resolve(selection.parse_ref("the fifth bar"), ENTRIES) == (None, "not_found")
    assert selection.parse_commands("What did I save?", date(2027, 1, 11)) == []
    assert selection.parse_commands("book the transfer and the skis", date(2027, 1, 11))[0].ref.kind != "restaurant"


def test_free_window_from_plan(s, container):
    from app.db.models import Property, Stay

    prop = s.scalar(select(Property))
    guest = Guest(channel="demo", external_id="window-guest")
    s.add(guest)
    s.flush()
    stay = Stay(guest_id=guest.id, property_id=prop.id, source_channel="demo")
    s.add(stay)
    s.flush()
    dinner = datetime(2026, 10, 5, 19, 0, tzinfo=timezone.utc) - timedelta(hours=2)    # 19:00 local
    itinerary.add(s, stay_id=stay.id, kind="FOOD", title="Dinner at Restoran Demo Ponoć",
                  status=ItemStatus.PLANNED, starts_at=dinner)
    w = schedule.free_window("two free hours before dinner", itinerary.plan(s, stay.id), MON, TZ)
    assert (w.start, w.end, w.from_plan) == (datetime(2026, 10, 5, 17, 0), datetime(2026, 10, 5, 19, 0), True)
    none = schedule.free_window("three free hours", [], MON, TZ)
    assert none.end - none.start == timedelta(hours=3) and not none.from_plan
    assert " ".join(schedule.strip_window_words("what can we do before dinner?").split()) == "what can we do ?"
