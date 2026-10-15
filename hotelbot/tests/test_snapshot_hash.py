"""Iteration 6.1: the world snapshot's content_hash proves "same evidence".
Two builds of the same world from the same inputs must hash the same, so the
runtime-generated row ids (random UUIDs) cannot take part in it: an entity is
identified by its natural key (source + record id, canonical slug, area
code), references by the key of what they point to. Identifiers that are
source CONTENT (an OSM tag that happens to hold a UUID) stay in the hash."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import sessionmaker

from app.db.session import create_schema, make_engine
from app.world import coverage, snapshots
from app.world.adapters import FixtureAdapter, SourceDescriptor
from app.world.ingest import run_sync

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
PHOTO = "8f14e45f-ceea-467a-9b8c-2c8c1d5f1e3a"           # a source value that looks like a row id


def _world(phone: str = "069 111 000", photo: str = PHOTO, second: str = "b") -> str:
    eng = make_engine("sqlite://")
    create_schema(eng)
    with sessionmaker(eng)() as s:
        coverage.upsert_area(s, code="town", kind="locality", name="Testtown", country_code="ME",
                             timezone="Europe/Podgorica", center=(43.14, 19.14), radius_km=30)
        for sid, records in (
                ("a", [{"id": "1", "name": "Konoba Lipa", "lat": 43.14, "lon": 19.14, "category": "restaurant",
                        "phone": phone, "photo": photo},
                       {"id": "2", "name": "Kafe Most", "lat": 43.141, "lon": 19.141, "category": "cafe"}]),
                (second, [{"id": "x", "name": "Konoba Lipa", "lat": 43.1401, "lon": 19.1401, "category": "restaurant",
                        "phone": phone},                                          # merges with a/1 (same phone)
                       {"id": "y", "name": "Kafe Most", "lat": 43.1411, "lon": 19.1411, "category": "cafe"}])):
            d = SourceDescriptor(source_id=sid, source_type="fixture", source_class="directory", name=sid,
                                 format="generic.v1", license="test licence",
                                 config={"country_code": "ME", "timezone": "Europe/Podgorica"})
            run_sync(s, FixtureAdapter(d, records), now=NOW)
            s.commit()
        snap = snapshots.create(s, "t", NOW)
        reviews = snapshots.load_content(snap)["tables"]["match_reviews"]
        assert reviews, "the fixture should exercise a review (name + geo only -> ambiguous)"
        return snap.content_hash


def test_two_builds_of_the_same_world_hash_the_same():
    assert _world() == _world()


def test_a_semantic_difference_changes_the_hash():
    assert _world(phone="069 111 000") != _world(phone="069 999 000")


def test_a_uuid_in_source_content_is_evidence():
    assert _world(photo=PHOTO) != _world(photo="11111111-2222-4333-8444-555555555555")


def test_which_source_said_it_is_evidence():
    assert _world(second="b") != _world(second="c")


def test_source_ids_and_natural_keys_appear_in_the_evidence():
    content = {"tables": {"world_sources": [{"id": "osm"}],
                          "source_entities": [{"id": "0c9f6c8e-1111-4222-8333-444455556666", "source_id": "osm",
                                               "source_record_id": "node/1", "raw": {"panoramax": PHOTO}}],
                          "entity_links": [{"id": "7a1b2c3d-1111-4222-8333-444455556666",
                                            "source_entity_id": "0c9f6c8e-1111-4222-8333-444455556666"}]}}
    ev = snapshots._evidence(content)
    assert '"source_id": "osm"' in ev and '"source_entity:osm/node/1"' in ev and PHOTO in ev
    assert "0c9f6c8e" not in ev and "7a1b2c3d" not in ev
