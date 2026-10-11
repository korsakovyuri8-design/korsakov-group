"""Iteration 6.1: a generic subcategory (tourism=attraction -> tourist_attraction)
says nothing about what the entity IS. In ER it is neither support nor
contradiction of the identity type: UNKNOWN, on either side of the pair. A
specific subcategory on the same canonical keeps taking part in the check."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db.models import SourceEntity
from app.db.session import create_schema, make_engine
from app.world import coverage
from app.world.adapters import FixtureAdapter, SourceDescriptor
from app.world.ingest import active_canonical, run_sync
from app.world.matching import GENERIC_SUBCATEGORIES

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def s():
    engine = make_engine("sqlite://")
    create_schema(engine)
    with sessionmaker(engine)() as session:
        coverage.upsert_area(session, code="town", kind="locality", name="Testtown", country_code="ME",
                             timezone="Europe/Podgorica", center=(43.14, 19.14), radius_km=30)
        yield session
    engine.dispose()


def _sync(s, source_id, records):  # noqa: ANN001
    d = SourceDescriptor(source_id=source_id, source_type="fixture", source_class="directory", name=source_id,
                         format="generic.v1", license="test licence",
                         config={"country_code": "ME", "timezone": "Europe/Podgorica"})
    return run_sync(s, FixtureAdapter(d, records), now=NOW)


def _cid(s, source_id, rid):  # noqa: ANN001
    se = s.scalar(select(SourceEntity).where(SourceEntity.source_id == source_id,
                                             SourceEntity.source_record_id == rid))
    return active_canonical(s, se.id)


def _rec(rid: str, category: str, lat: float = 43.14, name: str = "Gospa od Zdravlja") -> dict:
    return {"id": rid, "name": name, "lat": lat, "lon": 19.14, "category": category, "ids": {"wikidata": "Q4981066"}}


def test_generic_set_is_explicit_and_minimal():
    assert GENERIC_SUBCATEGORIES == frozenset({"tourist_attraction"})


def test_attraction_and_religious_site_with_shared_id_merge(s):
    _sync(s, "wd", [_rec("Q1", "religious_site")])
    _sync(s, "osm", [_rec("way/1", "tourist_attraction", lat=43.14013)])
    assert _cid(s, "wd", "Q1") == _cid(s, "osm", "way/1")


def test_symmetric_when_the_sources_arrive_the_other_way_round(s):
    _sync(s, "osm", [_rec("way/1", "tourist_attraction")])
    _sync(s, "wd", [_rec("Q1", "religious_site", lat=43.14013)])
    assert _cid(s, "wd", "Q1") == _cid(s, "osm", "way/1")


def test_generic_alone_creates_no_category_conflict(s):
    _sync(s, "osm", [_rec("way/1", "tourist_attraction", name="Lazaret")])
    _sync(s, "dir", [_rec("r1", "restaurant", lat=43.14013, name="Lazaret")])
    assert _cid(s, "osm", "way/1") == _cid(s, "dir", "r1")


def test_specific_subcategory_on_the_canonical_still_conflicts(s):
    _sync(s, "wd", [_rec("Q1", "religious_site")])
    _sync(s, "osm", [_rec("way/1", "tourist_attraction", lat=43.14013)])
    assert _cid(s, "wd", "Q1") == _cid(s, "osm", "way/1")
    _sync(s, "dir", [_rec("r1", "restaurant", lat=43.14005)])
    assert _cid(s, "dir", "r1") != _cid(s, "wd", "Q1")


def test_visitor_center_is_not_generic(s):
    _sync(s, "a", [_rec("v1", "visitor_center", name="Lazaret")])
    _sync(s, "b", [_rec("r1", "restaurant", lat=43.14013, name="Lazaret")])
    assert _cid(s, "a", "v1") != _cid(s, "b", "r1")
