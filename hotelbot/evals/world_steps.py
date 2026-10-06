"""World-data steps and checks for the evaluation harness.

A scenario declares `world_sources`; each `world:` step performs one fabric
operation (sync a source's records, apply a correction, split / merge,
reconcile, snapshot round-trip). Expectations look at the evidence
(source entities, links, assertions), the resolution, and the projection
the discovery engine reads. Records use the generic directory-like format
(app/world/normalize.py) with coordinates in the demo region.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Action,
    CanonicalEntity,
    EntityLink,
    Event,
    ExternalProvider,
    ExternalTransaction,
    FactAssertion,
    MatchReview,
    Offering,
    Place,
    Quote,
    SourceEntity,
    WorldChange,
    WorldSourceRow,
)
from app.world import corrections, identity, snapshots
from app.world.adapters import FixtureAdapter, SourceDescriptor
from app.world.facts import resolve
from app.world.ingest import active_canonical, reconcile_all, run_sync
from app.world.policies import load
from evals.schema import Scenario, WorldExpect, WorldRef, WorldStep

DEFAULT_CONFIG = {"country_code": "ME", "timezone": "Europe/Podgorica"}


class WorldState:
    """Per-scenario adapters (a source keeps its feed across steps)."""

    def __init__(self, scenario: Scenario) -> None:
        self.descriptors = {}
        for spec in scenario.world_sources:
            self.descriptors[spec.source_id] = SourceDescriptor(
                source_id=spec.source_id, source_type=spec.source_type, source_class=spec.source_class,
                name=spec.name or spec.source_id, format="generic.v1", license=spec.license,
                attribution_required=spec.attribution_required, attribution_text=spec.attribution_text,
                redistribution=spec.redistribution, retention_days=spec.retention_days, cache_raw=spec.cache_raw,
                default_confidence=spec.default_confidence, config={**DEFAULT_CONFIG, **spec.config})
        self.adapters: dict[str, FixtureAdapter] = {}
        self.last_report = None
        self.txn_hash: str | None = None


def _txn_hash(session: Session) -> str:
    rows = []
    for model in (Quote, ExternalTransaction, Action, Offering):
        for obj in session.scalars(select(model)):
            rows.append(json.dumps({c.key: str(getattr(obj, c.key)) for c in model.__table__.columns
                                    if c.key not in ("updated_at",)}, sort_keys=True))
    return hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest()


def ref_entity(session: Session, ref: WorldRef | dict) -> str | None:
    ref = ref if isinstance(ref, WorldRef) else WorldRef(**ref)
    se = session.scalar(select(SourceEntity).where(SourceEntity.source_id == ref.source,
                                                   SourceEntity.source_record_id == ref.id))
    return active_canonical(session, se.id) if se is not None else None


def run_world_step(step: WorldStep, state: WorldState, container, failures: list[str], prefix: str) -> str:  # noqa: ANN001
    now = container.clock.now()
    with container.session_factory() as s:
        state.txn_hash = _txn_hash(s)
        if step.sync is not None:
            p = step.sync
            src = p["source"]
            d = state.descriptors[src]
            adapter = state.adapters.get(src)
            if adapter is None:
                adapter = state.adapters[src] = FixtureAdapter(d)
            if "records" in p:
                adapter.records = list(p["records"])
            if "changes" in p:
                adapter.changes = adapter.changes + list(p["changes"])
            adapter.fail_after, adapter.served = p.get("fail_after"), 0
            adapter.page_size = p.get("page_size", 50)
            mode = p.get("mode", "full")
            if p.get("via_job"):
                from app.world.jobs import schedule_sync

                container.world_adapters[src] = adapter
                schedule_sync(s, container, src, mode=mode)
                s.commit()
                container.worker.drain(advance_clock=bool(p.get("retry", False)), max_rounds=20)
                with container.session_factory() as s2:
                    row = s2.get(WorldSourceRow, src)
                    state.last_report = type("R", (), {"status": "ok" if row.status == "healthy" else "failed",
                                                      "as_dict": lambda self=None: {"status": row.status}})()
                return f"[world sync {src} via job -> {row.status}]"
            report = run_sync(s, adapter, now=now, mode=mode, checkpoint=lambda sess: sess.commit())
            s.commit()
            state.last_report = report
            return f"[world sync {src} {mode}] {report.as_dict()}"
        if step.correction is not None:
            p = step.correction
            cid = ref_entity(s, p["entity"])
            actor_ref = p.get("actor_ref")
            if p.get("actor") == "provider" and actor_ref:
                prov = s.scalar(select(ExternalProvider).where(ExternalProvider.slug == actor_ref))
                actor_ref = prov.id if prov else actor_ref
            try:
                valid_until = datetime.fromisoformat(p["valid_until"]) if p.get("valid_until") else None
                corrections.submit(s, cid, p["field"], p["value"], actor_type=p["actor"], actor_ref=actor_ref,
                                   now=now, note=p.get("note"), valid_until=valid_until)
                s.commit()
                return f"[correction {p['actor']} {p['field']}={p['value']!r}]"
            except PermissionError as exc:
                s.rollback()
                if not p.get("expect_refused"):
                    failures.append(f"{prefix}: correction refused: {exc}")
                return f"[correction refused: {exc}]"
        if step.split is not None:
            ref = WorldRef(**step.split["entity"])
            se = s.scalar(select(SourceEntity).where(SourceEntity.source_id == ref.source,
                                                     SourceEntity.source_record_id == ref.id))
            identity.split(s, se.id, reason=step.split.get("reason", "eval split"), actor="eval", now=now)
            s.commit()
            return f"[split {ref.source}/{ref.id}]"
        if step.merge is not None:
            keep, other = ref_entity(s, step.merge["keep"]), ref_entity(s, step.merge["other"])
            identity.merge(s, keep, other, reason="eval merge", actor="eval", now=now)
            s.commit()
            return "[merge]"
        if step.revert_merge is not None:
            ref = WorldRef(**step.revert_merge["other"])
            se = s.scalar(select(SourceEntity).where(SourceEntity.source_id == ref.source,
                                                     SourceEntity.source_record_id == ref.id))
            # the entity this record belonged to before the merge
            first = s.scalar(select(EntityLink).where(EntityLink.source_entity_id == se.id)
                             .order_by(EntityLink.created_at, EntityLink.id))
            prior = [lk for lk in s.scalars(select(EntityLink).where(EntityLink.source_entity_id == se.id))
                     if (lk.evidence or {}).get("moved_from")]
            other_id = prior[-1].evidence["moved_from"] if prior else first.canonical_entity_id
            identity.revert_merge(s, other_id, actor="eval", now=now)
            s.commit()
            return "[revert merge]"
        if step.resolve_review is not None:
            cid = ref_entity(s, step.resolve_review["entity"])
            se_ids = [lk.source_entity_id for lk in s.scalars(select(EntityLink).where(
                EntityLink.canonical_entity_id == cid, EntityLink.active))]
            review = s.scalar(select(MatchReview).where(MatchReview.source_entity_id.in_(se_ids),
                                                        MatchReview.status == "open"))
            same = ref_entity(s, step.resolve_review["same_as"]) if step.resolve_review.get("same_as") else None
            identity.resolve_review(s, review.id, same_as=same, actor="eval", now=now)
            s.commit()
            return "[review resolved]"
        if step.link_provider is not None:
            cid = ref_entity(s, step.link_provider["entity"])
            prov = s.scalar(select(ExternalProvider).where(ExternalProvider.slug == step.link_provider["provider"]))
            identity.link_marketplace(s, cid, provider_id=prov.id, established_by="partner_mapping", now=now)
            s.commit()
            return "[provider linked]"
        if step.reconcile is not None:
            changed = reconcile_all(s, now)
            s.commit()
            return f"[existence reconciled: {len(changed)} changed]"
        if step.check is not None:
            from app.world.ingest import source_health

            source_health(s, now)
            s.commit()
            return "[world check]"
        if step.snapshot is not None:
            ok = _snapshot_roundtrip(s, now, failures, prefix)
            return f"[snapshot round-trip: {'identical' if ok else 'DIFFERENT'}]"
    return "[world check]"


def _projection(session: Session) -> list[tuple]:
    rows = []
    for p in session.scalars(select(Place).where(Place.canonical_entity_id.is_not(None))):
        rows.append(("place", p.region, p.slug, p.name, p.subcategory, p.latitude, p.longitude,
                     json.dumps(p.hours, sort_keys=True), json.dumps(p.attributes, sort_keys=True), p.active,
                     json.dumps({k: (v.get("state") if isinstance(v, dict) else v)
                                 for k, v in (p.resolution or {}).items()}, sort_keys=True, default=str)))
    for e in session.scalars(select(Event).where(Event.canonical_entity_id.is_not(None))):
        rows.append(("event", e.region, e.slug, json.dumps(e.title, sort_keys=True), str(e.start_at), e.active))
    return sorted(rows, key=str)


def _snapshot_roundtrip(session: Session, now: datetime, failures: list[str], prefix: str) -> bool:
    from sqlalchemy.orm import sessionmaker

    from app.db.session import create_schema, make_engine

    snap = snapshots.create(session, f"eval-{now.timestamp()}", now)
    session.commit()
    before = _projection(session)
    engine = make_engine("sqlite://")
    create_schema(engine)
    with sessionmaker(engine)() as fresh:
        snapshots.restore(fresh, snap.content, now)
        fresh.commit()
        after = _projection(fresh)
        same_evidence = snapshots.evidence_hash(fresh) == snap.content_hash
    engine.dispose()
    if before != after:
        failures.append(f"{prefix}: snapshot restore produced a different projection "
                        f"({len(before)} vs {len(after)} rows)")
    if not same_evidence:
        failures.append(f"{prefix}: snapshot evidence hash differs after restore")
    return before == after and same_evidence


def check_world(exp: WorldExpect, state: WorldState, container, failures: list[str], prefix: str) -> None:  # noqa: ANN001
    policies = load()
    now = container.clock.now()
    with container.session_factory() as s:
        srcs = list(state.descriptors)
        ses = list(s.scalars(select(SourceEntity).where(SourceEntity.source_id.in_(srcs))))
        if exp.entities is not None:
            ids = {active_canonical(s, se.id) for se in ses}
            active = {i for i in ids if i and s.get(CanonicalEntity, i).active}
            if len(active) != exp.entities:
                failures.append(f"{prefix}: {len(active)} active entities behind the sources, expected {exp.entities}")
        for group in exp.same:
            ids = [ref_entity(s, r) for r in group]
            if None in ids or len(set(ids)) != 1:
                failures.append(f"{prefix}: expected one entity for {[f'{r.source}/{r.id}' for r in group]}")
        for group in exp.distinct:
            ids = [ref_entity(s, r) for r in group]
            if len(set(ids)) != len(ids):
                failures.append(f"{prefix}: expected separate entities for {[f'{r.source}/{r.id}' for r in group]}")
        for rx in exp.resolution:
            cid = ref_entity(s, rx.entity)
            items = list(s.scalars(select(FactAssertion).where(FactAssertion.canonical_entity_id == cid,
                                                               FactAssertion.field_name == rx.field,
                                                               FactAssertion.superseded_at.is_(None))))
            r = resolve(items, policies.field(rx.field), policies, now)
            tag = f"{prefix}: {rx.field} of {rx.entity.source}/{rx.entity.id}"
            if rx.value is not None and r.value != rx.value:
                failures.append(f"{tag} = {r.value!r} ({r.state}: {r.reason}), expected {rx.value!r}")
            if rx.has_value is not None and (r.value is not None) != rx.has_value:
                failures.append(f"{tag}: has value {r.value!r}, expected has_value={rx.has_value}")
            if rx.state is not None and r.state != rx.state:
                failures.append(f"{tag}: state {r.state} ({r.reason}), expected {rx.state}")
            if rx.source is not None and (r.winner is None or r.winner.source_id != rx.source):
                failures.append(f"{tag}: winner {r.winner.source_id if r.winner else None}, expected {rx.source}")
            if rx.conflicting is not None and len(r.conflicting) != rx.conflicting:
                failures.append(f"{tag}: {len(r.conflicting)} conflicting kept, expected {rx.conflicting}")
        for px in exp.places:
            cid = ref_entity(s, px.entity)
            place = s.scalar(select(Place).where(Place.canonical_entity_id == cid)) or \
                s.scalar(select(Event).where(Event.canonical_entity_id == cid))
            tag = f"{prefix}: projection of {px.entity.source}/{px.entity.id}"
            if place is None:
                failures.append(f"{tag}: missing")
                continue
            if px.active is not None and place.active != px.active:
                failures.append(f"{tag}: active={place.active}, expected {px.active}")
            if px.name is not None and getattr(place, "name", None) != px.name:
                failures.append(f"{tag}: name={getattr(place, 'name', None)!r}, expected {px.name!r}")
            exist = (place.resolution or {}).get("_existence", {}).get("state")
            if px.existence is not None and exist != px.existence:
                failures.append(f"{tag}: existence={exist}, expected {px.existence}")
            tier = (getattr(place, "quality", None) or {}).get("tier")
            if px.quality_tier is not None and tier != px.quality_tier:
                failures.append(f"{tag}: quality tier {tier}, expected {px.quality_tier}")
            if px.attribution is not None and (place.resolution or {}).get("_attribution") != px.attribution:
                failures.append(f"{tag}: attribution {(place.resolution or {}).get('_attribution')}, "
                                f"expected {px.attribution}")
        if exp.source_entities is not None and len(ses) != exp.source_entities:
            failures.append(f"{prefix}: {len(ses)} source records kept, expected {exp.source_entities}")
        if exp.links_ended is not None:
            ended = len(list(s.scalars(select(EntityLink).where(
                EntityLink.active.is_(False), EntityLink.source_entity_id.in_([x.id for x in ses])))))
            if ended != exp.links_ended:
                failures.append(f"{prefix}: {ended} ended links kept as history, expected {exp.links_ended}")
        if exp.reviews_open is not None:
            n = len(list(s.scalars(select(MatchReview).where(MatchReview.status == "open",
                                                             MatchReview.source_entity_id.in_([x.id for x in ses])))))
            if n != exp.reviews_open:
                failures.append(f"{prefix}: {n} open match reviews, expected {exp.reviews_open}")
        for src, status in exp.source_health.items():
            row = s.get(WorldSourceRow, src)
            if row is None or row.status != status:
                failures.append(f"{prefix}: source {src} status {row.status if row else None}, expected {status}")
        recorded = {c.change_type for c in s.scalars(select(WorldChange))}
        for ct in exp.changes:
            if ct not in recorded:
                failures.append(f"{prefix}: change {ct!r} not recorded (have {sorted(recorded)})")
        if exp.transactions_unchanged and state.txn_hash is not None and _txn_hash(s) != state.txn_hash:
            failures.append(f"{prefix}: world operation changed transaction-world rows")
        if exp.license_preserved:
            for src, d in state.descriptors.items():
                row = s.get(WorldSourceRow, src)
                if row is None:
                    continue
                if (row.license, row.attribution_required, row.attribution_text, row.redistribution,
                        row.retention_days) != (d.license, d.attribution_required, d.attribution_text,
                                                d.redistribution, d.retention_days):
                    failures.append(f"{prefix}: licence metadata of {src} not preserved")
        if exp.no_traveler_data:
            dump = json.dumps([[se.raw, se.normalized] for se in s.scalars(select(SourceEntity))], default=str)
            dump += json.dumps([[a.value] for a in s.scalars(select(FactAssertion))], default=str)
            from app.db.models import WorldCorrection

            dump += json.dumps([[c.actor_ref, c.note, c.value] for c in s.scalars(select(WorldCorrection))],
                               default=str)
            for needle in exp.no_traveler_data:
                if needle.lower() in dump.lower():
                    failures.append(f"{prefix}: traveller data {needle!r} found in world records")
        for ref in exp.raw_present + exp.raw_absent:
            se = s.scalar(select(SourceEntity).where(SourceEntity.source_id == ref.source,
                                                     SourceEntity.source_record_id == ref.id))
            want = ref in exp.raw_present
            if se is None or (se.raw is not None) != want:
                failures.append(f"{prefix}: raw payload of {ref.source}/{ref.id} present={se.raw is not None if se else None}, "
                                f"expected {want}")
        if exp.sync_status is not None:
            got = getattr(state.last_report, "status", None)
            if got != exp.sync_status:
                failures.append(f"{prefix}: sync status {got}, expected {exp.sync_status}")
