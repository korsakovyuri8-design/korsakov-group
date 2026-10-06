"""Sync a WorldSource into the local-world tables.

Upsert by (region, slug). A record that its own source no longer provides
is deactivated (never deleted: saved plan items and ticket offerings point
at it). Records from other sources are untouched - several sources can
feed one region, each keeping its provenance.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Event, Place
from app.observability import log_event
from app.places.taxonomy import category_of, register_subcategory
from app.world.records import EVENT_CATEGORIES, Provenance
from app.world.sources import WorldSource


def _apply_provenance(row: Place | Event, prov: Provenance) -> None:
    row.source, row.source_id, row.source_type = prov.label or prov.source_id, prov.source_id, prov.source_type
    row.source_record_id, row.last_verified_at = prov.source_record_id, prov.last_verified_at
    row.confidence, row.provider_owned, row.is_synthetic = prov.confidence, prov.provider_owned, prov.is_synthetic


def sync(session: Session, source: WorldSource) -> dict[str, int]:
    region = source.region()
    for sub in source.taxonomy():
        register_subcategory(sub.key, sub.category, sub.labels, sub.keywords)

    places = {p.slug: p for p in session.scalars(select(Place).where(Place.region == region.slug))}
    seen: set[str] = set()
    for rec in source.places():
        seen.add(rec.slug)
        row = places.get(rec.slug) or Place(region=region.slug, slug=rec.slug)
        row.name, row.subcategory = rec.name, rec.subcategory
        row.category = rec.category or category_of(rec.subcategory)
        row.tags, row.description, row.attributes = {"tags": rec.tags}, rec.description, rec.attributes
        row.latitude, row.longitude, row.address = rec.latitude, rec.longitude, rec.address
        row.timezone, row.phone, row.website, row.price_range = rec.timezone, rec.phone, rec.website, rec.price_range
        row.hours, row.service_area_km, row.active = rec.hours, rec.service_area_km, True
        row.verification = {k: v.isoformat() for k, v in rec.verification.items() if v}
        _apply_provenance(row, rec.provenance)
        session.add(row)
        places[rec.slug] = row
    for slug, row in places.items():
        if slug not in seen and row.source_id in (None, source.source_id):
            row.active = False
    session.flush()

    events = {e.slug: e for e in session.scalars(select(Event).where(Event.region == region.slug))}
    seen_events: set[str] = set()
    for rec in source.events():
        seen_events.add(rec.slug)
        venue = places.get(rec.venue_slug) if rec.venue_slug else None
        row = events.get(rec.slug) or Event(region=region.slug, slug=rec.slug)
        row.title, row.tags = rec.title, {"tags": rec.tags}
        row.category = rec.category if rec.category in EVENT_CATEGORIES else "other"
        row.place_id, row.start_at, row.end_at = (venue.id if venue else None), rec.start, rec.end
        row.latitude = rec.latitude if rec.latitude is not None else (venue.latitude if venue else None)
        row.longitude = rec.longitude if rec.longitude is not None else (venue.longitude if venue else None)
        row.description, row.ticket_required = rec.description, rec.ticket_required
        row.ticket_price, row.currency, row.age_limit = rec.ticket_price, rec.currency, rec.age_limit
        row.language, row.booking_source, row.attributes = rec.language, rec.booking_source, rec.attributes
        row.active = True
        _apply_provenance(row, rec.provenance)
        session.add(row)
    for slug, row in events.items():
        if slug not in seen_events and row.source_id in (None, source.source_id):
            row.active = False
    session.flush()
    report = {"places": len(seen), "events": len(seen_events)}
    log_event("world_synced", region=region.slug, source=source.source_id, **report)
    return report
