"""Geographic coverage: country / administrative area / locality / bbox /
radius / provider-defined area. Nothing is hard-coded to one country.

Entities are assigned to the SMALLEST registered area that contains them
(a locality before its country); the area code is the `region` discovery
queries by. A sync can target any area (Scope(kind="area", area_code=...)).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CoverageArea
from app.shared.geo import Point, distance_km
from app.world.adapters import Scope

_KIND_ORDER = {"provider_area": 0, "locality": 1, "radius": 1, "bbox": 2, "admin_area": 3, "country": 4}


def upsert_area(session: Session, *, code: str, kind: str, name: str, country_code: str | None = None,
                parent_code: str | None = None, timezone: str | None = None,
                bbox: tuple[float, float, float, float] | None = None, center: tuple[float, float] | None = None,
                radius_km: float | None = None, attributes: dict[str, Any] | None = None) -> CoverageArea:
    area = session.scalar(select(CoverageArea).where(CoverageArea.code == code)) or CoverageArea(code=code)
    area.kind, area.name, area.country_code, area.parent_code, area.timezone = kind, name, country_code, parent_code, \
        timezone
    if bbox:
        area.min_lat, area.min_lon, area.max_lat, area.max_lon = bbox
    if center:
        area.center_lat, area.center_lon = center
    area.radius_km, area.attributes = radius_km, attributes or {}
    session.add(area)
    session.flush()
    return area


def contains(area: CoverageArea, lat: float, lon: float) -> bool:
    if area.min_lat is not None:
        return area.min_lat <= lat <= area.max_lat and area.min_lon <= lon <= area.max_lon
    if area.center_lat is not None and area.radius_km is not None:
        return distance_km(Point(area.center_lat, area.center_lon), Point(lat, lon)) <= area.radius_km
    return False


def _size(area: CoverageArea) -> float:
    if area.min_lat is not None:
        return (area.max_lat - area.min_lat) * (area.max_lon - area.min_lon)
    return (area.radius_km or 1e6) ** 2 / 12000


def assign(session: Session, lat: float | None, lon: float | None, hint: str | None = None) -> CoverageArea | None:
    """Smallest area containing the point; the hint (the sync's target
    area) when there are no coordinates or nothing more specific fits."""
    areas = list(session.scalars(select(CoverageArea)))
    if lat is not None and lon is not None:
        inside = [a for a in areas if contains(a, lat, lon)]
        if inside:
            return min(inside, key=lambda a: (_KIND_ORDER.get(a.kind, 9), _size(a), a.code))
    return next((a for a in areas if a.code == hint), None)


def scope_for(session: Session, area_code: str) -> Scope:
    area = session.scalar(select(CoverageArea).where(CoverageArea.code == area_code))
    if area is None:
        raise ValueError(f"unknown coverage area {area_code!r}")
    if area.min_lat is not None:
        return Scope("area", area_code, bbox=(area.min_lat, area.min_lon, area.max_lat, area.max_lon))
    return Scope("area", area_code, center=(area.center_lat, area.center_lon), radius_km=area.radius_km)


def locality_words(session: Session) -> set[str]:
    """Place names of registered areas: ignored when comparing business
    names ("Konoba Stari Grad Žabljak" ~ "Konoba Stari Grad")."""
    import re

    from app.text import fold

    words: set[str] = set()
    for area in session.scalars(select(CoverageArea)):
        for name in [area.name, area.code, *(area.attributes or {}).get("aliases", [])]:
            words |= {w for w in re.split(r"[^0-9a-z]+", fold(name or "")) if len(w) > 3}
    return words - {"area", "region", "synthetic", "data", "demo"}
