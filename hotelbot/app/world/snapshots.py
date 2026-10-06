"""World snapshots: freeze the fabric state for reproducible evaluation.

Real-world data changes; evals must not. A snapshot holds the evidence
(sources with licence terms and versions, source entities, links, aliases,
identifiers, reviews, assertions, corrections, coverage, canonical rows) -
NOT the projection, which is recomputed by restore() from the evidence.
Same evidence -> same projection; `content_hash` proves the evidence is the
same. Restoring requires an empty fabric (no silent mixing).
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.db.models import (
    CanonicalEntity,
    CoverageArea,
    EntityAlias,
    EntityIdentifier,
    EntityLink,
    FactAssertion,
    MarketplaceLink,
    MatchReview,
    SourceEntity,
    WorldCorrection,
    WorldSnapshot,
    WorldSourceRow,
)
from app.world.ingest import project_all
from app.world.policies import Policies, load

# dependency order (restore inserts in this order)
TABLES = [WorldSourceRow, CoverageArea, CanonicalEntity, SourceEntity, EntityLink, EntityAlias, EntityIdentifier,
          MatchReview, FactAssertion, WorldCorrection]
VOLATILE = {"created_at", "updated_at"}        # bookkeeping, not evidence


def _plain(v: Any) -> Any:
    if isinstance(v, datetime):
        return {"__dt__": v.isoformat()}
    if isinstance(v, Decimal):
        return str(v)
    return v


def _revive(v: Any) -> Any:
    if isinstance(v, dict) and set(v) == {"__dt__"}:
        return datetime.fromisoformat(v["__dt__"])
    return v


def _rows(session: Session, model) -> list[dict[str, Any]]:  # noqa: ANN001
    cols = [c.key for c in inspect(model).column_attrs]
    out = []
    for obj in session.scalars(select(model).order_by(*inspect(model).primary_key)):
        out.append({c: _plain(getattr(obj, c)) for c in cols})
    return out


def _evidence(content: dict[str, Any]) -> str:
    stripped = {t: [{k: v for k, v in row.items() if k not in VOLATILE} for row in rows]
                for t, rows in content["tables"].items()}
    return json.dumps(stripped, sort_keys=True, ensure_ascii=False, default=str)


def create(session: Session, name: str, now: datetime) -> WorldSnapshot:
    content = {"format": "hotelbot-world-snapshot/1",
               "tables": {m.__tablename__: _rows(session, m) for m in TABLES}}
    # canonical entity -> offering identity (the offering itself is transaction-world data)
    content["marketplace_links"] = [{"canonical_entity_id": lk.canonical_entity_id, "established_by":
                                     lk.established_by} for lk in session.scalars(select(MarketplaceLink))]
    versions = {s["id"]: {"last_success_at": s["last_success_at"], "cursor": s["cursor"],
                          "records_seen": s["records_seen"], "license": s["license"]}
                for s in content["tables"]["world_sources"]}
    blob = json.dumps(content, sort_keys=True, ensure_ascii=False, default=str).encode()
    snap = WorldSnapshot(name=name, created_at=now, source_versions=json.loads(json.dumps(versions, default=str)),
                         counts={t: len(r) for t, r in content["tables"].items()},
                         content_hash=hashlib.sha256(_evidence(content).encode()).hexdigest(),
                         content=base64.b64encode(gzip.compress(blob, mtime=0)).decode())
    session.add(snap)
    session.flush()
    return snap


def load_content(snapshot: WorldSnapshot | str) -> dict[str, Any]:
    raw = snapshot.content if isinstance(snapshot, WorldSnapshot) else snapshot
    return json.loads(gzip.decompress(base64.b64decode(raw)))


def restore(session: Session, snapshot: WorldSnapshot | str, now: datetime,
            policies: Policies | None = None) -> dict[str, int]:
    policies = policies or load()
    if session.scalar(select(SourceEntity.id).limit(1)) is not None:
        raise RuntimeError("restore needs an empty world fabric")
    content = load_content(snapshot)
    counts = {}
    for model in TABLES:
        rows = content["tables"].get(model.__tablename__, [])
        for row in rows:
            session.add(model(**{k: _revive(v) for k, v in row.items()}))
        session.flush()
        counts[model.__tablename__] = len(rows)
    project_all(session, {c.id for c in session.scalars(select(CanonicalEntity))}, policies, now)
    return counts


def evidence_hash(session: Session) -> str:
    content = {"tables": {m.__tablename__: _rows(session, m) for m in TABLES}}
    return hashlib.sha256(_evidence(content).encode()).hexdigest()
