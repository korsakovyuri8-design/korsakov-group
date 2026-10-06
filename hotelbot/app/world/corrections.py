"""Corrections: staff, provider, trusted operator, traveller.

A correction never mutates source-owned data. It is a FactAssertion from a
correction SOURCE ("correction:staff", "correction:provider", ...), whose
authority class comes from policies.yaml (`correction_classes`), so the
resolver weighs it like any other evidence and provenance is preserved.

* provider   may only correct an entity it is explicitly linked to
             (entity_marketplace_links) - a business maintains ITS data;
* traveller  reports carry no identity: the actor is an opaque random
             report id, never a guest id, name or phone (privacy boundary).
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CanonicalEntity, FactAssertion, MarketplaceLink, SourceEntity, WorldCorrection
from app.world import facts
from app.world.adapters import SourceDescriptor
from app.world.ingest import active_canonical, link, project_all, reconcile_existence, register_source
from app.world.policies import Policies, load

ACTORS = ("staff", "provider", "operator", "traveler")


def _source(session: Session, actor_type: str, policies: Policies) -> SourceDescriptor:
    d = SourceDescriptor(source_id=f"correction:{actor_type}", source_type="correction",
                         source_class=policies.correction_classes[actor_type], name=f"{actor_type} corrections",
                         format="correction", license="internal (own data)", default_confidence=0.9 if
                         actor_type != "traveler" else 0.5)
    register_source(session, d, policies)
    return d


def submit(session: Session, canonical_id: str, field: str, value: Any, *, actor_type: str, actor_ref: str | None,
           now: datetime, note: str | None = None, valid_until: datetime | None = None,
           policies: Policies | None = None) -> FactAssertion:
    if actor_type not in ACTORS:
        raise ValueError(f"unknown correction actor {actor_type!r}")
    policies = policies or load()
    canonical = session.get(CanonicalEntity, canonical_id)
    if canonical is None:
        raise ValueError("unknown entity")
    if actor_type == "provider":
        linked = session.scalar(select(MarketplaceLink.id).where(
            MarketplaceLink.canonical_entity_id == canonical_id, MarketplaceLink.provider_id == actor_ref,
            MarketplaceLink.active))
        if linked is None:
            raise PermissionError("a provider may only maintain data of an entity it is explicitly linked to")
    if actor_type == "traveler":
        actor_ref = "report-" + secrets.token_hex(6)          # never a guest identity
    d = _source(session, actor_type, policies)
    record_id = canonical_id
    se = session.scalar(select(SourceEntity).where(SourceEntity.source_id == d.source_id,
                                                   SourceEntity.source_record_id == record_id))
    if se is None:
        se = SourceEntity(source_id=d.source_id, source_type="correction", source_record_id=record_id,
                          entity_type=canonical.entity_type, raw=None, raw_hash="-", normalized={"fields": {}},
                          first_seen_at=now, last_seen_at=now, last_synced_at=now, active_at_source=True)
        session.add(se)
        session.flush()
    if active_canonical(session, se.id) != canonical_id:
        link(session, se, canonical_id, method="correction", score=1.0, evidence={"actor_type": actor_type},
             now=now, decided_by=actor_type)
    se.last_seen_at = se.last_synced_at = now
    verification = {"staff": "staff_verified", "provider": "provider_owned", "operator": "operator_verified",
                    "traveler": "traveler_reported"}[actor_type]
    facts.write_assertions(session, se, canonical_id, {field: value}, {field: now}, source_class=d.source_class,
                           confidence=d.default_confidence, now=now, verification_type=verification,
                           valid={field: (None, valid_until)}, retract_missing=False)
    session.flush()
    assertion = session.scalar(select(FactAssertion).where(
        FactAssertion.source_entity_id == se.id, FactAssertion.field_name == field,
        FactAssertion.superseded_at.is_(None)))
    session.add(WorldCorrection(canonical_entity_id=canonical_id, field_name=field, value=value,
                                actor_type=actor_type, actor_ref=str(actor_ref or actor_type)[:128], note=note,
                                assertion_id=assertion.id, created_at=now))
    changed = set(reconcile_existence(session, {canonical_id}, policies, now))
    project_all(session, {canonical_id} | changed, policies, now)
    return assertion


def withdraw(session: Session, correction_id: str, now: datetime, policies: Policies | None = None) -> None:
    policies = policies or load()
    corr = session.get(WorldCorrection, correction_id)
    if corr is None or corr.withdrawn_at:
        return
    corr.withdrawn_at = now
    a = session.get(FactAssertion, corr.assertion_id)
    if a is not None and a.superseded_at is None:
        a.superseded_at, a.superseded_reason = now, "withdrawn"
    project_all(session, {corr.canonical_entity_id}, policies, now)
