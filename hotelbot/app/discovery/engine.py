"""Discovery pipeline: RETRIEVE -> NORMALIZE -> HARD FILTER -> RANK -> EXPLAIN.

(UNDERSTAND - natural language -> DiscoveryQuery - is app/discovery/nlu.py;
it may build a query but never candidates.)

* RETRIEVE      only active records of the region, by category/subcategory.
* NORMALIZE     computed facts per candidate, in the PLACE's timezone: venue
                state, food-service state (kitchen hours only), state at a later
                time / through a window, straight-line distance, walking
                estimate, per-fact freshness.
* HARD FILTER   explicit constraints remove candidates, each with a recorded
                reason: exclusions, diet/accessibility/pets/family, age,
                group size, price ceiling, radius, closed / temporarily closed
                / kitchen closed when time matters, window too short.
* RANK          only survivors; a lexicographic key in policy order.
                With a time / open-state intent ("open now", "tonight"):
                  1 availability/open state  2 relevance to the request
                  3 traveller preferences    4 distance/convenience
                  5 quality/reliability (freshness, rating)  6 price/value
                  7 property relationship    8 commercial tie-breaker
                Without one, availability moves after reliability (a weak
                tie-breaker): most real places carry no hours, and unknown
                hours must not outrank geography.
                Essential needs (pharmacy, ATM...) rank by open state,
                distance and data reliability only. With no time asked
                they still mean "usable now": a place known closed / opening
                later goes down, open and unknown ones rank by distance.
* EXPLAIN       machine-readable reasons, caveats and score components; the
                renderer turns them into words. Nothing is invented.

WINNER != CERTAINTY. A high-dynamic fact (opening hours, kitchen hours,
temporary closure, event start, event availability) whose resolved winner is
CONTESTED - some source disagrees - never becomes a definitive claim: when
the dissenting value would say something different at the asked time, the
candidate carries `contested:<fact>:<claim>|<claim>...` (winner first) and
the renderer says both, and that it cannot confirm which is current.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import as_utc
from app.db.models import Event, Place
from app.places.freshness import RANK as FRESH_RANK
from app.places.freshness import FactFreshness, Freshness, classify, place_facts
from app.places.hours import (HoursStatus, OpenState, always_open, food_state, local_at, open_throughout,
                              status_at)
from app.shared.geo import Point, StraightLineEstimator, TravelEstimator, distance_km, walking_minutes
from app.text import contains_phrase, fold

_AVAIL = {OpenState.OPEN: 0, OpenState.OPEN_LATER: 1, OpenState.UNKNOWN: 2, OpenState.CLOSED: 3}


@dataclass
class DiscoveryQuery:
    region: str
    categories: set[str] = field(default_factory=set)          # top-level taxonomy
    subcategories: set[str] = field(default_factory=set)
    exclude_subcategories: set[str] = field(default_factory=set)
    # HARD constraints
    required: dict[str, Any] = field(default_factory=dict)     # attribute -> required value
    any_of: dict[str, list[Any]] = field(default_factory=dict) # attribute contains one of, e.g. cuisine
    exclude_attrs: dict[str, set[Any]] = field(default_factory=dict)   # e.g. dress_code: {formal}
    max_km: float | None = None                                 # explicit radius
    locality: str | None = None          # "in Kotor": a registered locality of the region scopes the search
    party_size: int | None = None                               # vs attributes.max_group
    min_age: int | None = None                                  # youngest guest vs age_restriction
    price_max: int | None = None                                # price_range ceiling ("cheap")
    at: datetime | None = None           # local time the guest means ("now", "at 22:30")
    open_at: bool = False                # must be open at `at`
    serving_at: bool = False             # food must be served at `at` (kitchen)
    open_until: datetime | None = None   # still open at this time ("after midnight")
    window_end: datetime | None = None   # open throughout [at, window_end] ("for three hours")
    visit_minutes: int | None = None     # the visit must fit the window
    # SOFT preferences (ranking only)
    preferred: dict[str, Any] = field(default_factory=dict)
    tags_preferred: set[str] = field(default_factory=set)
    relevance_tags: set[str] = field(default_factory=set)      # explicit request words ("jazz", "cocktails")
    relevance_words: set[str] = field(default_factory=set)     # found in a place's name/tags/attributes: evidence
    specific: set[str] = field(default_factory=set)            # kinds the guest named exactly
    price_pref: str | None = None                               # "low" | "high"
    traveller: dict[str, Any] = field(default_factory=dict)    # persisted traveller preferences (soft)
    # context
    near: Point | None = None
    anchor_label: str | None = None
    essential: bool = False
    limit: int = 3


@dataclass
class Candidate:
    place: Place
    distance_km: float | None
    status: HoursStatus                  # venue state at the relevant time
    kitchen: HoursStatus | None          # food service state (None = not asked / not food)
    reasons: list[str]                   # machine-readable reason codes, rendered by templates
    caveats: list[str]
    score: float = 0.0
    components: dict[str, Any] = field(default_factory=dict)
    freshness: dict[str, FactFreshness] = field(default_factory=dict)
    walk_minutes: int | None = None
    key: tuple = ()


@dataclass
class EventCandidate:
    event: Event
    place: Place | None
    reasons: list[str]
    caveats: list[str]
    distance_km: float | None = None


@dataclass
class DiscoveryResult:
    query: DiscoveryQuery
    candidates: list[Candidate]
    retrieved: int
    rejected: Counter = field(default_factory=Counter)       # reason -> count (explainable no-results)


Signals = Callable[[Place], tuple[int, float]]   # (relationship rank: 0 preferred / 1 none, commission)


# ================================================================= RETRIEVE
def retrieve(session: Session, q: DiscoveryQuery) -> list[Place]:
    """Active places of the resolved world (app/discovery/repository.py)."""
    from app.discovery.repository import DEFAULT

    return DEFAULT.candidates(session, q)


# ================================================================ NORMALIZE
@dataclass
class Facts:
    local: datetime
    venue: HoursStatus
    food: HoursStatus | None
    until: HoursStatus | None
    window_ok: bool | None
    distance: float | None
    walk: int | None
    fresh: dict[str, FactFreshness]
    contested: dict[str, list[str]] = field(default_factory=dict)   # fact -> [winner claim, other claims]


# high-dynamic place facts: resolution field -> (fact name, how a value enters place.hours)
_DYNAMIC = (("opening_hours", "hours"), ("temporary_closure", "closure"), ("kitchen_hours", "kitchen"))


def claim(st: HoursStatus, hours: dict[str, Any] | None = None) -> str:
    """The traveller-facing claim a status makes, as a stable code."""
    if st.state == OpenState.OPEN:
        if always_open(hours):
            return "open24"
        return f"open@{st.closes_at:%H:%M}" if st.closes_at else "open"
    if st.state == OpenState.OPEN_LATER:
        return f"opens@{st.opens_at:%H:%M}" if st.opens_at else "opens"
    return "closed" if st.state == OpenState.CLOSED else "unknown"


def contested_claims(place: Place, local: datetime) -> dict[str, list[str]]:
    """For each high-dynamic fact whose winner is CONTESTED: the claim the
    winner makes at `local`, then every different claim a dissenting value
    would make. A dissent that says the same thing at this moment (another
    day's hours) is not a contest of this claim and is left out."""
    resolution = place.resolution or {}
    hours = dict(place.hours or {})
    out: dict[str, list[str]] = {}
    for fld, fact in _DYNAMIC:
        r = resolution.get(fld) or {}
        if r.get("state") != "contested":
            continue
        claims: list[str] = []
        for value in [None] + [c.get("value") for c in r.get("conflicting") or []]:
            alt = hours
            if value is not None or claims:
                if fact == "hours":
                    alt = {**(value or {}), **{k: hours[k] for k in ("kitchen", "closed") if k in hours}}
                elif fact == "closure":
                    alt = {k: v for k, v in hours.items() if k != "closed"} | ({"closed": value} if value else {})
                else:
                    alt = {**hours, "kitchen": value} if value else {k: v for k, v in hours.items() if k != "kitchen"}
            if fact == "kitchen":
                c = claim(food_state(SimpleNamespace(attributes=place.attributes, hours=alt), local))
            else:
                c = claim(status_at(alt, local), alt)
            if c not in claims:
                claims.append(c)
        if len(claims) > 1:
            out[fact] = claims
    return out


def normalize(place: Place, q: DiscoveryQuery, now_utc: datetime, tz: str,
              estimator: TravelEstimator) -> Facts:
    local = q.at if q.at is not None else local_at(now_utc, place.timezone, tz)
    venue = status_at(place.hours, local)
    food = food_state(place, local) if (q.serving_at or place.category == "FOOD") else None
    until = status_at(place.hours, q.open_until) if q.open_until is not None else None
    window_ok = open_throughout(place, local, q.window_end) if q.window_end is not None else None
    dist = walk = None
    if q.near is not None and place.latitude is not None and place.longitude is not None:
        p = Point(place.latitude, place.longitude)
        dist, walk = distance_km(q.near, p), estimator.walking_minutes(q.near, p)
    facts_needed = ["hours"] + (["kitchen"] if (place.hours or {}).get("kitchen") else [])
    if (place.hours or {}).get("closed"):
        facts_needed.append("closure")
    return Facts(local, venue, food, until, window_ok, dist, walk, place_facts(place, now_utc, tuple(facts_needed)),
                 contested_claims(place, local))


# ============================================================== HARD FILTER
def _attr_ok(attrs: dict[str, Any], key: str, want: Any) -> bool:
    have = attrs.get(key)
    if isinstance(want, bool):
        return bool(have) is want
    if isinstance(want, (int, float)) and isinstance(have, (int, float)):
        return have <= want if key in ("price_range", "average_price", "cover_charge") else have == want
    return have == want


_OPPOSITE = {"noise_level": {"lively": {"quiet"}, "very_lively": {"quiet"}, "quiet": {"lively", "very_lively"}}}


def hard_filter(place: Place, f: Facts, q: DiscoveryQuery) -> str | None:
    """None = survives; else the reason it was removed."""
    attrs = place.attributes or {}
    if place.subcategory in q.exclude_subcategories:
        return "excluded_kind"
    for k, v in q.required.items():
        if not _attr_ok(attrs, k, v):
            return f"requires:{k}"
    for k, v in q.any_of.items():
        if not (set(v) & set(attrs.get(k) or [])):
            return f"requires:{k}"
    for k, bad in q.exclude_attrs.items():
        if attrs.get(k) in bad:
            return f"excluded:{k}"
    if any(attrs.get(k) in _OPPOSITE.get(k, {}).get(v, set()) for k, v in q.preferred.items()):
        return "opposite_of_preference"
    if q.max_km is not None and (f.distance is None or f.distance > q.max_km):
        return "outside_radius"
    if q.party_size and attrs.get("max_group") and q.party_size > int(attrs["max_group"]):
        return "group_too_large"
    if q.min_age is not None and attrs.get("age_restriction") and int(attrs["age_restriction"]) > q.min_age:
        return "age_restricted"
    if q.price_max is not None and place.price_range is not None and place.price_range > q.price_max:
        return "too_expensive"
    # time: only KNOWN-closed is removed; unknown/stale stays, with a caveat
    if (q.open_at or q.serving_at or q.essential and q.at is not None) and f.venue.state in (
            OpenState.CLOSED, OpenState.OPEN_LATER):
        return "temporarily_closed" if f.venue.reason else "closed_at_time"
    if q.serving_at and f.food is not None and f.food.state in (OpenState.CLOSED, OpenState.OPEN_LATER):
        return "kitchen_closed"
    if f.until is not None and f.until.state in (OpenState.CLOSED, OpenState.OPEN_LATER):
        return "closes_too_early"
    if f.window_ok is False:
        return "closes_before_window_ends"
    if q.visit_minutes and q.window_end is not None and q.at is not None:
        need = int(attrs.get("typical_visit_minutes") or attrs.get("estimated_visit_minutes") or 0) + 2 * (f.walk or 0)
        if need > (q.window_end - q.at).total_seconds() / 60:
            return "does_not_fit_window"
    return None


# ===================================================================== RANK
def rank(place: Place, f: Facts, q: DiscoveryQuery, signals: Signals | None) -> tuple[tuple, dict[str, Any]]:
    attrs = place.attributes or {}
    tags = set((place.tags or {}).get("tags", []))
    # Availability dominates only when the guest expressed a time / open-state
    # intent. Without one (most places carry no hours) it is a weak tie-breaker
    # after relevance, distance and reliability: an UNKNOWN state is not
    # treated almost like CLOSED, and is never turned into OPEN.
    state = f.food.state if (q.serving_at and f.food is not None) else f.venue.state
    availability = _AVAIL[state]
    relevance = (1 if place.subcategory in q.specific else 0) + len(tags & q.relevance_tags) \
        + sum(1 for t in q.relevance_tags if attrs.get(t) is True) + _word_evidence(place, tags, attrs, q)
    prefs = sum(1 for k, v in q.preferred.items() if _attr_ok(attrs, k, v)
                or (k == "noise_level" and v == "lively" and attrs.get(k) == "very_lively")) \
        + len(tags & q.tags_preferred) \
        + sum(1 for k, v in q.traveller.items() if _attr_ok(attrs, k, v))
    dist_bucket = round(f.distance / (0.1 if q.essential else 0.5)) if f.distance is not None else 999
    reliability = max((FRESH_RANK[x.state] for x in f.fresh.values()), default=2)
    resolution = place.resolution or {}
    if any((resolution.get(n) or {}).get("state") in ("needs_verification", "conflicted")
           for n in ("opening_hours", "kitchen_hours", "temporary_closure")) \
            or (resolution.get("_existence") or {}).get("state") == "unconfirmed" or f.contested:
        reliability = max(reliability, 3)       # disputed, contested or unconfirmed data ranks like stale data
    rating = -int(float(attrs.get("rating", 0)) * 2)
    value = 0
    if q.price_pref == "low" and place.price_range is not None:
        value = place.price_range
    elif q.price_pref == "high" and place.price_range is not None:
        value = -place.price_range
    relationship, commission = signals(place) if signals else (1, 0.0)
    comps = {"availability": availability, "relevance": relevance, "preferences": prefs,
             "distance_bucket": dist_bucket, "reliability": reliability, "rating": rating, "value": value,
             "relationship": relationship, "commission": commission,
             "quality": (place.quality or {}).get("tier")}
    if q.essential and time_intent(q):
        key = (availability, dist_bucket, reliability, place.name)
    elif q.essential:
        # "I need a pharmacy" means usable now: only DEFINITELY unavailable now
        # (closed / opens later) goes down; open and unknown rank by distance.
        unavailable = int(state in (OpenState.CLOSED, OpenState.OPEN_LATER))
        key = (unavailable, dist_bucket, reliability, availability, place.name)
    elif time_intent(q):
        key = (availability, -relevance, -prefs, dist_bucket, reliability, rating, value, relationship,
               -commission, place.name)
    else:
        key = (-relevance, -prefs, dist_bucket, reliability, availability, rating, value, relationship,
               -commission, place.name)
    return key, comps


def _word_evidence(place: Place, tags: set[str], attrs: dict[str, Any], q: DiscoveryQuery) -> int:
    """1 if a request word ("cocktail*") appears on the place itself - its
    name, tags or attributes. Evidence for ranking only, never a filter."""
    if not q.relevance_words:
        return 0
    text = fold(" ".join([place.name or "", *sorted(tags), *(f"{k} {v}" for k, v in sorted(attrs.items()))]))
    return int(any(contains_phrase(text, w) for w in q.relevance_words))


def time_intent(q: DiscoveryQuery) -> bool:
    """Did the guest say anything about time or being open ("open now",
    "tonight", "at 22:30", "after midnight", "for three hours")?"""
    return bool(q.open_at or q.serving_at or q.open_until is not None or q.window_end is not None
                or q.at is not None)


# ================================================================== EXPLAIN
def explain(place: Place, f: Facts, q: DiscoveryQuery) -> tuple[list[str], list[str]]:
    attrs = place.attributes or {}
    tags = set((place.tags or {}).get("tags", []))
    reasons: list[str] = [f"attr:{k}" for k in q.required]
    for k, v in q.any_of.items():
        reasons.append(f"match:{k}:{','.join(sorted(set(v) & set(attrs.get(k) or [])))}")
    reasons += [f"pref:{k}" for k, v in q.preferred.items() if _attr_ok(attrs, k, v)]
    reasons += [f"tag:{t}" for t in sorted(tags & (q.tags_preferred | q.relevance_tags))]
    caveats: list[str] = []
    # (an unknown venue state is already said by the status text; disputed
    #  hours get their own caveat below)
    if q.serving_at and f.food is not None and f.food.state == OpenState.UNKNOWN \
            and place.category in ("FOOD", "NIGHTLIFE"):
        caveats.append("kitchen_unknown")
    for fact in f.fresh.values():
        if fact.state in (Freshness.STALE, Freshness.UNKNOWN) and not (
                fact.fact == "hours" and f.venue.state == OpenState.UNKNOWN):
            caveats.append(f"fresh:{fact.fact}:{fact.state.value}:{fact.age_days if fact.age_days is not None else '-'}")
    resolution = place.resolution or {}
    for fld, fact in (("opening_hours", "hours"), ("kitchen_hours", "kitchen"), ("temporary_closure", "closure")):
        state = (resolution.get(fld) or {}).get("state")
        if state in ("needs_verification", "conflicted"):
            caveats.append(f"conflict:{fact}")
    for fact, claims in f.contested.items():
        caveats.append(f"contested:{fact}:{'|'.join(claims)}")
    if (resolution.get("_existence") or {}).get("state") == "unconfirmed":
        caveats.append("existence:unconfirmed")
    if attrs.get("reservation_required"):
        caveats.append("reservation_required")
    if attrs.get("age_restriction"):
        caveats.append(f"age:{attrs['age_restriction']}")
    if attrs.get("cover_charge"):
        caveats.append(f"cover:{attrs['cover_charge']}")
    if attrs.get("dress_code") and attrs["dress_code"] not in ("casual", "none"):
        caveats.append(f"dress:{attrs['dress_code']}")
    return reasons, caveats


# ====================================================================== RUN
def run(session: Session, q: DiscoveryQuery, now_utc: datetime, tz: str, *, signals: Signals | None = None,
        estimator: TravelEstimator | None = None) -> DiscoveryResult:
    estimator = estimator or StraightLineEstimator()
    area = locality_area(session, q)
    if area is not None and q.near is None:
        q = replace(q, near=area.center)                  # "a museum in Budva" from nowhere: measured from Budva
    pool = retrieve(session, q)
    rejected: Counter = Counter()
    survivors: list[Candidate] = []
    for place in pool:
        if area is not None and (place.latitude is None or not area.contains(place.latitude, place.longitude)):
            rejected["outside_locality"] += 1
            continue
        f = normalize(place, q, now_utc, tz, estimator)
        reason = hard_filter(place, f, q)
        if reason:
            rejected[reason] += 1
            continue
        key, comps = rank(place, f, q, signals)
        reasons, caveats = explain(place, f, q)
        survivors.append(Candidate(place, f.distance, f.venue, f.food, reasons, caveats,
                                   score=-float(comps["relevance"] + comps["preferences"]), components=comps,
                                   freshness=f.fresh, walk_minutes=f.walk, key=key))
    survivors.sort(key=lambda c: c.key)
    if any(c.components["availability"] == 0 for c in survivors) and (q.open_at or q.serving_at or q.window_end):
        # The guest asked for a time: places we KNOW are open/serving exist, so
        # places whose state is unknown are not used to pad the list.
        unknown = [c for c in survivors if c.components["availability"] == _AVAIL[OpenState.UNKNOWN]]
        rejected["state_unknown"] += len(unknown)
        survivors = [c for c in survivors if c not in unknown]
    return DiscoveryResult(q, survivors[: q.limit], len(pool), rejected)


def locality_area(session: Session | None, q: DiscoveryQuery):  # noqa: ANN201
    """The registered locality "in <town>" names (app/discovery/repository.py);
    None when there is none - an unknown town scopes nothing."""
    from app.discovery.repository import DEFAULT

    if not q.locality or session is None:
        return None
    return DEFAULT.locality(session, q)


def discover_places(session: Session, q: DiscoveryQuery, now_utc: datetime, tz: str,
                    signals: Signals | None = None) -> list[Candidate]:
    return run(session, q, now_utc, tz, signals=signals).candidates


# =================================================================== EVENTS
def discover_events(session: Session, region: str, now_utc: datetime, start: datetime, end: datetime,
                    tags: set[str] | None = None, categories: set[str] | None = None, limit: int = 5, *,
                    min_age: int | None = None, near: Point | None = None) -> list[EventCandidate]:
    """Events overlapping [start, end). Past and inactive events never appear;
    an age limit above the youngest guest's age is a hard exclusion."""
    rows = session.scalars(select(Event).where(Event.region == region, Event.active).order_by(Event.start_at))
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
        if min_age is not None and ev.age_limit and ev.age_limit > min_age:
            continue
        place = session.get(Place, ev.place_id) if ev.place_id else None
        caveats = []
        fresh = classify("event", ev.last_verified_at, now_utc, ev.confidence)
        if fresh.state in (Freshness.STALE, Freshness.UNKNOWN):
            caveats.append(f"fresh:event:{fresh.state.value}:{fresh.age_days if fresh.age_days is not None else '-'}")
        res = ev.resolution or {}
        for fld, fact in (("start_at", "start"), ("event_availability", "availability")):
            r = res.get(fld) or {}
            if r.get("state") == "contested":
                others = [c.get("value") for c in r.get("conflicting") or [] if c.get("value") is not None]
                if others:
                    caveats.append(f"contested:{fact}")
        if (ev.attributes or {}).get("availability") == "sold_out" and "contested:availability" not in caveats:
            caveats.append("sold_out")
        dist = None
        if near is not None and ev.latitude is not None and ev.longitude is not None:
            dist = distance_km(near, Point(ev.latitude, ev.longitude))
        out.append(EventCandidate(ev, place, [f"tag:{t}" for t in sorted((tags or set()) & ev_tags)], caveats, dist))
    return out[:limit]


def walking(dist_km: float | None) -> int | None:
    return walking_minutes(dist_km) if dist_km is not None else None


def local_window(day, start_h: int, end_h: int, tz: str) -> tuple[datetime, datetime]:  # noqa: ANN001
    z = ZoneInfo(tz)
    start = datetime.combine(day, datetime.min.time(), tzinfo=z) + timedelta(hours=start_h)
    return start, datetime.combine(day, datetime.min.time(), tzinfo=z) + timedelta(hours=end_h)
