from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db.models import (
    ExternalTransaction,
    Quote,
    TransactionContext,
    Trip,
    TripItem,
)
from app.db.session import (
    create_schema,
    make_engine,
    make_session_factory,
)
from app.mobility import marketplace


NOW = datetime(
    2026,
    10,
    17,
    15,
    0,
    tzinfo=timezone.utc,
)


def make_session():
    engine = make_engine("sqlite://")
    create_schema(engine)
    factory = make_session_factory(engine)
    return engine, factory()


def add_plan(session):
    trip = Trip(title="Investment Conference")
    session.add(trip)
    session.flush()

    plan = TripItem(
        trip_id=trip.id,
        kind="plan",
        identity="plan|conference",
        provider=None,
        booking_reference=None,
        status="stated",
        details={
            "text": "Investment Conference",
            "event": "Investment Conference",
            "place": "One&Only Portonovi",
            "date_text": "20-21 October",
            "place_role": "venue",
            "overnight_stay": False,
            "origin_text": "Podgorica",
            "arrival_time": "09:00",
            "party_size": 1,
            # Actual Atlas route-plan shape: local wall-clock values.
            "route_plan": {
                "origin": {
                    "name": "Podgorica",
                },
                "destination": {
                    "name": "One&Only Portonovi",
                },
                "arrive_by_local":
                    "2026-10-20T09:00",
                "leave_by_local":
                    "2026-10-20T06:21",
                "timezone":
                    "Europe/Podgorica",
            },
        },
        times={},
        current_revision=1,
        revisions=[],
    )

    session.add(plan)
    session.flush()

    return trip, plan


def count(session, model):
    return session.scalar(
        select(func.count()).select_from(model)
    )


def test_compare_transport_creates_no_quote_or_transaction():
    engine, session = make_session()

    try:
        _, plan = add_plan(session)

        result = marketplace.compare(
            session,
            plan,
            now=NOW,
        )

        modes = {
            x["mode"]: x
            for x in result["modes"]
        }

        transfer = modes["private_transfer"]

        assert transfer["status"] == "available"
        assert len(transfer["providers"]) == 1
        assert transfer["providers"][0]["sandbox"] is True

        assert modes["scheduled_bus"]["status"] == "unavailable"
        assert modes["rental_car"]["status"] == "unavailable"
        assert modes["rail"]["status"] == "unavailable"

        assert count(session, Quote) == 0
        assert count(session, ExternalTransaction) == 0
        assert count(session, TransactionContext) == 0

    finally:
        session.close()
        engine.dispose()


def test_find_transfer_creates_quote_but_not_booking():
    engine, session = make_session()

    try:
        trip, plan = add_plan(session)

        quote, provider = (
            marketplace.create_transfer_quote(
                session,
                trip.id,
                plan,
                now=NOW,
            )
        )

        assert provider == "Atlas Sandbox Transfer"

        assert quote.status.value == "offered"
        assert quote.amount == 60
        assert quote.currency == "EUR"

        assert quote.property_id is None
        assert quote.stay_id is None
        assert quote.conversation_id is None
        assert quote.transaction_context_id is not None

        assert count(session, Quote) == 1
        assert count(session, TransactionContext) == 1

        # Critical trust boundary.
        assert count(session, ExternalTransaction) == 0

        payload = marketplace.quote_payload(
            quote,
            provider,
        )

        assert payload["sandbox"] is True
        assert payload["booking_created"] is False
        assert payload["transaction_created"] is False

    finally:
        session.close()
        engine.dispose()


def test_identical_transfer_quote_reuses_valid_offer():
    engine, session = make_session()

    try:
        trip, plan = add_plan(session)

        first, _ = marketplace.create_transfer_quote(
            session,
            trip.id,
            plan,
            now=NOW,
        )

        second, _ = marketplace.create_transfer_quote(
            session,
            trip.id,
            plan,
            now=NOW,
        )

        assert second.id == first.id
        assert first.status.value == "offered"

        assert count(session, Quote) == 1
        assert count(session, ExternalTransaction) == 0

    finally:
        session.close()
        engine.dispose()


