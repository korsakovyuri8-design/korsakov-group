"""Sync an Iteration 4 WorldSource (region pack, FixtureSource) through the
world data fabric.

Kept as the v1 entry point: the source is wrapped in a V1Adapter, its region
becomes a coverage area, and the full fabric pipeline runs (source entities
-> entity resolution -> fact assertions -> resolution -> projection). The
places / events rows the discovery engine reads are the projection.

Behaviour change from Iteration 4 (by design, D-067): a record missing from
one sync is TOMBSTONED AT THE SOURCE; the place stays active (flagged
"existence unconfirmed") until the existence policy deactivates it.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.observability import log_event
from app.world import coverage
from app.world.adapters import Scope, V1Adapter
from app.world.ingest import run_sync
from app.world.sources import WorldSource

REGION_RADIUS_KM = 40.0


def sync(session: Session, source: WorldSource, *, now: datetime | None = None, **adapter_kw) -> dict[str, int]:
    region = source.region()
    adapter = V1Adapter(source, **adapter_kw)
    coverage.upsert_area(session, code=region.slug, kind="locality", name=region.name,
                         country_code=getattr(source, "country_code", None), timezone=region.timezone,
                         center=tuple(region.center), radius_km=REGION_RADIUS_KM)
    report = run_sync(session, adapter, now=now or datetime.now(timezone.utc),
                      scope=Scope("area", region.slug))
    counts = {"places": sum(1 for _ in source.places()), "events": sum(1 for _ in source.events())}
    log_event("world_synced_v1", region=region.slug, source=source.source_id, **counts,
              matched=report.matched, ambiguous=report.ambiguous, tombstoned=report.tombstoned)
    return counts
