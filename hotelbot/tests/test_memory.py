from datetime import date

import pytest

from app.agent.memory import extract_facts, known_facts, remember

TODAY = date(2026, 10, 5)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("We arrive on 20 December and leave on 23 December, 2 adults",
         {"arrival_date": "2026-12-20", "departure_date": "2026-12-23", "guest_count": 2}),
        ("Dolazimo 20.12. i ostajemo do 23.12., nas je troje",
         {"arrival_date": "2026-12-20", "departure_date": "2026-12-23", "guest_count": 3}),
        ("We are arriving December 20th", {"arrival_date": "2026-12-20"}),
        ("from 3/1 to 7/1 for two people",
         {"arrival_date": "2027-01-03", "departure_date": "2027-01-07", "guest_count": 2}),
        ("Stižemo 5. januara", {"arrival_date": "2027-01-05"}),
        ("checking out on 7 jan", {"departure_date": "2027-01-07"}),
        ("I'm in room 204, need towels", {"room_number": "204"}),
        ("Can we check in after 11pm?", {}),
        ("table for 4 at 19h", {}),
    ],
)
def test_extract_facts(text, expected):
    assert extract_facts(text, TODAY) == expected


def test_remember_marks_facts_unconfirmed_and_guest_stated():
    memory = remember({"_state": {"x": 1}}, {"guest_count": 2})
    assert memory["guest_count"]["value"] == 2
    assert memory["guest_count"]["confirmed"] is False
    assert memory["guest_count"]["source"] == "guest_stated"
    assert "_state" not in known_facts(memory)
