"""Durable world-data jobs on the existing PostgreSQL job queue.

    world_sync       {source_id, mode: full|incremental, area?}
    world_existence  periodic existence reconciliation (grace periods)
    world_retention  drop raw payloads past their licence retention

world_sync is idempotent (unchanged records are no-ops), checkpointed (the
cursor is committed after every page, so a crashed or retried job resumes),
bounded (queue max_attempts with backoff). A failing source is recorded in
source health and COMMITTED before the retry is scheduled; the known world
is never rolled back or removed because a sync failed.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Job, SourceEntity, WorldChange, WorldSourceRow
from app.jobs.queue import JobWorker, PermanentJobError, RetryableJobError, enqueue
from app.world import coverage
from app.world.adapters import Scope
from app.world.ingest import reconcile_all, run_sync

if TYPE_CHECKING:
    from app.container import Container

MAX_ATTEMPTS = 4


def schedule_sync(session: Session, container: Container, source_id: str, *, mode: str = "full",
                  area: str | None = None, key: str | None = None) -> Job:
    return enqueue(session, "world_sync", {"source_id": source_id, "mode": mode, "area": area},
                   key or f"world_sync:{source_id}:{mode}:{area}:{container.clock.now().isoformat()}",
                   clock=container.clock, max_attempts=MAX_ATTEMPTS)


def enforce_retention(session: Session, now) -> int:  # noqa: ANN001
    """Raw payloads are kept only as long as the source's licence allows."""
    dropped = 0
    for src in session.scalars(select(WorldSourceRow).where(WorldSourceRow.retention_days.is_not(None))):
        limit = now - timedelta(days=src.retention_days)
        for se in session.scalars(select(SourceEntity).where(SourceEntity.source_id == src.id,
                                                             SourceEntity.raw.is_not(None),
                                                             SourceEntity.last_synced_at < limit)):
            se.raw = None
            dropped += 1
            session.add(WorldChange(source_entity_id=se.id, source_id=src.id, change_type="raw_payload_expired",
                                    detail={"retention_days": src.retention_days}, detected_at=now))
    return dropped


def register_world_handlers(worker: JobWorker, container: Container) -> None:
    def world_sync(s: Session, job: Job) -> None:
        p = job.payload
        adapter = container.world_adapters.get(p["source_id"])
        if adapter is None:
            raise PermanentJobError(f"no adapter registered for source {p['source_id']!r}")
        scope = coverage.scope_for(s, p["area"]) if p.get("area") else Scope()
        report = run_sync(s, adapter, now=container.clock.now(), scope=scope, mode=p.get("mode", "full"),
                          checkpoint=lambda sess: sess.commit())
        if report.status != "ok":
            s.commit()                      # keep the failure in source health, then retry with backoff
            raise RetryableJobError(report.error or "source sync failed")

    def world_existence(s: Session, job: Job) -> None:
        reconcile_all(s, container.clock.now())

    def world_retention(s: Session, job: Job) -> None:
        enforce_retention(s, container.clock.now())

    worker.register("world_sync", world_sync)
    worker.register("world_existence", world_existence)
    worker.register("world_retention", world_retention)
