"""Region pack: the local world around (or without) a property.

    region: {slug, name, timezone, center: {lat, lon}, source, synthetic, verified_at}
    providers: [ProviderSpec ...]          # region-scoped (usable by any property / traveller)
    places:    [{slug, name, subcategory, category?, tags, description, attributes,
                 location: {lat, lon, address}, hours, source?, last_verified_at?, confidence?, provider_owned?}]
    events:    [{slug, title, category, tags, place?, start, end?, ticket_required, ticket_price?, currency?, ...}]
    offerings: [{slug, service_type, title, place?, provider, attributes, price_from?, currency?,
                 availability: [{start, end, capacity, attributes?}],
                 recurring:    [{days: [sat, sun], start: "09:00", end: "13:00", from: date, to: date,
                                 capacity, attributes?}]}]

Times in the pack are local to the region's timezone. Ingestion is
idempotent (upsert by slug; records missing from the pack are deactivated
or removed; availability is regenerated but never below what is already
held by accepted bookings).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml  # noqa: F401
from functools import lru_cache

from app.yamlio import yaml_load
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.db.models import AvailabilitySlot, Event, ExternalProvider, Offering, Place
from app.knowledge.schemas import ProviderSpec
from app.observability import log_event
from app.places.hours import DAYS
from app.knowledge.ingest import apply_marketplace
from app.places.taxonomy import Category
from app.transactions.catalog import SERVICE_CATALOG


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegionInfo(_M):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]*$")
    name: str
    timezone: str
    center: dict[str, float]
    source: str
    synthetic: bool = False
    verified_at: date | None = None


class Location(_M):
    lat: float
    lon: float
    address: str | None = None


class PlaceSpec(_M):
    slug: str
    name: str
    subcategory: str
    category: Category | None = None          # derived from the taxonomy when omitted
    tags: list[str] = Field(default_factory=list)
    description: dict[str, str] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)
    location: Location | None = None
    service_area_km: float | None = None
    timezone: str | None = None
    phone: str | None = None
    website: str | None = None
    price_range: int | None = Field(default=None, ge=1, le=4)
    # Per dynamic fact: {hours: date, kitchen: date, prices: date, closure: date}.
    verification: dict[str, date] = Field(default_factory=dict)
    hours: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None
    last_verified_at: date | None = None
    confidence: float = 1.0
    provider_owned: bool = False


class EventSpec(_M):
    slug: str
    title: dict[str, str]
    category: str
    tags: list[str] = Field(default_factory=list)
    place: str | None = None
    start: datetime
    end: datetime | None = None
    ticket_required: bool | None = None        # None = the source does not say
    ticket_price: Decimal | None = None
    currency: str | None = None
    age_limit: int | None = None
    language: str | None = None
    booking_source: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    description: dict[str, str] = Field(default_factory=dict)
    location: Location | None = None
    source: str | None = None
    last_verified_at: date | None = None
    confidence: float = 1.0


class TaxonomySpec(_M):
    """A subcategory this source introduces - the taxonomy grows by data."""

    key: str = Field(pattern=r"^[a-z0-9_]+$")
    category: str
    labels: dict[str, str]
    keywords: list[str] = Field(default_factory=list)


class SlotSpec(_M):
    start: datetime
    end: datetime
    capacity: int = Field(ge=0)
    attributes: dict[str, Any] = Field(default_factory=dict)


class RecurringSpec(_M):
    days: list[str]
    start: str
    end: str
    capacity: int = Field(ge=0, default=0)
    # Sized inventory: {"170": 3, "180": 2} = one slot per variant with that capacity.
    variants: dict[str, int] | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    from_: date = Field(alias="from")
    to: date

    @field_validator("days")
    @classmethod
    def _days(cls, v: list[str]) -> list[str]:
        bad = [d for d in v if d not in DAYS]
        if bad:
            raise ValueError(f"unknown days {bad}")
        return v


class OfferingSpec(_M):
    slug: str
    service_type: str
    title: dict[str, str]
    place: str | None = None
    provider: str
    event: str | None = None                   # ticket for this event (slug)
    attributes: dict[str, Any] = Field(default_factory=dict)
    price_from: Decimal | None = None
    currency: str | None = None
    pricing: dict[str, Any] = Field(default_factory=dict)      # app/marketplace/pricing.py
    policies: dict[str, Any] = Field(default_factory=dict)     # app/marketplace/terms.py (overrides provider's)
    commission_type: str | None = None
    commission_value: Decimal | None = None
    partner_price: Decimal | None = None
    guest_price: Decimal | None = None
    availability: list[SlotSpec] = Field(default_factory=list)
    recurring: list[RecurringSpec] = Field(default_factory=list)
    source: str | None = None
    last_verified_at: date | None = None

    @field_validator("service_type")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in SERVICE_CATALOG:
            raise ValueError(f"unknown service type {v!r}")
        return v


class RegionPack(_M):
    region: RegionInfo
    taxonomy: list[TaxonomySpec] = Field(default_factory=list)
    providers: list[ProviderSpec] = Field(default_factory=list)
    places: list[PlaceSpec] = Field(default_factory=list)
    events: list[EventSpec] = Field(default_factory=list)
    offerings: list[OfferingSpec] = Field(default_factory=list)


def load_region_pack(path: str | Path) -> RegionPack:
    p = Path(path)
    return _load_region_cached(str(p.resolve()), p.stat().st_mtime_ns)


@lru_cache(maxsize=32)
def _load_region_cached(path: str, _mtime: int) -> RegionPack:
    return RegionPack.model_validate(yaml_load(Path(path).read_text(encoding="utf-8")))


def _verified(d: date | None, default: date | None, tz: ZoneInfo) -> datetime | None:
    day = d or default
    return datetime.combine(day, datetime.min.time(), tzinfo=tz).astimezone(_UTC) if day else None


_UTC = ZoneInfo("UTC")


def _local(dt: datetime, tz: ZoneInfo) -> datetime:
    """Pack times are local wall-clock times; stored as UTC (SQLite keeps no
    offsets, so anything else would silently shift by the zone offset)."""
    return (dt if dt.tzinfo else dt.replace(tzinfo=tz)).astimezone(_UTC)


def ingest_region(session: Session, pack: RegionPack, now: datetime | None = None) -> dict[str, int]:
    """The pack carries two worlds: the LOCAL world (places, events) goes
    through the generic WorldSource sync; the TRANSACTION world (providers,
    offerings, inventory) is loaded here."""
    from app.world.sources import SyntheticRegionSource
    from app.world.store import sync

    world = sync(session, SyntheticRegionSource(pack), now=now)     # the container's clock, not the wall clock
    info = pack.region
    tz = ZoneInfo(info.timezone)
    src, synth = info.source, info.synthetic

    # providers (region-scoped)
    existing = {p.slug: p for p in session.scalars(select(ExternalProvider).where(ExternalProvider.region == info.slug))}
    providers: dict[str, ExternalProvider] = {}
    for spec in pack.providers:
        row = existing.get(spec.slug) or ExternalProvider(region=info.slug, slug=spec.slug, property_id=None)
        row.name, row.provider_type, row.integration_type = spec.name, spec.provider_type, spec.integration_type
        row.active, row.services, row.config = spec.active, {"service_types": spec.services}, spec.config
        apply_marketplace(row, spec)
        session.add(row)
        providers[spec.slug] = row
    for slug, row in existing.items():
        if slug not in providers:
            row.active = False
    session.flush()
    places = {p.slug: p for p in session.scalars(select(Place).where(Place.region == info.slug))}
    events = {e.slug: e for e in session.scalars(select(Event).where(Event.region == info.slug))}

    # offerings + availability
    offerings = {o.slug: o for o in session.scalars(select(Offering).where(Offering.region == info.slug))}
    slots = 0
    for spec in pack.offerings:
        provider = providers.get(spec.provider)
        if provider is None:
            raise ValueError(f"offering {spec.slug!r}: unknown region provider {spec.provider!r}")
        place = places.get(spec.place) if spec.place else None
        row = offerings.get(spec.slug) or Offering(region=info.slug, slug=spec.slug)
        row.service_type, row.title, row.attributes = spec.service_type, spec.title, spec.attributes
        row.place_id, row.provider_id, row.active = place.id if place else None, provider.id, True
        row.event_id = events[spec.event].id if spec.event and spec.event in events else None
        row.price_from, row.currency = spec.price_from, spec.currency or (provider.config or {}).get("currency")
        row.pricing, row.policies = spec.pricing, spec.policies
        row.commission_type, row.commission_value = spec.commission_type, spec.commission_value
        row.partner_price, row.guest_price = spec.partner_price, spec.guest_price
        row.source, row.is_synthetic = spec.source or src, synth
        row.source_id, row.source_type, row.source_record_id = f"pack:{info.slug}", "synthetic" if synth else "manual", \
            spec.slug
        row.last_verified_at = _verified(spec.last_verified_at, info.verified_at, tz)
        session.add(row)
        session.flush()
        _link_identity(session, row, place, events.get(spec.event) if spec.event else None)
        # Slots are capacity only; bookings live in inventory_holds (by window,
        # not by slot id), so re-ingesting slots never loses a booking.
        session.execute(delete(AvailabilitySlot).where(AvailabilitySlot.offering_id == row.id))
        verified = _verified(spec.last_verified_at, info.verified_at, tz)
        rows = []
        for start, end, cap, attrs in _expand(spec, tz):
            rows.append({"id": str(uuid.uuid4()), "offering_id": row.id, "starts_at": start, "ends_at": end,
                         "capacity": cap, "remaining": cap, "attributes": attrs,
                         "source": spec.source or src, "last_verified_at": verified})
        if rows:   # bulk insert: thousands of slots per region
            session.execute(insert(AvailabilitySlot), rows)
        slots += len(rows)
    session.flush()
    report = {"providers": len(pack.providers), **world, "offerings": len(pack.offerings), "slots": slots}
    log_event("region_ingested", region=info.slug, **report)
    return report


def _link_identity(session: Session, offering: Offering, place: Place | None, event: Event | None) -> None:
    """Explicit identity between the worlds, established at ingest (the pack
    SAYS this offering is a table at that place): canonical entity <->
    offering. Never inferred from names at transaction time."""
    from app.db.models import MarketplaceLink

    for target in (place, event):
        if target is None or target.canonical_entity_id is None:
            continue
        exists = session.scalar(select(MarketplaceLink.id).where(
            MarketplaceLink.canonical_entity_id == target.canonical_entity_id,
            MarketplaceLink.offering_id == offering.id))
        if exists is None:
            session.add(MarketplaceLink(canonical_entity_id=target.canonical_entity_id, offering_id=offering.id,
                                        established_by="ingest", active=True))


def _expand(spec: OfferingSpec, tz: ZoneInfo):
    for s in spec.availability:
        yield _local(s.start, tz), _local(s.end, tz), s.capacity, s.attributes
    for r in spec.recurring:
        day = r.from_
        t_start = datetime.strptime(r.start, "%H:%M").time()
        t_end = datetime.strptime(r.end, "%H:%M").time()
        while day <= r.to:
            if DAYS[day.weekday()] in r.days:
                start = datetime.combine(day, t_start, tzinfo=tz).astimezone(_UTC)
                end = datetime.combine(day, t_end, tzinfo=tz).astimezone(_UTC)
                if end <= start:
                    end += timedelta(days=1)
                if r.variants:
                    for variant, cap in r.variants.items():
                        yield start, end, cap, {**r.attributes, "variant": str(variant)}
                else:
                    yield start, end, r.capacity, r.attributes
            day += timedelta(days=1)
