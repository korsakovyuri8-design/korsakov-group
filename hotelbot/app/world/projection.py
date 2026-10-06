"""Canonical entity + resolved facts -> the traveller-facing projection
(`places` / `events` rows) that the discovery engine reads.

The discovery engine never sees sources: it sees one row per real-world
thing, with the resolved values, the per-fact verification dates of the
WINNING assertions (freshness), `resolution` (state, winner source,
supporting / conflicting sources per field) and `quality` (components, not
one number). A NEEDS_VERIFICATION high-risk field is projected as missing
(hours unknown), never as either disputed value.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import as_utc
from app.db.models import (
    CanonicalEntity,
    EntityLink,
    Event,
    MatchReview,
    Place,
    SourceEntity,
    WorldSourceRow,
)
from app.places.freshness import Freshness
from app.shared import geohash
from app.world.facts import CONFLICTED, CONTESTED, NEEDS_VERIFICATION, RESOLVED, Resolution, resolve_entity
from app.world.policies import Policies

HOURS_FIELDS = {"opening_hours": "hours", "kitchen_hours": "kitchen", "temporary_closure": "closure",
                "price_range": "prices"}
KEY_DYNAMIC = ("opening_hours", "kitchen_hours", "temporary_closure")
EXPECTED = ("name", "coordinates", "subcategory", "opening_hours")
_FRESH_ORDER = [Freshness.FRESH.value, Freshness.AGING.value, Freshness.UNKNOWN.value, Freshness.STALE.value]


def _val(res: dict[str, Resolution], name: str, default: Any = None) -> Any:
    r = res.get(name)
    return r.value if r is not None and r.state != NEEDS_VERIFICATION and r.value is not None else default


def follow_merges(session: Session, canonical_id: str | None) -> str | None:
    seen = set()
    while canonical_id and canonical_id not in seen:
        seen.add(canonical_id)
        c = session.get(CanonicalEntity, canonical_id)
        if c is None or not c.merged_into_id:
            return canonical_id
        canonical_id = c.merged_into_id
    return canonical_id


def _links(session: Session, canonical: CanonicalEntity) -> list[tuple[EntityLink, SourceEntity, WorldSourceRow]]:
    rows = session.execute(
        select(EntityLink, SourceEntity, WorldSourceRow)
        .join(SourceEntity, EntityLink.source_entity_id == SourceEntity.id)
        .join(WorldSourceRow, SourceEntity.source_id == WorldSourceRow.id)
        .where(EntityLink.canonical_entity_id == canonical.id, EntityLink.active)).all()
    return [tuple(r) for r in rows]


def existence(links: list[tuple[EntityLink, SourceEntity, WorldSourceRow]]) -> dict[str, Any]:
    links = [lk for lk in links if lk[1].source_type != "correction"]    # a correction is not a listing
    tomb = [se.tombstoned_at for _, se, _ in links if not se.active_at_source]
    if links and len(tomb) == len(links):
        return {"state": "unconfirmed", "since": max(as_utc(t) for t in tomb if t).isoformat()}
    return {"state": "listed", "sources": len(links) - len(tomb)}


def quality(session: Session, canonical: CanonicalEntity, res: dict[str, Resolution],
            links: list[tuple[EntityLink, SourceEntity, WorldSourceRow]], policies: Policies) -> dict[str, Any]:
    """Data quality as COMPONENTS; `tier` is derived from them, transparently."""
    scores = [lk.score if lk.score is not None else 1.0 for lk, _, _ in links]
    open_review = session.scalar(select(MatchReview.id).where(
        MatchReview.source_entity_id.in_([se.id for _, se, _ in links]), MatchReview.status == "open"))
    identity = round(min(scores) if scores else 0.0, 3)
    authority = {}
    for name in ("name", "coordinates", "opening_hours"):
        r = res.get(name)
        if r is not None and r.winner is not None:
            authority[name] = r.winner.source_class
    fresh = {n: res[n].freshness for n in KEY_DYNAMIC if n in res}
    worst = max(fresh.values(), key=_FRESH_ORDER.index) if fresh else Freshness.UNKNOWN.value
    present = [n for n in EXPECTED if n in res and res[n].state != NEEDS_VERIFICATION]
    conflicts = {n: r.state for n, r in res.items() if r.state in (CONTESTED, CONFLICTED, NEEDS_VERIFICATION)}
    verified = sorted({a.verification_type for r in res.values() for a in r.supporting
                       if a.verification_type != "source_reported"})
    exist = existence(links)
    high_risk_open = [n for n, s in conflicts.items() if s in (NEEDS_VERIFICATION, CONFLICTED)
                      and policies.field(n).high_risk]
    if high_risk_open or exist["state"] == "unconfirmed" or worst == Freshness.STALE.value or identity < 0.6:
        tier = "low"
    elif not conflicts and worst == Freshness.FRESH.value and identity >= 0.9 and len(present) == len(EXPECTED):
        tier = "high"
    else:
        tier = "medium"
    return {"tier": tier, "identity_confidence": identity, "identity_under_review": bool(open_review),
            "source_authority": authority, "freshness": fresh, "worst_freshness": worst,
            "completeness": round(len(present) / len(EXPECTED), 2), "missing": [n for n in EXPECTED if n not in present],
            "conflicts": conflicts, "verification": verified, "existence": exist,
            "sources": sorted({src.id for _, _, src in links})}


def _attribution(res: dict[str, Resolution], sources: dict[str, WorldSourceRow]) -> list[str]:
    needed = set()
    for r in res.values():
        if r.winner is not None:
            src = sources.get(r.winner.source_id)
            if src is not None and src.attribution_required:
                needed.add(src.id)
    return sorted(needed)


def _resolution_json(res: dict[str, Resolution]) -> dict[str, Any]:
    return {n: r.summary() for n, r in sorted(res.items()) if not n.startswith("attributes.") or r.state != RESOLVED}


def project(session: Session, canonical: CanonicalEntity, policies: Policies, now: datetime) -> Place | Event | None:
    if canonical.entity_type in ("PLACE", "VENUE"):
        return _project_place(session, canonical, policies, now)
    if canonical.entity_type == "EVENT":
        return _project_event(session, canonical, policies, now)
    return None


def _verified(res: dict[str, Resolution], name: str) -> str | None:
    r = res.get(name)
    if r is None:
        return None
    lead = r.winner or (max(r.conflicting, key=lambda a: as_utc(a.observed_at)) if r.conflicting else None)
    return as_utc(lead.observed_at).isoformat() if lead is not None else None


def _primary(res: dict[str, Resolution], key: str) -> Any:
    r = res.get(key) or res.get("title")
    return r.winner if r is not None else None


def _project_place(session: Session, canonical: CanonicalEntity, policies: Policies, now: datetime) -> Place:
    from app.places.taxonomy import category_of

    row = session.scalar(select(Place).where(Place.canonical_entity_id == canonical.id))
    if row is None:
        row = session.scalar(select(Place).where(Place.region == canonical.region,
                                                 Place.slug == canonical.canonical_slug,
                                                 Place.canonical_entity_id.is_(None)))   # adopt a pre-fabric row
    if row is None:
        row = Place(region=canonical.region or "unassigned", slug=canonical.canonical_slug)
    row.canonical_entity_id = canonical.id
    links = _links(session, canonical)
    sources = {src.id: src for _, _, src in links}
    if canonical.merged_into_id:
        row.active = False
        row.resolution = {"_merged_into": canonical.merged_into_id}
        session.add(row)
        return row
    res = resolve_entity(session, canonical.id, policies, now)
    row.region, row.slug = canonical.region or row.region, canonical.canonical_slug
    row.name = _val(res, "name", canonical.canonical_name)
    row.subcategory = _val(res, "subcategory", "unclassified")
    row.category = category_of(row.subcategory)
    coords = _val(res, "coordinates")
    row.latitude, row.longitude = (coords["lat"], coords["lon"]) if coords else (None, None)
    row.geohash = geohash.encode(row.latitude, row.longitude) if coords else None
    row.address, row.timezone = _val(res, "address"), _val(res, "timezone")
    row.phone, row.website, row.price_range = _val(res, "phone"), _val(res, "website"), _val(res, "price_range")
    row.service_area_km = _val(res, "service_area_km")
    row.description = _val(res, "description", {}) or {}
    row.tags = {"tags": list(_val(res, "tags", []) or [])}
    row.attributes = {n.split(".", 1)[1]: _val(res, n) for n in sorted(res) if n.startswith("attributes.")
                      and _val(res, n) is not None}
    hours: dict[str, Any] = dict(_val(res, "opening_hours", {}) or {})
    if _val(res, "kitchen_hours") is not None:
        hours["kitchen"] = _val(res, "kitchen_hours")
    if _val(res, "temporary_closure") is not None:
        hours["closed"] = _val(res, "temporary_closure")
    row.hours = hours
    row.verification = {fact: _verified(res, f) for f, fact in HOURS_FIELDS.items() if f in res and _verified(res, f)}
    name_win = _primary(res, "name")
    row.last_verified_at = as_utc(name_win.observed_at) if name_win is not None else None
    lead_source = sources.get(name_win.source_id) if name_win is not None else None
    lead_se = next((se for _, se, _ in links if name_win is not None and se.id == name_win.source_entity_id), None)
    row.source_id = lead_source.id if lead_source else None
    row.source_type = lead_source.source_type if lead_source else None
    row.source_record_id = lead_se.source_record_id if lead_se else None
    row.source = ((lead_se.normalized or {}).get("meta", {}).get("source_label") if lead_se else None) or \
        (lead_source.name if lead_source else "unknown")
    row.confidence = max((src.default_confidence for src in sources.values()), default=0.0)
    if lead_se is not None and (lead_se.normalized or {}).get("confidence") is not None:
        row.confidence = lead_se.normalized["confidence"]
    row.is_synthetic = any((src.config or {}).get("synthetic") for src in sources.values())
    oh = res.get("opening_hours")
    row.provider_owned = bool(oh and oh.winner and oh.winner.source_class == "provider_owned") or any(
        (se.normalized or {}).get("meta", {}).get("provider_owned") for _, se, _ in links)
    row.resolution = {**_resolution_json(res), "_attribution": _attribution(res, sources),
                      "_existence": existence(links)}
    row.quality = quality(session, canonical, res, links, policies)
    row.active = canonical.active
    if coords:
        canonical.latitude, canonical.longitude, canonical.geohash = row.latitude, row.longitude, row.geohash
    canonical.canonical_name = row.name
    session.add(row)
    return row


def _project_event(session: Session, canonical: CanonicalEntity, policies: Policies, now: datetime) -> Event | None:
    row = session.scalar(select(Event).where(Event.canonical_entity_id == canonical.id))
    if row is None:
        row = session.scalar(select(Event).where(Event.region == canonical.region,
                                                 Event.slug == canonical.canonical_slug,
                                                 Event.canonical_entity_id.is_(None)))
    links = _links(session, canonical)
    sources = {src.id: src for _, _, src in links}
    if canonical.merged_into_id:
        if row is not None:
            row.active, row.resolution = False, {"_merged_into": canonical.merged_into_id}
        return row
    res = resolve_entity(session, canonical.id, policies, now)
    start_r = res.get("start_at")
    start = _val(res, "start_at")
    if start is None and start_r is not None and start_r.conflicting:
        start = min(a.value for a in start_r.conflicting)      # placeholder for the row; flagged as disputed
    if start is None:
        return row                                             # no time at all: not an event we can show
    if row is None:
        row = Event(region=canonical.region or "unassigned", slug=canonical.canonical_slug)
    row.canonical_entity_id = canonical.id
    row.region, row.slug = canonical.region or row.region, canonical.canonical_slug
    title = _val(res, "title", {}) or {"en": canonical.canonical_name}
    row.title = title
    from app.world.records import EVENT_CATEGORIES

    cat = _val(res, "event_category", "other")
    row.category = cat if cat in EVENT_CATEGORIES else "other"
    row.start_at = datetime.fromisoformat(start)
    end = _val(res, "end_at")
    row.end_at = datetime.fromisoformat(end) if end else None
    venue = follow_merges(session, _val(res, "venue"))
    place = session.scalar(select(Place).where(Place.canonical_entity_id == venue)) if venue else None
    row.place_id = place.id if place is not None else None
    coords = _val(res, "coordinates")
    row.latitude = coords["lat"] if coords else (place.latitude if place is not None else None)
    row.longitude = coords["lon"] if coords else (place.longitude if place is not None else None)
    row.description = _val(res, "description", {}) or {}
    row.tags = {"tags": list(_val(res, "tags", []) or [])}
    row.ticket_required = bool(_val(res, "ticket_required", False))
    price = _val(res, "ticket_price")
    row.ticket_price = Decimal(str(price)) if price is not None else None
    row.currency, row.age_limit = _val(res, "currency"), _val(res, "age_limit")
    row.language, row.booking_source = _val(res, "language"), _val(res, "booking_source")
    row.attributes = {n.split(".", 1)[1]: _val(res, n) for n in sorted(res) if n.startswith("attributes.")
                      and _val(res, n) is not None}
    lead = _primary(res, "title")
    lead_source = sources.get(lead.source_id) if lead is not None else None
    lead_se = next((se for _, se, _ in links if lead is not None and se.id == lead.source_entity_id), None)
    row.last_verified_at = as_utc(lead.observed_at) if lead is not None else None
    row.source_id = lead_source.id if lead_source else None
    row.source_type = lead_source.source_type if lead_source else None
    row.source_record_id = lead_se.source_record_id if lead_se else None
    row.source = ((lead_se.normalized or {}).get("meta", {}).get("source_label") if lead_se else None) or \
        (lead_source.name if lead_source else "unknown")
    row.confidence = max((src.default_confidence for src in sources.values()), default=0.0)
    if lead_se is not None and (lead_se.normalized or {}).get("confidence") is not None:
        row.confidence = lead_se.normalized["confidence"]
    row.is_synthetic = any((src.config or {}).get("synthetic") for src in sources.values())
    row.resolution = {**_resolution_json(res), "_attribution": _attribution(res, sources),
                      "_existence": existence(links)}
    row.active = canonical.active
    canonical.starts_at = row.start_at
    session.add(row)
    return row
