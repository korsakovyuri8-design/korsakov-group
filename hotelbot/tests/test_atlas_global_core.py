from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.atlas_web import intake
from app.db.models import (
    ExternalProvider,
    ExternalTransaction,
    Quote,
    Trip,
    TripItem,
)
from app.db.session import (
    create_schema,
    make_engine,
    make_session_factory,
)
from app.journey import place_resolver, route_planner
from app.journey.parsers import plan as plan_parser
from app.mobility import marketplace
from app.mobility.sandbox import ensure_transfer_provider


NOW = datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("name,key,zone", [
    ("Rome, Italy", "it:rome", "Europe/Rome"),
    ("Milan, Italy", "it:milan", "Europe/Rome"),
    ("Paris, France", "fr:paris", "Europe/Paris"),
    ("London, United Kingdom", "gb:london", "Europe/London"),
    ("Istanbul, Turkey", "tr:istanbul", "Europe/Istanbul"),
    ("Tbilisi, Georgia", "ge:tbilisi", "Asia/Tbilisi"),
    ("Tashkent, Uzbekistan", "uz:tashkent", "Asia/Tashkent"),
    ("New York City, United States", "us:nyc", "America/New_York"),
    ("Tokyo, Japan", "jp:tokyo", "Asia/Tokyo"),
])
def test_international_city(name, key, zone):
    place = place_resolver.resolve_place(name)

    assert place is not None
    assert place["entity_key"] == key
    assert place["timezone"] == zone
    assert place["resolver"] == "atlas_global_seed"
    assert place["coordinate_precision"] == "city_centroid"


def test_unknown_and_unqualified_places_are_not_guessed():
    assert place_resolver.resolve_place("Rome") is None
    assert place_resolver.resolve_place("Unknown City, Italy") is None


def test_montenegro_backward_compatibility():
    assert place_resolver.resolve_place("Kotor")["entity_key"] == "me:kotor"
    assert place_resolver.resolve_place("Podgorica")["entity_key"] == "me:podgorica"


def test_international_natural_language_plan():
    parsed = plan_parser.parse(
        intake.prepare(
            "I'm going from Rome, Italy to Milan, Italy "
            "for 20 November 2026"
        )
    )

    assert parsed is not None

    fields = {
        field: value
        for field, value, _ in parsed.observations
    }

    assert fields["origin_text"] == "Rome, Italy"
    assert fields["place"] == "Milan, Italy"
    assert fields["date_text"] == "20 November 2026"


def test_route_uses_destination_timezone():
    origin = place_resolver.resolve_place("London, United Kingdom")
    destination = place_resolver.resolve_place("Paris, France")

    assert origin is not None
    assert destination is not None

    plan = route_planner.build_route_plan(
        origin=origin,
        destination=destination,
        arrival_by_local="2026-11-20T18:00",
        estimate={
            "distance_m": 450000,
            "duration_seconds": 21600,
        },
        generated_at=NOW,
    )

    assert plan["timezone"] == "Europe/Paris"
    assert plan["leave_by_local"] == "2026-11-20T11:30"
    assert plan["traffic_live"] is False
    assert plan["provider_guarantee"] is False


def make_plan(session, origin, destination):
    trip = Trip(title="Global Core Test")
    session.add(trip)
    session.flush()

    plan = TripItem(
        trip_id=trip.id,
        kind="plan",
        identity="plan|global-core",
        provider=None,
        booking_reference=None,
        status="stated",
        details={
            "origin_text": origin,
            "place": destination,
            "party_size": 2,
            "route_plan": {
                "origin": {"name": origin},
                "destination": {"name": destination},
                "arrive_by_local": "2026-11-20T18:00",
                "leave_by_local": "2026-11-20T12:00",
                "timezone": "Europe/Rome",
            },
        },
        times={},
        current_revision=1,
        revisions=[],
    )
    session.add(plan)
    session.flush()

    return trip, plan


@pytest.mark.parametrize("origin,destination", [
    ("Rome, Italy", "Milan, Italy"),
    ("Podgorica", "Rome, Italy"),
    ("Rome", "Milan, Italy"),
])
def test_foreign_routes_never_get_montenegro_provider(
    origin, destination
):
    engine = make_engine("sqlite://")
    create_schema(engine)

    try:
        with make_session_factory(engine)() as db:
            trip, plan = make_plan(db, origin, destination)

            comparison = marketplace.compare(db, plan, now=NOW)

            assert all(
                mode["status"] == "unavailable"
                for mode in comparison["modes"]
            )
            assert all(
                not mode["providers"]
                for mode in comparison["modes"]
            )

            with pytest.raises(
                ValueError,
                match="no_transfer_provider_for_region",
            ):
                marketplace.create_transfer_quote(
                    db, trip.id, plan, now=NOW
                )

            assert db.scalar(
                select(func.count()).select_from(ExternalProvider)
            ) == 0

            assert db.scalar(
                select(func.count()).select_from(Quote)
            ) == 0

            assert db.scalar(
                select(func.count()).select_from(ExternalTransaction)
            ) == 0
    finally:
        engine.dispose()


def test_existing_sandbox_provider_cannot_leak_to_italy():
    engine = make_engine("sqlite://")
    create_schema(engine)

    try:
        with make_session_factory(engine)() as db:
            ensure_transfer_provider(db)

            _, plan = make_plan(
                db, "Rome, Italy", "Milan, Italy"
            )

            result = marketplace.compare(db, plan, now=NOW)

            assert all(
                not mode["providers"]
                for mode in result["modes"]
            )

            assert marketplace._region_for_request(
                marketplace.request_from_plan_details(
                    plan.details
                )
            ) is None
    finally:
        engine.dispose()
