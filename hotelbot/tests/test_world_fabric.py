"""Iteration 5: the world data fabric - normalization, entity resolution,
field resolution, merge safety, sync robustness, spatial index, snapshots,
licensing. Runs on a bare database (no container)."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db.models import (
    EntityLink,
    FactAssertion,
    Place,
    SourceEntity,
    WorldCorrection,
    WorldSourceRow,
)
from app.db.session import create_schema, make_engine
from app.shared.geo import Point, distance_km
from app.world import coverage, corrections, identity, snapshots
from app.world.adapters import FixtureAdapter, Scope, SourceDescriptor
from app.world.facts import CONFLICTED, CONTESTED, NEEDS_VERIFICATION, RESOLVED, resolve
from app.world.ingest import active_canonical, run_sync
from app.world.jobs import enforce_retention
from app.world.matching import name_similarity
from app.world.normalize import normalize_coordinates, normalize_phone, normalize_url, parse_osm_hours
from app.world.policies import load
from app.world.spatial import GeohashIndex

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
P = load()


@pytest.fixture
def s():
    engine = make_engine("sqlite://")
    create_schema(engine)
    with sessionmaker(engine)() as session:
        coverage.upsert_area(session, code="town", kind="locality", name="Testtown", country_code="ME",
                             timezone="Europe/Podgorica", center=(43.14, 19.14), radius_km=30)
        yield session
    engine.dispose()


def _d(source_id: str, cls: str = "directory", **kw) -> SourceDescriptor:
    return SourceDescriptor(source_id=source_id, source_type="fixture", source_class=cls, name=source_id,
                            format="generic.v1", license=kw.pop("license", "test licence"),
                            config={"country_code": "ME", "timezone": "Europe/Podgorica"}, **kw)


def _sync(s, source_id, records, cls="directory", now=NOW, **kw):  # noqa: ANN001
    return run_sync(s, FixtureAdapter(_d(source_id, cls), records, **kw), now=now)


def _cid(s, source_id, rid):  # noqa: ANN001
    se = s.scalar(select(SourceEntity).where(SourceEntity.source_id == source_id,
                                             SourceEntity.source_record_id == rid))
    return active_canonical(s, se.id)


# ============================================================ normalization
def test_phone_normalization_never_invents_a_country():
    assert normalize_phone("069 111 222", "ME") == ("+38269111222", None)
    assert normalize_phone("00382 69 111 222", None) == ("+38269111222", None)
    assert normalize_phone("+382 (69) 111-222", None) == ("+38269111222", None)
    value, issue = normalize_phone("069 111 222", None)
    assert value is None and "country" in issue


def test_url_and_coordinates():
    assert normalize_url("www.Example.me/menu/") == ("https://example.me/menu", "example.me", None)
    assert normalize_url("no-dot")[0] is None
    assert normalize_coordinates(0, 0)[0] is None and "0,0" in normalize_coordinates(0, 0)[2]
    assert normalize_coordinates(95, 10)[0] is None
    assert normalize_coordinates("43.1", "19.2")[:2] == (43.1, 19.2)


def test_opening_hours_parse_or_report():
    hours, issue = parse_osm_hours("Mo-Fr 09:00-18:00; Sa 10:00-14:00; Su off")
    assert issue is None and hours["weekly"]["mon"] == [["09:00", "18:00"]] and hours["weekly"]["sun"] == []
    assert parse_osm_hours("24/7")[0]["weekly"]["wed"] == [["00:00", "00:00"]]
    assert parse_osm_hours("Fr-Sa 18:00-02:00")[0]["weekly"]["sat"] == [["18:00", "02:00"]]
    hours, issue = parse_osm_hours("open most days")
    assert hours is None and "not understood" in issue          # reported, not guessed


def test_unparseable_values_are_issues_not_assertions(s):
    _sync(s, "dir:a", [{"id": "1", "name": "Galerija X", "lat": 43.14, "lon": 19.14, "category": "gallery",
                        "phone": "call us", "opening_hours": "sometimes", "price": "cheap-ish"}])
    se = s.scalar(select(SourceEntity))
    assert {"opening_hours", "phone", "price_range"}.isdisjoint(se.normalized["fields"])
    assert len(se.issues) == 3 and se.raw["opening_hours"] == "sometimes"      # raw kept for audit


def test_descriptor_requires_licence_terms():
    with pytest.raises(ValueError, match="licence"):
        _d("x", license="unknown").validate(P.source_classes)
    with pytest.raises(ValueError, match="attribution"):
        _d("x", attribution_required=True).validate(P.source_classes)
    with pytest.raises(ValueError, match="source_class"):
        _d("x", cls="best").validate(P.source_classes)


# ============================================================ names / ER
def test_name_similarity_classes():
    assert name_similarity("Konoba Stari Grad", "Stari Grad Restaurant")[0] == 1.0      # type words = class
    sim, conflict = name_similarity("Pizzeria Roma", "Restoran Roma")
    assert conflict                                                                   # different kinds
    assert name_similarity("Konoba Stari Grad Žabljak", "Konoba Stari Grad", {"zabljak"})[0] == 1.0
    assert name_similarity("Коноба Ђурђевића Тара", "Konoba Đurđevića Tara")[0] >= 0.85
    assert name_similarity("Black Lake Café", "Kafe Crno Jezero")[0] < 0.6            # translation != match


def test_conservative_resolution_classes(s):
    base = {"lat": 43.1400, "lon": 19.1400, "category": "restaurant"}
    _sync(s, "a", [{"id": "1", "name": "Konoba Stari Grad", "phone": "069 111 222", **base},
                   {"id": "2", "name": "Pizzeria Napoli", "website": "napoli.me", "lat": 43.1500, "lon": 19.14,
                    "category": "restaurant"}])
    _sync(s, "b", [{"id": "x", "name": "Stari Grad Restaurant", **{**base, "lat": 43.14002}},       # match
                   {"id": "y", "name": "Pizzeria Napoli", "website": "https://napoli.me", "lat": 43.1680,
                    "lon": 19.14, "category": "restaurant"},                                          # chain: 2 km
                   {"id": "z", "name": "Konoba Stari Mlin", "phone": "069 333 444",
                    **{**base, "lat": 43.1407}}])                                                     # similar, 80 m
    assert _cid(s, "a", "1") == _cid(s, "b", "x")
    assert _cid(s, "a", "2") != _cid(s, "b", "y")
    assert _cid(s, "b", "z") not in (_cid(s, "a", "1"), _cid(s, "a", "2"))


def test_same_source_records_are_never_merged(s):
    _sync(s, "a", [{"id": "1", "name": "Galerija Sjever", "lat": 43.14, "lon": 19.14, "category": "gallery"},
                   {"id": "2", "name": "Galerija Sjever", "lat": 43.14, "lon": 19.14, "category": "gallery"}])
    assert _cid(s, "a", "1") != _cid(s, "a", "2")


# ========================================================== field resolution
@dataclass
class A:
    source_id: str
    source_class: str
    value: Any
    observed_at: datetime
    confidence: float = 0.8
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    verification_type: str = "source_reported"
    source_entity_id: str = "se"


def _r(field: str, *items: A):
    return resolve(list(items), P.field(field), P, NOW)


def test_field_specific_authority():
    day = NOW - timedelta(days=2)
    coords = _r("coordinates", A("own", "provider_owned", {"lat": 43.0, "lon": 19.0}, day),
                A("geo", "geo_verified", {"lat": 43.01, "lon": 19.0}, day))
    hours = _r("opening_hours", A("own", "provider_owned", {"weekly": {"mon": []}}, day),
               A("geo", "geo_verified", {"weekly": {"mon": [["9:00", "17:00"]]}}, day))
    assert coords.winner.source_id == "geo" and hours.winner.source_id == "own"      # no global priority
    assert coords.state == CONTESTED and len(coords.conflicting) == 1


def test_equal_authority_conflict_by_risk():
    a, b = NOW - timedelta(days=2), NOW - timedelta(days=1)
    high = _r("opening_hours", A("d1", "directory", {"weekly": {"mon": [["10:00", "23:00"]]}}, a),
              A("d2", "directory", {"weekly": {"mon": [["10:00", "00:00"]]}}, b))
    assert high.state == NEEDS_VERIFICATION and high.value is None and len(high.conflicting) == 2
    low = _r("website", A("d1", "directory", "https://a.me", a), A("d2", "directory", "https://b.me", b))
    assert low.state == CONFLICTED and low.value == "https://b.me"            # newest shown, flagged


def test_majority_recency_and_staleness():
    d = NOW - timedelta(days=3)
    maj = _r("website", A("d1", "directory", "https://a.me", d), A("d2", "directory", "https://a.me", d),
             A("d3", "directory", "https://b.me", d))
    assert maj.value == "https://a.me" and maj.state == CONTESTED
    rec = _r("opening_hours", A("d1", "directory", {"x": 1}, NOW - timedelta(days=25)),
             A("d2", "directory", {"x": 2}, NOW - timedelta(days=2)))
    assert rec.value == {"x": 2} and "newer by" in rec.reason
    stale_auth = _r("opening_hours", A("own", "provider_owned", {"x": 1}, NOW - timedelta(days=150)),
                    A("d", "directory", {"x": 2}, NOW - timedelta(days=1)))
    assert stale_auth.state == NEEDS_VERIFICATION and stale_auth.value is None
    agree = _r("opening_hours", A("own", "provider_owned", {"x": 1}, d), A("d", "directory", {"x": 1}, d))
    assert agree.state == RESOLVED and agree.confidence > 0.9               # two sources agree


def test_temporary_assertions_expire():
    tmp = A("staff", "staff_verified", [{"from": "2026-10-01", "to": "2026-10-10"}], NOW,
            valid_until=NOW + timedelta(days=5))
    assert _r("temporary_closure", tmp).value
    assert resolve([tmp], P.field("temporary_closure"), P, NOW + timedelta(days=6)).state == "unknown"


def test_no_llm_in_resolution():
    """The fabric never imports a language model."""
    import ast
    from pathlib import Path

    for f in (Path(__file__).resolve().parents[1] / "app" / "world").glob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not {m for m in mods if m.startswith(("app.llm", "app.agent"))}, f.name


# ================================================================ merge safety
def test_split_and_merge_keep_every_record_and_assertion(s):
    rec = {"name": "Konoba Stari Grad", "lat": 43.14, "lon": 19.14, "phone": "069 111 222", "category": "konoba"}
    _sync(s, "a", [{"id": "1", **rec, "opening_hours": "Mo-Su 12:00-23:00"}])
    _sync(s, "b", [{"id": "2", **rec, "opening_hours": "Mo-Su 12:00-22:00"}])
    shared = _cid(s, "a", "1")
    assert shared == _cid(s, "b", "2")
    n_assert = s.query(FactAssertion).count()
    se_b = s.scalar(select(SourceEntity).where(SourceEntity.source_id == "b"))
    new = identity.split(s, se_b.id, reason="wrong merge", actor="test", now=NOW)
    assert _cid(s, "b", "2") == new != shared
    assert s.query(FactAssertion).count() == n_assert and s.query(SourceEntity).count() == 2
    assert s.query(EntityLink).filter(EntityLink.active.is_(False)).count() == 1      # history kept
    assert {a.canonical_entity_id for a in s.scalars(select(FactAssertion).where(
        FactAssertion.source_entity_id == se_b.id))} == {new}
    identity.merge(s, shared, new, reason="test", actor="test", now=NOW)
    assert _cid(s, "b", "2") == shared
    place = s.scalar(select(Place).where(Place.canonical_entity_id == new))
    assert place is not None and not place.active and place.resolution["_merged_into"] == shared
    identity.revert_merge(s, new, actor="test", now=NOW)
    assert _cid(s, "b", "2") == new and s.scalar(select(Place).where(Place.canonical_entity_id == new)).active


# ============================================================ sync robustness
def test_interrupted_sync_resumes_from_checkpoint(s):
    records = [{"id": str(i), "name": f"Galerija {i}", "lat": 43.14 + i / 1000, "lon": 19.14,
                "category": "gallery"} for i in range(5)]
    _sync(s, "a", records)
    adapter = FixtureAdapter(_d("a"), records[:4], page_size=1, fail_after=2)       # record 4 removed
    report = run_sync(s, adapter, now=NOW + timedelta(days=1), checkpoint=lambda x: x.flush())
    assert report.status == "failed" and s.get(WorldSourceRow, "a").status == "failing"
    assert s.query(SourceEntity).filter(SourceEntity.active_at_source.is_(False)).count() == 0   # nothing lost
    assert s.get(WorldSourceRow, "a").cursor["in_progress"]["cursor"]["offset"] == 2
    adapter.fail_after, adapter.served = None, 0
    report = run_sync(s, adapter, now=NOW + timedelta(days=1, minutes=5))
    assert report.status == "ok" and report.seen == 2                                # resumed at offset 2
    gone = s.scalar(select(SourceEntity).where(SourceEntity.source_record_id == "4"))
    assert not gone.active_at_source and report.tombstoned == 1                      # only the really missing one
    assert s.get(WorldSourceRow, "a").status == "healthy"


def test_incremental_feed_deletions_are_tombstones(s):
    adapter = FixtureAdapter(_d("a"), [{"id": "1", "name": "Galerija Sjena", "lat": 43.14, "lon": 19.14,
                                        "category": "gallery"}])
    run_sync(s, adapter, now=NOW)
    adapter.changes = [{"version": 1, "deleted": "1"}]
    run_sync(s, adapter, now=NOW, mode="incremental")
    se = s.scalar(select(SourceEntity))
    assert not se.active_at_source and s.scalar(select(Place)).active          # source said gone; entity kept
    assert s.get(WorldSourceRow, "a").cursor["incremental"]["version"] == 1


def test_retention_drops_raw_payload_but_keeps_facts(s):
    run_sync(s, FixtureAdapter(_d("a", retention_days=30), [{"id": "1", "name": "Galerija Sjena", "lat": 43.14,
                                                             "lon": 19.14, "category": "gallery"}]), now=NOW)
    assert enforce_retention(s, NOW + timedelta(days=10)) == 0
    assert enforce_retention(s, NOW + timedelta(days=31)) == 1
    se = s.scalar(select(SourceEntity))
    assert se.raw is None and se.raw_hash and se.normalized["fields"]["name"] == "Galerija Sjena"


# ============================================================ corrections
def test_traveller_correction_carries_no_identity(s):
    _sync(s, "a", [{"id": "1", "name": "Galerija Sjena", "lat": 43.14, "lon": 19.14, "category": "gallery"}])
    corrections.submit(s, _cid(s, "a", "1"), "attributes.wheelchair_access", False, actor_type="traveler",
                       actor_ref="guest-123-secret", now=NOW)
    corr = s.scalar(select(WorldCorrection))
    assert corr.actor_ref.startswith("report-") and "guest-123" not in corr.actor_ref


# ================================================================== spatial
def test_geohash_index_matches_full_scan(s):
    rng = random.Random(7)
    pts = [(43.0 + rng.random() * 0.4, 19.0 + rng.random() * 0.4) for _ in range(400)]
    _sync(s, "a", [{"id": str(i), "name": f"Place {i}", "lat": la, "lon": lo, "category": "gallery"}
                   for i, (la, lo) in enumerate(pts)], page_size=500)
    idx = GeohashIndex()
    for _ in range(20):
        c = Point(43.0 + rng.random() * 0.4, 19.0 + rng.random() * 0.4)
        r = rng.choice([0.3, 1.0, 2.5, 7.0])
        got = {p.slug for p, _ in idx.within_radius(s, c, r)}
        brute = {p.slug for p in s.scalars(select(Place)) if distance_km(c, Point(p.latitude, p.longitude)) <= r}
        assert got == brute
        nearest = [p.slug for p, _ in idx.nearest(s, c, 5)]
        brute_n = sorted(s.scalars(select(Place)), key=lambda p: (distance_km(c, Point(p.latitude, p.longitude)),
                                                                   p.slug))[:5]
        assert nearest == [p.slug for p in brute_n]
    box = (43.1, 19.1, 43.2, 19.25)
    assert {p.slug for p in idx.within_bbox(s, box)} == {
        p.slug for p in s.scalars(select(Place)) if box[0] <= p.latitude <= box[2] and box[1] <= p.longitude <= box[3]}


# ================================================================ snapshots
def test_snapshot_restores_the_same_world(s):
    _sync(s, "a", [{"id": "1", "name": "Sladoled Polar", "lat": 43.147, "lon": 19.147, "category": "dessert",
                    "opening_hours": "Mo-Su 10:00-23:00"}])
    _sync(s, "own", [{"id": "p", "name": "Sladoled Polar", "lat": 43.14701, "lon": 19.14702, "category": "dessert",
                      "opening_hours": "Mo-Su 10:00-00:00"}], cls="provider_owned")
    snap = snapshots.create(s, "t", NOW)
    engine = make_engine("sqlite://")
    create_schema(engine)
    with sessionmaker(engine)() as fresh:
        snapshots.restore(fresh, snap.content, NOW)
        assert snapshots.evidence_hash(fresh) == snap.content_hash
        a = s.scalar(select(Place).where(Place.active))
        b = fresh.scalar(select(Place).where(Place.active))
        assert (a.name, a.hours, a.resolution["opening_hours"]["source"]) == \
            (b.name, b.hours, b.resolution["opening_hours"]["source"])
        with pytest.raises(RuntimeError):
            snapshots.restore(fresh, snap.content, NOW)          # never mixes into a non-empty fabric
    engine.dispose()


def test_licence_metadata_survives_snapshot(s):
    run_sync(s, FixtureAdapter(_d("osm", attribution_required=True, attribution_text="© OSM", license="ODbL-1.0",
                                  redistribution="attribution"),
                               [{"id": "n", "name": "Galerija", "lat": 43.14, "lon": 19.14, "category": "gallery"}]),
             now=NOW)
    content = snapshots.load_content(snapshots.create(s, "lic", NOW))
    row = next(r for r in content["tables"]["world_sources"] if r["id"] == "osm")
    assert (row["license"], row["attribution_required"], row["attribution_text"], row["redistribution"]) == \
        ("ODbL-1.0", True, "© OSM", "attribution")
    assert s.scalar(select(Place)).resolution["_attribution"] == ["osm"]


def test_scope_area_assignment(s):
    coverage.upsert_area(s, code="old-town", kind="locality", name="Old Town", country_code="ME",
                         bbox=(43.139, 19.139, 43.141, 19.141))
    _sync(s, "a", [{"id": "1", "name": "Galerija A", "lat": 43.140, "lon": 19.140, "category": "gallery"},
                   {"id": "2", "name": "Galerija B", "lat": 43.150, "lon": 19.150, "category": "gallery"}])
    regions = {p.name: p.region for p in s.scalars(select(Place))}
    assert regions == {"Galerija A": "old-town", "Galerija B": "town"}          # smallest containing area
    assert coverage.scope_for(s, "old-town").contains(43.1405, 19.1405) is True
    assert Scope("radius", center=(43.14, 19.14), radius_km=1).contains(43.20, 19.14) is False


# ============================================ adversarial identity classes
def test_relocation_needs_strong_identity_and_older_location(s):
    old = {"name": "Restoran Lipa", "phone": "069 444 555", "website": "restoran-lipa.me", "category": "restaurant"}
    _sync(s, "a", [{"id": "1", **old, "lat": 43.140, "lon": 19.140, "updated_at": "2026-02-01T10:00:00+01:00"}])
    _sync(s, "b", [{"id": "x", **old, "lat": 43.158, "lon": 19.142, "updated_at": "2026-10-01T10:00:00+02:00"}],
          cls="partner_feed")
    assert _cid(s, "a", "1") == _cid(s, "b", "x")                         # moved: one business
    _sync(s, "c", [{"id": "y", **old, "lat": 43.149, "lon": 19.160, "updated_at": "2026-10-02T10:00:00+02:00"}])
    assert _cid(s, "c", "y") != _cid(s, "a", "1")                          # both locations current: a chain


def test_contradicted_identifier_does_not_merge(s):
    _sync(s, "a", [{"id": "1", "name": "Galerija Kamen", "lat": 43.14, "lon": 19.14, "category": "gallery",
                    "phone": "069 111 000", "ids": {"tripdir": "T-1"}}])
    _sync(s, "b", [{"id": "2", "name": "Restoran Vidikovac", "lat": 43.15, "lon": 19.14, "category": "restaurant",
                    "phone": "067 222 000", "ids": {"tripdir": "T-1"}}])
    assert _cid(s, "a", "1") != _cid(s, "b", "2")


def test_stale_phone_is_not_identity_evidence(s):
    _sync(s, "a", [{"id": "1", "name": "Kafe Stari Most", "lat": 43.143, "lon": 19.143, "category": "cafe",
                    "phone": "069 900 100", "updated_at": "2023-05-01T10:00:00+02:00"}])
    _sync(s, "b", [{"id": "2", "name": "Kafe Lipa", "lat": 43.1432, "lon": 19.1431, "category": "cafe",
                    "phone": "069 900 100", "updated_at": "2026-10-01T10:00:00+02:00"}])
    assert _cid(s, "a", "1") != _cid(s, "b", "2")


def test_future_observation_is_clamped_and_recorded(s):
    from app.db.models import WorldChange

    _sync(s, "a", [{"id": "1", "name": "Galerija", "lat": 43.14, "lon": 19.14, "category": "gallery",
                    "updated_at": "2027-06-01T10:00:00+02:00"}])
    a = s.scalar(select(FactAssertion).where(FactAssertion.field_name == "name"))
    assert a.observed_at.replace(tzinfo=timezone.utc) <= NOW
    assert s.scalar(select(WorldChange).where(WorldChange.change_type == "future_observation_clamped")) is not None


def test_licence_change_is_recorded_and_attribution_is_sticky(s):
    rec = [{"id": "n", "name": "Galerija", "lat": 43.14, "lon": 19.14, "category": "gallery"}]
    run_sync(s, FixtureAdapter(_d("osm", attribution_required=True, attribution_text="© OSM", license="ODbL-1.0"),
                               rec), now=NOW)
    run_sync(s, FixtureAdapter(_d("osm", license="CC0-1.0"), rec), now=NOW + timedelta(days=1))
    row = s.get(WorldSourceRow, "osm")
    assert row.license == "CC0-1.0" and row.config["terms_history"][0]["license"] == "ODbL-1.0"
    assert s.scalar(select(Place)).resolution["_attribution"] == ["osm"]        # published under ODbL terms
