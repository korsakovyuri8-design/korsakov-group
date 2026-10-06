"""Field-level facts: assertions in, resolved values out.

WRITE   every field of every source entity is a FactAssertion. A changed
        value supersedes the source's previous assertion (history kept, a
        WorldChange recorded); a field the source stops sending is
        retracted (superseded, not deleted); an unchanged value is
        re-observed (observed_at moves forward).

RESOLVE resolve(assertions, policy, now) -> Resolution. Deterministic, no
        LLM, explained:
          1. only assertions valid now (valid_from / valid_until);
          2. stale assertions step aside when fresh ones exist;
          3. the strongest authority FOR THIS FIELD wins;
          4. same authority, different values: more independent sources
             win (majority), else a clearly newer value wins (recency gap),
             else CONFLICTED - and for a HIGH-RISK field (hours, closures,
             event time) NEEDS_VERIFICATION with no value at all;
          5. a stale but stronger source disagreeing with the winner makes
             a high-risk field NEEDS_VERIFICATION (we can neither confirm
             the authority nor trust the weaker source over it);
          6. every disagreement is kept in `conflicting` - never erased.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import as_utc
from app.db.models import FactAssertion, SourceEntity, WorldChange
from app.places.freshness import Freshness, classify
from app.shared.geo import Point, distance_km
from app.world.policies import FieldPolicy, Policies

RESOLVED, CONTESTED, CONFLICTED, NEEDS_VERIFICATION, UNKNOWN = (
    "resolved", "contested", "conflicted", "needs_verification", "unknown")


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def values_equal(policy: FieldPolicy, a: Any, b: Any) -> bool:
    if policy.equal_within_m is not None and isinstance(a, dict) and isinstance(b, dict) and "lat" in a and "lat" in b:
        return distance_km(Point(a["lat"], a["lon"]), Point(b["lat"], b["lon"])) * 1000 <= policy.equal_within_m
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().casefold() == b.strip().casefold()
    return _canon(a) == _canon(b)


# ================================================================== write
def write_assertions(session: Session, se: SourceEntity, canonical_id: str, fields: dict[str, Any],
                     observed: dict[str, datetime], *, source_class: str, confidence: float, now: datetime,
                     verification_type: str = "source_reported", valid: dict[str, tuple] | None = None,
                     retract_missing: bool = True) -> list[str]:
    """Diff the source entity's fields against its current assertions.
    Returns the changed field names."""
    current = {a.field_name: a for a in session.scalars(select(FactAssertion).where(
        FactAssertion.source_entity_id == se.id, FactAssertion.superseded_at.is_(None)))}
    changed = []
    for name, value in fields.items():
        when = observed.get(name) or now
        if as_utc(when) > as_utc(now):
            # a source clock ahead of ours: impossible freshness must not win
            # anything - the observation counts as "now", and the skew is recorded
            session.add(WorldChange(canonical_entity_id=canonical_id, source_entity_id=se.id, source_id=se.source_id,
                                    change_type="future_observation_clamped", field_name=name,
                                    detail={"reported": as_utc(when).isoformat(), "used": as_utc(now).isoformat()},
                                    detected_at=now))
            when = now
        old = current.pop(name, None)
        if old is not None and _canon(old.value) == _canon(value):
            if as_utc(when) > as_utc(old.observed_at):
                old.observed_at = when             # re-observed: the fact is confirmed again
            old.confidence = confidence
            continue
        if old is not None:
            old.superseded_at, old.superseded_reason = now, "changed"
        vf, vu = (valid or {}).get(name, (None, None))
        session.add(FactAssertion(canonical_entity_id=canonical_id, source_entity_id=se.id, source_id=se.source_id,
                                  source_class=source_class, field_name=name, value=value, observed_at=when,
                                  valid_from=vf, valid_until=vu, confidence=confidence,
                                  verification_type=verification_type, created_at=now))
        session.add(WorldChange(canonical_entity_id=canonical_id, source_entity_id=se.id, source_id=se.source_id,
                                change_type="field_changed" if old is not None else "field_added", field_name=name,
                                old_value=old.value if old is not None else None, new_value=value, detected_at=now))
        changed.append(name)
    if retract_missing:
        for name, old in current.items():
            old.superseded_at, old.superseded_reason = now, "retracted"
            session.add(WorldChange(canonical_entity_id=canonical_id, source_entity_id=se.id, source_id=se.source_id,
                                    change_type="field_retracted", field_name=name, old_value=old.value,
                                    detected_at=now))
            changed.append(name)
    return changed


def current_assertions(session: Session, canonical_id: str) -> list[FactAssertion]:
    return list(session.scalars(select(FactAssertion).where(
        FactAssertion.canonical_entity_id == canonical_id, FactAssertion.superseded_at.is_(None))
        .order_by(FactAssertion.field_name, FactAssertion.created_at)))


# ================================================================ resolve
@dataclass
class Resolution:
    field: str
    state: str
    value: Any = None
    winner: FactAssertion | None = None
    supporting: list[FactAssertion] = field(default_factory=list)
    conflicting: list[FactAssertion] = field(default_factory=list)
    confidence: float = 0.0
    freshness: str = Freshness.UNKNOWN.value
    reason: str = ""

    def summary(self) -> dict[str, Any]:
        out = {"state": self.state, "reason": self.reason, "freshness": self.freshness,
               "confidence": round(self.confidence, 3),
               "supporting": sorted({a.source_id for a in self.supporting}),
               "conflicting": [{"source": a.source_id, "value": a.value} for a in self.conflicting]}
        if self.winner is not None:
            out["source"] = self.winner.source_id
            out["source_class"] = self.winner.source_class
            out["observed_at"] = as_utc(self.winner.observed_at).isoformat()
        return out


@dataclass
class _Group:
    value: Any
    members: list[FactAssertion]
    rank: int

    @property
    def sources(self) -> set[str]:
        return {a.source_id for a in self.members}

    @property
    def newest(self) -> datetime:
        return max(as_utc(a.observed_at) for a in self.members)


def _groups(assertions: list[FactAssertion], policy: FieldPolicy) -> list[_Group]:
    groups: list[_Group] = []
    for a in sorted(assertions, key=lambda a: (policy.rank(a.source_class), -as_utc(a.observed_at).timestamp())):
        g = next((g for g in groups if values_equal(policy, g.value, a.value)), None)
        if g is None:
            groups.append(_Group(a.value, [a], policy.rank(a.source_class)))
        else:
            g.members.append(a)
            g.rank = min(g.rank, policy.rank(a.source_class))
    return groups


def _fresh(a: FactAssertion, policy: FieldPolicy, now: datetime) -> Freshness:
    return classify(policy.fact_class, a.observed_at, now, a.confidence).state


def resolve(assertions: list[FactAssertion], policy: FieldPolicy, policies: Policies, now: datetime,
            at: datetime | None = None) -> Resolution:
    at = as_utc(at or now)
    valid = [a for a in assertions if (a.valid_from is None or as_utc(a.valid_from) <= at)
             and (a.valid_until is None or as_utc(a.valid_until) > at)]
    if not valid:
        return Resolution(policy.name, UNKNOWN, reason="no source asserts this")
    not_stale = [a for a in valid if _fresh(a, policy, now) != Freshness.STALE]
    pool = not_stale or valid
    groups = _groups(pool, policy)
    best = min(g.rank for g in groups)
    top = [g for g in groups if g.rank == best]
    state, reason = RESOLVED, ""
    if len(top) == 1:
        winner = top[0]
        reason = f"{winner.members[0].source_class} has the strongest authority for {policy.name}"
    else:
        counts = sorted((len(g.sources) for g in top), reverse=True)
        newest = sorted(top, key=lambda g: g.newest, reverse=True)
        if policies.majority_wins and counts[0] > counts[1]:
            winner = max(top, key=lambda g: len(g.sources))
            state, reason = CONTESTED, f"{len(winner.sources)} sources agree against {counts[1]} at equal authority"
        elif newest[0].newest - newest[1].newest >= policies.same_rank_recency_gap:
            winner = newest[0]
            gap = (newest[0].newest - newest[1].newest).days
            state, reason = CONTESTED, f"newer by {gap} days at equal authority"
        elif policy.high_risk:
            conflicting = [a for g in top for a in g.members]
            return Resolution(policy.name, NEEDS_VERIFICATION, None, None, [], conflicting, 0.0,
                              _fresh(newest[0].members[0], policy, now).value,
                              f"equally authoritative sources disagree on a high-risk fact ({policy.name})")
        else:
            winner = newest[0]
            state, reason = CONFLICTED, "equally authoritative sources disagree; newest shown, flagged"
    conflicting = [a for g in groups if g is not winner for a in g.members]
    stale_stronger = [a for a in valid if a not in pool and policy.rank(a.source_class) < winner.rank
                      and not values_equal(policy, a.value, winner.value)]
    if stale_stronger and policy.high_risk:
        return Resolution(policy.name, NEEDS_VERIFICATION, None, None, winner.members,
                          conflicting + stale_stronger, 0.0, Freshness.STALE.value,
                          f"a stronger source ({stale_stronger[0].source_class}) disagrees but is stale; "
                          f"the weaker {winner.members[0].source_class} cannot override it")
    conflicting += stale_stronger
    # stale WEAKER sources that disagree are dissent too: never silently erased
    conflicting += [a for a in valid if a not in pool and a not in stale_stronger
                    and not values_equal(policy, a.value, winner.value)]
    if conflicting and state == RESOLVED:
        state = CONTESTED
        reason += f"; {len({a.source_id for a in conflicting})} weaker source(s) disagree"
    lead = max(winner.members, key=lambda a: (-policy.rank(a.source_class), as_utc(a.observed_at)))
    conf = 1.0
    for src in winner.sources:
        c = max(a.confidence for a in winner.members if a.source_id == src)
        conf *= (1 - c)
    conf = 1 - conf
    conf *= {RESOLVED: 1.0, CONTESTED: 0.85, CONFLICTED: 0.5}[state]
    newest_member = max(winner.members, key=lambda a: as_utc(a.observed_at))
    return Resolution(policy.name, state, winner.value, lead, winner.members, conflicting, conf,
                      _fresh(newest_member, policy, now).value, reason)


def resolve_entity(session: Session, canonical_id: str, policies: Policies, now: datetime,
                   at: datetime | None = None) -> dict[str, Resolution]:
    by_field: dict[str, list[FactAssertion]] = {}
    for a in current_assertions(session, canonical_id):
        by_field.setdefault(a.field_name, []).append(a)
    return {name: resolve(items, policies.field(name), policies, now, at) for name, items in by_field.items()}
