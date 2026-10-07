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
    tool = lambda name, *a: subprocess.run([sys.executable, str(ROOT / "tools" / name), "--pilot", str(pilot), *a],  # noqa: E731
                                           capture_output=True, text=True, cwd=ROOT)
    # option B: response bodies fetched elsewhere are imported byte for byte
    osm_body = json.dumps({"version": 0.6, "elements": [OSM_RESTAURANT, OSM_MUSEUM_WAY, OSM_UNMAPPED]}).encode()
    (tmp_path / "osm_kotor.json").write_bytes(osm_body)
    wd_body = json.dumps({"head": {}, "results": {"bindings": _wd_rows("Q999001", {"sr-el": "Muzej Test"},
                                                                       osm_way="2002")}}).encode()
    (tmp_path / "wd_kotor.json").write_bytes(wd_body)
    for source, body in (("osm", "osm_kotor.json"), ("wikidata", "wd_kotor.json")):
        r = tool("pilot_fetch.py", "--import", source, f"kotor={tmp_path / body}", "--fetched-at", NOW.isoformat())
        assert r.returncode == 0, r.stderr[-2000:]
    import hashlib

    sums = (pilot / "raw" / "SHA256SUMS").read_text()
    assert hashlib.sha256(osm_body).hexdigest() in sums and hashlib.sha256(wd_body).hexdigest() in sums
    again = tool("pilot_fetch.py", "--import", "osm", f"kotor={tmp_path / 'osm_kotor.json'}", "--fetched-at",
                 NOW.isoformat())
    assert again.returncode != 0                                    # a raw dump is frozen once written

    run = tool("pilot_run.py")
    assert run.returncode == 0, run.stderr[-2000:]
    out = pilot / "out" / "raw_v1"
    metrics = json.loads((out / "metrics.json").read_text())
    assert metrics["sources"]["osm"]["field_coverage"]["counts"]["records"] == 3
    assert metrics["world"]["source_entities"] == 4 and metrics["snapshot"]["content_hash"]
    assert set(metrics["provenance"]["dumps"]) == {"osm", "wikidata"} and metrics["provenance"]["pilot_yaml_sha256"]
    # an explicit OSM<->Wikidata link is reported by its chain, never as independent support
    assert metrics["pairs"]["matches"]["id_link_direction"] == {"both_directions": 1}
    assert metrics["pairs"]["matches"]["only_by_id_link_chain"] == 1
    assert metrics["pairs"]["matches"]["with_independent_support"] == 0
    pairs = [json.loads(line) for line in (out / "audit_pairs.jsonl").open()]
    match = next(p for p in pairs if p["decision"] == "MATCH")
    assert "identifier:ext:osm" in match["candidate_reason"] or "identifier:ext:wikidata" in match["candidate_reason"]
    for name in ("audit_pairs.csv", "same_source_lookalikes.csv", "hours_audit.csv", "discovery_sample.md",
                 "snapshot.json"):
        assert (out / name).exists(), name
    assert tool("pilot_run.py").returncode == 3                      # the raw report is write-once

    # human labels: UNSURE is neither an error nor a success
    (pilot / "labels_raw_v1.csv").write_text("pair_id,label,notes\n" + "\n".join(
        f"{p['pair_id']},{'UNSURE' if p['decision'] == 'MATCH' else 'DIFFERENT_ENTITY'}," for p in pairs) + "\n")
    labels = tool("pilot_labels.py")
    assert labels.returncode == 0, labels.stderr[-2000:]
    scored = json.loads((out / "label_metrics.json").read_text())
    assert scored["unsure_total"] == 1 and scored["false_merges"] == []
    assert scored["precision_on_random_match_sample"] is None       # no SAME/DIFFERENT label on a MATCH yet
