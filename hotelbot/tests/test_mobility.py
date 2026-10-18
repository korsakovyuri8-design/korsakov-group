from app.mobility import (
    MODE_SPECS,
    ModeEvidence,
    ModeStatus,
    MobilityMode,
    assess_modes,
    request_from_plan_details,
    search_constraints,
)
from app.transactions.catalog import (
    SERVICE_CATALOG,
    TOPIC_SERVICES,
)
from app.db.models import RequestType


PLAN = {
    "origin_text": "Podgorica",
    "place": "One&Only Portonovi",
    "party_size": 1,
    "route_plan": {
        "origin": {
            "entity_key": "me:podgorica",
            "name": "Podgorica",
        },
        "destination": {
            "entity_key": "me:oneandonly_portonovi",
            "name": "One&Only Portonovi",
        },
        "arrive_by_local": "2026-10-20T09:00:00+02:00",
        "leave_by_local": "2026-10-20T06:21:00+02:00",
        "timezone": "Europe/Podgorica",
    },
}


def test_mobility_modes_map_to_transaction_services():
    assert MODE_SPECS[
        MobilityMode.PRIVATE_TRANSFER
    ].service_type == "intercity_transfer"

    assert MODE_SPECS[
        MobilityMode.SCHEDULED_BUS
    ].service_type == "bus_ticket"

    # Car rental deliberately reuses generic rental architecture.
    rental = MODE_SPECS[MobilityMode.RENTAL_CAR]

    assert rental.service_type == "rental"
    assert rental.fixed_details == {
        "category": "car",
        "quantity": 1,
    }

    assert MODE_SPECS[
        MobilityMode.RAIL
    ].service_type == "rail_ticket"


def test_bus_and_rail_are_real_transaction_service_types():
    bus = SERVICE_CATALOG["bus_ticket"]
    rail = SERVICE_CATALOG["rail_ticket"]

    assert bus.domain == "transport"
    assert rail.domain == "transport"

    assert [
        f.key for f in bus.fields
    ] == [
        "departure_time",
        "origin",
        "destination",
        "party_size",
        "travel_class",
    ]

    assert [
        f.key for f in rail.fields
    ] == [
        "departure_time",
        "origin",
        "destination",
        "party_size",
        "travel_class",
    ]

    transport = TOPIC_SERVICES[RequestType.TRANSPORT]

    assert "bus_ticket" in transport
    assert "rail_ticket" in transport


def test_plan_becomes_one_multimodal_request():
    request = request_from_plan_details(PLAN)

    assert request.origin == "Podgorica"
    assert request.destination == "One&Only Portonovi"
    assert request.party_size == 1
    assert request.timezone == "Europe/Podgorica"

    assert request.arrive_by_local.isoformat() == \
        "2026-10-20T09:00:00+02:00"

    assert request.leave_by_local.isoformat() == \
        "2026-10-20T06:21:00+02:00"


def test_transfer_uses_deterministic_leave_by():
    request = request_from_plan_details(PLAN)

    details = search_constraints(
        request,
        MobilityMode.PRIVATE_TRANSFER,
    )

    assert details == {
        "pickup": "Podgorica",
        "destination": "One&Only Portonovi",
        "party_size": 1,
        "pickup_time": "2026-10-20T06:21:00+02:00",
    }


def test_car_rental_reuses_rental_category_car():
    request = request_from_plan_details(PLAN)

    details = search_constraints(
        request,
        MobilityMode.RENTAL_CAR,
    )

    assert details["category"] == "car"
    assert details["quantity"] == 1
    assert details["start_time"] == \
        "2026-10-20T06:21:00+02:00"

    # These remain journey constraints. They do NOT pretend to describe
    # a return location or rental duration that the traveller never stated.
    assert details["origin"] == "Podgorica"
    assert details["destination"] == "One&Only Portonovi"


def test_scheduled_transport_is_search_not_quote_yet():
    request = request_from_plan_details(PLAN)

    for mode in (
        MobilityMode.SCHEDULED_BUS,
        MobilityMode.RAIL,
    ):
        details = search_constraints(
            request,
            mode,
        )

        assert details == {
            "origin": "Podgorica",
            "destination": "One&Only Portonovi",
            "party_size": 1,
            "arrive_by": "2026-10-20T09:00:00+02:00",
        }

        # A specific departure must come from provider evidence.
        assert "departure_time" not in details


def test_no_provider_evidence_never_becomes_an_offer():
    assessments = {
        x.mode: x
        for x in assess_modes({})
    }

    for result in assessments.values():
        assert result.status == ModeStatus.UNAVAILABLE
        assert result.reason == "no_provider_evidence"


def test_network_can_explicitly_rule_out_rail():
    evidence = {
        MobilityMode.PRIVATE_TRANSFER: ModeEvidence(
            provider_available=True,
            source="provider_registry",
        ),
        MobilityMode.RENTAL_CAR: ModeEvidence(
            provider_available=True,
            source="provider_registry",
        ),
        MobilityMode.RAIL: ModeEvidence(
            provider_available=False,
            network_applicable=False,
            source="rail_network",
            reason="destination_not_served_by_rail",
        ),
    }

    results = {
        x.mode: x
        for x in assess_modes(evidence)
    }

    assert (
        results[MobilityMode.PRIVATE_TRANSFER].status
        == ModeStatus.APPLICABLE
    )

    assert (
        results[MobilityMode.RENTAL_CAR].status
        == ModeStatus.APPLICABLE
    )

    assert (
        results[MobilityMode.SCHEDULED_BUS].status
        == ModeStatus.UNAVAILABLE
    )

    assert (
        results[MobilityMode.RAIL].status
        == ModeStatus.NOT_APPLICABLE
    )

    assert (
        results[MobilityMode.RAIL].reason
        == "destination_not_served_by_rail"
    )
