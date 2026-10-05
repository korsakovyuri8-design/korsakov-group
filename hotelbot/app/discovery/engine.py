"""Discovery engine: retrieval -> hard filters -> ranking -> explanation facts.

Candidates come only from structured data (places, events, offerings) -
never from a model's memory. Every reason and caveat attached to a result
is derived from stored fields (hours, attributes, coordinates, provenance),
so any later LLM phrasing can only explain, not invent.

Hard constraints (dietary, accessibility, pets, open at a time, category
exclusions, distance) are filters. Ranking only orders what passed them;
commercial weight, if ever added, can never re-admit a filtered candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import as_utc
from app.db.models import Event, Place
from app.places.freshness import Freshness, freshness
from app.places.geo import Point, distance_km, walking_minutes
from app.places.hours import HoursStatus, OpenState, status_at


@dataclass
class DiscoveryQuery:
    region: str
    categories: set[str] = field(default_factory=set)          # top-level taxonomy
    subcategories: set[str] = field(default_factory=set)
    exclude_subcategories: set[str] = field(default_factory=set)
    required: dict[str, Any] = field(default_factory=dict)     # attribute -> required value (hard)
    any_of: dict[str, list[Any]] = field(default_factory=dict) # attribute contains one of (hard), e.g. cuisine
    preferred: dict[str, Any] = field(default_factory=dict)    # soft: boosts ranking
    tags_preferred: set[str] = field(default_factory=set)
    near: Point | None = None
    max_km: float | None = None
    at: datetime | None = None           # local time the guest means ("now", "at 22:30")
    open_at: bool = False                # hard: must be open (venue) at `at`
    serving_at: bool = False             # hard: kitchen must be open at `at`
    open_until: datetime | None = None   # hard: still open at this time ("after midnight")
    party_size: int | None = None
    limit: int = 3


@dataclass
class Candidate:
    place: Place
    distance_km: float | None
    status: HoursStatus
    kitchen: HoursStatus | None
    reasons: list[str]          # machine-readable reason codes, rendered by templates
    caveats: list[str]
    score: float


@dataclass
class EventCandidate:
    event: Event
    place: Place | None
    reasons: list[str]
    caveats: list[str]


def _attr_ok(attrs: dict[str, Any], key: str, want: Any) -> bool:
    have = attrs.get(key)
    if isinstance(want, bool):
        return bool(have) is want
    if isinstance(want, (int, float)) and isinstance(have, (int, float)):
        return have <= want if key in ("price_range", "average_price", "cover_charge") else have == want
    return have == want


def discover_places(session: Session, q: DiscoveryQuery, now_utc: datetime, tz: str) -> list[Candidate]:
    stmt = select(Place).where(Place.region == q.region, Place.active)
    if q.subcategories:
        stmt = stmt.where(Place.subcategory.in_(q.subcategories))
    elif q.categories:
        stmt = stmt.where(Place.category.in_(q.categories))
    local_at = (q.at or now_utc.astimezone(ZoneInfo(tz))).replace(tzinfo=None)
    out: list[Candidate] = []
    for place in session.scalars(stmt):
        attrs = place.attributes or {}
        if place.subcategory in q.exclude_subcategories:
            continue
        if any(not _attr_ok(attrs, k, v) for k, v in q.required.items()):
            continue
        if any(not (set(v) & set(attrs.get(k) or [])) for k, v in q.any_of.items()):
            continue
        dist = None
        if q.near and place.latitude is not None and place.longitude is not None:
            dist = distance_km(q.near, Point(place.latitude, place.longitude))
            if q.max_km is not None and dist > q.max_km:
                continue
        status = status_at(place.hours, local_at)
        kitchen = status_at(place.hours, local_at, key="kitchen") if (place.hours or {}).get("kitchen") else None
        if q.open_at and status.state != OpenState.OPEN:
            continue
        if q.serving_at:
            serving = kitchen if kitchen is not None else status
            if serving.state != OpenState.OPEN:
                continue
        if q.open_until is not None:
            until = status_at(place.hours, q.open_until.replace(tzinfo=None))
            if until.state != OpenState.OPEN:
                continue
        reasons, caveats, score = _explain(place, attrs, q, status, kitchen, dist, now_utc)
        out.append(Candidate(place, dist, status, kitchen, reasons, caveats, score))
    out.sort(key=lambda c: (-c.score, c.distance_km if c.distance_km is not None else 99, c.place.name))
    return out[: q.limit]


def _explain(place: Place, attrs: dict[str, Any], q: DiscoveryQuery, status: HoursStatus, kitchen: HoursStatus | None,
             dist: float | None, now_utc: datetime) -> tuple[list[str], list[str], float]:
    reasons: list[str] = []
    caveats: list[str] = []
    score = 0.0
    for k, v in q.required.items():
        reasons.append(f"attr:{k}")
    for k, v in q.any_of.items():
        reasons.append(f"match:{k}:{','.join(sorted(set(v) & set(attrs.get(k) or [])))}")
    for k, v in q.preferred.items():
        if _attr_ok(attrs, k, v):
            score += 1.0
            reasons.append(f"pref:{k}")
    tags = set((place.tags or {}).get("tags", []))
    hits = tags & q.tags_preferred
    score += len(hits)
    reasons.extend(f"tag:{t}" for t in sorted(hits))
    if dist is not None:
        score -= dist * 0.2
    if status.state == OpenState.UNKNOWN:
        caveats.append("hours_unknown")
    if attrs.get("reservation_required"):
        caveats.append("reservation_required")
    if attrs.get("age_restriction"):
        caveats.append(f"age:{attrs['age_restriction']}")
    if attrs.get("cover_charge"):
        caveats.append(f"cover:{attrs['cover_charge']}")
    fresh = freshness(place.last_verified_at, now_utc, dynamic=True, confidence=place.confidence)
    if fresh != Freshness.FRESH:
        caveats.append(f"data:{fresh.value}")
    return reasons, caveats, score


def discover_events(session: Session, region: str, now_utc: datetime, start: datetime, end: datetime,
                    tags: set[str] | None = None, categories: set[str] | None = None, limit: int = 5) -> list[EventCandidate]:
    """Events overlapping [start, end). Past events never appear."""
    rows = session.scalars(select(Event).where(Event.region == region).order_by(Event.start_at))
    out: list[EventCandidate] = []
    for ev in rows:
        ev_start = as_utc(ev.start_at)
        ev_end = as_utc(ev.end_at) if ev.end_at else ev_start + timedelta(hours=2)
        if ev_end <= now_utc:              # already over
            continue
        if not (ev_start < as_utc(end) and ev_end > as_utc(start)):
            continue
        ev_tags = set((ev.tags or {}).get("tags", []))
        if tags and not (tags & ev_tags) and ev.category not in tags:
            continue
        if categories and ev.category not in categories:
            continue
        place = session.get(Place, ev.place_id) if ev.place_id else None
        caveats = []
        fresh = freshness(ev.last_verified_at, now_utc, dynamic=True, confidence=ev.confidence)
        if fresh != Freshness.FRESH:
            caveats.append(f"data:{fresh.value}")
        out.append(EventCandidate(ev, place, [f"tag:{t}" for t in sorted((tags or set()) & ev_tags)], caveats))
    return out[:limit]


def walking(dist_km: float | None) -> int | None:
    return walking_minutes(dist_km) if dist_km is not None else None
