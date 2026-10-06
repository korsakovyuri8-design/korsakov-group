"""Identity operations: split, merge, revert a merge, resolve a review.

Nothing is destroyed: links are ENDED (ended_at, end_reason) and new links
created; assertions keep their source entity and only move to the entity
their source entity now belongs to; a merged-away entity stays as a row
with `merged_into_id` (so old references - plan items, offerings - still
resolve). Every operation is recorded in world_changes.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import (
    CanonicalEntity,
    EntityAlias,
    EntityLink,
    FactAssertion,
    MatchReview,
    SourceEntity,
    WorldChange,
)
from app.world.ingest import active_canonical, create_canonical, link, project_all, reconcile_existence
from app.world.normalize import NormalizedEntity
from app.world.policies import Policies, load


def _move(session: Session, se: SourceEntity, canonical_id: str) -> None:
    session.execute(update(FactAssertion).where(FactAssertion.source_entity_id == se.id)
                    .values(canonical_entity_id=canonical_id))
    session.execute(update(EntityAlias).where(EntityAlias.source_entity_id == se.id)
                    .values(canonical_entity_id=canonical_id))


def _entity_for(se: SourceEntity) -> NormalizedEntity:
    n = se.normalized or {}
    f = n.get("fields", {})
    return NormalizedEntity(se.entity_type, se.source_record_id, fields=f, latitude=se.latitude,
                            longitude=se.longitude, slug_hint=None, region_hint=n.get("region_hint"))


def split(session: Session, source_entity_id: str, *, reason: str, actor: str, now: datetime,
          policies: Policies | None = None) -> str:
    """Take one source entity out of its canonical entity into a new one."""
    policies = policies or load()
    se = session.get(SourceEntity, source_entity_id)
    old = active_canonical(session, se.id)
    old_row = session.get(CanonicalEntity, old) if old else None
    c = create_canonical(session, _entity_for(se), old_row.region if old_row else None,
                         old_row.country_code if old_row else None, now)
    link(session, se, c.id, method="split", score=None, evidence={"split_from": old, "reason": reason}, now=now,
         decided_by=actor)
    _move(session, se, c.id)
    session.add(WorldChange(canonical_entity_id=old, source_entity_id=se.id, change_type="split",
                            detail={"new_entity": c.id, "reason": reason, "actor": actor}, detected_at=now))
    _close_reviews(session, se.id, "split", now)
    affected = {c.id} | ({old} if old else set())
    affected |= set(reconcile_existence(session, affected, policies, now))
    project_all(session, affected, policies, now)
    return c.id


def merge(session: Session, keep_id: str, other_id: str, *, reason: str, actor: str, now: datetime,
          policies: Policies | None = None) -> None:
    """Move every source entity of `other` to `keep`; `other` remains as a
    merged-away row (revertible)."""
    policies = policies or load()
    if keep_id == other_id:
        return
    other = session.get(CanonicalEntity, other_id)
    for se in session.scalars(select(SourceEntity).join(EntityLink, EntityLink.source_entity_id == SourceEntity.id)
                              .where(EntityLink.canonical_entity_id == other_id, EntityLink.active)).all():
        link(session, se, keep_id, method="manual", score=None,
             evidence={"moved_from": other_id, "reason": reason}, now=now, decided_by=actor)
        _move(session, se, keep_id)
        _close_reviews(session, se.id, "merged", now)
    other.merged_into_id, other.active, other.deactivated_at = keep_id, False, now
    other.deactivation_reason = f"merged into {keep_id}"
    session.add(WorldChange(canonical_entity_id=keep_id, change_type="merged",
                            detail={"merged": other_id, "reason": reason, "actor": actor}, detected_at=now))
    project_all(session, {keep_id, other_id}, policies, now)


def revert_merge(session: Session, other_id: str, *, actor: str, now: datetime,
                 policies: Policies | None = None) -> None:
    """Undo merge(): every link created by moving from `other` goes back."""
    policies = policies or load()
    other = session.get(CanonicalEntity, other_id)
    keep_id = other.merged_into_id
    for lk in session.scalars(select(EntityLink).where(EntityLink.active, EntityLink.method == "manual")).all():
        if (lk.evidence or {}).get("moved_from") == other_id:
            se = session.get(SourceEntity, lk.source_entity_id)
            link(session, se, other_id, method="manual", score=None, evidence={"unmerged_from": keep_id},
                 now=now, decided_by=actor)
            _move(session, se, other_id)
    other.merged_into_id, other.active, other.deactivated_at, other.deactivation_reason = None, True, None, None
    session.add(WorldChange(canonical_entity_id=other_id, change_type="unmerged", detail={"from": keep_id,
                                                                                         "actor": actor},
                            detected_at=now))
    project_all(session, {other_id, keep_id}, policies, now)


def resolve_review(session: Session, review_id: str, *, same_as: str | None, actor: str, now: datetime,
                   policies: Policies | None = None) -> None:
    """A person decides an AMBIGUOUS case: `same_as` = the canonical entity it
    really is (merge), or None (they are different - keep apart)."""
    policies = policies or load()
    review = session.get(MatchReview, review_id)
    se = session.get(SourceEntity, review.source_entity_id)
    if same_as:
        merge(session, same_as, active_canonical(session, se.id), reason="review: same entity", actor=actor,
              now=now, policies=policies)
    review.status, review.resolution, review.resolved_at = "resolved", "same" if same_as else "different", now


def _close_reviews(session: Session, se_id: str, resolution: str, now: datetime) -> None:
    for r in session.scalars(select(MatchReview).where(MatchReview.source_entity_id == se_id,
                                                       MatchReview.status == "open")):
        r.status, r.resolution, r.resolved_at = "resolved", resolution, now


def link_marketplace(session: Session, canonical_id: str, *, provider_id: str | None = None,
                     offering_id: str | None = None, established_by: str = "partner_mapping",
                     now: datetime | None = None) -> None:
    """Explicit canonical entity <-> provider / offering identity (partner
    mapping or ingest). The only way the two worlds learn they describe the
    same business."""
    from datetime import timezone

    from app.db.models import MarketplaceLink

    now = now or datetime.now(timezone.utc)
    if not (provider_id or offering_id):
        raise ValueError("link to a provider or an offering")
    session.add(MarketplaceLink(canonical_entity_id=canonical_id, provider_id=provider_id, offering_id=offering_id,
                                established_by=established_by, active=True, created_at=now))
    session.add(WorldChange(canonical_entity_id=canonical_id, change_type="marketplace_linked",
                            detail={"provider_id": provider_id, "offering_id": offering_id,
                                    "established_by": established_by}, detected_at=now))
    session.flush()
