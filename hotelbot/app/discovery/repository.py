"""Where discovery gets candidates: the RESOLVED world, through an interface.

The engine does not know whether a place came from one fixture, five feeds,
a partner or a staff correction - it reads the projection (one row per
canonical entity, resolved values, resolution and quality metadata).

    WorldRepository.candidates(session, query) -> list[Place]

ProjectionRepository: with an explicit radius around a point it asks the
spatial index (radius search on the geohash index, exact haversine after);
otherwise it reads the region's active places of the asked kinds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import CoverageArea, Place
from app.shared.geo import Point
from app.text import fold
from app.world import coverage
from app.world.spatial import INDEX, SpatialIndex

if TYPE_CHECKING:
    from app.discovery.engine import DiscoveryQuery


@dataclass(frozen=True)
class Locality:
    """A registered locality ("in Kotor"): its own bbox or radius, nothing invented."""
    area: CoverageArea

    @property
    def center(self) -> Point:
        a = self.area
        if a.min_lat is not None:
            return Point((a.min_lat + a.max_lat) / 2, (a.min_lon + a.max_lon) / 2)
        return Point(a.center_lat, a.center_lon)

    def contains(self, lat: float, lon: float) -> bool:
        return coverage.contains(self.area, lat, lon)


class WorldRepository(Protocol):
    def candidates(self, session: Session, q: DiscoveryQuery) -> list[Place]: ...

    def locality(self, session: Session, q: DiscoveryQuery) -> Locality | None: ...


def _words(text: str | None) -> set[str]:
    return set(re.split(r"[^0-9a-z]+", fold(text or ""))) - {""}


def _kind_condition(q: DiscoveryQuery):
    if q.subcategories and q.categories:
        return (or_(Place.subcategory.in_(q.subcategories), Place.category.in_(q.categories)),)
    if q.subcategories:
        return (Place.subcategory.in_(q.subcategories),)
    if q.categories:
        return (Place.category.in_(q.categories),)
    return ()


class ProjectionRepository:
    def __init__(self, index: SpatialIndex = INDEX) -> None:
        self.index = index

    def candidates(self, session: Session, q: DiscoveryQuery) -> list[Place]:
        kind = _kind_condition(q)
        if q.near is not None and q.max_km is not None:
            found = self.index.within_radius(session, q.near, q.max_km, region=q.region, conditions=kind)
            return sorted((p for p, _ in found), key=lambda p: p.slug)
        stmt = select(Place).where(Place.region == q.region, Place.active, *kind)
        return list(session.scalars(stmt.order_by(Place.slug)))

    def locality(self, session: Session, q: DiscoveryQuery) -> Locality | None:
        """The region's registered locality / admin area whose name or alias
        holds every word of q.locality; the smallest wins. Unknown -> None."""
        words = _words(q.locality)
        found = []
        for a in session.scalars(select(CoverageArea).where(CoverageArea.kind.in_(("locality", "admin_area")))):
            if q.region and not (a.code == q.region or a.code.startswith(f"{q.region}:") or a.parent_code == q.region):
                continue
            if words and any(words <= _words(n) for n in [a.name, *(a.attributes or {}).get("aliases", [])]):
                found.append(a)
        area = min(found, key=lambda a: coverage._size(a), default=None)
        return Locality(area) if area is not None else None


DEFAULT = ProjectionRepository()