def test_changed_transfer_request_supersedes_old_offer():
    engine, session = make_session()

    try:
        trip, plan = add_plan(session)

        first, _ = marketplace.create_transfer_quote(
            session,
            trip.id,
            plan,
            now=NOW,
        )

        plan.details = {
            **plan.details,
            "party_size": 2,
        }
        session.flush()

        second, _ = marketplace.create_transfer_quote(
            session,
            trip.id,
            plan,
            now=NOW,
        )

        assert second.id != first.id
        assert first.status.value == "superseded"
        assert second.status.value == "offered"
        assert second.amount == 65

        assert count(session, Quote) == 2
        assert count(session, ExternalTransaction) == 0

    finally:
        session.close()
        engine.dispose()


def test_expired_transfer_offer_is_expired_and_replaced():
    engine, session = make_session()

    try:
        trip, plan = add_plan(session)

        first, _ = marketplace.create_transfer_quote(
            session,
            trip.id,
            plan,
            now=NOW,
        )

        later = NOW + timedelta(minutes=16)

        second, _ = marketplace.create_transfer_quote(
            session,
            trip.id,
            plan,
            now=later,
        )

        assert second.id != first.id
        assert first.status.value == "expired"
        assert first.decided_at == later
        assert second.status.value == "offered"

        assert count(session, Quote) == 2
        assert count(session, ExternalTransaction) == 0

    finally:
        session.close()
        engine.dispose()


def test_confirm_transfer_requires_explicit_consent_and_is_idempotent():
    engine, session = make_session()

    try:
        trip, plan = add_plan(session)

        quote, _ = marketplace.create_transfer_quote(
            session,
            trip.id,
            plan,
            now=NOW,
        )

        import pytest

        with pytest.raises(
            ValueError,
            match="explicit_consent_required",
        ):
            marketplace.confirm_transfer_quote(
                session,
                trip.id,
                plan,
                quote.id,
                consent=False,
                now=NOW,
            )

        txn = marketplace.confirm_transfer_quote(
            session,
            trip.id,
            plan,
            quote.id,
            consent=True,
            now=NOW,
        )

        assert txn.action.status.value == "accepted"
        assert txn.provider_reference
        assert txn.provider_reference.startswith("MOCK-")

        assert quote.status.value == "accepted_by_guest"
        assert quote.consent["text"] == "Confirm 60.00 EUR"

        link = plan.details["transport_transaction"]

        assert link["transaction_id"] == txn.id
        assert link["status"] == "accepted"
        assert link["pickup"] == "Podgorica"
        assert link["destination"] == "One&Only Portonovi"
        assert link["sandbox"] is True

        # Browser retry / double-click cannot create a second transaction.
        again = marketplace.confirm_transfer_quote(
            session,
            trip.id,
            plan,
            quote.id,
            consent=True,
            now=NOW,
        )

        assert again.id == txn.id
        assert count(session, ExternalTransaction) == 1

    finally:
        session.close()
        engine.dispose()


def test_compare_works_without_party_size_but_quote_does_not():
    engine, session = make_session()

    try:
        trip, plan = add_plan(session)

        details = dict(plan.details)
        details.pop("party_size", None)
        plan.details = details
        session.flush()

        comparison = marketplace.compare(
            session,
            plan,
            now=NOW,
        )

        transfer = next(
            mode
            for mode in comparison["modes"]
            if mode["mode"] == "private_transfer"
        )

        assert transfer["status"] == "available"
        assert comparison["request"]["party_size"] is None

        import pytest

        with pytest.raises(
            ValueError,
            match="party_size_missing",
        ):
            marketplace.create_transfer_quote(
                session,
                trip.id,
                plan,
                now=NOW,
            )

    finally:
        session.close()
        engine.dispose()
