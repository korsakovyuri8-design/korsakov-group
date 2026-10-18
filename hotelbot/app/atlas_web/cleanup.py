"""Retention policy for anonymous Atlas web trips.

Anonymous demo/travel state may be deleted when its web session ends, unless
the trip owns a real-world provider transaction. Real provider obligations
and their audit trail must survive browser reset / session expiry.

Synthetic providers explicitly declare profile.synthetic = true.
Anything else is treated conservatively as real-world.
"""

from __future__ import annotations

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    Action,
    ActionEvent,
    AtlasWebSession,
    ExternalProvider,
    ExternalTransaction,
    InventoryHold,
    ItineraryItem,
    Job,
    ProviderEvent,
    Quote,
    TransactionContext,
    Trip,
    TripIssue,
    TripItem,
    TripObservation,
    TripSourceRecord,
)


def _transaction_rows(
    db: Session,
    trip_id: str,
) -> list[ExternalTransaction]:
    return list(
        db.scalars(
            select(ExternalTransaction)
            .join(
                Action,
                ExternalTransaction.action_id == Action.id,
            )
            .join(
                TransactionContext,
                Action.transaction_context_id
                == TransactionContext.id,
            )
            .where(
                TransactionContext.trip_id == trip_id,
            )
        )
    )


def has_real_world_obligation(
    db: Session,
    trip_id: str,
) -> bool:
    for transaction in _transaction_rows(db, trip_id):
        provider = db.get(
            ExternalProvider,
            transaction.provider_id,
        )

        # Missing/unknown provider metadata is treated conservatively.
        if provider is None:
            return True

        synthetic = (
            isinstance(provider.profile, dict)
            and provider.profile.get("synthetic") is True
        )

        if not synthetic:
            return True

    return False


def revoke_web_session(
    db: Session,
    trip_id: str,
) -> None:
    db.execute(
        delete(AtlasWebSession).where(
            AtlasWebSession.trip_id == trip_id,
        )
    )


def purge_anonymous_trip(
    db: Session,
    trip_id: str,
) -> bool:
    """Delete one anonymous trip iff no real-world obligation exists.

    Returns True when the Trip itself was deleted.
    """

    if has_real_world_obligation(db, trip_id):
        revoke_web_session(db, trip_id)
        return False

    context_ids = list(
        db.scalars(
            select(TransactionContext.id).where(
                TransactionContext.trip_id == trip_id,
            )
        )
    )

    quote_ids: list[str] = []
    action_ids: list[str] = []

    if context_ids:
        quote_ids = list(
            db.scalars(
                select(Quote.id).where(
                    Quote.transaction_context_id.in_(context_ids)
                )
            )
        )

        action_ids = list(
            db.scalars(
                select(Action.id).where(
                    Action.transaction_context_id.in_(context_ids)
                )
            )
        )

    transaction_ids: list[str] = []

    if quote_ids or action_ids:
        conditions = []

        if quote_ids:
            conditions.append(
                ExternalTransaction.quote_id.in_(quote_ids)
            )

        if action_ids:
            conditions.append(
                ExternalTransaction.action_id.in_(action_ids)
            )

        transaction_ids = list(
            db.scalars(
                select(ExternalTransaction.id).where(
                    or_(*conditions)
                )
            )
        )

    # Durable outbox has no FK by design, so remove jobs by their stable
    # transaction/action idempotency-key prefixes before deleting owners.
    job_conditions = []

    for transaction_id in transaction_ids:
        job_conditions.extend([
            Job.idempotency_key
            == f"submit:{transaction_id}",
            Job.idempotency_key
            == f"cancel:{transaction_id}",
            Job.idempotency_key
            == f"reconcile:{transaction_id}",
        ])

    for action_id in action_ids:
        job_conditions.append(
            Job.idempotency_key.like(
                f"notify:{action_id}:%"
            )
        )

    if job_conditions:
        db.execute(
            delete(Job).where(
                or_(*job_conditions)
            )
        )

    if quote_ids or transaction_ids:
        hold_conditions = []

        if quote_ids:
            hold_conditions.append(
                InventoryHold.quote_id.in_(quote_ids)
            )

        if transaction_ids:
            hold_conditions.append(
                InventoryHold.transaction_id.in_(
                    transaction_ids
                )
            )

        db.execute(
            delete(InventoryHold).where(
                or_(*hold_conditions)
            )
        )

    if quote_ids or action_ids:
        itinerary_conditions = []

        if quote_ids:
            itinerary_conditions.append(
                ItineraryItem.quote_id.in_(quote_ids)
            )

        if action_ids:
            itinerary_conditions.append(
                ItineraryItem.action_id.in_(action_ids)
            )

        db.execute(
            delete(ItineraryItem).where(
                or_(*itinerary_conditions)
            )
        )

    if action_ids:
        db.execute(
            delete(ProviderEvent).where(
                ProviderEvent.action_id.in_(action_ids)
            )
        )

    if transaction_ids:
        db.execute(
            delete(ExternalTransaction).where(
                ExternalTransaction.id.in_(
                    transaction_ids
                )
            )
        )

    if action_ids:
        db.execute(
            delete(ActionEvent).where(
                ActionEvent.action_id.in_(action_ids)
            )
        )

        db.execute(
            delete(Action).where(
                Action.id.in_(action_ids)
            )
        )

    if quote_ids:
        db.execute(
            delete(Quote).where(
                Quote.id.in_(quote_ids)
            )
        )

    if context_ids:
        db.execute(
            delete(TransactionContext).where(
                TransactionContext.id.in_(context_ids)
            )
        )

    db.execute(
        delete(TripIssue).where(
            TripIssue.trip_id == trip_id,
        )
    )

    db.execute(
        delete(TripObservation).where(
            TripObservation.trip_id == trip_id,
        )
    )

    db.execute(
        delete(TripSourceRecord).where(
            TripSourceRecord.trip_id == trip_id,
        )
    )

    db.execute(
        delete(TripItem).where(
            TripItem.trip_id == trip_id,
        )
    )

    revoke_web_session(db, trip_id)

    db.execute(
        delete(Trip).where(
            Trip.id == trip_id,
        )
    )

    return True
