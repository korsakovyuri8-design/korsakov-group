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
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import KnowledgeDocument, Property
from app.knowledge.schemas import KnowledgePack
from app.observability import log_event


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
    raw = Path(path).read_text(encoding="utf-8")
    data = json.loads(raw) if str(path).endswith(".json") else yaml.safe_load(raw)
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
    }
    session.flush()
    return prop


def ingest_pack(session: Session, pack: KnowledgePack) -> IngestReport:
    prop = _upsert_property(session, pack)
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
