"""Provider discovery: which providers/offerings can fulfil THIS request.

Candidates come only from the provider registry (region-scoped providers
shared by many properties, plus a property's own partners) - never from a
model. Each offering is checked against the request:

    static fit   category, language, activity/specialty, format (private/group),
                 vehicle size, child seats, luggage, group size, minimum duration
    details      fields the offering itself requires (ski heights, rental days...)
    time         a concrete window (fixed-schedule offerings resolve a day-only
                 request to their scheduled start; others need a time)
    inventory    live capacity minus holds, per variant
    price        the offering's declared pricing (estimate; the provider quotes)

Property relationships shape the result: BLOCKED providers are dropped,
EXCLUSIVE ones are the only candidates for their services, DEFAULT /
PREFERRED (and a capability's declared provider) rank first. Ranking never
re-admits a candidate that failed a constraint. Nothing here needs a
property: a traveller without one gets the same discovery for a region.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import ExternalProvider, Offering, PropertyProvider, ProviderRelation
from app.marketplace import pricing, terms
from app.places import availability
from app.places.availability import NoAvailability, SlotMatch, most_specific


@dataclass
class Candidate:
    provider: ExternalProvider
    offering: Offering | None
    start: datetime | None = None
    end: datetime | None = None
    need: dict[str | None, int] = field(default_factory=dict)
    amount: Decimal | None = None
    currency: str | None = None
    missing: list[str] = field(default_factory=list)       # details the offering still needs
    resolved: dict[str, Any] = field(default_factory=dict)  # e.g. start_time from a fixed schedule
    terms: dict[str, Any] = field(default_factory=dict)
    rank: tuple = ()

    @property
    def format(self) -> str | None:
        return (self.offering.attributes or {}).get("format") if self.offering else None


@dataclass
class DiscoveryResult:
    candidates: list[Candidate]           # ready to quote, best first
    pending: list[Candidate]              # suitable so far, but need more details
    reason: str | None = None             # most specific reason when nothing fits
    alternatives: list[SlotMatch] = field(default_factory=list)


def relations(session: Session, property_id: str | None, service_type: str) -> dict[str, ProviderRelation]:
    if not property_id:
        return {}
    out = {}
    for rel in session.scalars(select(PropertyProvider).where(PropertyProvider.property_id == property_id)):
        services = (rel.services or {}).get("service_types")
        if not services or service_type in services:
            out[rel.provider_id] = rel.relation
    return out


def providers_in_scope(session: Session, service_type: str, *, property_id: str | None,
                       region: str | None) -> list[ExternalProvider]:
    scopes = []
    if property_id:
        scopes.append(ExternalProvider.property_id == property_id)
    if region:
        scopes.append(ExternalProvider.region == region)
    if not scopes:
        return []
    rows = session.scalars(select(ExternalProvider).where(or_(*scopes), ExternalProvider.active)
                           .order_by(ExternalProvider.slug))
    return [p for p in rows if service_type in (p.services or {}).get("service_types", [])]


def _static_misfit(offering: Offering, details: dict[str, Any], provider: ExternalProvider) -> str | None:
    attrs = offering.attributes or {}
    cats = attrs.get("categories")
    if details.get("category") and cats is not None and details["category"] not in cats:
        return "category_unsupported"
    langs = attrs.get("languages")
    if details.get("language") and langs and details["language"] not in langs:
        return "language_unavailable"
    specialties = attrs.get("specialties")
    if details.get("activity") and specialties is not None and details["activity"] not in specialties:
        return "activity_unavailable"
    if details.get("format") and attrs.get("format") and details["format"] != attrs["format"]:
        return "format_unavailable"
    if details.get("child_seats") and int(details["child_seats"]) > int(attrs.get("child_seats", 0)):
        return "no_child_seats"
    if details.get("luggage") and attrs.get("luggage_capacity") is not None and \
            int(details["luggage"]) > int(attrs["luggage_capacity"]):
        return "luggage_too_large"
    vehicle = (details.get("vehicle") or "").lower()
    if vehicle and attrs.get("vehicle_types") and not any(v in vehicle for v in attrs["vehicle_types"]):
        return "vehicle_unavailable"
    min_hours = {**(provider.policies or {}), **(offering.policies or {})}.get("min_duration_hours")
    if min_hours:
        requested = int(details.get("hours") or 0) or int(details.get("days") or 1) * 24
        if requested < int(min_hours):
            return "below_minimum_duration"
    if attrs.get("max_party") and attrs.get("unit") not in ("group", "vehicle") and \
            int(details.get("party_size") or 1) > int(attrs["max_party"]):
        return "party_too_large"
    try:
        availability.demand(offering, {**details, "heights": details.get("heights") or []})
    except NoAvailability as exc:
        if exc.reason not in ("size_unknown",):
            return exc.reason
    return None


def discover(session: Session, *, service_type: str, details: dict[str, Any], region: str | None,
             property_id: str | None, now: datetime, tz: str, declared: str | None = None,
             start: datetime | None = None, day: date | None = None,
             venue_id: str | None = None, replaces: str | None = None) -> DiscoveryResult:
    """`replaces`: the transaction a change would replace - its own inventory
    counts as free for the new offer (it is released if the change goes through)."""
    rel = relations(session, property_id, service_type)
    providers = [p for p in providers_in_scope(session, service_type, property_id=property_id, region=region)
                 if rel.get(p.id) != ProviderRelation.BLOCKED]
    exclusive = [p for p in providers if rel.get(p.id) == ProviderRelation.EXCLUSIVE]
    if exclusive:
        providers = exclusive

    ready: list[Candidate] = []
    pending: list[Candidate] = []
    reasons: list[str] = []
    alts: list[SlotMatch] = []
    for provider in providers:
        boost = 0 if (rel.get(provider.id) in (ProviderRelation.DEFAULT, ProviderRelation.PREFERRED,
                                               ProviderRelation.EXCLUSIVE) or provider.slug == declared) else 1
        q = select(Offering).where(Offering.provider_id == provider.id, Offering.service_type == service_type,
                                   Offering.active).order_by(Offering.slug)
        if venue_id:
            q = q.where(Offering.place_id == venue_id)
        offerings = list(session.scalars(q))
        if not offerings:
            if venue_id is None:   # provider without catalogued offerings: it prices and decides itself
                ready.append(Candidate(provider, None, start=start, rank=(boost, 1, Decimal(0), provider.slug)))
            continue
        for offering in offerings:
            cand = _evaluate(session, provider, offering, details, now=now, tz=tz, start=start, day=day,
                             reasons=reasons, alts=alts, replaces=replaces)
            if cand is None:
                continue
            cand.rank = (boost, 0, cand.amount if cand.amount is not None else Decimal("1e9"), provider.slug,
                         offering.slug)
            (pending if cand.missing else ready).append(cand)
    ready.sort(key=lambda c: c.rank)
    pending.sort(key=lambda c: c.rank)
    reason = None if (ready or pending) else most_specific(reasons or ["not_offered"])
    alts.sort(key=lambda m: abs(m.starts_at - start) if (m.starts_at and start) else 0)
    return DiscoveryResult(ready, pending, reason, _dedupe(alts)[:3])


def _dedupe(alts: list[SlotMatch]) -> list[SlotMatch]:
    seen, out = set(), []
    for m in alts:
        key = (m.offering.id, m.starts_at)
        if key not in seen:
            seen.add(key)
            out.append(m)
    return out


def _evaluate(session: Session, provider: ExternalProvider, offering: Offering, details: dict[str, Any], *,
              now: datetime, tz: str, start: datetime | None, day: date | None, reasons: list[str],
              alts: list[SlotMatch], replaces: str | None = None) -> Candidate | None:
    attrs = offering.attributes or {}
    misfit = _static_misfit(offering, details, provider)
    if misfit:
        reasons.append(misfit)
        return None
    cand = Candidate(provider, offering, currency=offering.currency or (provider.config or {}).get("currency"),
                     terms=terms.merged(provider.policies, offering.policies, attrs))
    cand.missing = [f for f in attrs.get("requires", []) if details.get(f) in (None, [], "")]
    if attrs.get("variants") and details.get("heights") and \
            len(details["heights"]) < int(details.get("quantity") or 1):
        cand.missing.append("heights")
    if cand.missing:
        return cand
    if not availability.has_inventory(session, offering):
        if start is None:
            cand.missing = ["start_time"]
            return cand
        cand.start = start
    else:
        w = availability.window(session, offering, start, day, days=int(details.get("days") or 1),
                                hours=int(details["hours"]) if details.get("hours") else None, tz=tz)
        need = availability.demand(offering, details)
        if w is None:
            if start is None and day is not None and not attrs.get("fixed_start"):
                cand.missing = ["start_time"]       # e.g. a taxi needs an exact time
                return cand
            reasons.append("not_offered_at_that_time")
            near = start or datetime.combine(day, datetime.min.time(), tzinfo=now.tzinfo) if (start or day) else now
            alts.extend(availability.alternatives(session, offering, near, need, now, ignore_transaction=replaces))
            return None
        s, e, _slot = w
        problem = availability.check(session, offering, s, e, need, now, replaces)
        if problem:
            reasons.append(problem)
            alts.extend(availability.alternatives(session, offering, s, need, now, ignore_transaction=replaces))
            return None
        cand.start, cand.end, cand.need = s, e, need
        if start is None or (attrs.get("fixed_start") and s != start):
            # The offering runs on a schedule: quote ITS start time (shown to the
            # guest), never the time they guessed. Local, like guest-given times.
            cand.resolved["start_time"] = s.astimezone(ZoneInfo(tz)).isoformat()
    priced = {**details}
    if cand.start is not None and "pickup_time" in details:
        priced["pickup_time"] = details["pickup_time"]
    cand.amount = pricing.compute(offering.pricing, priced, tz=tz)
    return cand


def options(result: DiscoveryResult, *, by_format: bool) -> list[Candidate]:
    """What to put in front of the guest: the best candidate, plus the best
    of each other format (private vs group tour) when the guest has not
    chosen one - so they compare real offers, not descriptions."""
    if not result.candidates:
        return []
    if not by_format:
        return [result.candidates[0]]
    out, seen = [], set()
    for c in result.candidates:
        if c.format not in seen:
            seen.add(c.format)
            out.append(c)
    return out[:2]
