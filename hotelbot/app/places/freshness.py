"""Data provenance and freshness.

Static facts (address, cuisine) age slowly; dynamic facts (opening hours,
prices, events, availability) go stale. Stale or low-confidence facts are
still usable but are flagged to the guest - never presented as certain.
"""

from __future__ import annotations

import enum
from datetime import datetime, timedelta

from app.clock import as_utc

DYNAMIC_MAX_AGE = timedelta(days=30)
STATIC_MAX_AGE = timedelta(days=365)
MIN_CONFIDENCE = 0.7


class Freshness(str, enum.Enum):
    FRESH = "fresh"
    STALE = "stale"
    UNVERIFIED = "unverified"


def freshness(last_verified_at: datetime | None, now: datetime, *, dynamic: bool, confidence: float = 1.0) -> Freshness:
    if last_verified_at is None or confidence < MIN_CONFIDENCE:
        return Freshness.UNVERIFIED
    age = now - as_utc(last_verified_at)
    return Freshness.FRESH if age <= (DYNAMIC_MAX_AGE if dynamic else STATIC_MAX_AGE) else Freshness.STALE
