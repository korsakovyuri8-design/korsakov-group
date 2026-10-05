"""Load a property pack into the database.

Idempotent: the Property row is upserted by slug (type, timezone,
capabilities, policy...), knowledge items are upserted by (property, key),
unchanged items are left alone, and items no longer in the pack are deleted -
the pack file is the source of truth.

CLI:  python -m app.knowledge.ingest data/hotel/example_hotel.yaml [more packs...]
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass
from typing import Any
from pathlib import Path

from functools import lru_cache
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import ExternalProvider, KnowledgeDocument, Property, PropertyProvider, ProviderRelation
from app.knowledge.schemas import KnowledgePack
from app.observability import log_event
from app.yamlio import yaml_load


@dataclass
class IngestReport:
    property_id: str
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0

    @property
    def hotel_id(self) -> str:  # Core v1 compatibility
        return self.property_id


def load_pack(path: str | Path) -> KnowledgePack:
    p = Path(path)
    return _load_pack_cached(str(p.resolve()), p.stat().st_mtime_ns)


@lru_cache(maxsize=64)
def _load_pack_cached(path: str, _mtime: int) -> KnowledgePack:
    raw = Path(path).read_text(encoding="utf-8")
    data = json.loads(raw) if path.endswith(".json") else yaml_load(raw)
    return KnowledgePack.model_validate(data)


def _hash(item_dump: dict) -> str:
    return hashlib.sha256(json.dumps(item_dump, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _upsert_property(session: Session, pack: KnowledgePack) -> Property:
    info = pack.pack
    prop = session.scalar(select(Property).where(Property.slug == info.property_slug))
    if prop is None:
        prop = Property(slug=info.property_slug)
        session.add(prop)
    prop.name = info.property_name
    prop.property_type = info.property_type
    prop.timezone = info.timezone
    prop.default_language = info.default_language
    prop.is_synthetic = info.synthetic
    prop.active = True
    prop.capabilities = pack.capabilities.model_dump() if pack.capabilities else {}
    prop.extra = {
        "emergency_number": info.emergency_number,
        "whatsapp_phone_number_id": info.whatsapp_phone_number_id,
        "policy": pack.policy.model_dump(),
        "pack_version": info.version,
        "region": info.region,
        "location": info.location,
    }
    session.flush()
    return prop


def _upsert_providers(session: Session, prop: Property, pack: KnowledgePack) -> None:
    """Providers are upserted by (property, slug). Providers removed from the
    pack are deactivated, not deleted: quotes and transactions reference them."""
    existing = {p.slug: p for p in session.scalars(select(ExternalProvider).where(ExternalProvider.property_id == prop.id))}
    seen = set()
    for spec in pack.providers:
        seen.add(spec.slug)
        row = existing.get(spec.slug) or ExternalProvider(property_id=prop.id, slug=spec.slug)
        row.name, row.provider_type, row.integration_type = spec.name, spec.provider_type, spec.integration_type
        row.active, row.services, row.config = spec.active, {"service_types": spec.services}, spec.config
        apply_marketplace(row, spec)
        session.add(row)
    for slug, row in existing.items():
        if slug not in seen:
            row.active = False
    session.flush()


def apply_marketplace(row: ExternalProvider, spec: Any) -> None:
    row.profile, row.policies = spec.profile, spec.policies
    row.commission_type, row.commission_value = spec.commission_type, spec.commission_value


def _upsert_relationships(session: Session, prop: Property, pack: KnowledgePack) -> None:
    """Relationships to providers: the property's own or those of its region
    (region packs are ingested first). A provider that is not loaded is
    skipped with a warning (e.g. a pack ingested without its region data)."""
    region = (prop.extra or {}).get("region")
    existing = {r.provider_id: r for r in session.scalars(select(PropertyProvider)
                                                          .where(PropertyProvider.property_id == prop.id))}
    seen = set()
    for spec in pack.provider_relationships:
        scope = ExternalProvider.property_id == prop.id
        if region:
            scope = or_(scope, ExternalProvider.region == region)
        provider = session.scalar(select(ExternalProvider).where(ExternalProvider.slug == spec.provider, scope))
        if provider is None:
            log_event("provider_relationship_unresolved", property=prop.slug, provider=spec.provider)
            continue
        row = existing.get(provider.id) or PropertyProvider(property_id=prop.id, provider_id=provider.id)
        row.relation = ProviderRelation(spec.relation)
        row.services = {"service_types": spec.services} if spec.services else {}
        session.add(row)
        seen.add(provider.id)
    for provider_id, row in existing.items():
        if provider_id not in seen:
            session.delete(row)
    session.flush()


def ingest_pack(session: Session, pack: KnowledgePack) -> IngestReport:
    prop = _upsert_property(session, pack)
    _upsert_providers(session, prop, pack)
    _upsert_relationships(session, prop, pack)
    report = IngestReport(property_id=prop.id)
    existing = {
        d.item_key: d
        for d in session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.property_id == prop.id))
    }
    seen: set[str] = set()
    for item in pack.items:
        seen.add(item.key)
        source = item.source or pack.pack.source
        metadata = {**item.metadata, "pack_version": pack.pack.version, "topic": item.topic or item.key}
        if pack.pack.synthetic:
            metadata["synthetic"] = True
        digest = _hash({**item.model_dump(), "source": source, "metadata": metadata})
        doc = existing.get(item.key)
        if doc is None:
            session.add(
                KnowledgeDocument(
                    property_id=prop.id,
                    item_key=item.key,
                    category=item.category,
                    source=source,
                    content=item.content,
                    keywords=item.keywords,
                    extra=metadata,
                    content_hash=digest,
                )
            )
            report.created += 1
        elif doc.content_hash != digest:
            doc.category, doc.source, doc.content = item.category, source, item.content
            doc.keywords, doc.extra, doc.content_hash = item.keywords, metadata, digest
            report.updated += 1
        else:
            report.unchanged += 1
    for key, doc in existing.items():
        if key not in seen:
            session.delete(doc)
            report.deleted += 1
    session.flush()

    shared = sorted(t for t, n in Counter(i.topic for i in pack.items if i.topic).items() if n > 1)
    if shared:
        log_event("knowledge_conflict_risk", property=pack.pack.property_slug, shared_topics=shared)
    log_event("knowledge_ingested", property=pack.pack.property_slug, **vars(report))
    return report


def main(argv: list[str]) -> int:
    from app.config import get_settings
    from app.db.session import create_schema, make_engine, make_session_factory

    paths = argv[1:] or get_settings().all_pack_paths
    engine = make_engine(get_settings().database_url)
    create_schema(engine)
    with make_session_factory(engine)() as session:
        for path in paths:
            print(path, ingest_pack(session, load_pack(path)))
        session.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
