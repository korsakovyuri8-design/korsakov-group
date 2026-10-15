"""Iteration 7, slice 1 (docs/ITERATION_7_DESIGN.md §13-14): Trip,
TripSourceRecord, TripObservation, TripItem with revisions, the airline
confirmation parser for F1. Gates: provenance, time zones with a full time
record, two-level idempotency, a change notice as a revision, no invented
status, and the trip layer never touching transaction / Stay state."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db.models import TripIssue, TripItem, TripObservation, TripSourceRecord
from app.db.session import create_schema, make_engine
from app.journey import ingest, timeinfo
from tests.conftest import ROOT

FIX = ROOT / "data" / "trips" / "fixtures"
RECEIVED = datetime(2026, 10, 6, 9, 0, tzinfo=timezone.utc)


def _raw(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


@pytest.fixture
def s():
    eng = make_engine("sqlite://")
    create_schema(eng)
    with sessionmaker(eng)() as session:
        yield session
    eng.dispose()


@pytest.fixture
def trip(s):
    return ingest.create_trip(s, title="London - Kotor")


def _import(s, trip, name: str, source_id: str | None = None):  # noqa: ANN001, ANN202
    return ingest.import_document(s, trip.id, kind="airline_email", raw=_raw(name),
                                  source_id=source_id or name, received_at=RECEIVED)


# ------------------------------------------------------------------ the booking
def test_airline_confirmation_becomes_one_confirmed_flight(s, trip):
    rep = _import(s, trip, "F1_airline_lgw_tgd.eml")
    item = rep.item
    assert rep.record_new and rep.item_new
    assert item.kind == "flight" and item.provider == "demo air" and item.booking_reference == "DMX7Q2"
    assert item.details == {"carrier": "DM", "flight_number": "DM1432", "origin": "LGW", "destination": "TGD",
                            "cabin": "Economy", "passengers": ["Ana Example", "Marko Example"]}
    assert item.status == "confirmed" and item.current_revision == 1


def test_every_resolved_field_has_provenance_in_the_raw_document(s, trip):
    rep = _import(s, trip, "F1_airline_lgw_tgd.eml")
    raw = rep.record.raw
    obs = s.scalars(select(TripObservation).where(TripObservation.item_id == rep.item.id)).all()
    fields = {o.field for o in obs}
    assert {"status", "booking_reference", "flight_number", "origin", "destination", "departs", "arrives",
            "passengers"} <= fields
    for o in obs:
        assert o.record_id == rep.record.id and o.evidence and o.evidence in raw, o.field
    status = next(o for o in obs if o.field == "status")
    assert "confirmed" in status.evidence.lower()


# ------------------------------------------------------------------ time
def test_times_are_utc_plus_the_zone_of_the_airport_with_a_full_record(s, trip):
    item = _import(s, trip, "F1_airline_lgw_tgd.eml").item
    dep, arr = item.times["departs"], item.times["arrives"]
    assert dep["raw"] == "18 Oct 2026 09:15" and dep["local"] == "2026-10-18T09:15" and dep["tz"] == "Europe/London"
    assert dep["utc"] == "2026-10-18T08:15:00+00:00"                       # BST, UTC+1
    assert arr["tz"] == "Europe/Podgorica" and arr["utc"] == "2026-10-18T10:55:00+00:00"   # CEST, UTC+2
    assert dep["evidence"] and arr["evidence"]
    assert dep["registry"] == timeinfo.REGISTRY_VERSION


def test_an_unknown_place_has_an_unknown_zone_and_no_utc():
    rec, issue = timeinfo.time_record("18 Oct 2026 09:15", datetime(2026, 10, 18, 9, 15), "XYZ", "airport",
                                      evidence="e1")
    assert rec["tz"] is None and rec["utc"] is None and rec["local"] == "2026-10-18T09:15"
    assert issue == "unknown_timezone"


@pytest.mark.parametrize("local, problem", [
    (datetime(2026, 10, 25, 1, 30), "ambiguous_local_time"),       # Europe/London falls back at 02:00 BST
    (datetime(2026, 3, 29, 1, 30), "nonexistent_local_time"),      # ... springs forward 01:00 -> 02:00
])
def test_dst_ambiguity_is_an_issue_not_a_silent_choice(local, problem):
    rec, issue = timeinfo.time_record("raw", local, "LGW", "airport", evidence="e1")
    assert rec["tz"] == "Europe/London" and rec["utc"] is None and issue == problem


# ------------------------------------------------------------------ idempotency
def _counts(s):  # noqa: ANN001, ANN202
    return (len(s.scalars(select(TripSourceRecord)).all()), len(s.scalars(select(TripItem)).all()),
            len(s.scalars(select(TripObservation)).all()))


def test_identical_input_is_a_redelivery(s, trip):
    _import(s, trip, "F1_airline_lgw_tgd.eml")
    before = _counts(s)
    rep = _import(s, trip, "F1_airline_lgw_tgd.eml", source_id="again")
    assert not rep.record_new and not rep.item_new and _counts(s) == before


@pytest.mark.parametrize("copy", ["F1b_airline_lgw_tgd_fwd.eml", "F1d_airline_lgw_tgd_reformatted.eml"])
def test_forwarded_or_reformatted_copy_is_a_new_record_but_the_same_item(s, trip, copy):
    first = _import(s, trip, "F1_airline_lgw_tgd.eml")
    rep = _import(s, trip, copy)
    assert rep.record_new and not rep.item_new and rep.item.id == first.item.id
    assert rep.item.current_revision == 1 and rep.new_revision is None
    assert len(s.scalars(select(TripItem)).all()) == 1


# ------------------------------------------------------------------ revisions
def test_a_schedule_change_is_a_revision_and_the_status_is_unchanged(s, trip):
    first = _import(s, trip, "F1_airline_lgw_tgd.eml")
    rep = _import(s, trip, "F1c_airline_lgw_tgd_change.eml")
    item = rep.item
    assert item.id == first.item.id and rep.new_revision == 2 and item.current_revision == 2
    assert item.status == "confirmed"
    assert item.times["departs"]["local"] == "2026-10-18T11:15" and item.times["arrives"]["local"] == "2026-10-18T14:55"
    old = item.revisions[0]
    assert old["revision"] == 1 and old["times"]["departs"]["local"] == "2026-10-18T09:15"   # kept
    assert item.revisions[1]["changed_fields"] == ["arrives", "departs"]


def test_an_older_document_arriving_later_does_not_become_current(s, trip):
    _import(s, trip, "F1c_airline_lgw_tgd_change.eml")
    rep = _import(s, trip, "F1_airline_lgw_tgd.eml")
    assert rep.item.times["departs"]["local"] == "2026-10-18T11:15"         # issued later -> still current
    assert [r["issued_at"] for r in rep.item.revisions] == sorted(r["issued_at"] for r in rep.item.revisions)


# ------------------------------------------------------------------ no invented state
def test_a_proposed_status_without_evidence_in_the_raw_is_rejected(s, trip):
    rep = _import(s, trip, "F1c_airline_lgw_tgd_change.eml")
    out = ingest.accept_proposals(s, rep.record.id, rep.item.id, [
        {"field": "status", "value": "cancelled", "evidence": None, "extractor": "model"},
        {"field": "status", "value": "cancelled", "evidence": "Your booking was cancelled.", "extractor": "model"},
    ])
    assert out.accepted == [] and len(out.rejected) == 2
    assert rep.item.status == "confirmed"


def test_unknown_zone_is_surfaced_as_an_issue(s, trip):
    raw = _raw("F1_airline_lgw_tgd.eml").replace("(TGD)", "(QQQ)")
    rep = ingest.import_document(s, trip.id, kind="airline_email", raw=raw, source_id="qqq", received_at=RECEIVED)
    assert rep.item.times["arrives"]["tz"] is None and rep.item.times["arrives"]["utc"] is None
    kinds = {i.kind for i in s.scalars(select(TripIssue).where(TripIssue.item_id == rep.item.id))}
    assert "unknown_timezone" in kinds


# ------------------------------------------------------------------ ownership
def test_the_journey_layer_never_writes_transaction_or_stay_state():
    import ast

    banned = ("app.transactions", "app.marketplace", "app.stays")
    for f in (ROOT / "app" / "journey").rglob("*.py"):
        tree = ast.parse(f.read_text())
        mods = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module] + \
               [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        assert not [m for m in mods if m.startswith(banned)], f
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
                {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        assert not names & {"Quote", "Action", "ExternalTransaction", "Stay"}, f


def test_fixture_files_exist():
    assert all(Path(FIX / n).exists() for n in ("F1_airline_lgw_tgd.eml", "F1b_airline_lgw_tgd_fwd.eml",
                                                "F1c_airline_lgw_tgd_change.eml", "F1d_airline_lgw_tgd_reformatted.eml"))


def test_a_field_the_change_notice_does_not_mention_is_not_changed(s, trip):
    _import(s, trip, "F1_airline_lgw_tgd.eml")
    item = _import(s, trip, "F1c_airline_lgw_tgd_change.eml").item
    assert item.details["passengers"] == ["Ana Example", "Marko Example"]
    assert "passengers" not in item.revisions[1]["details"] and "passengers" not in item.revisions[1]["changed_fields"]


def test_details_are_validated_per_kind():
    ingest.validate_details("flight", {"carrier": "DM", "passengers": []})
    with pytest.raises(ValueError):
        ingest.validate_details("flight", {"seat_map": "x"})
    with pytest.raises(ValueError):
        ingest.validate_details("flight", {"passengers": "Ana"})
