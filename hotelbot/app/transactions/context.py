"""Atlas-native ownership for external transactions.

A traveller can transact directly from an Atlas Trip without inventing
a Property, Stay or hotel booking.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import TransactionContext, Trip


def get_or_create_for_trip(
    session: Session,
    trip_id: str,
    *,
    timezone: str,
    locale: str | None = None,
    guest_id: str | None = None,
    channel: str = "atlas_web",
) -> TransactionContext:
    """Return the single transaction context owned by this Atlas Trip."""

    trip = session.get(Trip, trip_id)

    if trip is None:
        raise ValueError("unknown trip")

    existing = session.scalar(
        select(TransactionContext).where(
            TransactionContext.trip_id == trip_id,
        )
    )

    if existing is not None:
        # Never silently move a transaction context to another traveller.
        if (
            guest_id is not None
            and existing.guest_id is not None
            and existing.guest_id != guest_id
        ):
            raise ValueError(
                "transaction context belongs to another guest"
            )

        # These are runtime/presentation facts and may become more precise.
        existing.timezone = timezone

        if locale is not None:
            existing.locale = locale

        if (
            existing.guest_id is None
            and guest_id is not None
        ):
            existing.guest_id = guest_id

        session.flush()
        return existing

    context = TransactionContext(
        kind="atlas_trip",
        trip_id=trip.id,
        guest_id=guest_id,
        channel=channel,
        locale=locale,
        timezone=timezone,
        status="active",
        extra={
            "basis": "atlas_trip",
        },
    )

    session.add(context)
    session.flush()

    return context
