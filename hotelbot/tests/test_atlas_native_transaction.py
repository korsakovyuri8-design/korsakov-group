from datetime import datetime, timezone

from sqlalchemy import select

from app.actions.executors import default_executors
from app.clock import FrozenClock
from app.db.models import (
    ActionStatus,
    ExternalProvider,
    ExternalTransaction,
    Job,
    Quote,
    QuoteStatus,
    Trip,
)
from app.db.session import (
    create_schema,
    make_engine,
    make_session_factory,
)
from app.transactions.context import get_or_create_for_trip
from app.transactions.providers.registry import ProviderRegistry
from app.transactions.service import (
    TransactionService,
    TxnDeps,
)
from app.whatsapp.mock import MockWhatsAppTransport


NOW = datetime(
    2026,
    10,
    17,
    13,
    0,
    tzinfo=timezone.utc,
)


def make_db():
    engine = make_engine("sqlite://")
    create_schema(engine)
    factory = make_session_factory(engine)
    return engine, factory


def make_deps():
    clock = FrozenClock(NOW)
    outbox = MockWhatsAppTransport()

    return TxnDeps(
        clock=clock,
        providers=ProviderRegistry(clock),
        executors=default_executors(),
        transport_for=lambda _: outbox,
    )


def add_provider(session):
    provider = ExternalProvider(
        property_id=None,
        region="montenegro-beta",
        slug="sandbox-transfer",
        name="Sandbox Transfer",
        provider_type="transport",
        integration_type="mock",
        active=True,
        services={
            "service_types": [
                "intercity_transfer",
            ]
        },
        config={
            "currency": "EUR",
            "pricing": {
                "base": 50,
                "per_extra_person": 5,
            },
            "validity_minutes": 15,
            "conditions": "Sandbox provider only.",
        },
    )

    session.add(provider)
    session.flush()

    return provider


DETAILS = {
    "pickup_time": "2026-10-20T06:21:00+02:00",
    "pickup": "Podgorica",
    "destination": "One&Only Portonovi",
    "party_size": 1,
}


def test_atlas_native_quote_has_no_fake_hotel_or_stay():
    engine, factory = make_db()

    try:
        with factory() as session:
            trip = Trip(
                title="Podgorica to Portonovi",
            )
            session.add(trip)
            session.flush()

            context = get_or_create_for_trip(
                session,
                trip.id,
                timezone="Europe/Podgorica",
                locale="en",
            )

            provider = add_provider(session)

            service = TransactionService(
                session,
                make_deps(),
            )

            quote = service.request_quote(
                context=context,
                provider=provider,
                service_type="intercity_transfer",
                details=DETAILS,
                locale="en",
            )

            assert quote.status == QuoteStatus.OFFERED

            assert quote.transaction_context_id == context.id

            assert quote.property_id is None
            assert quote.stay_id is None
            assert quote.guest_id is None
            assert quote.conversation_id is None

            assert quote.amount == 50
            assert quote.currency == "EUR"

    finally:
        engine.dispose()


def test_quote_is_not_a_booking():
    engine, factory = make_db()

    try:
        with factory() as session:
            trip = Trip(title="Trip")
            session.add(trip)
            session.flush()

            context = get_or_create_for_trip(
                session,
                trip.id,
                timezone="Europe/Podgorica",
            )

            provider = add_provider(session)

            service = TransactionService(
                session,
                make_deps(),
            )

            quote = service.request_quote(
                context=context,
                provider=provider,
                service_type="intercity_transfer",
                details=DETAILS,
                locale="en",
            )

            assert quote.status == QuoteStatus.OFFERED

            assert session.scalar(
                select(ExternalTransaction)
            ) is None

            assert session.scalar(
                select(Job).where(
                    Job.kind == "provider_submit"
                )
            ) is None

    finally:
        engine.dispose()


def test_explicit_consent_creates_transaction_and_submit_job():
    engine, factory = make_db()

    try:
        with factory() as session:
            trip = Trip(title="Trip")
            session.add(trip)
            session.flush()

            context = get_or_create_for_trip(
                session,
                trip.id,
                timezone="Europe/Podgorica",
            )

            provider = add_provider(session)

            service = TransactionService(
                session,
                make_deps(),
            )

            quote = service.request_quote(
                context=context,
                provider=provider,
                service_type="intercity_transfer",
                details=DETAILS,
                locale="en",
            )

            txn = service.accept(
                quote,
                message_id="atlas-consent-test-1",
                text="Confirm 50.00 EUR",
            )

            assert (
                quote.status
                == QuoteStatus.ACCEPTED_BY_GUEST
            )

            assert quote.consent["text"] == "Confirm 50.00 EUR"
            assert quote.consent["amount"] == "50.00"
            assert quote.consent["currency"] == "EUR"

            assert txn.action.status == ActionStatus.PROPOSED

            assert txn.action.property_id is None
            assert txn.action.stay_id is None
            assert txn.action.conversation_id is None

            assert (
                txn.action.transaction_context_id
                == context.id
            )

            assert txn.quote_id == quote.id

            job = session.scalar(
                select(Job).where(
                    Job.kind == "provider_submit"
                )
            )

            assert job is not None
            assert job.payload == {
                "transaction_id": txn.id,
            }

    finally:
        engine.dispose()


def test_new_quote_supersedes_only_same_trip_context():
    engine, factory = make_db()

    try:
        with factory() as session:
            trip_a = Trip(title="Trip A")
            trip_b = Trip(title="Trip B")

            session.add_all([
                trip_a,
                trip_b,
            ])
            session.flush()

            context_a = get_or_create_for_trip(
                session,
                trip_a.id,
                timezone="Europe/Podgorica",
            )

            context_b = get_or_create_for_trip(
                session,
                trip_b.id,
                timezone="Europe/Podgorica",
            )

            provider = add_provider(session)

            service = TransactionService(
                session,
                make_deps(),
            )

            a1 = service.request_quote(
                context=context_a,
                provider=provider,
                service_type="intercity_transfer",
                details=DETAILS,
                locale="en",
            )

            b1 = service.request_quote(
                context=context_b,
                provider=provider,
                service_type="intercity_transfer",
                details=DETAILS,
                locale="en",
            )

            a2 = service.request_quote(
                context=context_a,
                provider=provider,
                service_type="intercity_transfer",
                details=DETAILS,
                locale="en",
            )

            assert a1.status == QuoteStatus.SUPERSEDED
            assert a2.status == QuoteStatus.OFFERED

            # Another Atlas trip is untouched.
            assert b1.status == QuoteStatus.OFFERED

            open_a = service.open_quotes_for_context(
                context_a.id
            )

            assert [q.id for q in open_a] == [a2.id]

    finally:
        engine.dispose()


def test_request_quote_rejects_ambiguous_or_ownerless_context():
    engine, factory = make_db()

    try:
        with factory() as session:
            provider = add_provider(session)

            service = TransactionService(
                session,
                make_deps(),
            )

            try:
                service.request_quote(
                    provider=provider,
                    service_type="intercity_transfer",
                    details=DETAILS,
                    locale="en",
                )
            except ValueError as exc:
                assert "exactly one owner" in str(exc)
            else:
                raise AssertionError(
                    "ownerless quote was accepted"
                )

    finally:
        engine.dispose()
