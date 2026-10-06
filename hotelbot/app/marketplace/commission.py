"""Commission accounting: only confirmed or completed operations count.

A quote stores a POTENTIAL commission snapshot (pricing.commission). Nothing
is earned at quote time, on guest consent, on submission, on a provisional
(weather-dependent) acceptance or when the outcome is unknown. A booking
that is later cancelled or rejected earns nothing.

The numbers in the demo data are synthetic placeholders, not a market model.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Action, ActionStatus, ExternalTransaction, Quote

EARNING = (ActionStatus.ACCEPTED, ActionStatus.COMPLETED)


@dataclass(frozen=True)
class Earned:
    quote_id: str
    service_type: str
    status: str
    amount: Decimal


def earned(session: Session, *, property_id: str | None = None) -> list[Earned]:
    stmt = (select(ExternalTransaction, Action, Quote)
            .join(Action, ExternalTransaction.action_id == Action.id)
            .join(Quote, ExternalTransaction.quote_id == Quote.id)
            .where(Action.status.in_(EARNING)))
    if property_id:
        stmt = stmt.where(Quote.property_id == property_id)
    out = []
    for _txn, action, quote in session.execute(stmt):
        value = (quote.commercial or {}).get("commission")
        if value is not None:
            out.append(Earned(quote.id, quote.service_type, action.status.value, Decimal(value)))
    return out


def total(session: Session, *, property_id: str | None = None) -> Decimal:
    return sum((e.amount for e in earned(session, property_id=property_id)), Decimal("0"))
