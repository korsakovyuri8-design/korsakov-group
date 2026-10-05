"""Load a knowledge pack file into the database.

Idempotent: items are upserted by (hotel, key), unchanged items are left
alone, and items no longer in the pack are deleted - the pack file is the
source of truth.

CLI:  python -m app.knowledge.ingest data/hotel/example_hotel.yaml
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Hotel, HotelKnowledgeDocument
from app.knowledge.schemas import KnowledgePack
from app.observability import log_event


@dataclass
class IngestReport:
    hotel_id: str
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0


def load_pack(path: str | Path) -> KnowledgePack:
    raw = Path(path).read_text(encoding="utf-8")
    data = json.loads(raw) if str(path).endswith(".json") else yaml.safe_load(raw)
    return KnowledgePack.model_validate(data)


def _hash(item_dump: dict) -> str:
    return hashlib.sha256(json.dumps(item_dump, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def ingest_pack(session: Session, pack: KnowledgePack) -> IngestReport:
    hotel = session.scalar(select(Hotel).where(Hotel.slug == pack.pack.hotel_slug))
    if hotel is None:
        hotel = Hotel(slug=pack.pack.hotel_slug, name=pack.pack.hotel_name, is_synthetic=pack.pack.synthetic)
        session.add(hotel)
        session.flush()
    else:
        hotel.name = pack.pack.hotel_name
        hotel.is_synthetic = pack.pack.synthetic

    report = IngestReport(hotel_id=hotel.id)
    existing = {
        d.item_key: d
        for d in session.scalars(select(HotelKnowledgeDocument).where(HotelKnowledgeDocument.hotel_id == hotel.id))
    }
    seen: set[str] = set()
    for item in pack.items:
        seen.add(item.key)
        source = item.source or pack.pack.source
        metadata = {**item.metadata, "pack_version": pack.pack.version}
        if pack.pack.synthetic:
            metadata["synthetic"] = True
        digest = _hash({**item.model_dump(), "source": source, "metadata": metadata})
        doc = existing.get(item.key)
        if doc is None:
            session.add(
                HotelKnowledgeDocument(
                    hotel_id=hotel.id,
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
    log_event("knowledge_ingested", hotel=pack.pack.hotel_slug, **{k: v for k, v in vars(report).items()})
    return report


def main(argv: list[str]) -> int:
    from app.config import get_settings
    from app.db.session import create_schema, make_engine, make_session_factory

    path = argv[1] if len(argv) > 1 else get_settings().knowledge_path
    engine = make_engine(get_settings().database_url)
    create_schema(engine)
    with make_session_factory(engine)() as session:
        report = ingest_pack(session, load_pack(path))
        session.commit()
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
