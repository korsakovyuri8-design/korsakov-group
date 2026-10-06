"""The ONLY seam between the two worlds.

    LOCAL WORLD (discovery)            TRANSACTION WORLD (execution)
    Place / Venue / Event        ──►   ExternalProvider / Offering / Quote / Transaction
    "where / what's open / near"       "quote / book / order / change / cancel"

A real business may live in both (a restaurant is a Place AND may have a
table-reservation Offering); most of the local world never transacts (a
pharmacy, an ATM, a viewpoint), and some transactions have no Place (a
taxi). Discovery code never queries offerings, and transaction code never
queries places; when one needs the other, it asks here.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.capabilities.registry import CapabilityRegistry
from app.db.models import Event, Offering, Place


@dataclass(frozen=True)
class ExecutionOption:
    """Something the guest could transact about a place or event."""

    offering: Offering
    service_type: str


def options_for(session: Session, *, place: Place | None = None, event: Event | None = None,
                capabilities: CapabilityRegistry | None = None) -> list[ExecutionOption]:
    """Transactable offerings for a place or an event: active, sold through a
    provider, and enabled by this property's transaction capabilities (no
    property = any)."""
    conds = []
    if place is not None:
        conds.append(Offering.place_id == place.id)
    if event is not None:
        conds.append(Offering.event_id == event.id)
    if not conds:
        return []
    rows = session.scalars(select(Offering).where(or_(*conds), Offering.active, Offering.provider_id.is_not(None))
                           .order_by(Offering.slug))
    return [ExecutionOption(o, o.service_type) for o in rows
            if capabilities is None or capabilities.service(o.service_type) is not None]
