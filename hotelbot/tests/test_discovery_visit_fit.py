"""Atlas demo correctness patch: a trip window is a FLEXIBLE visit window.

The guest need not spend the whole free window at one place: a candidate
fits when some visit [s, s + visit] lies inside
    [window.start + travel(start -> candidate), window.end - travel(candidate -> end)]
while the place is open throughout the visit. It may open after the window
starts and close before it ends. `window_end` alone keeps its old meaning
("open for the next three hours")."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from app.db.session import create_schema, make_engine
from app.discovery.engine import DiscoveryQuery, run
from app.shared.geo import Point, km_for_walk
from app.trip import journey_bridge
from app.world.records import PlaceRecord, Provenance, RegionRecord
from app.world.sources import FixtureSource
from app.world.store import sync

REGION, TZ = "visit-fit-demo", "Europe/Podgorica"
NOW = datetime(2026, 10, 18, 10, 0, tzinfo=timezone.utc)
START, END = datetime(2026, 10, 18, 16, 30), datetime(2026, 10, 18, 19, 30)   # a Sunday
HOTEL = Point(43.4247, 18.7712)
KM_PER_DEG = 111.195


def _north(p: Point, minutes: int) -> Point:
    return Point(p.lat + km_for_walk(minutes) / KM_PER_DEG, p.lon)


RESTAURANT = _north(HOTEL, 25)            # hotel -> museum 10 min, museum -> restaurant 15 min
ON_THE_WAY = _north(HOTEL, 10)


def _hours(open_: str, close: str, **extra) -> dict:
    return {"weekly": {d: [[open_, close]] for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}, **extra}


PLACES = {   # slug -> (point, hours, typical visit)
    "museum-17-1830": (ON_THE_WAY, _hours("17:00", "18:30"), 60),
    "museum-18-1830": (ON_THE_WAY, _hours("18:00", "18:30"), 60),
    "museum-closed-for-works": (ON_THE_WAY, _hours("09:00", "21:00", closed=[
        {"from": "2026-10-01", "to": "2026-10-31", "reason": "renovation"}]), 60),
    # 60 min south of the hotel, 85 min from the restaurant: there-and-back fits (17:30-18:30), onward
    # it does not (arrive 17:30, must leave by 18:05)
    "museum-wrong-way": (Point(HOTEL.lat - km_for_walk(60) / KM_PER_DEG, HOTEL.lon), _hours("17:00", "18:30"), 60),
}


@pytest.fixture
def s():  # noqa: ANN201
    eng = make_engine("sqlite://")
    create_schema(eng)
    with sessionmaker(eng)() as session:
        records = []
        for slug, (p, hours, visit) in {**PLACES, "hotel": (HOTEL, {}, None),
                                        "restaurant": (RESTAURANT, _hours("12:00", "23:00"), None)}.items():
            prov = Provenance(source_id="fixture:fit", source_type="fixture", source_record_id=slug, label="fit",
                              last_verified_at=datetime(2026, 10, 10, tzinfo=timezone.utc), confidence=1.0,
                              provider_owned=False, is_synthetic=True)
            records.append(PlaceRecord(slug=slug, name=slug, provenance=prov, latitude=p.lat, longitude=p.lon,
                                       timezone=TZ, hours=hours,
                                       subcategory="museum" if slug.startswith("museum") else "konoba",
                                       attributes={"typical_visit_minutes": visit} if visit else {}))
        sync(session, FixtureSource(RegionRecord(REGION, "Fit demo", TZ, (HOTEL.lat, HOTEL.lon)), records, [],
                                    source_id="fixture:fit"), now=datetime(2026, 10, 10, tzinfo=timezone.utc))
        yield session
    eng.dispose()


def _flex(s, **kw):  # noqa: ANN001, ANN202
    q = DiscoveryQuery(region=REGION, categories={"CULTURE"}, at=START, window_end=END, visit_minutes=60,
                       fit_within_window=True, near=HOTEL, end_near=RESTAURANT, limit=10, **kw)
    res = run(s, q, NOW, TZ)
    return {c.place.slug: c for c in res.candidates}, res


def test_a_museum_open_17_to_1830_fits_a_16_30_to_19_30_window(s):
    found, _ = _flex(s)
    c = found["museum-17-1830"]
    assert c.visit_slot == (datetime(2026, 10, 18, 17, 0), datetime(2026, 10, 18, 18, 0))   # opens after the start


def test_a_museum_open_18_to_1830_does_not_fit_a_60_minute_visit(s):
    found, res = _flex(s)
    assert "museum-18-1830" not in found and res.rejected["does_not_fit_window"] >= 1


def test_it_may_close_before_the_window_ends(s):
    found, _ = _flex(s)
    assert found["museum-17-1830"].visit_slot[1] <= datetime(2026, 10, 18, 18, 30) < END


def test_both_travel_legs_count(s):
    found, _ = _flex(s)
    assert "museum-wrong-way" not in found
    q = DiscoveryQuery(region=REGION, categories={"CULTURE"}, at=START, window_end=END, visit_minutes=60,
                       fit_within_window=True, near=HOTEL, limit=10)
    assert "museum-wrong-way" in {c.place.slug for c in run(s, q, NOW, TZ).candidates}


def test_a_temporarily_closed_place_has_no_slot(s):
    found, _ = _flex(s)
    assert "museum-closed-for-works" not in found


def test_window_end_alone_keeps_its_old_meaning(s):
    q = DiscoveryQuery(region=REGION, categories={"CULTURE"}, at=START, window_end=END, open_at=True,
                       near=HOTEL, limit=10)
    res = run(s, q, NOW, TZ)
    assert res.candidates == [] and all(c.visit_slot is None for c in res.candidates)
    assert q.fit_within_window is False


def test_the_bridge_asks_for_a_flexible_visit(s):
    from app.db.models import Place
    from sqlalchemy import select

    links = {"hotel|x": "hotel", "restaurant|x": "restaurant"}
    window = {"start": "2026-10-18T16:30", "end": "2026-10-18T19:30", "minutes": 180, "timezone": TZ,
              "start_anchor": {"identity": "hotel|x", "role": "check_in", "place": "h"},
              "end_anchor": {"identity": "restaurant|x", "role": "reservation", "place": "r"}}
    res = journey_bridge.suggest(s, window, journey_bridge.world_locator(s, links, region=REGION), region=REGION,
                                 now_utc=NOW, limit=10)
    assert res.query.fit_within_window and not res.query.open_at
    assert [c.place.slug for c in res.candidates] == ["museum-17-1830"]
    assert s.scalar(select(Place).where(Place.slug == "hotel")) is not None
