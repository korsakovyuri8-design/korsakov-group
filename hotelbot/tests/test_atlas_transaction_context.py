import pytest
from sqlalchemy import select

from app.actions.executors import default_executors
from app.actions.service import ActionService
from app.db.models import (
    Action,
    TransactionContext,
    Trip,
)
from app.db.session import create_schema, make_engine, make_session_factory
from app.transactions.context import get_or_create_for_trip


@pytest.fixture
def db():
    engine = make_engine("sqlite://")
    create_schema(engine)
    factory = make_session_factory(engine)

    with factory() as session:
        yield session

    engine.dispose()


def make_trip(db, title="Atlas trip"):
    trip = Trip(title=title)
    db.add(trip)
    db.flush()
    return trip


def test_one_transaction_context_per_trip(db):
    trip = make_trip(db)

    first = get_or_create_for_trip(
        db,
        trip.id,
        timezone="Europe/Podgorica",
        locale="en",
    )

    second = get_or_create_for_trip(
        db,
        trip.id,
        timezone="Europe/Podgorica",
        locale="en",
    )

    assert first.id == second.id

    rows = list(
        db.scalars(
            select(TransactionContext).where(
                TransactionContext.trip_id == trip.id
            )
        )
    )

    assert len(rows) == 1
    assert rows[0].kind == "atlas_trip"
    assert rows[0].guest_id is None
    assert rows[0].timezone == "Europe/Podgorica"


def test_unknown_trip_cannot_get_transaction_context(db):
    with pytest.raises(ValueError, match="unknown trip"):
        get_or_create_for_trip(
            db,
            "does-not-exist",
            timezone="Europe/Podgorica",
        )


def test_atlas_native_action_can_belong_to_context_without_property(db):
    trip = make_trip(db)

    context = get_or_create_for_trip(
        db,
        trip.id,
        timezone="Europe/Podgorica",
    )

    service = ActionService(
        db,
        default_executors(),
    )

    action = service.propose(
        property_id=None,
        transaction_context_id=context.id,
        action_type="intercity_transfer",
        summary="Transfer Podgorica to Portonovi",
        executor="provider",
    )

    assert action.property_id is None
    assert action.stay_id is None
    assert action.conversation_id is None
    assert action.transaction_context_id == context.id

    stored = db.get(Action, action.id)

    assert stored is not None
    assert stored.transaction_context_id == context.id


def test_action_cannot_be_ownerless(db):
    service = ActionService(
        db,
        default_executors(),
    )

    with pytest.raises(
        ValueError,
        match="exactly one owner",
    ):
        service.propose(
            property_id=None,
            transaction_context_id=None,
            action_type="intercity_transfer",
            summary="Invalid",
        )


def test_action_cannot_have_property_and_atlas_context(db):
    trip = make_trip(db)

    context = get_or_create_for_trip(
        db,
        trip.id,
        timezone="Europe/Podgorica",
    )

    service = ActionService(
        db,
        default_executors(),
    )

    with pytest.raises(
        ValueError,
        match="exactly one owner",
    ):
        service.propose(
            property_id="legacy-property",
            transaction_context_id=context.id,
            action_type="intercity_transfer",
            summary="Invalid double owner",
        )
