"""Trip windows -> Discovery (docs/ITERATION_7_DESIGN.md §18).

Orchestration only: app/journey decides the windows (it imports neither
Discovery nor this module), Discovery ranks places, and this adapter turns
one TripWindow into one DiscoveryQuery. Both anchors are located through a
`locate(anchor) -> Point | None` callback; the candidate must fit
start -> candidate -> end inside the window (`end_near`). World links are
identity only: they locate an anchor and never change booking truth -
nothing here writes a TripItem."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Place
from app.discovery.engine import DiscoveryQuery, DiscoveryResult, run
from app.shared.geo import Point

Locate = Callable[[dict[str, Any]], Point | None]


def world_locator(session: Session, links: dict[str, str], *, region: str | None = None) -> Locate:
    """anchor -> the point of the World place its item is linked to (item identity -> place slug)."""
    def locate(anchor: dict[str, Any]) -> Point | None:
        slug = links.get(anchor.get("identity") or "")
        if not slug:
            return None
        stmt = select(Place).where(Place.slug == slug, Place.active)
        place = session.scalar(stmt.where(Place.region == region) if region else stmt)
        if place is None or place.latitude is None or place.longitude is None:
            return None
        return Point(place.latitude, place.longitude)
    return locate


def window_query(window: dict[str, Any], locate: Locate, *, region: str,
                 categories: Iterable[str] = ("CULTURE",), limit: int = 3) -> DiscoveryQuery | None:
    """None when the window's start cannot be located: there is nothing to measure from.
    An unlocated end falls back to Discovery's own rule (back to the start)."""
    start = locate(window["start_anchor"])
    if start is None:
        return None
    return DiscoveryQuery(region=region, categories=set(categories), at=datetime.fromisoformat(window["start"]),
                          window_end=datetime.fromisoformat(window["end"]), open_at=True,
                          visit_minutes=window["minutes"], near=start, end_near=locate(window["end_anchor"]),
                          limit=limit)


def suggest(session: Session, window: dict[str, Any], locate: Locate, *, region: str, now_utc: datetime,
            categories: Iterable[str] = ("CULTURE",), limit: int = 3) -> DiscoveryResult | None:
    q = window_query(window, locate, region=region, categories=categories, limit=limit)
    return None if q is None else run(session, q, now_utc, window["timezone"])
