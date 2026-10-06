"""Geohash grid: deterministic spatial keys for indexing without PostGIS.

A geohash is a string whose prefixes are nested rectangles, so "all points
in this cell" is a B-tree range scan (`geohash >= 'u2' AND geohash < 'u2~'`)
on any database. Radius / bounding-box / nearest-N queries first collect the
covering cells, then filter exactly with haversine (app/shared/geo.py) - the
index only narrows candidates, it never decides distance.
"""

from __future__ import annotations

import math

_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"
_DECODE = {c: i for i, c in enumerate(_BASE32)}
# Approximate cell size (height km, width km at the equator) per precision.
CELL_KM = {1: (5000, 5000), 2: (625, 1250), 3: (156, 156), 4: (19.5, 39.1), 5: (4.9, 4.9), 6: (0.61, 1.2),
           7: (0.153, 0.153), 8: (0.019, 0.038)}
INDEX_PRECISION = 8          # stored on rows; queries use a prefix of it


def encode(lat: float, lon: float, precision: int = INDEX_PRECISION) -> str:
    lat_lo, lat_hi, lon_lo, lon_hi = -90.0, 90.0, -180.0, 180.0
    out, bits, bit, even = [], 0, 0, True
    while len(out) < precision:
        if even:
            mid = (lon_lo + lon_hi) / 2
            if lon >= mid:
                bits, lon_lo = bits * 2 + 1, mid
            else:
                bits, lon_hi = bits * 2, mid
        else:
            mid = (lat_lo + lat_hi) / 2
            if lat >= mid:
                bits, lat_lo = bits * 2 + 1, mid
            else:
                bits, lat_hi = bits * 2, mid
        even = not even
        bit += 1
        if bit == 5:
            out.append(_BASE32[bits])
            bits, bit = 0, 0
    return "".join(out)


def bounds(cell: str) -> tuple[float, float, float, float]:
    """(min_lat, min_lon, max_lat, max_lon)."""
    lat_lo, lat_hi, lon_lo, lon_hi = -90.0, 90.0, -180.0, 180.0
    even = True
    for c in cell:
        v = _DECODE[c]
        for mask in (16, 8, 4, 2, 1):
            if even:
                mid = (lon_lo + lon_hi) / 2
                lon_lo, lon_hi = (mid, lon_hi) if v & mask else (lon_lo, mid)
            else:
                mid = (lat_lo + lat_hi) / 2
                lat_lo, lat_hi = (mid, lat_hi) if v & mask else (lat_lo, mid)
            even = not even
    return lat_lo, lon_lo, lat_hi, lon_hi


def precision_for(radius_km: float) -> int:
    """The finest precision whose cells are at least as large as the radius
    (so a 3x3 neighbourhood covers the circle)."""
    for p in range(INDEX_PRECISION, 0, -1):
        h, w = CELL_KM[p]
        if min(h, w) >= radius_km:
            return p
    return 1


def cells_for_bbox(min_lat: float, min_lon: float, max_lat: float, max_lon: float, precision: int,
                   limit: int = 400) -> list[str]:
    """Cells of `precision` covering the box (coarser if that would be too many)."""
    while precision > 1:
        b = bounds(encode(min_lat, min_lon, precision))
        dlat, dlon = b[2] - b[0], b[3] - b[1]
        n = (math.floor((max_lat - min_lat) / dlat) + 2) * (math.floor((max_lon - min_lon) / dlon) + 2)
        if n <= limit:
            break
        precision -= 1
    b = bounds(encode(min_lat, min_lon, precision))
    dlat, dlon = b[2] - b[0], b[3] - b[1]
    out = set()
    lat = min_lat
    while lat <= max_lat + dlat:
        lon = min_lon
        while lon <= max_lon + dlon:
            out.add(encode(max(-90.0, min(lat, 90.0)), ((lon + 180.0) % 360.0) - 180.0, precision))
            lon += dlon
        lat += dlat
    return sorted(out)


def cells_for_radius(lat: float, lon: float, radius_km: float) -> list[str]:
    dlat = radius_km / 111.0
    dlon = radius_km / max(111.0 * math.cos(math.radians(lat)), 1e-6)
    return cells_for_bbox(lat - dlat, lon - dlon, lat + dlat, lon + dlon, precision_for(radius_km))


def prefix_range(cell: str) -> tuple[str, str]:
    """Half-open string range [cell, cell~) matching every longer hash with this prefix."""
    return cell, cell + "~"
