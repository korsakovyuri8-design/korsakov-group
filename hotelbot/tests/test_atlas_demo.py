"""Atlas demo v1: five scenes computed by the real pipeline, offline
(fixtures -> Journey Graph -> timeline -> windows -> demo World -> Discovery).
Nothing in a scene is typed in by hand: these gates pin what the viewer sees."""

from __future__ import annotations

import json

import pytest

from app.trip import atlas_demo


@pytest.fixture(scope="module")
def demo() -> dict:
    return atlas_demo.run()


def _scene(demo: dict, sid: str) -> dict:
    return next(s for s in demo["scenes"] if s["id"] == sid)


def test_the_demo_is_deterministic(demo):
    assert json.dumps(atlas_demo.run(), sort_keys=True) == json.dumps(demo, sort_keys=True)


def test_scene_1_the_bookings_become_a_trip(demo):
    s = _scene(demo, "bookings")
    assert s["route"] == ["London", "Podgorica", "Kotor"] and s["dates"] == "18–21 October"
    assert [b["kind"] for b in s["bookings"]] == ["flight", "accommodation", "restaurant"]
    assert all(b["status"] == "confirmed" for b in s["bookings"])


def test_scene_2_atlas_notices_the_missing_transfer(demo):
    s = _scene(demo, "problem")
    assert s["lines"] == ["You land in Podgorica at 12:55.",
                          "Your next confirmed place is dinner at Konoba Demo in Kotor at 20:00.",
                          "There is no transport between them."]
    assert s["action"] == "Arrange transfer" and s["free_time"] == []      # no window is claimed


def test_scene_3_the_transfer_rebuilds_the_day(demo):
    s = _scene(demo, "rebuilt")
    assert [(i["time"], i["label"]) for i in s["itinerary"]] == [
        ("09:15–12:55", "Flight DM1432 London (LGW) → Podgorica (TGD)"),
        ("15:00–16:00", "Transfer from TGD"),
        ("16:00", "Hotel arrival — Hotel Demo Kotor"),
        ("16:00–16:30", "Check-in"),
        ("16:30–19:30", "Free time"),
        ("20:00", "Dinner at Konoba Demo")]
    assert s["issues"] == []


def test_scene_4_three_things_that_actually_fit(demo):
    s = _scene(demo, "suggestions")
    assert s["headline"] == "You have 3 hours before dinner. Here are 3 things that actually fit."
    assert len(s["suggestions"]) == 3 and s["considered"] == 6 and s["left_out"] == 3
    for x in s["suggestions"]:          # hotel -> place -> visit -> dinner, inside 16:30-19:30
        assert "16:30" <= x["leave_hotel"] and x["arrive_dinner"] <= "19:30"
        assert x["visit_from"] < x["visit_to"]
    names = {x["name"] for x in s["suggestions"]}
    assert not names & {"Icon Gallery", "Palace Collection", "Mountain Mausoleum"}


def test_scene_5_a_flight_change_is_noticed_not_rewritten(demo):
    s = _scene(demo, "change")
    assert s["headline"] == "Demo Air changed your flight."
    assert s["lines"][:2] == ["Departure moved from 09:15 to 11:15.", "You now land in Podgorica at 14:55."]
    assert "Your existing transfer may no longer fit." in s["lines"]
    assert s["free_time"] == []                                   # the 16:30 window is no longer proven
    assert any(i["kind"] == "impossible_connection" for i in s["issues"])


def test_the_brand_lines(demo):
    assert demo["brand"] == {"name": "KORSAKOV ATLAS", "tagline": "Your entire trip, understood.",
                             "cta": "Forward your bookings. Atlas handles the rest."}
