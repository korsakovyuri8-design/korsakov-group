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

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.capabilities.registry import CapabilityRegistry
from app.db.models import CanonicalEntity, Event, ExternalProvider, MarketplaceLink, Offering, Place


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
    # Explicit identity (entity_marketplace_links, set at ingest or by partner
    # mapping) - including entities later merged into this one. Never by name.
    canonicals = _identity_closure(session, [t.canonical_entity_id for t in (place, event)
                                             if t is not None and t.canonical_entity_id])
    if canonicals:
        linked = select(MarketplaceLink.offering_id).where(
            MarketplaceLink.canonical_entity_id.in_(canonicals), MarketplaceLink.active,
            MarketplaceLink.offering_id.is_not(None))
        conds.append(Offering.id.in_(linked))
    rows = session.scalars(select(Offering).where(or_(*conds), Offering.active, Offering.provider_id.is_not(None))
                           .order_by(Offering.slug))
    return [ExecutionOption(o, o.service_type) for o in rows
            if capabilities is None or capabilities.service(o.service_type) is not None]


def _identity_closure(session: Session, canonical_ids: list[str]) -> set[str]:
    """These entities plus every entity merged into them (transitively)."""
    out, frontier = set(canonical_ids), list(canonical_ids)
    while frontier:
        merged = set(session.scalars(select(CanonicalEntity.id).where(CanonicalEntity.merged_into_id.in_(frontier))))
        frontier = list(merged - out)
        out |= merged
    return out


def ranking_signals(session: Session, *, property_id: str | None,
                    capabilities: CapabilityRegistry | None = None) -> Callable[[Place], tuple[int, float]]:
    """Discovery's commercial inputs, computed on the transaction side:
    (relationship rank, potential commission) per place. The engine uses
    them ONLY as the last tie-breakers, after every hard constraint and
    every guest-facing criterion. Commission is a percentage of a future
    completed booking; nothing is earned or recorded here.

    preferred / exclusive (explicit property choice) -> 0; anything else -> 1.
    A blocked provider's offerings never count."""
    from app.db.models import ProviderRelation
    from app.marketplace.discovery import relations

    cache: dict[str, tuple[int, float]] = {}

    def signals(place: Place) -> tuple[int, float]:
        if place.id in cache:
            return cache[place.id]
        rank, commission = 1, 0.0
        for opt in options_for(session, place=place, capabilities=capabilities):
            rel = relations(session, property_id, opt.service_type).get(opt.offering.provider_id)
            if rel == ProviderRelation.BLOCKED:
                continue
            if rel in (ProviderRelation.PREFERRED, ProviderRelation.EXCLUSIVE):
                rank = 0
            provider = session.get(ExternalProvider, opt.offering.provider_id)
            ctype = opt.offering.commission_type or (provider.commission_type if provider else None)
            value = opt.offering.commission_value if opt.offering.commission_value is not None else (
                provider.commission_value if provider else None)
            if ctype == "percent" and value is not None:
                commission = max(commission, float(value))
        cache[place.id] = (rank, commission)
        return cache[place.id]

    return signals
