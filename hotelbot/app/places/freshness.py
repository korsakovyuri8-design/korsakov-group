"""Provenance freshness, per KIND of fact.

Dynamic facts age fast (opening hours, kitchen hours, prices, closures,
event details); static facts (address, cuisine, type) age slowly. Each fact
of a candidate is classified on its own verification date, so "the address
is fine but today's hours are not confirmed" can be said precisely.

    FRESH    verified recently enough to state as current
    AGING    older; still used, ranked below fresher data
    STALE    too old to present as current - always disclosed
    UNKNOWN  never verified / low confidence - always disclosed

A model may never upgrade any of these.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.clock import as_utc

MIN_CONFIDENCE = 0.7


class Freshness(str, enum.Enum):
    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"
    UNKNOWN = "unknown"
    UNVERIFIED = "unknown"      # Iteration 3 name (alias)


# fact class -> (fresh up to, aging up to)
FACT_CLASSES: dict[str, tuple[timedelta, timedelta]] = {
    "hours": (timedelta(days=30), timedelta(days=90)),
    "kitchen": (timedelta(days=30), timedelta(days=90)),
    "closure": (timedelta(days=14), timedelta(days=30)),
    "prices": (timedelta(days=60), timedelta(days=180)),
    "event": (timedelta(days=14), timedelta(days=45)),
    "static": (timedelta(days=365), timedelta(days=730)),
}
RANK = {Freshness.FRESH: 0, Freshness.AGING: 1, Freshness.UNKNOWN: 2, Freshness.STALE: 3}


@dataclass(frozen=True)
class FactFreshness:
    fact: str
    state: Freshness
    verified_at: datetime | None
    age_days: int | None


def classify(fact: str, verified_at: datetime | None, now: datetime, confidence: float = 1.0) -> FactFreshness:
    if verified_at is None or confidence < MIN_CONFIDENCE:
        return FactFreshness(fact, Freshness.UNKNOWN, verified_at, None)
    fresh, aging = FACT_CLASSES.get(fact, FACT_CLASSES["static"])
    age = max(now - as_utc(verified_at), timedelta(0))    # a source ahead of our clock counts as just verified
    state = Freshness.FRESH if age <= fresh else Freshness.AGING if age <= aging else Freshness.STALE
    return FactFreshness(fact, state, verified_at, age.days)


def freshness(last_verified_at: datetime | None, now: datetime, *, dynamic: bool, confidence: float = 1.0) -> Freshness:
    """Iteration 3 API: one state for a whole record."""
    return classify("hours" if dynamic else "static", last_verified_at, now, confidence).state


def place_facts(place, now: datetime, facts: tuple[str, ...] = ("hours",)) -> dict[str, FactFreshness]:  # noqa: ANN001
    """Freshness of the given facts of a place: per-fact verification date
    when the source provides one, else the record's own date."""
    out = {}
    verification = place.verification or {}
    for fact in facts:
        raw = verification.get(fact)
        verified = datetime.fromisoformat(raw) if isinstance(raw, str) else (raw or place.last_verified_at)
        out[fact] = classify(fact, verified, now, place.confidence)
    return out
