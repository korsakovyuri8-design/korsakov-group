from datetime import datetime, timezone

from app.journey import route_planner


ORIGIN = {
    "entity_key": "me:podgorica",
    "canonical_name": "Podgorica",
    "locality": "Podgorica",
    "timezone": "Europe/Podgorica",
    "coordinates": {
        "lat": 42.44123775,
        "lon": 19.26308552,
    },
}

DESTINATION = {
    "entity_key": "me:oneandonly_portonovi",
    "canonical_name": "One&Only Portonovi",
    "locality": "Kumbor",
    "timezone": "Europe/Podgorica",
    "coordinates": {
        "lat": 42.435287,
        "lon": 18.59857,
    },
}


def test_route_plan_keeps_provider_time_and_atlas_buffer_separate():
    out = route_planner.build_route_plan(
        origin=ORIGIN,
        destination=DESTINATION,
        arrival_by_local="2026-10-20T09:00",
        estimate={
            "distance_m": 96400,
            "duration_seconds": 129 * 60,
        },
        generated_at=datetime(
            2026, 10, 17, 10, 0,
            tzinfo=timezone.utc,
        ),
    )

    assert out["distance_km"] == 96.4
    assert out["route_duration_minutes"] == 129
    assert out["planning_buffer_minutes"] == 30

    # 09:00 - 2h09 route - 30m Atlas buffer
    assert out["leave_by_local"] == "2026-10-20T06:21"
    assert out["arrive_by_local"] == "2026-10-20T09:00"

    assert out["traffic_live"] is False
    assert out["provider"] == "osrm_demo"
    assert out["provider_guarantee"] is False


def test_route_plan_never_calls_buffer_live_traffic():
    out = route_planner.build_route_plan(
        origin=ORIGIN,
        destination=DESTINATION,
        arrival_by_local="2026-10-20T09:00",
        estimate={
            "distance_m": 96400,
            "duration_seconds": 7740,
        },
        generated_at=datetime(
            2026, 10, 17, 10, 0,
            tzinfo=timezone.utc,
        ),
    )

    assert out["basis"] == (
        "external_route_estimate_plus_atlas_policy_buffer"
    )
    assert out["traffic_live"] is False
