"""Iteration 7, slice 1.1 hardening (before hotel / restaurant):

1. provider provenance - every source-derived field of a TripItem, the
   provider included (it is part of the identity), has an observation;
2. segment anchor identity - provider + ref + carrier/number + route +
   ORIGINAL service date, fixed at first sight; a change notice keeps the
   segment, a second date under the same ref is another segment, and an
   unresolvable change is an issue, never a guess;
3. status from status EVENTS (confirm / cancel / reinstate) ordered by the
   provider's issue time: CANCELLED dominates, only an explicit, later
   reinstatement revives (D-081)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from app.db.models import TripIssue, TripItem, TripObservation, TripSourceRecord
from app.db.session import create_schema, make_engine
from app.journey import ingest
from tests.conftest import ROOT

FIX = ROOT / "data" / "trips" / "fixtures"
RECEIVED = datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def s():
    eng = make_engine("sqlite://")
    create_schema(eng)
    with sessionmaker(eng)() as session:
        yield session
    eng.dispose()


@pytest.fixture
def trip(s):
    return ingest.create_trip(s, title="t")


def _imp(s, trip, *names):  # noqa: ANN001, ANN202
    rep = None
    for n in names:
        rep = ingest.import_document(s, trip.id, kind="airline_email", raw=(FIX / n).read_text(encoding="utf-8"),
                                     source_id=n, received_at=RECEIVED)
    return rep


def _items(s):  # noqa: ANN001, ANN202
    return s.scalars(select(TripItem).order_by(TripItem.created_at)).all()


# ------------------------------------------------------------ 1. provider provenance
def test_provider_has_an_observation_with_the_provider_line(s, trip):
    rep = _imp(s, trip, "F1_airline_lgw_tgd.eml")
    o = s.scalar(select(TripObservation).where(TripObservation.item_id == rep.item.id,
                                               TripObservation.field == "provider"))
    assert o.value == "demo air" and o.evidence == "Demo Air - booking confirmation" and o.evidence in rep.record.raw


def test_provenance_gate_fails_when_the_provider_observation_is_missing(s, trip):
    rep = _imp(s, trip, "F1_airline_lgw_tgd.eml")
    assert ingest.provenance_violations(s, rep.item) == []
    s.execute(delete(TripObservation).where(TripObservation.item_id == rep.item.id,
                                            TripObservation.field == "provider"))
    assert ingest.provenance_violations(s, rep.item) == ["provider"]


def test_provenance_gate_covers_every_source_derived_field(s, trip):
    rep = _imp(s, trip, "F1_airline_lgw_tgd.eml", "F1c_airline_lgw_tgd_change.eml")
    assert ingest.provenance_violations(s, rep.item) == []
    s.execute(delete(TripObservation).where(TripObservation.item_id == rep.item.id,
                                            TripObservation.field == "booking_reference"))
    assert "booking_reference" in ingest.provenance_violations(s, rep.item)


# ------------------------------------------------------------ 2. segment identity
def test_two_dates_under_one_ref_are_two_segments(s, trip):
    a = _imp(s, trip, "F12a_segment_18oct.eml").item
    b = _imp(s, trip, "F12b_segment_25oct.eml").item
    assert a.id != b.id and len(_items(s)) == 2
    assert a.details["service_date"] == "2026-10-18" and b.details["service_date"] == "2026-10-25"


def test_a_change_over_midnight_stays_the_same_segment(s, trip):
    a = _imp(s, trip, "F12a_segment_18oct.eml").item
    _imp(s, trip, "F12b_segment_25oct.eml")
    rep = _imp(s, trip, "F12c_change_18oct_over_midnight.eml")
    assert rep.item.id == a.id and len(_items(s)) == 2
    assert rep.item.times["departs"]["local"] == "2026-10-19T01:30"
    assert rep.item.details["service_date"] == "2026-10-18"                # the anchor does not move


def test_a_change_with_one_candidate_updates_it_even_if_the_date_moved(s, trip):
    a = _imp(s, trip, "F12a_segment_18oct.eml").item
    rep = _imp(s, trip, "F12d_change_without_original_date.eml")
    assert rep.item.id == a.id and rep.item.times["departs"]["local"] == "2026-10-20T23:30"


def test_an_unresolvable_change_is_an_issue_not_a_merge(s, trip):
    a = _imp(s, trip, "F12a_segment_18oct.eml").item
    b = _imp(s, trip, "F12b_segment_25oct.eml").item
    rep = _imp(s, trip, "F12d_change_without_original_date.eml")
    assert rep.item is None and rep.record_new and "ambiguous_segment_identity" in rep.issues
    assert {i.kind for i in s.scalars(select(TripIssue))} >= {"ambiguous_segment_identity"}
    s.refresh(a)
    s.refresh(b)
    assert a.times["departs"]["local"] == "2026-10-18T23:30" and b.times["departs"]["local"] == "2026-10-25T23:30"
    assert s.scalar(select(TripSourceRecord).where(TripSourceRecord.id == rep.record.id)) is not None   # kept


# ------------------------------------------------------------ 3. status events
def _status(s, trip, *names):  # noqa: ANN001, ANN202
    return _imp(s, trip, *names).item.status


def test_confirmation_then_cancellation_is_cancelled(s, trip):
    assert _status(s, trip, "F1_airline_lgw_tgd.eml", "F1c_airline_lgw_tgd_change.eml",
                   "F8_airline_cancel.eml") == "cancelled"


def test_cancellation_first_then_the_older_confirmation_is_still_cancelled(s, trip):
    assert _status(s, trip, "F8_airline_cancel.eml", "F1_airline_lgw_tgd.eml") == "cancelled"


def test_an_old_confirmation_forwarded_after_the_cancellation_does_not_revive(s, trip):
    assert _status(s, trip, "F1_airline_lgw_tgd.eml", "F8_airline_cancel.eml",
                   "F1b_airline_lgw_tgd_fwd.eml") == "cancelled"


def test_a_later_ordinary_confirmation_does_not_revive_and_is_surfaced(s, trip):
    rep = _imp(s, trip, "F1_airline_lgw_tgd.eml", "F8_airline_cancel.eml", "F8c_airline_confirm_after_cancel.eml")
    assert rep.item.status == "cancelled"
    assert "confirmation_after_cancellation" in {i.kind for i in s.scalars(select(TripIssue))}


def test_an_explicit_later_reinstatement_revives(s, trip):
    assert _status(s, trip, "F1_airline_lgw_tgd.eml", "F8_airline_cancel.eml",
                   "F8b_airline_reinstated.eml") == "confirmed"


def test_reinstatement_arriving_before_the_cancellation_still_wins_by_issue_time(s, trip):
    assert _status(s, trip, "F8b_airline_reinstated.eml", "F1_airline_lgw_tgd.eml",
                   "F8_airline_cancel.eml") == "confirmed"


def test_every_status_event_stays_in_the_history(s, trip):
    rep = _imp(s, trip, "F1_airline_lgw_tgd.eml", "F8_airline_cancel.eml", "F8c_airline_confirm_after_cancel.eml",
               "F8b_airline_reinstated.eml")
    events = sorted(o.value for o in s.scalars(select(TripObservation).where(
        TripObservation.item_id == rep.item.id, TripObservation.field == "status_event")))
    assert events == ["cancel", "confirm", "confirm", "reinstate"]


def test_a_model_may_not_propose_a_status_event(s, trip):
    rep = _imp(s, trip, "F8_airline_cancel.eml")
    out = ingest.accept_proposals(s, rep.record.id, rep.item.id, [
        {"field": "status_event", "value": "reinstate", "evidence": "Your booking has been cancelled.",
         "extractor": "model"}])
    assert out.accepted == [] and rep.item.status == "cancelled"
