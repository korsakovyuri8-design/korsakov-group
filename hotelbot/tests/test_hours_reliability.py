"""Iteration 6.1: a place with NO opening-hours schedule must not look more
reliable in ranking than one with a real, even old, schedule
(docs/ITERATION_6_1.md §4). In the rank only, absent hours weigh like STALE
(3); the data keeps saying UNKNOWN - freshness, open state and wording are
unchanged."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.discovery import engine
from app.discovery.engine import DiscoveryQuery, run
from app.discovery.render import candidate_line
from app.places.freshness import Freshness, place_facts
from app.places.hours import OpenState
from tests.test_ranking_time_intent import ANCHOR, NOW, OPEN_ALL_DAY, TZ, _place


def _hours_place(name: str, km: float, verified_days_ago: int | None, sub: str = "restaurant"):  # noqa: ANN202
    p = _place(name, km, sub, OPEN_ALL_DAY if verified_days_ago is not None else None, "FOOD")
    if verified_days_ago is not None:
        p.verification = {"hours": (NOW - timedelta(days=verified_days_ago)).isoformat()}
    return p


def _run(monkeypatch, places, **kw):  # noqa: ANN001, ANN202
    monkeypatch.setattr(engine, "retrieve", lambda session, q: list(places))
    q = DiscoveryQuery(region="r", near=ANCHOR, limit=10, subcategories={p.subcategory for p in places}, **kw)
    return run(None, q, NOW, TZ).candidates


def _reliability(monkeypatch, place) -> int:  # noqa: ANN001
    return _run(monkeypatch, [place])[0].components["reliability"]


def test_absent_schedule_ranks_like_stale_not_fresh(monkeypatch):
    assert _reliability(monkeypatch, _hours_place("No hours, fresh record", 0.1, None)) == 3


def test_real_schedules_keep_their_freshness_rank(monkeypatch):
    assert _reliability(monkeypatch, _hours_place("Fresh", 0.1, 5)) == 0
    assert _reliability(monkeypatch, _hours_place("Aging", 0.1, 60)) == 1
    assert _reliability(monkeypatch, _hours_place("Stale", 0.1, 186)) == 3


def test_absent_hours_data_and_wording_are_untouched():
    p = _hours_place("No hours", 0.1, None)
    assert place_facts(p, NOW, ("hours",))["hours"].state == Freshness.FRESH      # the record itself, untouched
    from app.discovery.engine import Candidate
    from app.places.hours import HoursStatus

    line = candidate_line(1, Candidate(p, 0.1, HoursStatus(OpenState.UNKNOWN), None, [], []), "en")
    assert "no opening-hours data" in line and "stale" not in line and "may have changed" not in line


def test_same_distance_bucket_real_hours_beat_no_hours(monkeypatch):
    # Konoba Galeb (0.08 km, no hours) vs Astoria (0.10 km, open, hours 186 days old)
    order = [c.place.name for c in _run(monkeypatch, [_hours_place("Galeb", 0.08, None),
                                                      _hours_place("Astoria", 0.10, 186)])]
    assert order == ["Astoria", "Galeb"]


def test_distance_still_beats_reliability(monkeypatch):
    near = _place("ATM 40 m", 0.04, "atm", None, "FINANCIAL_SERVICE")
    far = _place("ATM 780 m", 0.78, "atm", OPEN_ALL_DAY, "FINANCIAL_SERVICE")
    order = [c.place.name for c in _run(monkeypatch, [far, near], essential=True)]
    assert order[0] == "ATM 40 m"


def test_open_state_and_hard_filter_are_unchanged(monkeypatch):
    places = [_hours_place("No hours", 0.05, None), _hours_place("Open", 3.0, 186)]
    res = _run(monkeypatch, places, open_at=True, at=datetime(2026, 10, 8, 21, 0))
    assert [c.place.name for c in res] == ["Open"]                      # unknown padding rule as before
    assert _run(monkeypatch, [_hours_place("No hours", 0.05, None)])[0].status.state == OpenState.UNKNOWN
