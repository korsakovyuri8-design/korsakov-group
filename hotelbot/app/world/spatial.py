"""Spatial queries over the resolved world (the `places` projection).

`SpatialIndex` is the repository abstraction. `GeohashIndex` is the
portable implementation (PostgreSQL and SQLite): an indexed geohash column,
range scans per covering cell, then an exact haversine filter, so results
are identical to a full scan (the correctness baseline, tested). A PostGIS
implementation (geography column + GiST index, ST_DWithin / KNN `<->`) can
replace it behind the same three calls where the extension is available;
the test environment does not need it.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.db.models import Place
from app.shared import geohash
from app.shared.geo import Point, distance_km


class SpatialIndex(Protocol):
    def within_radius(self, session: Session, center: Point, radius_km: float, *, region: str | None = None,
                      conditions: tuple = ()) -> list[tuple[Place, float]]: ...

    def within_bbox(self, session: Session, bbox: tuple[float, float, float, float], *,
                    region: str | None = None, conditions: tuple = ()) -> list[Place]: ...

    def nearest(self, session: Session, center: Point, n: int, *, region: str | None = None,
                conditions: tuple = (), max_km: float = 50.0) -> list[tuple[Place, float]]: ...


def _cells_condition(cells: list[str]):
    return or_(*[and_(Place.geohash >= lo, Place.geohash < hi) for lo, hi in map(geohash.prefix_range, cells)])


class GeohashIndex:
    def _query(self, session: Session, cells: list[str], region: str | None, conditions: tuple) -> list[Place]:
        q = select(Place).where(_cells_condition(cells), Place.active, *conditions)
        if region is not None:
            q = q.where(Place.region == region)
        return list(session.scalars(q))

    def within_radius(self, session: Session, center: Point, radius_km: float, *, region: str | None = None,
                      conditions: tuple = ()) -> list[tuple[Place, float]]:
        cells = geohash.cells_for_radius(center.lat, center.lon, radius_km)
        out = []
        for p in self._query(session, cells, region, conditions):
            d = distance_km(center, Point(p.latitude, p.longitude))
            if d <= radius_km:
                out.append((p, d))
        return sorted(out, key=lambda x: (x[1], x[0].slug))

    def within_bbox(self, session: Session, bbox: tuple[float, float, float, float], *, region: str | None = None,
                    conditions: tuple = ()) -> list[Place]:
        a, b, c, d = bbox
        cells = geohash.cells_for_bbox(a, b, c, d, geohash.precision_for(max(c - a, d - b) * 111 / 3))
        return sorted((p for p in self._query(session, cells, region, conditions)
                       if a <= p.latitude <= c and b <= p.longitude <= d), key=lambda p: p.slug)

    def nearest(self, session: Session, center: Point, n: int, *, region: str | None = None,
                conditions: tuple = (), max_km: float = 50.0) -> list[tuple[Place, float]]:
        """Expanding rings: stop as soon as n results lie within the ring
        (anything outside it is farther), or at max_km."""
        radius = 0.5
        while True:
            found = self.within_radius(session, center, radius, region=region, conditions=conditions)
            if len(found) >= n or radius >= max_km:
                return found[:n]
            radius = min(radius * 3, max_km)


INDEX: SpatialIndex = GeohashIndex()
