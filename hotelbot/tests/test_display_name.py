"""Iteration 6.1: a technical OSM fallback name (node/123, way/456,
relation/789) is an internal identifier, never a user-facing name. The
renderer shows the localised kind instead (plus the address the source
gives, if any) - it invents no name, number or street. Place.name, the
projection and the ranking are untouched (docs/ITERATION_6_1.md)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.discovery.engine import Candidate, EventCandidate
from app.discovery.render import candidate_line, display_name, event_line
from app.places.hours import HoursStatus, OpenState


def _place(name: str | None, sub: str = "atm", address: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(name=name, subcategory=sub, address=address, category="FINANCIAL_SERVICE", resolution={},
                           hours=None, attributes={}, tags={"tags": []})


@pytest.mark.parametrize("name", ["node/13716103405", "way/305481242", "relation/789"])
def test_technical_osm_ids_are_never_shown(name):
    assert display_name(_place(name, "atm"), "en") == "ATM"


def test_unnamed_museum_uses_the_localised_kind():
    p = _place("way/305481242", "museum")
    assert [display_name(p, loc) for loc in ("en", "cnr", "ru", "cnr-Cyrl")] == ["Museum", "Muzej", "Музей", "Музеј"]


def test_real_names_are_unchanged():
    for name in ("Vegapharm", "Node Cafe", "node/abc", "Way Out Bar", "Muzej mačaka"):
        assert display_name(_place(name, "cafe"), "en") == name


def test_missing_name_falls_back_to_the_kind():
    assert display_name(_place(None, "public_toilet"), "en") == "Public toilet"


def test_address_completes_but_does_not_replace_the_kind():
    assert display_name(_place("node/1", "atm", "Mediteranska 4, Budva"), "en") == "ATM, Mediteranska 4, Budva"
    assert display_name(_place("Euronet", "atm", "Mediteranska 4, Budva"), "en") == "Euronet"


def _candidate(place) -> Candidate:
    return Candidate(place, 0.04, HoursStatus(OpenState.UNKNOWN), None, [], [], walk_minutes=1)


def test_candidate_line_never_contains_a_technical_id():
    line = candidate_line(1, _candidate(_place("node/13716103405")), "en", anchor="budva old town")
    assert line.startswith("1. ATM - ") and "node/" not in line


def test_candidate_line_for_a_named_place_is_unchanged():
    line = candidate_line(1, _candidate(_place("Euronet")), "en", anchor="budva old town")
    assert line.startswith("1. Euronet (ATM) - ")


def test_event_venue_with_a_technical_name_uses_the_fallback():
    event = SimpleNamespace(title={"en": "Klapa evening"}, slug="klapa", ticket_required=False, ticket_price=None,
                            attributes={}, tags={}, resolution={}, currency=None, age_limit=None,
                            start_at=datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc))
    line = event_line(1, EventCandidate(event, _place("way/305481242", "museum"), [], []), "en",
                      lambda dt: dt.strftime("%H:%M"))
    assert "@ Museum" in line and "way/" not in line
