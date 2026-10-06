"""Trip plan (itinerary) for a stay.

Items are lightweight: SAVED / SHORTLISTED / PROPOSED / DISMISSED for things
that are not transactions (a bar to remember, a restaurant shortlist). Items
linked to a quote or action never store a status of their own - their
effective status is read from the quote/action, so the plan can never
disagree with the transaction state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import as_utc
from app.db.models import Action, ActionStatus, ItemStatus, ItineraryItem, Quote, QuoteStatus


@dataclass(frozen=True)
class PlanEntry:
    item: ItineraryItem
    status: str          # saved | shortlisted | proposed | offered | accepted | submitted | ... (from source)
    starts_at: datetime | None


def add(session: Session, *, stay_id: str, kind: str, title: str, status: ItemStatus = ItemStatus.SAVED,
        starts_at: datetime | None = None, place_id: str | None = None, event_id: str | None = None,
        offering_id: str | None = None, quote_id: str | None = None, action_id: str | None = None,
        details: dict | None = None) -> ItineraryItem:
    if starts_at is not None and starts_at.tzinfo is not None:
        starts_at = starts_at.astimezone(timezone.utc)   # stored as UTC (SQLite drops offsets)
    item = ItineraryItem(stay_id=stay_id, kind=kind, title=title[:300], status=status, starts_at=starts_at,
                         place_id=place_id, event_id=event_id, offering_id=offering_id, quote_id=quote_id,
                         action_id=action_id, details=details or {})
    session.add(item)
    session.flush()
    return item


def link_quote(session: Session, *, stay_id: str, kind: str, title: str, quote: Quote,
               starts_at: datetime | None) -> ItineraryItem:
    """One plan item per service: a new/updated offer for the same service
    replaces the previous offer on the same item."""
    for item in session.scalars(select(ItineraryItem).where(ItineraryItem.stay_id == stay_id,
                                                            ItineraryItem.action_id.is_(None),
                                                            ItineraryItem.quote_id.is_not(None))):
        old = session.get(Quote, item.quote_id)
        if old is not None and old.service_type == quote.service_type and old.status in (
                QuoteStatus.OFFERED, QuoteStatus.SUPERSEDED, QuoteStatus.EXPIRED):
            if starts_at is not None and starts_at.tzinfo is not None:
                starts_at = starts_at.astimezone(timezone.utc)
            item.quote_id, item.title, item.starts_at = quote.id, title[:300], starts_at
            return item
    return add(session, stay_id=stay_id, kind=kind, title=title, status=ItemStatus.PROPOSED,
               starts_at=starts_at, quote_id=quote.id)


def link_action(session: Session, quote_id: str, action_id: str) -> None:
    for item in session.scalars(select(ItineraryItem).where(ItineraryItem.quote_id == quote_id)):
        item.action_id = action_id


def effective_status(session: Session, item: ItineraryItem) -> str:
    if item.action_id:
        action = session.get(Action, item.action_id)
        if action is None:
            return "unknown"
        if action.executor == "provider" and action.status == ActionStatus.PROPOSED:
            return "accepted_by_guest"   # consent given, being sent to the provider
        return action.status.value
    if item.quote_id:
        quote = session.get(Quote, item.quote_id)
        return quote.status.value if quote else "unknown"
    return item.status.value


def plan(session: Session, stay_id: str, *, include_dismissed: bool = False) -> list[PlanEntry]:
    rows = session.scalars(select(ItineraryItem).where(ItineraryItem.stay_id == stay_id))
    out = []
    for item in rows:
        status = effective_status(session, item)
        if not include_dismissed and status in ("dismissed", "declined_by_guest", "superseded"):
            continue
        out.append(PlanEntry(item, status, as_utc(item.starts_at) if item.starts_at else None))
    out.sort(key=lambda e: (e.starts_at is None, e.starts_at or datetime.max, e.item.created_at))
    return out


class StayPlanHooks:
    """The transaction dialogue's PlanHooks: offers and bookings appear in
    the stay plan with their status read from the source."""

    def offered(self, turn, quote: Quote, *, kind: str, title: str, starts_at: datetime | None) -> None:  # noqa: ANN001
        link_quote(turn.session, stay_id=turn.stay.id, kind=kind, title=title, quote=quote, starts_at=starts_at)

    def booked(self, turn, quote: Quote, action_id: str) -> None:  # noqa: ANN001
        link_action(turn.session, quote.id, action_id)
