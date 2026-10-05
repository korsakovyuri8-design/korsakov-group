"""Inventory checks for offerings (tables, ski sets, guides...).

The bot never claims availability that is not recorded as an
AvailabilitySlot (deterministic demo inventory / provider-synchronised data)
or returned by a provider. If an offering has modelled inventory and nothing
fits, the guest gets the nearest real alternatives - never a fake slot.

Units: by default a slot's capacity counts people (seats, ski sets). With
attributes.unit == "group" it counts whole bookings (a guide takes one
group, up to offering.attributes.max_party people).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import as_utc
from app.db.models import AvailabilitySlot, Offering

SEARCH_WINDOW = timedelta(days=2)


@dataclass
class SlotMatch:
    offering: Offering
    slot: AvailabilitySlot


@dataclass
class NoAvailability(Exception):
    reason: str
    alternatives: list[SlotMatch] = field(default_factory=list)


def _units(slot: AvailabilitySlot, party: int) -> int:
    return 1 if (slot.attributes or {}).get("unit") == "group" else party


def fits(offering: Offering, slot: AvailabilitySlot, party: int, language: str | None) -> bool:
    attrs = offering.attributes or {}
    if language and language not in attrs.get("languages", [language]):
        return False
    if (slot.attributes or {}).get("unit") == "group" and party > int(attrs.get("max_party", 99)):
        return False
    return slot.remaining >= _units(slot, party)


def offerings_for(session: Session, provider_id: str, service_type: str, place_id: str | None = None) -> list[Offering]:
    q = select(Offering).where(Offering.provider_id == provider_id, Offering.service_type == service_type,
                               Offering.active).order_by(Offering.slug)
    if place_id:
        q = q.where(Offering.place_id == place_id)
    return list(session.scalars(q))


def find_slot(session: Session, offerings: list[Offering], at: datetime, party: int,
              language: str | None = None) -> SlotMatch | None:
    """A fitting slot, None when no inventory is modelled (the provider
    decides), or NoAvailability with real alternatives."""
    if not offerings:
        return None
    at = as_utc(at)
    ids = {o.id: o for o in offerings}
    slots = list(session.scalars(select(AvailabilitySlot).where(AvailabilitySlot.offering_id.in_(ids))))
    if not slots:
        return None
    for o in offerings:
        for s in slots:
            if s.offering_id == o.id and as_utc(s.starts_at) <= at < as_utc(s.ends_at) and fits(o, s, party, language):
                return SlotMatch(o, s)
    nearby = sorted(
        (SlotMatch(ids[s.offering_id], s) for s in slots
         if abs(as_utc(s.starts_at) - at) <= SEARCH_WINDOW and as_utc(s.ends_at) > at - SEARCH_WINDOW
         and fits(ids[s.offering_id], s, party, language)),
        key=lambda m: abs(as_utc(m.slot.starts_at) - at))
    covering = [s for s in slots if as_utc(s.starts_at) <= at < as_utc(s.ends_at)]
    reason = "no_capacity" if covering else "not_offered_at_that_time"
    speaking = [s for s in covering if not language
                or language in (ids[s.offering_id].attributes or {}).get("languages", [language])]
    if speaking and all(s.remaining >= 1 and (s.attributes or {}).get("unit") == "group"
                        and party > int((ids[s.offering_id].attributes or {}).get("max_party", 99))
                        for s in speaking):
        reason = "party_too_large"
    if language and not any(language in (o.attributes or {}).get("languages", [language]) for o in offerings):
        reason = "language_unavailable"
    raise NoAvailability(reason, nearby[:3])


def hold(slot: AvailabilitySlot, party: int) -> int:
    units = _units(slot, party)
    if slot.remaining < units:
        raise NoAvailability("no_capacity")
    slot.remaining -= units
    return units


def release(session: Session, slot_id: str, units: int) -> None:
    slot = session.get(AvailabilitySlot, slot_id)
    if slot is not None:
        slot.remaining = min(slot.capacity, slot.remaining + units)
