"""Geo helpers. Distances are computed, never estimated by a model.

Walking / driving minutes are explicit straight-line heuristics (labelled
as approximate); real routing belongs to a future maps/transit provider
behind the same interface.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

WALK_KMH = 4.5
WALK_DETOUR = 1.3       # streets are not straight lines


@dataclass(frozen=True)
class Point:
    lat: float
    lon: float


def distance_km(a: Point, b: Point) -> float:
    r = 6371.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = p2 - p1, math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def walking_minutes(km: float) -> int:
    return max(1, round(km * WALK_DETOUR / WALK_KMH * 60))
