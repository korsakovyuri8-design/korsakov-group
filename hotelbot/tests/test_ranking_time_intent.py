"""Iteration 6.1: availability dominates ranking only when the guest expressed
a time / open-state intent (docs/ITERATION_6_1.md §3). Without one, UNKNOWN
hours are not treated almost like CLOSED and do not outrank geography; UNKNOWN
is never turned into OPEN. Found on the frozen Kotor/Budva world, where only
~20 % of places carry hours."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.discovery import engine
from app.discovery.engine import DiscoveryQuery, run
from app.places.hours import OpenState
from app.shared.geo import Point

TZ = "Europe/Podgorica"
NOW = datetime(2026, 10, 8, 9, 17, tzinfo=timezone.utc)          # Thu 11:17 local
ANCHOR = Point(42.4247, 18.7712)
OPEN_ALL_DAY = {"weekly": {d: [["08:00", "22:00"]] for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}}
CLOSED_TODAY = {"weekly": {d: ([] if d == "thu" else [["08:00", "22:00"]])
                           for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}}
KM = 1 / 111.2                                                     # ~1 km of latitude


def _place(name: str, km: float, sub: str, hours: dict | None = None, category: str = "CULTURE",
           tags: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(name=name, slug=name.lower(), subcategory=sub, category=category, hours=hours,
                           latitude=ANCHOR.lat + km * KM, longitude=ANCHOR.lon, timezone=None, attributes={},
                           tags={"tags": tags or []}, resolution={}, verification={}, last_verified_at=NOW,
                           confidence=0.7, price_range=None, quality={})


def _order(monkeypatch, places, **kw) -> list[str]:
    monkeypatch.setattr(engine, "retrieve", lambda session, q: list(places))
    q = DiscoveryQuery(region="r", near=ANCHOR, limit=10, **kw)
    return [c.place.name for c in run(None, q, NOW, TZ).candidates]


# ------------------------------------------------- no time intent: geography first
def test_nearest_pharmacy_with_unknown_hours_beats_a_far_open_one(monkeypatch):
    places = [_place("Far open", 6.5, "pharmacy", OPEN_ALL_DAY, "HEALTH"),
              _place("Near unknown", 0.08, "pharmacy", None, "HEALTH")]
    assert _order(monkeypatch, places, subcategories={"pharmacy"}, essential=True)[0] == "Near unknown"


def test_close_atms_with_unknown_hours_beat_open_ones_farther_away(monkeypatch):
    places = [_place("Open 360m", 0.36, "atm", OPEN_ALL_DAY, "FINANCIAL_SERVICE"),
              _place("Open 780m", 0.78, "atm", OPEN_ALL_DAY, "FINANCIAL_SERVICE"),
              _place("Unknown 40m", 0.04, "atm", None, "FINANCIAL_SERVICE"),
              _place("Unknown 90m", 0.09, "atm", None, "FINANCIAL_SERVICE")]
    order = _order(monkeypatch, places, subcategories={"atm"}, essential=True, max_km=2.0)
    assert order[:2] == ["Unknown 40m", "Unknown 90m"]


def test_close_museum_beats_far_open_museums(monkeypatch):
    places = [_place("Open 6.5km", 6.5, "museum", OPEN_ALL_DAY), _place("Open 17km", 17.2, "museum", OPEN_ALL_DAY),
              _place("Unknown 40m", 0.04, "museum", None)]
    order = _order(monkeypatch, places, subcategories={"museum"}, specific={"museum"})
    assert order[0] == "Unknown 40m"


def test_close_beach_beats_a_beach_club_2km_away(monkeypatch):
    places = [_place("Beach club", 2.0, "beach", OPEN_ALL_DAY, "NATURE"),
              _place("City beach", 0.1, "beach", None, "NATURE")]
    assert _order(monkeypatch, places, subcategories={"beach"}, specific={"beach"})[0] == "City beach"


def test_unknown_is_not_turned_into_open(monkeypatch):
    places = [_place("Unknown", 0.1, "museum", None)]
    monkeypatch.setattr(engine, "retrieve", lambda session, q: list(places))
    c = run(None, DiscoveryQuery(region="r", near=ANCHOR, subcategories={"museum"}), NOW, TZ).candidates[0]
    assert c.status.state == OpenState.UNKNOWN


def test_availability_still_breaks_ties_at_the_same_distance(monkeypatch):
    places = [_place("Unknown", 0.10, "museum", None), _place("Open", 0.10, "museum", OPEN_ALL_DAY),
              _place("Closed", 0.10, "museum", CLOSED_TODAY)]
    assert _order(monkeypatch, places, subcategories={"museum"}) == ["Open", "Unknown", "Closed"]


def test_explicit_relevance_still_beats_a_small_distance_difference(monkeypatch):
    places = [_place("Plain bar", 0.3, "bar", OPEN_ALL_DAY, "NIGHTLIFE"),
              _place("Jazz bar", 0.9, "bar", None, "NIGHTLIFE", tags=["jazz"])]
    order = _order(monkeypatch, places, subcategories={"bar"}, relevance_tags={"jazz"})
    assert order[0] == "Jazz bar"


# ------------------------------------------------- time intent: availability first (unchanged)
@pytest.mark.parametrize("kw", [{"open_at": True, "at": datetime(2026, 10, 8, 11, 17)},
                                {"open_at": True, "at": datetime(2026, 10, 8, 21, 0)}])
def test_open_now_or_tonight_keeps_open_above_unknown(monkeypatch, kw):
    places = [_place("Near unknown", 0.05, "museum", None), _place("Far open", 3.0, "museum", OPEN_ALL_DAY)]
    assert _order(monkeypatch, places, subcategories={"museum"}, **kw)[0] == "Far open"


def test_time_query_still_drops_unknown_padding_when_known_open_exist(monkeypatch):
    places = [_place("Near unknown", 0.05, "restaurant", None, "FOOD"),
              _place("Far open", 3.0, "restaurant", OPEN_ALL_DAY, "FOOD")]
    monkeypatch.setattr(engine, "retrieve", lambda session, q: list(places))
    q = DiscoveryQuery(region="r", near=ANCHOR, subcategories={"restaurant"}, open_at=True,
                       at=datetime(2026, 10, 8, 21, 0))
    res = run(None, q, NOW, TZ)
    assert [c.place.name for c in res.candidates] == ["Far open"] and res.rejected["state_unknown"] == 1


def test_essential_open_now_keeps_availability_first(monkeypatch):
    places = [_place("Near unknown", 0.08, "pharmacy", None, "HEALTH"),
              _place("Far open", 6.5, "pharmacy", OPEN_ALL_DAY, "HEALTH")]
    order = _order(monkeypatch, places, subcategories={"pharmacy"}, essential=True, open_at=True,
                   at=datetime(2026, 10, 8, 11, 17))
    assert order[0] == "Far open"


# ------------------------------------------------- essential, no time: "definitely unavailable now" goes down
OPENS_LATER = {"weekly": {d: [["14:00", "22:00"]] for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}}


def _essential(monkeypatch, places) -> list[str]:
    return _order(monkeypatch, places, subcategories={"pharmacy"}, essential=True)


def test_essential_near_closed_ranks_below_farther_open(monkeypatch):
    places = [_place("Near closed", 0.05, "pharmacy", CLOSED_TODAY, "HEALTH"),
              _place("Farther open", 0.15, "pharmacy", OPEN_ALL_DAY, "HEALTH")]
    assert _essential(monkeypatch, places) == ["Farther open", "Near closed"]


def test_essential_near_open_later_ranks_below_farther_open(monkeypatch):
    places = [_place("Near opens later", 0.05, "pharmacy", OPENS_LATER, "HEALTH"),
              _place("Farther open", 0.15, "pharmacy", OPEN_ALL_DAY, "HEALTH")]
    assert _essential(monkeypatch, places) == ["Farther open", "Near opens later"]


def test_essential_near_closed_ranks_below_farther_unknown(monkeypatch):
    places = [_place("Near closed", 0.05, "pharmacy", CLOSED_TODAY, "HEALTH"),
              _place("Farther unknown", 0.15, "pharmacy", None, "HEALTH")]
    assert _essential(monkeypatch, places) == ["Farther unknown", "Near closed"]


def test_essential_available_group_is_distance_first(monkeypatch):
    places = [_place("Open 400m", 0.4, "pharmacy", OPEN_ALL_DAY, "HEALTH"),
              _place("Unknown 80m", 0.08, "pharmacy", None, "HEALTH"),
              _place("Closed 50m", 0.05, "pharmacy", CLOSED_TODAY, "HEALTH")]
    assert _essential(monkeypatch, places) == ["Unknown 80m", "Open 400m", "Closed 50m"]


def test_normal_no_time_closed_may_still_beat_a_very_far_open(monkeypatch):
    places = [_place("Far open", 17.0, "museum", OPEN_ALL_DAY), _place("Near closed", 0.09, "museum", CLOSED_TODAY)]
    assert _order(monkeypatch, places, subcategories={"museum"})[0] == "Near closed"
