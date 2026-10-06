"""Normalised local-world records. Every source (synthetic region pack,
official tourism feed, mapping database, partner data, property-curated
list, events API...) is converted into these before the engine sees it.
The discovery engine never knows which source a record came from - but the
record always does (provenance)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

SOURCE_TYPES = ("synthetic", "fixture", "official_feed", "mapping", "partner", "property_curated", "manual",
                "events_api", "other")

EVENT_CATEGORIES = ("concert", "festival", "sports", "market", "exhibition", "theater", "nightlife",
                    "conference", "local_event", "other")


@dataclass(frozen=True)
class Provenance:
    source_id: str                     # which WorldSource ("synthetic:zabljak-demo", "osm:me", ...)
    source_type: str                   # one of SOURCE_TYPES
    source_record_id: str              # the record's id in that source
    label: str = ""                    # human-readable source name
    last_verified_at: datetime | None = None
    confidence: float = 1.0
    provider_owned: bool = False       # supplied by the business itself
    is_synthetic: bool = False


@dataclass(frozen=True)
class RegionRecord:
    slug: str
    name: str
    timezone: str
    center: tuple[float, float]


@dataclass
class PlaceRecord:
    slug: str
    name: str
    subcategory: str
    provenance: Provenance
    category: str | None = None        # derived from the taxonomy when None
    description: dict[str, str] = field(default_factory=dict)
    latitude: float | None = None
    longitude: float | None = None
    address: str | None = None
    timezone: str | None = None
    phone: str | None = None
    website: str | None = None
    price_range: int | None = None
    tags: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    hours: dict[str, Any] = field(default_factory=dict)
    service_area_km: float | None = None
    verification: dict[str, datetime] = field(default_factory=dict)   # per dynamic fact


@dataclass
class EventRecord:
    slug: str
    title: dict[str, str]
    category: str                      # one of EVENT_CATEGORIES
    start: datetime                    # UTC
    provenance: Provenance
    end: datetime | None = None
    venue_slug: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    description: dict[str, str] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    ticket_required: bool = False
    ticket_price: Decimal | None = None
    currency: str | None = None
    age_limit: int | None = None
    language: str | None = None
    booking_source: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SubcategoryRecord:
    """A subcategory a source introduces ("padel court") - taxonomy grows by data."""

    key: str
    category: str
    labels: dict[str, str]
    keywords: tuple[str, ...] = ()

