"""Iteration 6.1: one display-name rule for every guest-facing path
(app/places/naming.py): discovery lines, event venues, saved / shortlisted /
planned items and their replies. A technical OSM id is never shown or stored
as a title; real names and event titles are unchanged."""

from __future__ import annotations

from types import SimpleNamespace

from app.places import naming
from app.trip.commands import PlanCommands
from app.trip.planner import shortlist_names


def _place(name, sub="atm", address=None):  # noqa: ANN001, ANN202
    return SimpleNamespace(name=name, subcategory=sub, address=address)


def test_one_rule_for_discovery_and_trip():
    from app.discovery import render

    assert render.display_name is naming.display_name


def test_title_of_an_unnamed_place_is_its_localised_kind():
    for lang, want in (("en", "ATM"), ("cnr", "Bankomat"), ("ru", "Банкомат")):
        assert PlanCommands._title(None, SimpleNamespace(language=lang), _place("node/13716103405"), None) == want
    museum = _place("way/305481242", "museum")
    assert PlanCommands._title(None, SimpleNamespace(language="en"), museum, None) == "Museum"


def test_title_of_a_named_place_and_of_an_event_is_unchanged():
    turn = SimpleNamespace(language="en")
    assert PlanCommands._title(None, turn, _place("Vegapharm", "pharmacy"), None) == "Vegapharm"
    event = SimpleNamespace(title={"en": "Klapa evening"}, slug="klapa")
    assert PlanCommands._title(None, turn, _place("way/1", "museum"), event) == "Klapa evening"


def test_shortlist_title_never_holds_a_technical_id():
    results = [SimpleNamespace(place=_place("node/1")), SimpleNamespace(place=_place("Euronet"))]
    assert shortlist_names(results, "en") == "ATM (+1)"
    assert shortlist_names(results[1:], "en") == "Euronet"


def test_commands_never_put_a_raw_place_name_in_guest_text():
    import inspect

    import app.trip.commands as commands

    assert "place.name" not in inspect.getsource(commands)       # every guest-facing name goes through naming
