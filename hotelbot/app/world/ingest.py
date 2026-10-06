"""The fabric pipeline: SOURCE -> SOURCE ENTITY -> ENTITY RESOLUTION ->
FACT ASSERTIONS -> RESOLUTION -> PROJECTION.

    run_sync(session, adapter, scope, mode)

* Idempotent: an unchanged record (same payload hash) only refreshes
  last_seen_at; replaying a page or a whole sync changes nothing else.
* Checkpointed: the cursor is stored after every page (`checkpoint` hook can
  commit), so an interrupted sync resumes; `run_started_at` survives a resume.
* Safe on failure: a source error marks the source FAILING with the error;
  nothing already known is removed or deactivated.
* Tombstones only after a COMPLETED full sync, for the records of that
  source in that scope that were not seen - at the SOURCE level. The
  canonical entity is deactivated only by policy (existence rules).
* Every change is recorded in world_changes.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.clock import as_utc
from app.db.models import (
    CanonicalEntity,
    EntityAlias,
    EntityIdentifier,
    EntityLink,
    MatchReview,
    SourceEntity,
    WorldChange,
    WorldSourceRow,
)
from app.observability import log_event
from app.shared import geohash
from app.text import fold
from app.world import coverage, facts, matching, projection
from app.world.adapters import Scope, SourceAdapter, SourceDescriptor, SourceError, SourceRecord
from app.world.normalize import NormalizedEntity, normalize
from app.world.policies import Policies, load


@dataclass
class SyncReport:
    source_id: str
    mode: str
    status: str = "ok"
    seen: int = 0
    created: int = 0
    changed: int = 0
    unchanged: int = 0
    tombstoned: int = 0
    deleted_at_source: int = 0
    matched: int = 0
    ambiguous: int = 0
    error: str | None = None
    affected: set[str] = field(default_factory=set)
    seen_ids: set[str] = field(default_factory=set)

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k not in ("affected", "seen_ids")} | \
            {"affected": len(self.affected)}


# ============================================================== sources
TERMS = ("license", "attribution_required", "attribution_text", "redistribution", "retention_days", "cache_raw")


def register_source(session: Session, d: SourceDescriptor, policies: Policies | None = None,
                    now: datetime | None = None) -> WorldSourceRow:
    """Register / update a source. A change of licence terms is never silent:
    the previous terms go to `config.terms_history` (with the time they
    ended) and a WorldChange is recorded; data published under earlier terms
    keeps their attribution (projection._attribution)."""
    from datetime import timezone

    d.validate((policies or load()).source_classes)
    now = now or datetime.now(timezone.utc)
    existing = session.get(WorldSourceRow, d.source_id)
    row = existing or WorldSourceRow(id=d.source_id, status="never_synced", consecutive_failures=0, records_seen=0,
                                     records_changed=0, cursor={})
    history = list((existing.config or {}).get("terms_history", [])) if existing else []
    if existing is not None:
        old = {k: getattr(existing, k) for k in TERMS}
        new = {k: getattr(d, k) for k in TERMS}
        if old != new:
            row.terms_changed = True          # transient flag: the caller re-projects this source's entities
            history.append({**old, "until": now.isoformat()})
            session.add(WorldChange(source_id=d.source_id, change_type="source_terms_changed", old_value=old,
                                    new_value=new, detected_at=now))
    row.source_type, row.source_class, row.name, row.format = d.source_type, d.source_class, d.name, d.format
    row.license, row.attribution_required, row.attribution_text = d.license, d.attribution_required, d.attribution_text
    row.redistribution, row.retention_days, row.cache_raw = d.redistribution, d.retention_days, d.cache_raw
    row.default_confidence = d.default_confidence
    row.config = {**dict(d.config), **({"terms_history": history} if history else {})}
    session.add(row)
    session.flush()
    return row


def _hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")[:90] or "entity"


def _unique_slug(session: Session, entity_type: str, region: str | None, base: str) -> str:
    slug, n = base, 1
    while session.scalar(select(CanonicalEntity.id).where(CanonicalEntity.region == region,
                                                          CanonicalEntity.canonical_slug == slug,
                                                          CanonicalEntity.entity_type == entity_type)):
        n += 1
        slug = f"{base}-{n}"
    return slug


def _normalized_json(ent: NormalizedEntity) -> dict[str, Any]:
    return {"fields": ent.fields, "identifiers": [list(i) for i in ent.identifiers],
            "aliases": [list(a) for a in ent.aliases], "venue_ref": ent.venue_ref, "slug_hint": ent.slug_hint,
            "region_hint": ent.region_hint, "confidence": ent.confidence, "meta": ent.meta,
            "venue_canonical": ent.meta.get("venue_canonical"),
            "observed": {k: (v.isoformat() if v else None) for k, v in ent.observed.items()}}


# ============================================================ canonical
def create_canonical(session: Session, ent: NormalizedEntity, region: str | None, country: str | None,
                     now: datetime) -> CanonicalEntity:
    name = ent.name or ent.record_id
    base = ent.slug_hint or _slugify(name)
    c = CanonicalEntity(entity_type=ent.entity_type, canonical_name=name[:300],
                        canonical_slug=_unique_slug(session, ent.entity_type, region, base), region=region,
                        country_code=country, latitude=ent.latitude, longitude=ent.longitude,
                        geohash=geohash.encode(ent.latitude, ent.longitude) if ent.latitude is not None else None,
                        starts_at=datetime.fromisoformat(ent.fields["start_at"]) if ent.fields.get("start_at") else None,
                        active=True, created_at=now, updated_at=now)
    session.add(c)
    session.flush()
    session.add(WorldChange(canonical_entity_id=c.id, change_type="entity_created", new_value=name, detected_at=now))
    return c


def link(session: Session, se: SourceEntity, canonical_id: str, *, method: str, score: float | None,
         evidence: dict[str, Any], now: datetime, decided_by: str = "resolver") -> EntityLink:
    for old in session.scalars(select(EntityLink).where(EntityLink.source_entity_id == se.id, EntityLink.active)):
        old.active, old.ended_at, old.end_reason = False, now, f"relinked ({method})"
    lk = EntityLink(source_entity_id=se.id, canonical_entity_id=canonical_id, active=True, method=method,
                    score=score, evidence=evidence, decided_by=decided_by, created_at=now)
    session.add(lk)
    session.flush()
    return lk


def active_canonical(session: Session, se_id: str) -> str | None:
    return session.scalar(select(EntityLink.canonical_entity_id).where(EntityLink.source_entity_id == se_id,
                                                                       EntityLink.active))


def _write_aliases(session: Session, se: SourceEntity, canonical_id: str, ent: NormalizedEntity) -> None:
    session.execute(update(EntityAlias).where(EntityAlias.source_entity_id == se.id)
                    .values(canonical_entity_id=canonical_id))
    have = {(a.folded, a.language) for a in session.scalars(
        select(EntityAlias).where(EntityAlias.source_entity_id == se.id))}
    for alias, lang, kind in ent.aliases:
        if (fold(alias), lang) not in have:
            session.add(EntityAlias(canonical_entity_id=canonical_id, source_entity_id=se.id, alias=alias[:300],
                                    folded=fold(alias)[:300], language=lang, kind=kind))


def _write_identifiers(session: Session, se: SourceEntity, ent: NormalizedEntity) -> None:
    have = {(i.kind, i.value) for i in session.scalars(select(EntityIdentifier)
                                                       .where(EntityIdentifier.source_entity_id == se.id))}
    want = set(ent.identifiers)
    for kind, value in want - have:
        session.add(EntityIdentifier(source_entity_id=se.id, kind=kind, value=value[:300]))
    for i in session.scalars(select(EntityIdentifier).where(EntityIdentifier.source_entity_id == se.id)):
        if (i.kind, i.value) not in want:
            session.delete(i)


# ============================================================== records
def _venue(session: Session, source_id: str, ent: NormalizedEntity) -> None:
    if ent.venue_ref:
        se = session.scalar(select(SourceEntity).where(SourceEntity.source_id == source_id,
                                                       SourceEntity.source_record_id == ent.venue_ref))
        canonical = active_canonical(session, se.id) if se is not None else None
        if canonical:
            ent.meta["venue_canonical"] = canonical
            ent.fields["venue"] = canonical
            ent.observed.setdefault("venue", ent.observed.get("title") or ent.observed.get("name"))


def process_record(session: Session, src: WorldSourceRow, d: SourceDescriptor, rec: SourceRecord, now: datetime,
                   report: SyncReport, policies: Policies, locality: set[str], scope: Scope) -> None:
    se = session.scalar(select(SourceEntity).where(SourceEntity.source_id == d.source_id,
                                                   SourceEntity.source_record_id == rec.record_id))
    if rec.deleted:
        if se is not None and se.active_at_source:
            _tombstone(session, se, now, "deleted at source")
            report.deleted_at_source += 1
            if (c := active_canonical(session, se.id)):
                report.affected.add(c)
        return
    if rec.kind == "taxonomy":
        from app.places.taxonomy import register_subcategory

        p = rec.payload
        register_subcategory(p["key"], p["category"], p.get("labels", {}), tuple(p.get("keywords", ())))
        return
    report.seen += 1
    report.seen_ids.add(rec.record_id)
    raw_hash = _hash(rec.payload)
    if se is not None and se.raw_hash == raw_hash and se.active_at_source:
        se.last_seen_at = se.last_synced_at = now
        report.unchanged += 1
        return
    ent = normalize(rec, d)
    _venue(session, d.source_id, ent)
    confidence = ent.confidence if ent.confidence is not None else d.default_confidence
    new = se is None
    if new:
        se = SourceEntity(source_id=d.source_id, source_type=d.source_type, source_record_id=rec.record_id,
                          entity_type=ent.entity_type, first_seen_at=now)
        session.add(se)
    elif not se.active_at_source:
        se.active_at_source, se.tombstoned_at = True, None
        session.add(WorldChange(source_entity_id=se.id, source_id=d.source_id, change_type="reappeared_at_source",
                                canonical_entity_id=active_canonical(session, se.id), detected_at=now))
    se.raw = dict(rec.payload) if d.cache_raw else None
    se.raw_hash, se.normalized, se.issues = raw_hash, _normalized_json(ent), list(ent.issues)
    se.source_url = rec.url
    se.latitude, se.longitude = ent.latitude, ent.longitude
    se.geohash = geohash.encode(ent.latitude, ent.longitude) if ent.latitude is not None else None
    se.observed_at = rec.observed_at
    se.last_seen_at = se.last_synced_at = now
    session.flush()
    if new:
        report.created += 1
        decision = matching.resolve(session, ent, d.source_id, locality)
        if decision.outcome == matching.MATCH:
            canonical_id = decision.canonical_id
            link(session, se, canonical_id, method="auto_match", score=decision.score, evidence=decision.evidence,
                 now=now)
            report.matched += 1
            session.add(WorldChange(canonical_entity_id=canonical_id, source_entity_id=se.id, source_id=d.source_id,
                                    change_type="linked", detail=decision.evidence, detected_at=now))
        else:
            area = coverage.assign(session, ent.latitude, ent.longitude, scope.area_code or ent.region_hint)
            region = ent.region_hint or (area.code if area else None)
            if ent.region_hint is None and area is not None:
                region = area.code
            c = create_canonical(session, ent, region, (area.country_code if area else None)
                                 or d.config.get("country_code"), now)
            canonical_id = c.id
            link(session, se, canonical_id, method="new", score=1.0 if decision.outcome == matching.NO_MATCH else 0.5,
                 evidence=decision.evidence or {}, now=now)
            if decision.outcome == matching.AMBIGUOUS:
                report.ambiguous += 1
                session.add(MatchReview(source_entity_id=se.id, candidates=decision.candidates, status="open",
                                        created_at=now))
                session.add(WorldChange(canonical_entity_id=canonical_id, source_entity_id=se.id,
                                        source_id=d.source_id, change_type="ambiguous_match_kept_separate",
                                        detail={"candidates": decision.candidates}, detected_at=now))
    else:
        report.changed += 1
        canonical_id = active_canonical(session, se.id)
        if canonical_id is None:                 # split leftovers: give it an entity again
            c = create_canonical(session, ent, ent.region_hint, d.config.get("country_code"), now)
            canonical_id = c.id
            link(session, se, canonical_id, method="new", score=1.0, evidence={}, now=now)
    old_ids = {(i.kind, i.value) for i in session.scalars(select(EntityIdentifier)
                                                          .where(EntityIdentifier.source_entity_id == se.id))}
    _write_identifiers(session, se, ent)
    if not new:
        canonical_id = _rematch_on_new_identifier(session, se, ent, d, canonical_id, old_ids, locality, now,
                                                  policies, report)
    _write_aliases(session, se, canonical_id, ent)
    facts.write_assertions(session, se, canonical_id, ent.fields, ent.observed, source_class=d.source_class,
                           confidence=confidence, now=now)
    report.affected.add(canonical_id)


def _rematch_on_new_identifier(session: Session, se: SourceEntity, ent: NormalizedEntity, d: SourceDescriptor,
                               canonical_id: str, old_ids: set, locality: set[str], now: datetime,
                               policies: Policies, report: SyncReport) -> str:
    """Identity evidence can arrive LATER: a record re-sent with an explicit
    shared identifier (organizer id, partner id) that another entity already
    carries. Only that - an explicit identifier - may merge after the fact
    (through the revertible merge); name or distance changes never do."""
    fresh = {(k, v) for k, v in ent.identifiers if k.startswith("ext:") and k != f"ext:{d.source_id}"} - old_ids
    if not fresh:
        return canonical_id
    decision = matching.resolve(session, ent, d.source_id, locality)
    if decision.outcome != matching.MATCH or decision.canonical_id in (None, canonical_id) or \
            decision.evidence.get("rule") != "shared explicit identifier":
        return canonical_id
    from app.world import identity

    session.flush()
    identity.merge(session, decision.canonical_id, canonical_id, reason="new shared identifier: " +
                   ", ".join(decision.evidence.get("shared_ids", [])), actor="resolver", now=now, policies=policies)
    report.matched += 1
    report.affected.discard(canonical_id)
    return decision.canonical_id


def _tombstone(session: Session, se: SourceEntity, now: datetime, reason: str) -> None:
    se.active_at_source, se.tombstoned_at = False, now
    session.add(WorldChange(source_entity_id=se.id, source_id=se.source_id, change_type="tombstoned_at_source",
                            canonical_entity_id=active_canonical(session, se.id), detail={"reason": reason},
                            detected_at=now))


# ============================================================ existence
def reconcile_existence(session: Session, canonical_ids: set[str], policies: Policies, now: datetime) -> list[str]:
    """Deactivate a canonical entity ONLY on evidence: an authoritative
    permanent closure, or every linked source tombstoned for the grace
    period. Reactivate when a source lists it again."""
    changed = []
    for cid in sorted(canonical_ids):
        c = session.get(CanonicalEntity, cid)
        if c is None or c.merged_into_id:
            continue
        closed_claims = list(session.scalars(select(facts.FactAssertion).where(
            facts.FactAssertion.canonical_entity_id == cid, facts.FactAssertion.field_name == "permanently_closed",
            facts.FactAssertion.superseded_at.is_(None))))
        res = facts.resolve(closed_claims, policies.field("permanently_closed"), policies, now) \
            if closed_claims else None
        closed = bool(res and res.value is True and res.winner is not None
                      and res.winner.source_class in policies.closed_authority)
        ses = session.scalars(select(SourceEntity).join(EntityLink, EntityLink.source_entity_id == SourceEntity.id)
                              .where(EntityLink.canonical_entity_id == cid, EntityLink.active,
                                     SourceEntity.source_type != "correction")).all()
        all_gone = bool(ses) and all(not s.active_at_source for s in ses)
        expired = all_gone and all(s.tombstoned_at and now - as_utc(s.tombstoned_at) >= policies.existence_grace
                                   for s in ses)
        no_sources = not ses
        should_be_active = not (closed or expired or no_sources)
        if c.active and not should_be_active:
            c.active, c.deactivated_at = False, now
            c.deactivation_reason = ("permanently closed per " + res.winner.source_class) if closed else (
                "no source lists it any more" if no_sources else
                f"every source stopped listing it more than {policies.existence_grace.days} days ago")
            session.add(WorldChange(canonical_entity_id=cid, change_type="entity_deactivated",
                                    detail={"reason": c.deactivation_reason}, detected_at=now))
            changed.append(cid)
        elif not c.active and should_be_active:
            c.active, c.deactivated_at, c.deactivation_reason = True, None, None
            session.add(WorldChange(canonical_entity_id=cid, change_type="entity_reactivated", detected_at=now))
            changed.append(cid)
    return changed


def reconcile_all(session: Session, now: datetime, policies: Policies | None = None) -> list[str]:
    """Periodic existence check over every entity that has a tombstoned
    source (the `world_existence` job) - grace periods expire without a sync."""
    policies = policies or load()
    ids = set(session.scalars(select(EntityLink.canonical_entity_id)
                              .join(SourceEntity, EntityLink.source_entity_id == SourceEntity.id)
                              .where(EntityLink.active, SourceEntity.active_at_source.is_(False))))
    changed = reconcile_existence(session, ids, policies, now)
    project_all(session, set(changed), policies, now)
    return changed


def project_all(session: Session, canonical_ids: set[str], policies: Policies, now: datetime) -> None:
    """Places before events (an event's venue must be projected first)."""
    rows = [session.get(CanonicalEntity, cid) for cid in canonical_ids]
    for c in sorted((r for r in rows if r is not None), key=lambda c: (c.entity_type == "EVENT", c.id)):
        projection.project(session, c, policies, now)
    session.flush()


# ================================================================= sync
def run_sync(session: Session, adapter: SourceAdapter, *, now: datetime, scope: Scope | None = None,
             mode: str = "full", checkpoint: Callable[[Session], None] | None = None,
             policies: Policies | None = None) -> SyncReport:
    policies = policies or load()
    d = adapter.descriptor
    scope = scope or Scope()
    src = register_source(session, d, policies, now)
    report = SyncReport(d.source_id, mode)
    if getattr(src, "terms_changed", False):
        report.affected |= set(session.scalars(
            select(EntityLink.canonical_entity_id).join(SourceEntity, EntityLink.source_entity_id == SourceEntity.id)
            .where(SourceEntity.source_id == d.source_id, EntityLink.active)))
    cursor = dict(src.cursor or {})
    resume = cursor.get("in_progress") if cursor.get("in_progress", {}).get("mode") == mode else None
    started = datetime.fromisoformat(resume["run_started_at"]) if resume else now
    src.last_attempt_at = now
    locality = coverage.locality_words(session)
    try:
        if mode == "full":
            pages = adapter.sync_full(scope, resume.get("cursor") if resume else None)
        else:
            pages = adapter.sync_incremental(scope, cursor.get("incremental", {}))
        for page in pages:
            for rec in page.records:
                process_record(session, src, d, rec, now, report, policies, locality, scope)
            if mode == "incremental":
                cursor["incremental"] = dict(page.cursor)
            cursor["in_progress"] = {"mode": mode, "cursor": dict(page.cursor), "run_started_at": started.isoformat()}
            src.cursor = dict(cursor)
            if not page.last:                     # the final projection happens once, below
                project_all(session, report.affected, policies, now)
            if checkpoint is not None:
                checkpoint(session)
    except SourceError as exc:
        src.status, src.last_error = "failing", str(exc)[:1000]
        src.consecutive_failures = (src.consecutive_failures or 0) + 1
        report.status, report.error = "failed", str(exc)
        session.add(WorldChange(source_id=d.source_id, change_type="sync_failed", detail={"error": str(exc)},
                                detected_at=now))
        session.flush()
        log_event("world_sync_failed", source=d.source_id, error=str(exc)[:200])
        return report
    if mode == "full":
        q = select(SourceEntity).where(SourceEntity.source_id == d.source_id, SourceEntity.active_at_source)
        for se in session.scalars(q).all():
            # seen in this run, or (resumed run) in an earlier attempt of it
            if se.source_record_id in report.seen_ids or (resume and as_utc(se.last_seen_at) >= as_utc(started)):
                continue
            if se.source_record_id.startswith("taxonomy:") or scope.contains(se.latitude, se.longitude) is False:
                continue
            _tombstone(session, se, now, "missing from a completed full sync")
            report.tombstoned += 1
            if (c := active_canonical(session, se.id)):
                report.affected.add(c)
        src.last_full_sync_at = now
    pending = set(session.scalars(select(EntityLink.canonical_entity_id)
                                  .join(SourceEntity, EntityLink.source_entity_id == SourceEntity.id)
                                  .where(SourceEntity.source_id == d.source_id, EntityLink.active,
                                         SourceEntity.active_at_source.is_(False))))
    report.affected |= set(reconcile_existence(session, report.affected | pending, policies, now))
    project_all(session, report.affected, policies, now)
    cursor.pop("in_progress", None)
    src.cursor = cursor
    src.status, src.last_error, src.consecutive_failures = "healthy", None, 0
    src.last_success_at = now
    src.records_seen, src.records_changed = report.seen, report.created + report.changed + report.tombstoned
    session.flush()
    if checkpoint is not None:
        checkpoint(session)
    log_event("world_synced", **report.as_dict())
    return report


def source_health(session: Session, now: datetime) -> list[dict[str, Any]]:
    """A stale or broken source never looks healthy: staleness is computed
    from the source's expected sync interval (config `max_age_hours`)."""
    out = []
    for src in session.scalars(select(WorldSourceRow).order_by(WorldSourceRow.id)):
        status = src.status
        max_age = (src.config or {}).get("max_age_hours")
        if status == "healthy" and max_age and src.last_success_at and \
                (now - as_utc(src.last_success_at)).total_seconds() > max_age * 3600:
            status = src.status = "stale"
        out.append({"source_id": src.id, "status": status, "last_success_at": src.last_success_at,
                    "last_attempt_at": src.last_attempt_at, "error": src.last_error,
                    "records_seen": src.records_seen, "records_changed": src.records_changed,
                    "cursor": src.cursor, "consecutive_failures": src.consecutive_failures})
    return out
