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

from typing import TYPE_CHECKING, Protocol

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import Place
from app.world.spatial import INDEX, SpatialIndex

if TYPE_CHECKING:
    from app.discovery.engine import DiscoveryQuery


class WorldRepository(Protocol):
    def candidates(self, session: Session, q: DiscoveryQuery) -> list[Place]: ...


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


DEFAULT = ProjectionRepository()
