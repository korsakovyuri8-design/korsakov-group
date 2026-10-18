from app.journey import place_resolver, planning


def detail(origin: str | None):
    return {
        "event": None,
        "place": "Kotor",
        "date_text": "30–31 October",
        "overnight_stay": False,
        "origin_text": origin,
        "arrival_time": "18:00",
        "party_size": 2,
        "resolved_place":
            place_resolver.resolve_place("Kotor"),
        "resolved_date": {
            "status": "resolved",
            "start_date": "2026-10-30",
            "end_date": "2026-10-31",
            "timezone": "Europe/Podgorica",
            "basis": "test",
        },
    }


def codes(origin: str | None) -> list[str]:
    result = planning.needs_for_plan(
        detail(origin),
        [],
    )
    return [x["code"] for x in result["needs"]]


def test_missing_origin_is_unknown():
    assert codes(None) == [
        "arrival_origin_unknown",
    ]


def test_unresolvable_origin_stops_transport_planning():
    assert codes("Definitely Not A Montenegro Place") == [
        "arrival_origin_unresolved",
    ]


def test_resolvable_origin_can_advance_to_transport():
    assert codes("Podgorica") == [
        "arrival_transport_unconfirmed",
    ]
