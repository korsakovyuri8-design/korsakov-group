"""Stay resolution and stay-scoped memory.

A Stay connects one Guest to one Property for one visit. Every
conversation belongs to a stay, so anything the guest tells us ("we are 2",
"arriving 20 Dec") is stored on that stay and cannot leak into the guest's
stay at another property or a later visit.

Two kinds of stay data are kept strictly apart:

* authoritative fields (booking_reference, arrival_at, departure_at,
  party_size, status) - written only by staff or an integration;
* `facts` - guest-stated, unconfirmed, written by the agent.

Global guest preferences (Guest.preferences) are a third, cross-stay bucket.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.memory import remember
from app.db.models import OPEN_STAY_STATUSES, Guest, Property, Stay, StayStatus

# An open stay whose verified departure is this far in the past is treated as
# finished, so a returning guest starts a fresh stay.
STALE_AFTER = timedelta(days=2)


class StayService:
    def __init__(self, session: Session) -> None:
        self.s = session

    def current_stay(self, guest: Guest, prop: Property, channel: str, now: datetime | None = None) -> Stay:
        now = now or datetime.now(timezone.utc)
        candidates = self.s.scalars(
            select(Stay)
            .where(Stay.guest_id == guest.id, Stay.property_id == prop.id, Stay.status.in_(OPEN_STAY_STATUSES))
            .order_by(Stay.created_at.desc())
        )
        for stay in candidates:
            departure = stay.departure_at
            if departure is not None and departure.tzinfo is None:
                departure = departure.replace(tzinfo=timezone.utc)
            if departure is None or departure + STALE_AFTER >= now:
                return stay
        stay = Stay(guest_id=guest.id, property_id=prop.id, status=StayStatus.INQUIRY,
                    source_channel=channel, facts={}, extra={})
        self.s.add(stay)
        self.s.flush()
        return stay

    @staticmethod
    def record_facts(stay: Stay, facts: dict[str, Any]) -> None:
        """Store guest statements as unconfirmed facts. Never touches the
        authoritative fields (party_size, arrival_at, ...)."""
        if facts:
            stay.facts = remember(stay.facts or {}, facts)

    @staticmethod
    def record_preference(guest: Guest, key: str, value: Any) -> None:
        prefs = dict(guest.preferences or {})
        if prefs.get(key, {}).get("value") != value:
            prefs[key] = {"value": value, "source": "observed",
                          "updated_at": datetime.now(timezone.utc).isoformat()}
            guest.preferences = prefs
