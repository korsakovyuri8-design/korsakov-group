"""Iteration 6: real-source FORMATS (OpenStreetMap Overpass JSON, Wikidata
SPARQL JSON) and the offline pilot tooling.

The records below are hand-written in the sources' wire formats to test
PARSING and the tool chain. They are not real places and say nothing about
ER quality - that is measured only on real dumps with human labels."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db.models import SourceEntity
from app.db.session import create_schema, make_engine
from app.world.adapters import RawDumpAdapter, Scope, SourceDescriptor
from app.world.ingest import active_canonical, run_sync
from app.world.normalize import normalize
from app.world.real_formats import osm_to_generic, wikidata_to_generic
from tests.conftest import ROOT

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
CFG = {"category_map": {"amenity=restaurant": "konoba", "tourism=museum": "museum", "Q33506": "museum"},
       "country_code": "ME", "timezone": "Europe/Podgorica", "region": "test-region", "local_language": "sr"}

OSM_RESTAURANT = {"type": "node", "id": 1001, "lat": 42.42, "lon": 18.77, "timestamp": "2025-06-01T10:00:00Z",
                  "tags": {"amenity": "restaurant", "name": "Konoba Test", "name:en": "Test Tavern",
                           "name:sr": "Конoба Тест", "old_name": "Former Name", "alt_name": "Test A;Test B",
                           "phone": "+382 32 111 222; +382 69 333 444", "website": "https://konoba-test.me",
                           "addr:street": "Test Street", "addr:housenumber": "5", "addr:city": "Kotor",
                           "opening_hours": "Mo-Su 12:00-23:00", "check_date:opening_hours": "2026-05-01",
                           "cuisine": "regional;seafood"}}
OSM_MUSEUM_WAY = {"type": "way", "id": 2002, "center": {"lat": 42.425, "lon": 18.771},
                  "timestamp": "2024-01-01T00:00:00Z",
                  "tags": {"tourism": "museum", "name": "Muzej Test", "wikidata": "Q999001",
                           "opening_hours": "Mo-Fr 09:00-17:00; PH off; Jun-Sep Mo-Su 09:00-20:00"}}
OSM_UNMAPPED = {"type": "node", "id": 3003, "lat": 42.43, "lon": 18.78, "tags": {"tourism": "attraction",
                                                                                   "name": "Vidikovac Test"}}


@pytest.fixture
def s():
    engine = make_engine("sqlite://")
    create_schema(engine)
    with sessionmaker(engine)() as session:
        yield session
    engine.dispose()


def _wd_rows(qid: str, labels: dict[str, str], osm_way: str | None = None) -> list[dict]:
    base = {"item": {"type": "uri", "value": f"http://www.wikidata.org/entity/{qid}"},
            "coord": {"type": "literal", "value": "Point(18.7711 42.4251)"},
            "type": {"type": "uri", "value": "http://www.wikidata.org/entity/Q33506"}}
    if osm_way:
        base["osmWay"] = {"type": "literal", "value": osm_way}
    return [{**base, "label": {"xml:lang": lang, "type": "literal", "value": v}} for lang, v in labels.items()]


def _d(source_id: str, fmt: str) -> SourceDescriptor:
    return SourceDescriptor(source_id, "api", "directory", source_id, fmt, "test-licence", config=CFG)


def test_osm_mapping_never_invents_and_keeps_raw_semantics():
    g = osm_to_generic(OSM_RESTAURANT)
    assert g["id"] == "node/1001" and g["phone"] == "+382 32 111 222"
    assert g["_issues"] == ["several phones in one tag; the first is used for identity"]
    assert g["address"] == "Test Street 5, Kotor"
    assert g["names"]["en"] == "Test Tavern" and g["names"]["alt"] == "Test A" and g["names"]["alt1"] == "Test B"
    assert "Former Name" not in json.dumps(g["names"])           # a former name is not an alias
    assert g["verified"] == {"opening_hours": "2026-05-01"}
    assert "kitchen_hours" not in g and "price" not in g          # absent stays absent
    way = osm_to_generic(OSM_MUSEUM_WAY)
    assert (way["lat"], way["lon"]) == (42.425, 18.771) and way["ids"] == {"osm": "way/2002", "wikidata": "Q999001"}


def test_osm_normalization_reports_what_it_cannot_parse():
    from app.world.adapters import SourceRecord

    d = _d("osm", "osm.overpass.v1")
    ent = normalize(SourceRecord("way/2002", "place", OSM_MUSEUM_WAY), d)
    assert "opening_hours" not in ent.fields                      # PH / month ranges: not guessed
    assert any(i.startswith("opening hours not understood") for i in ent.issues)
    unmapped = normalize(SourceRecord("node/3003", "place", OSM_UNMAPPED), d)
    assert unmapped.fields["subcategory"] == "unclassified"
    assert "category not mapped: 'tourism=attraction'" in unmapped.issues
    ok = normalize(SourceRecord("node/1001", "place", OSM_RESTAURANT), d)
    assert ok.fields["subcategory"] == "konoba" and ok.fields["opening_hours"]["weekly"]["mon"] == [["12:00", "23:00"]]
    assert ("phone", "+38232111222") in ok.identifiers and ("domain", "konoba-test.me") in ok.identifiers


def test_wikidata_mapping_groups_rows_and_links_osm():
    g = wikidata_to_generic("Q999001", _wd_rows("Q999001", {"sr-el": "Muzej Test", "en": "Test Museum",
                                                              "sr": "Музеј Тест"}, osm_way="2002"))
    assert g["name"] == "Muzej Test" and g["names"] == {"en": "Test Museum", "sr": "Музеј Тест"}
    assert g["ids"] == {"wikidata": "Q999001", "osm": "way/2002"}
    assert (g["lat"], g["lon"]) == (42.4251, 18.7711)


def test_raw_dump_adapter_ingests_and_keeps_the_source_record_exactly(s):
    osm = {"format": "osm.overpass.v1", "fetched_at": NOW.isoformat(),
           "response": {"elements": [OSM_RESTAURANT, OSM_MUSEUM_WAY, OSM_UNMAPPED]}}
    wd = {"format": "wikidata.sparql.v1", "fetched_at": NOW.isoformat(),
          "response": {"results": {"bindings": _wd_rows("Q999001", {"sr-el": "Muzej Test", "en": "Test Museum"},
                                                        osm_way="2002")}}}
    run_sync(s, RawDumpAdapter(_d("osm", "osm.overpass.v1"), osm), now=NOW)
    run_sync(s, RawDumpAdapter(_d("wikidata", "wikidata.sparql.v1"), wd), now=NOW)
    se = s.scalar(select(SourceEntity).where(SourceEntity.source_record_id == "node/1001"))
    assert se.raw == OSM_RESTAURANT                                 # source-native, untouched
    museum = s.scalar(select(SourceEntity).where(SourceEntity.source_record_id == "way/2002"))
    item = s.scalar(select(SourceEntity).where(SourceEntity.source_record_id == "Q999001"))
    # the cross-source explicit ids (OSM wikidata=Q.. / Wikidata OSM way) are identity evidence
    assert active_canonical(s, museum.id) == active_canonical(s, item.id)
    assert len(list(RawDumpAdapter(_d("osm", "osm.overpass.v1"), osm).sync_full(Scope()))) == 1


def test_pilot_tools_run_end_to_end_offline(tmp_path):
    pilot = tmp_path / "pilot"
    (pilot / "raw").mkdir(parents=True)
    shutil.copy(ROOT / "data" / "pilot" / "kotor_budva" / "pilot.yaml", pilot / "pilot.yaml")
    cfg = yaml.safe_load((pilot / "pilot.yaml").read_text())
    dumps = {"osm": {"format": "osm.overpass.v1", "fetched_at": NOW.isoformat(), "parts": [],
                     "response": {"elements": [OSM_RESTAURANT, OSM_MUSEUM_WAY, OSM_UNMAPPED]}},
             "wikidata": {"format": "wikidata.sparql.v1", "fetched_at": NOW.isoformat(), "parts": [],
                          "response": {"results": {"bindings": _wd_rows("Q999001", {"sr-el": "Muzej Test"},
                                                                        osm_way="2002")}}}}
    for src in cfg["sources"]:
        (pilot / src["dump"]).write_text(json.dumps(dumps[src["source_id"]]))
    run = subprocess.run([sys.executable, str(ROOT / "tools" / "pilot_run.py"), "--pilot", str(pilot)],
                         capture_output=True, text=True, cwd=ROOT)
    assert run.returncode == 0, run.stderr[-2000:]
    metrics = json.loads((pilot / "out" / "metrics.json").read_text())
    assert metrics["sources"]["osm"]["field_coverage"]["counts"]["records"] == 3
    assert metrics["world"]["source_entities"] == 4 and metrics["snapshot"]["content_hash"]
    for name in ("audit_pairs.csv", "audit_pairs.jsonl", "same_source_lookalikes.csv", "hours_audit.csv",
                 "discovery_sample.md", "snapshot.json"):
        assert (pilot / "out" / name).exists(), name
    labels = subprocess.run([sys.executable, str(ROOT / "tools" / "pilot_labels.py"), "--pilot", str(pilot)],
                            capture_output=True, text=True, cwd=ROOT)
    assert labels.returncode == 0, labels.stderr[-2000:]
