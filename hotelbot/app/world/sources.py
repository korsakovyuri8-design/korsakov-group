"""WorldSource: where local-world records come from.

The engine consumes normalised records (app/world/records.py), never a file
format. Implemented now:

* SyntheticRegionSource - a region pack (YAML), used for the demo region;
* FixtureSource         - in-memory records for tests.

Future sources (official tourism feeds, mapping databases, event APIs,
partner and property-curated data) implement the same protocol and are
merged by app/world/store.py, each keeping its own provenance.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from app.world.records import EventRecord, PlaceRecord, Provenance, RegionRecord, SubcategoryRecord

_UTC = ZoneInfo("UTC")


class WorldSource(Protocol):
    source_id: str
    source_type: str

    def region(self) -> RegionRecord: ...

    def places(self) -> Iterable[PlaceRecord]: ...

    def events(self) -> Iterable[EventRecord]: ...

    def taxonomy(self) -> Iterable[SubcategoryRecord]: ...


class FixtureSource:
    """In-memory source for tests and hand-curated records."""

    def __init__(self, region: RegionRecord, places: list[PlaceRecord] | None = None,
                 events: list[EventRecord] | None = None, *, source_id: str = "fixture",
                 source_type: str = "fixture", taxonomy: list[SubcategoryRecord] | None = None) -> None:
        self.source_id, self.source_type = source_id, source_type
        self._region, self._places, self._events = region, places or [], events or []
        self._taxonomy = taxonomy or []

    def region(self) -> RegionRecord:
        return self._region

    def places(self) -> Iterable[PlaceRecord]:
        return list(self._places)

    def events(self) -> Iterable[EventRecord]:
        return list(self._events)

    def taxonomy(self) -> Iterable[SubcategoryRecord]:
        return list(self._taxonomy)


def _local(dt: datetime, tz: ZoneInfo) -> datetime:
    return (dt if dt.tzinfo else dt.replace(tzinfo=tz)).astimezone(_UTC)


def _day(d, tz: ZoneInfo) -> datetime | None:  # noqa: ANN001
    return datetime.combine(d, datetime.min.time(), tzinfo=tz).astimezone(_UTC) if d else None


class SyntheticRegionSource:
    """The world part of a region pack (places, events, taxonomy additions).
    Pack times are local to the region; records come out in UTC."""

    source_type = "synthetic"

    def __init__(self, pack) -> None:  # noqa: ANN001  (app.places.pack.RegionPack)
        self.pack = pack
        info = pack.region
        self.source_id = f"{'synthetic' if info.synthetic else 'pack'}:{info.slug}"
        if not info.synthetic:
            self.source_type = "manual"
        self.tz = ZoneInfo(info.timezone)

    @classmethod
    def from_path(cls, path: str | Path) -> SyntheticRegionSource:
        from app.places.pack import load_region_pack

        return cls(load_region_pack(path))

    def _prov(self, slug: str, verified, confidence: float = 1.0, provider_owned: bool = False,  # noqa: ANN001
              label: str | None = None) -> Provenance:
        info = self.pack.region
        return Provenance(source_id=self.source_id, source_type=self.source_type, source_record_id=slug,
                          label=label or info.source, last_verified_at=_day(verified or info.verified_at, self.tz),
                          confidence=confidence, provider_owned=provider_owned, is_synthetic=info.synthetic)

    def region(self) -> RegionRecord:
        info = self.pack.region
        return RegionRecord(info.slug, info.name, info.timezone, (info.center["lat"], info.center["lon"]))

    def places(self) -> Iterable[PlaceRecord]:
        for spec in self.pack.places:
            yield PlaceRecord(
                slug=spec.slug, name=spec.name, subcategory=spec.subcategory,
                category=spec.category.value if spec.category else None,
                provenance=self._prov(spec.slug, spec.last_verified_at, spec.confidence, spec.provider_owned,
                                      spec.source),
                description=spec.description, latitude=spec.location.lat if spec.location else None,
                longitude=spec.location.lon if spec.location else None,
                address=spec.location.address if spec.location else None, timezone=spec.timezone,
                phone=spec.phone, website=spec.website, price_range=spec.price_range or spec.attributes.get("price_range"), tags=spec.tags,
                attributes=spec.attributes, hours=spec.hours, service_area_km=spec.service_area_km,
                verification={k: _day(v, self.tz) for k, v in spec.verification.items()},
            )

    def events(self) -> Iterable[EventRecord]:
        for spec in self.pack.events:
            yield EventRecord(
                slug=spec.slug, title=spec.title, category=spec.category, start=_local(spec.start, self.tz),
                end=_local(spec.end, self.tz) if spec.end else None, venue_slug=spec.place,
                latitude=spec.location.lat if spec.location else None,
                longitude=spec.location.lon if spec.location else None, description=spec.description,
                tags=spec.tags, ticket_required=bool(spec.ticket_required), ticket_price=spec.ticket_price,
                currency=spec.currency, age_limit=spec.age_limit, language=spec.language,
                booking_source=spec.booking_source,
                attributes={**spec.attributes, **({"ticketing": "unknown"} if spec.ticket_required is None else {})},
                provenance=self._prov(spec.slug, spec.last_verified_at, spec.confidence, label=spec.source),
            )

    def taxonomy(self) -> Iterable[SubcategoryRecord]:
        for t in self.pack.taxonomy:
            yield SubcategoryRecord(t.key, t.category, t.labels, tuple(t.keywords))
