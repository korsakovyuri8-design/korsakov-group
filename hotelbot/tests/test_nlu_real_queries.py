"""Iteration 6.1: discovery NLU defects found on the frozen Kotor/Budva world
(docs/ITERATION_6_1.md §3), each fixed on its own: "interesting",
"in <town>", "late"."""

from __future__ import annotations

from datetime import datetime

from app.discovery.nlu import parse_discovery

LOCAL = datetime(2026, 10, 8, 11, 17)
SIGHTS = {"ATTRACTION", "CULTURE", "NATURE"}


def _p(text: str, require_cue: bool = True):
    return parse_discovery(text, LOCAL, require_cue=require_cue)


# ------------------------------------------------------------ "interesting"
def test_something_interesting_near_the_old_town_is_a_sightseeing_request():
    for require_cue in (True, False):
        req = _p("Something interesting near the Old Town", require_cue)
        assert req is not None and req.label == "sights" and req.query.categories == SIGHTS
        assert req.anchor_text == "old town"                    # near the anchor, not the whole region
        assert not req.query.subcategories and not req.query.open_at


def test_interesting_with_a_cue_in_other_languages():
    assert _p("Anything interesting to see in town?").query.categories == SIGHTS
    assert _p("Nešto zanimljivo u blizini?").query.categories == SIGHTS
    assert _p("Что-нибудь интересное рядом?").query.categories == SIGHTS


def test_interesting_alone_is_not_a_discovery_request():
    for text in ("That's interesting", "Interesting, thanks!", "What an interesting idea"):
        assert _p(text) is None and _p(text, require_cue=False) is None, text


def test_interesting_happening_stays_an_events_request():
    req = _p("Anything interesting happening tonight?")
    assert req.events and not req.query.categories


# ------------------------------------------------------------ "in <town>"
import pytest  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db.session import create_schema, make_engine  # noqa: E402
from app.discovery import engine  # noqa: E402
from app.discovery.engine import run  # noqa: E402
from app.shared.geo import Point  # noqa: E402
from app.world import coverage  # noqa: E402
from tests.test_ranking_time_intent import NOW, TZ, _place  # noqa: E402

KOTOR_GATE = Point(42.4247, 18.7712)


def test_in_town_is_extracted_as_a_locality():
    assert _p("A museum in Kotor").query.locality == "kotor"
    assert _p("Find a beach in Budva").query.locality == "budva"
    assert _p("Where can I get cocktails in Budva?").query.locality == "budva"
    assert _p("Find a restaurant in Kotor open tonight").query.locality == "kotor"


def test_no_locality_from_ordinary_phrases():
    for text in ("Where can we eat in the evening?", "Something interesting near the Old Town",
                 "Find a restaurant in town", "Nearest pharmacy"):
        req = _p(text, require_cue=False)
        assert req is None or req.query.locality is None, text


@pytest.fixture
def areas():
    eng = make_engine("sqlite://")
    create_schema(eng)
    with sessionmaker(eng)() as s:     # the pilot's two localities (pilot.yaml scope boxes)
        coverage.upsert_area(s, code="kb:kotor", kind="locality", name="Kotor", bbox=(42.400, 18.690, 42.470, 18.790))
        coverage.upsert_area(s, code="kb:budva", kind="locality", name="Budva", bbox=(42.250, 18.810, 42.310, 18.900))
        yield s
    eng.dispose()


def _museums():
    from tests.test_ranking_time_intent import OPEN_ALL_DAY

    return [_place("Maritimum", 0.04, "museum", None),                        # Kotor old town
            _at(_place("Budva museum", 0, "museum", OPEN_ALL_DAY), 42.2778, 18.8382),
            _at(_place("Tivat collection", 0, "museum", OPEN_ALL_DAY), 42.4340, 18.6950)]


def _at(p, lat, lon):
    p.latitude, p.longitude = lat, lon
    return p


def _run(monkeypatch, s, text, places, near=KOTOR_GATE):
    q = _p(text).query
    q.region, q.near, q.limit = "kb", near, 10
    monkeypatch.setattr(engine, "retrieve", lambda session, query: list(places))
    return run(s, q, NOW, TZ)


def test_in_kotor_scopes_to_the_kotor_locality(monkeypatch, areas):
    res = _run(monkeypatch, areas, "A museum in Kotor", _museums())
    names = [c.place.name for c in res.candidates]
    assert "Budva museum" not in names and res.rejected["outside_locality"] == 1
    assert names[0] == "Maritimum"                              # local first; Tivat is inside the pilot's Kotor box


def test_without_a_town_nothing_is_scoped(monkeypatch, areas):
    res = _run(monkeypatch, areas, "A museum nearby please", _museums())
    assert not res.rejected.get("outside_locality")


def test_unknown_town_is_not_a_filter(monkeypatch, areas):
    res = _run(monkeypatch, areas, "A museum in Paris", _museums())
    assert len(res.candidates) == 3 and not res.rejected.get("outside_locality")


def test_town_without_a_point_measures_from_the_town(monkeypatch, areas):
    res = _run(monkeypatch, areas, "A museum in Budva", _museums(), near=None)
    assert [c.place.name for c in res.candidates] == ["Budva museum"]
    assert res.candidates[0].distance_km is not None and res.candidates[0].distance_km < 3.0   # from the box centre
