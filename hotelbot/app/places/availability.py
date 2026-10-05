"""Inventory: what an offering can still sell for a time window.

    available(offering, variant, window) = slot capacity - live holds

* Capacity comes only from AvailabilitySlot rows (deterministic demo
  inventory today, provider-synchronised later). Without modelled slots the
  provider decides at quote/submit time - the bot never invents capacity.
* A hold is created when a QUOTE is made (expires with the quote), becomes
  CONFIRMED on consent and is RELEASED when the quote expires/is declined or
  the booking is rejected/cancelled/failed. An expired hold stops counting
  automatically - no sweeper needed.
* Holds are created under a row lock on the offering (SELECT ... FOR UPDATE
  on PostgreSQL), so two guests can never both get the last item.

Units (offering.attributes.unit): "person" (party size, default), "item"
(quantity, optionally per variant such as ski length), "group" / "vehicle"
(one per booking, limited by max_party / max_passengers).
Window (offering.attributes.window): "slot" (default: the slot that contains
the requested start; multi-day rentals take one slot per day) or "duration"
(start + duration_minutes, e.g. a transfer).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.clock import as_utc
from app.db.models import AvailabilitySlot, HoldStatus, InventoryHold, Offering

SEARCH_WINDOW = timedelta(days=2)


@dataclass
class SlotMatch:
    """A concrete window an offering can sell (kept name: Iteration 3 API)."""

    offering: Offering
    slot: AvailabilitySlot
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    variants: dict[str | None, int] = field(default_factory=dict)


@dataclass
class NoAvailability(Exception):
    reason: str
    alternatives: list[SlotMatch] = field(default_factory=list)


# ------------------------------------------------------------------ helpers
def unit_of(offering: Offering) -> str:
    return (offering.attributes or {}).get("unit", "person")


def demand(offering: Offering, details: dict[str, Any]) -> dict[str | None, int]:
    """Units needed per variant. Raises NoAvailability for structural misfits."""
    attrs = offering.attributes or {}
    unit = unit_of(offering)
    party = int(details.get("party_size") or 1)
    if unit in ("group", "vehicle", "booking"):
        limit = attrs.get("max_party") or attrs.get("max_passengers")
        if limit and party > int(limit):
            raise NoAvailability("party_too_large" if unit == "group" else "vehicle_too_small")
        return {None: 1}
    if unit == "item":
        qty = int(details.get("quantity") or 1)
        rule = attrs.get("variants")
        heights = details.get("heights") or []
        if rule and heights:
            sizes = Counter(variant_for(rule, h) for h in heights[:qty])
            if None in sizes:
                raise NoAvailability("size_unavailable")
            missing = qty - sum(sizes.values())
            if missing > 0:     # fewer heights than items: cannot size the rest
                raise NoAvailability("size_unknown")
            return dict(sizes)
        return {None: qty}
    return {None: party}


def variant_for(rule: dict[str, Any], height: int) -> str | None:
    for lo, hi, variant in rule.get("bands", []):
        if lo <= height < hi:
            return str(variant)
    return None


def _slots(session: Session, offering: Offering, start: datetime, end: datetime,
           variant: str | None) -> list[AvailabilitySlot]:
    rows = session.scalars(select(AvailabilitySlot).where(
        AvailabilitySlot.offering_id == offering.id, AvailabilitySlot.starts_at < end,
        AvailabilitySlot.ends_at > start).order_by(AvailabilitySlot.starts_at))
    return [s for s in rows if (s.attributes or {}).get("variant") in (variant, None)
            and ((s.attributes or {}).get("variant") is None) == (variant is None)]


def has_inventory(session: Session, offering: Offering) -> bool:
    return session.scalar(select(AvailabilitySlot.id).where(AvailabilitySlot.offering_id == offering.id)
                          .limit(1)) is not None


def used(session: Session, offering_id: str, variant: str | None, start: datetime, end: datetime,
         now: datetime, ignore_transaction: str | None = None) -> int:
    rows = session.scalars(select(InventoryHold).where(
        InventoryHold.offering_id == offering_id, InventoryHold.starts_at < end, InventoryHold.ends_at > start,
        or_(InventoryHold.status == HoldStatus.CONFIRMED,
            (InventoryHold.status == HoldStatus.HELD) & (InventoryHold.expires_at > now))))
    return sum(h.quantity for h in rows if h.variant == variant
               and not (ignore_transaction and h.transaction_id == ignore_transaction))


def window(session: Session, offering: Offering, start: datetime | None, day: Any = None, *, days: int = 1,
           hours: int | None = None, tz: str = "UTC") -> tuple[datetime, datetime, AvailabilitySlot | None] | None:
    """The concrete window an offering would sell for this request. A
    day-only request resolves to the offering's scheduled start that day
    only for fixed-schedule offerings (day rentals, fixed tours)."""
    attrs = offering.attributes or {}
    mode = attrs.get("window", "slot")
    if mode == "duration":
        if start is None:
            return None
        minutes = int(attrs.get("duration_minutes") or 60) if not hours else hours * 60
        return start, start + timedelta(minutes=minutes), None
    if start is None:
        if day is None or not attrs.get("fixed_start"):
            return None
        rows = [s for s in session.scalars(select(AvailabilitySlot).where(AvailabilitySlot.offering_id == offering.id)
                                           .order_by(AvailabilitySlot.starts_at))
                if as_utc(s.starts_at).astimezone(ZoneInfo(tz)).date() == day]
        if not rows:
            return None
        slot = rows[0]
    else:
        slot = session.scalar(select(AvailabilitySlot).where(
            AvailabilitySlot.offering_id == offering.id, AvailabilitySlot.starts_at <= as_utc(start),
            AvailabilitySlot.ends_at > as_utc(start)).order_by(AvailabilitySlot.starts_at).limit(1))
        if slot is None:
            if hours:   # hourly offering: the window is start + hours, inside opening slots
                return as_utc(start), as_utc(start) + timedelta(hours=hours), None
            return None
    s, e = as_utc(slot.starts_at), as_utc(slot.ends_at)
    if hours and start is not None:
        s, e = as_utc(start), as_utc(start) + timedelta(hours=hours)
    if days > 1:
        e = e + timedelta(days=days - 1)
    return s, e, slot


def check(session: Session, offering: Offering, start: datetime, end: datetime, need: dict[str | None, int],
          now: datetime, ignore_transaction: str | None = None) -> str | None:
    """None if the window is sellable for `need`, else a reason."""
    for variant, qty in need.items():
        slots = _slots(session, offering, start, end, variant)
        if not slots:
            return "size_unavailable" if variant is not None and _any_slots(session, offering, start, end) \
                else "not_offered_at_that_time"
        if not _covers(slots, start, end):
            return "not_offered_at_that_time"
        cap = min(s.capacity for s in slots)
        if cap - used(session, offering.id, variant, start, end, now, ignore_transaction) < qty:
            return "no_capacity"
    return None


def _any_slots(session: Session, offering: Offering, start: datetime, end: datetime) -> bool:
    return session.scalar(select(AvailabilitySlot.id).where(
        AvailabilitySlot.offering_id == offering.id, AvailabilitySlot.starts_at < end,
        AvailabilitySlot.ends_at > start).limit(1)) is not None


def _dates(start: datetime, end: datetime) -> set:
    last = (end - timedelta(seconds=1)).date()
    out, d = set(), start.date()
    while d <= last:
        out.add(d)
        d += timedelta(days=1)
    return out


def _covers(slots: list[AvailabilitySlot], start: datetime, end: datetime) -> bool:
    """Every calendar day of the window has an overlapping slot (a shop is
    closed overnight; a 2-day rental needs a slot on both days)."""
    have: set = set()
    for s in slots:
        have |= _dates(as_utc(s.starts_at), as_utc(s.ends_at))
    return _dates(start, end) <= have


def alternatives(session: Session, offering: Offering, near: datetime, need: dict[str | None, int], now: datetime,
                 limit: int = 3, ignore_transaction: str | None = None) -> list[SlotMatch]:
    attrs = offering.attributes or {}
    if attrs.get("window") == "duration":
        # Timed services (a transfer at 07:00): nearby start times, not slot starts.
        length = timedelta(minutes=int(attrs.get("duration_minutes") or 60))
        out = []
        for delta_h in (-1, 1, -2, 2, -3, 3, 24, -24):
            s = as_utc(near) + timedelta(hours=delta_h)
            if s <= now:
                continue
            if check(session, offering, s, s + length, need, now, ignore_transaction) is None:
                slot = session.scalar(select(AvailabilitySlot).where(
                    AvailabilitySlot.offering_id == offering.id, AvailabilitySlot.starts_at <= s,
                    AvailabilitySlot.ends_at > s).limit(1))
                if slot is not None:
                    out.append(SlotMatch(offering, slot, s, s + length, need))
            if len(out) >= limit:
                break
        return out
    rows = session.scalars(select(AvailabilitySlot).where(
        AvailabilitySlot.offering_id == offering.id,
        AvailabilitySlot.starts_at >= as_utc(near) - SEARCH_WINDOW,
        AvailabilitySlot.starts_at <= as_utc(near) + SEARCH_WINDOW))
    out: list[SlotMatch] = []
    seen = set()
    for slot in sorted(rows, key=lambda s: abs(as_utc(s.starts_at) - as_utc(near))):
        key = as_utc(slot.starts_at)
        if key in seen or as_utc(slot.starts_at) <= now:
            continue
        s, e = as_utc(slot.starts_at), as_utc(slot.ends_at)
        if check(session, offering, s, e, need, now, ignore_transaction) is None:
            seen.add(key)
            out.append(SlotMatch(offering, slot, s, e, need))
        if len(out) >= limit:
            break
    return out


# -------------------------------------------------------------------- holds
def lock(session: Session, offering: Offering) -> None:
    """Serialise hold creation per offering (no-op on SQLite, which
    serialises writers anyway)."""
    if session.get_bind().dialect.name == "postgresql":
        session.execute(select(Offering.id).where(Offering.id == offering.id).with_for_update())


def hold(session: Session, offering: Offering, start: datetime, end: datetime, need: dict[str | None, int], *,
         now: datetime, expires_at: datetime, quote_id: str | None = None,
         ignore_transaction: str | None = None) -> list[InventoryHold]:
    lock(session, offering)
    reason = check(session, offering, start, end, need, now, ignore_transaction)
    if reason is not None:
        raise NoAvailability(reason if reason != "no_capacity" else "slot_gone")
    holds = []
    for variant, qty in need.items():
        h = InventoryHold(offering_id=offering.id, variant=variant, starts_at=start, ends_at=end, quantity=qty,
                          status=HoldStatus.HELD, expires_at=expires_at, quote_id=quote_id, created_at=now)
        session.add(h)
        holds.append(h)
    session.flush()
    return holds


def holds_for_quote(session: Session, quote_id: str) -> list[InventoryHold]:
    return list(session.scalars(select(InventoryHold).where(InventoryHold.quote_id == quote_id)))


def confirm(session: Session, quote_id: str, transaction_id: str, now: datetime) -> bool:
    """Turn a quote's live holds into confirmed ones. False if they lapsed
    (the quote expired) - the caller must re-check availability."""
    holds = holds_for_quote(session, quote_id)
    if any(h.status != HoldStatus.HELD or (h.expires_at and as_utc(h.expires_at) <= now) for h in holds):
        return False
    for h in holds:
        h.status, h.expires_at, h.transaction_id = HoldStatus.CONFIRMED, None, transaction_id
    return True


def release_quote(session: Session, quote_id: str) -> None:
    for h in holds_for_quote(session, quote_id):
        if h.status == HoldStatus.HELD:
            h.status = HoldStatus.RELEASED


def release_transaction(session: Session, transaction_id: str) -> None:
    for h in session.scalars(select(InventoryHold).where(InventoryHold.transaction_id == transaction_id)):
        h.status = HoldStatus.RELEASED


# ---------------------------------------------- Iteration 3 compatibility API
def offerings_for(session: Session, provider_id: str, service_type: str, place_id: str | None = None) -> list[Offering]:
    q = select(Offering).where(Offering.provider_id == provider_id, Offering.service_type == service_type,
                               Offering.active).order_by(Offering.slug)
    if place_id:
        q = q.where(Offering.place_id == place_id)
    return list(session.scalars(q))


def find_slot(session: Session, offerings: list[Offering], at: datetime, party: int,
              language: str | None = None, *, now: datetime | None = None,
              details: dict[str, Any] | None = None) -> SlotMatch | None:
    """First offering that can sell `at` for this party (None when no
    inventory is modelled: the provider decides), or NoAvailability with
    real alternatives and the most specific reason."""
    now = now or datetime.now(tz=at.tzinfo)
    details = {"party_size": party, "language": language, **(details or {})}
    modelled = [o for o in offerings if has_inventory(session, o)]
    if not modelled:
        return None
    reasons: list[str] = []
    alts: list[SlotMatch] = []
    for o in modelled:
        langs = (o.attributes or {}).get("languages")
        if language and langs and language not in langs:
            reasons.append("language_unavailable")
            continue
        try:
            need = demand(o, details)
        except NoAvailability as exc:
            reasons.append(exc.reason)
            continue
        w = window(session, o, as_utc(at))
        if w is None:
            reasons.append("not_offered_at_that_time")
            alts += alternatives(session, o, at, need, now)
            continue
        start, end, slot = w
        reason = check(session, o, start, end, need, now)
        if reason is None:
            return SlotMatch(o, slot, start, end, need)   # type: ignore[arg-type]
        reasons.append(reason)
        alts += alternatives(session, o, at, need, now)
    alts.sort(key=lambda m: abs(as_utc(m.slot.starts_at) - as_utc(at)))
    raise NoAvailability(most_specific(reasons), alts[:3])


# Later stages explain more ("it is full") than earlier ones ("nobody speaks it").
_REASON_ORDER = ["no_capacity", "slot_gone", "size_unavailable", "size_unknown", "party_too_large",
                 "vehicle_too_small", "no_child_seats", "luggage_too_large", "below_minimum_duration",
                 "not_offered_at_that_time", "activity_unavailable", "format_unavailable", "language_unavailable",
                 "category_unsupported", "not_offered"]


def most_specific(reasons: list[str]) -> str:
    if not reasons:
        return "not_offered"
    return min(reasons, key=lambda r: _REASON_ORDER.index(r) if r in _REASON_ORDER else len(_REASON_ORDER))
