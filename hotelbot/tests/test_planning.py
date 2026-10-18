from types import SimpleNamespace

from app.journey import planning


PLAN = {
    "event": "Investment Conference",
    "place": "One&Only Portonovi",
    "resolved_place": {
        "canonical_name": "One&Only Portonovi",
        "locality": "Kumbor",
        "municipality": "Herceg Novi",
        "address": "Portonovi Resort, Herceg Novi 85340, Montenegro",
    },
    "resolved_date": {
        "start_date": "2026-10-20",
        "end_date": "2026-10-21",
    },
}


def item(kind, status, *, details=None, times=None, provider=None):
    return SimpleNamespace(
        kind=kind,
        status=status,
        details=details or {},
        times=times or {},
        provider=provider,
    )


def codes(result):
    return [x["code"] for x in result["needs"]]


def test_unknown_plan_asks_before_assuming_stay_or_transport():
    result = planning.needs_for_plan(PLAN, [])

    assert codes(result) == [
        "place_role_unknown",
        "overnight_stay_unknown",
        "arrival_origin_unknown",
    ]


def test_declared_stay_requires_confirmation_but_not_another_stay_question():
    result = planning.needs_for_plan(
        {
            **PLAN,
            "place_role": "stay",
        },
        [],
    )

    assert codes(result) == [
        "accommodation_unconfirmed",
        "arrival_origin_unknown",
    ]


def test_no_overnight_removes_accommodation_need():
    result = planning.needs_for_plan(
        {
            **PLAN,
            "place_role": "venue",
            "overnight_stay": False,
        },
        [],
    )

    assert "overnight_stay_unknown" not in codes(result)
    assert "accommodation_unconfirmed" not in codes(result)


def test_known_origin_asks_for_arrival_time_before_transport():
    result = planning.needs_for_plan(
        {
            **PLAN,
            "place_role": "venue",
            "overnight_stay": False,
            "origin_text": "Podgorica",
        },
        [],
    )

    assert codes(result) == ["arrival_time_unknown"]


def test_matching_confirmed_transfer_closes_transport_need():
    transfer = item(
        "transfer",
        "confirmed",
        details={
            "pickup": "Podgorica",
            "dropoff": "One&Only Portonovi, Herceg Novi, Montenegro",
        },
        times={
            "dropoff_at": {
                "local": "2026-10-20T14:00",
            },
        },
    )

    result = planning.needs_for_plan(
        {
            **PLAN,
            "place_role": "venue",
            "overnight_stay": False,
            "origin_text": "Podgorica",
            "arrival_time": "15:00",
        },
        [transfer],
    )

    assert codes(result) == []
    assert result["status"] == "covered"


def test_known_origin_requires_arrival_time_before_transport():
    result = planning.needs_for_plan(
        {
            **PLAN,
            "place_role": "venue",
            "overnight_stay": False,
            "origin_text": "Podgorica",
        },
        [],
    )

    assert codes(result) == ["arrival_time_unknown"]


def test_known_arrival_time_surfaces_transport_gap():
    result = planning.needs_for_plan(
        {
            **PLAN,
            "place_role": "venue",
            "overnight_stay": False,
            "origin_text": "Podgorica",
            "arrival_time": "09:00",
            "party_size": 1,
        },
        [],
    )

    assert codes(result) == ["arrival_transport_unconfirmed"]
    assert result["derived"]["arrival_by_local"] == "2026-10-20T09:00"


def test_confirmed_transfer_after_arrival_deadline_does_not_cover_plan():
    transfer = item(
        "transfer",
        "confirmed",
        details={
            "pickup": "Podgorica",
            "dropoff": "One&Only Portonovi, Herceg Novi, Montenegro",
        },
        times={
            "dropoff_at": {
                "local": "2026-10-20T14:00",
            },
        },
    )

    result = planning.needs_for_plan(
        {
            **PLAN,
            "place_role": "venue",
            "overnight_stay": False,
            "origin_text": "Podgorica",
            "arrival_time": "09:00",
            "party_size": 1,
        },
        [transfer],
    )

    assert codes(result) == ["arrival_transport_unconfirmed"]
    assert result["derived"]["arrival_by_local"] == "2026-10-20T09:00"


def test_transport_gap_surfaces_without_party_size():
    result = planning.needs_for_plan(
        {
            **PLAN,
            "place_role": "venue",
            "overnight_stay": False,
            "origin_text": "Podgorica",
            "arrival_time": "09:00",
        },
        [],
    )

    assert codes(result) == ["arrival_transport_unconfirmed"]


def test_party_size_completes_transport_request_shape():
    result = planning.needs_for_plan(
        {
            **PLAN,
            "place_role": "venue",
            "overnight_stay": False,
            "origin_text": "Podgorica",
            "arrival_time": "09:00",
            "party_size": 1,
        },
        [],
    )

    assert codes(result) == ["arrival_transport_unconfirmed"]
    assert result["answers"]["party_size"] == 1


def test_accepted_provider_transaction_closes_transport_planning_gap():
    result = planning.needs_for_plan(
        {
            **PLAN,
            "place_role": "venue",
            "overnight_stay": False,
            "origin_text": "Podgorica",
            "arrival_time": "09:00",
            "party_size": 1,
            "route_plan": {
                "origin": {"name": "Podgorica"},
                "destination": {
                    "name": "One&Only Portonovi",
                },
                "leave_by_local":
                    "2026-10-20T06:21",
                "arrive_by_local":
                    "2026-10-20T09:00",
                "timezone":
                    "Europe/Podgorica",
            },
            "transport_transaction": {
                "status": "accepted",
                "pickup": "Podgorica",
                "destination":
                    "One&Only Portonovi",
                "pickup_time":
                    "2026-10-20T06:21:00+02:00",
                "party_size": 1,
            },
        },
        [],
    )

    assert (
        "arrival_transport_unconfirmed"
        not in codes(result)
    )
