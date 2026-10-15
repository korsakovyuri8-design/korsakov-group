"""Iteration 7, slice 2: hotel (F2, HTML copy F9), restaurant (F3, cancel F3b,
explicit reinstatement F3c) and a guest statement (F6 / F6b) next to the
airline booking (F1). Gates: an accommodation item and never a Stay; copies
are records, not items; date-only times stay date-only; status by events;
field-level provenance for every kind; a guest statement is its own STATED
item that never overwrites provider facts; a disagreement beyond the policy
tolerance (data/trips/policies.yaml only) is an issue; import order and
re-import change nothing."""

from __future__ import annotations

import itertools
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db.models import Stay, TripIssue, TripItem, TripObservation, TripSourceRecord
from app.db.session import create_schema, make_engine
from app.journey import ingest, policies, view
from tests.conftest import ROOT

FIX = ROOT / "data" / "trips" / "fixtures"
RECEIVED = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
DOC_KIND = {"F1": "airline_email", "F2": "hotel_email", "F9": "hotel_email", "F3": "restaurant_email",
            "F6": "guest_statement"}


def _kind(name: str) -> str:
    return DOC_KIND[name.split("_")[0].rstrip("bcd")]


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


def _imp(s, trip, *names):  # noqa: ANN001, ANN202
    rep = None
    for n in names:
        rep = ingest.import_document(s, trip.id, kind=_kind(n), raw=(FIX / n).read_text(encoding="utf-8"),
                                     source_id=n, received_at=RECEIVED)
    return rep


def _items(s, kind=None):  # noqa: ANN001, ANN202
    q = select(TripItem)
    if kind:
        q = q.where(TripItem.kind == kind)
    return s.scalars(q).all()


# ------------------------------------------------------------------ hotel
def test_a_hotel_email_is_an_accommodation_item_and_never_a_stay(s, trip):
    item = _imp(s, trip, "F2_hotel_kotor.eml").item
    assert item.kind == "accommodation" and item.status == "confirmed" and item.booking_reference == "HK20931"
    assert item.provider == "hotel demo kotor" and item.details["guests"] == 2
    assert s.scalars(select(Stay)).all() == []


def test_date_only_check_in_and_out_stay_date_only(s, trip):
    item = _imp(s, trip, "F2_hotel_kotor.eml").item
    ci, co = item.times["check_in"], item.times["check_out"]
    assert ci["local"] == "2026-10-18" and ci["date_only"] and ci["utc"] is None and ci["tz"] == "Europe/Podgorica"
    assert co["local"] == "2026-10-21" and co["date_only"] and co["utc"] is None
    assert item.details["check_in_from"] == "14:00" and item.details["check_out_until"] == "11:00"   # stated, apart


def test_the_html_copy_is_a_new_record_but_the_same_item_and_revision(s, trip):
    first = _imp(s, trip, "F2_hotel_kotor.eml")
    rep = _imp(s, trip, "F9_hotel_kotor.html")
    assert rep.record_new and not rep.item_new and rep.item.id == first.item.id
    assert rep.new_revision is None and rep.item.current_revision == 1 and len(_items(s, "accommodation")) == 1
    assert ingest.provenance_violations(s, rep.item) == []


# ------------------------------------------------------------------ restaurant
def test_restaurant_confirmation_is_confirmed(s, trip):
    item = _imp(s, trip, "F3_restaurant_kotor.eml").item
    assert item.kind == "restaurant" and item.status == "confirmed" and item.booking_reference == "R4417"
    assert item.details["venue"] == "Konoba Demo" and item.details["party"] == 2
    assert item.times["starts"]["local"] == "2026-10-18T20:00" and item.times["starts"]["utc"] == "2026-10-18T18:00:00+00:00"


def test_restaurant_cancellation_then_old_confirmation_again_then_explicit_reinstatement(s, trip):
    assert _imp(s, trip, "F3_restaurant_kotor.eml", "F3b_restaurant_cancel.eml").item.status == "cancelled"
    rep = ingest.import_document(s, trip.id, kind="restaurant_email",
                                 raw="Fwd:\n\n" + (FIX / "F3_restaurant_kotor.eml").read_text(encoding="utf-8"),
                                 source_id="fwd-old", received_at=RECEIVED)
    assert rep.item.status == "cancelled"                                   # an old confirmation never revives
    assert _imp(s, trip, "F3c_restaurant_reinstated.eml").item.status == "confirmed"
    events = sorted(o.value for o in s.scalars(select(TripObservation).where(
        TripObservation.item_id == rep.item.id, TripObservation.field == "status_event")))
    assert events == ["cancel", "confirm", "confirm", "reinstate"]           # every event kept


# ------------------------------------------------------------------ provenance, every kind
def test_every_kind_has_field_level_provenance(s, trip):
    _imp(s, trip, "F1_airline_lgw_tgd.eml", "F2_hotel_kotor.eml", "F3_restaurant_kotor.eml",
         "F3b_restaurant_cancel.eml", "F3c_restaurant_reinstated.eml", "F6_guest_statement.json")
    for item in _items(s):
        assert ingest.provenance_violations(s, item) == [], item.kind
        if item.provider:
            assert any(o.field == "provider" for o in s.scalars(select(TripObservation).where(
                TripObservation.item_id == item.id)))


# ------------------------------------------------------------------ guest statement
def test_a_guest_statement_is_its_own_stated_item_and_changes_no_provider_fact(s, trip):
    flight = _imp(s, trip, "F1_airline_lgw_tgd.eml").item
    before = (flight.status, dict(flight.times), dict(flight.details), flight.current_revision)
    stmt = _imp(s, trip, "F6_guest_statement.json").item
    assert stmt.kind == "statement" and stmt.status == "stated" and stmt.id != flight.id
    assert stmt.times["at"]["local"] == "2026-10-18T14:00" and stmt.times["at"]["approximate"]
    s.refresh(flight)
    assert (flight.status, flight.times, flight.details, flight.current_revision) == before


def test_disagreement_beyond_the_tolerance_is_an_issue(s, trip):
    _imp(s, trip, "F1_airline_lgw_tgd.eml", "F6_guest_statement.json")
    [issue] = s.scalars(select(TripIssue).where(TripIssue.kind == "guest_provider_disagreement")).all()
    d = issue.detail
    assert d["difference_minutes"] == 65 and d["tolerance_minutes"] == 30
    assert d["policy_version"] == policies.load()["version"]
    assert d["provider_local"] == "2026-10-18T12:55" and d["guest_local"] == "2026-10-18T14:00"


def test_within_the_tolerance_both_observations_stay_and_there_is_no_issue(s, trip):
    _imp(s, trip, "F1_airline_lgw_tgd.eml", "F6b_guest_statement_close.json")
    assert not s.scalars(select(TripIssue).where(TripIssue.kind == "guest_provider_disagreement")).all()
    stmt = _items(s, "statement")[0]
    [rel] = stmt.details["related"]
    assert rel["difference_minutes"] == 5 and rel["within_tolerance"]
    assert {o.field for o in s.scalars(select(TripObservation).where(TripObservation.item_id == stmt.id))} >= {"at"}


def test_the_tolerance_comes_only_from_the_policy_file(s, trip, monkeypatch):
    real = policies.load()
    monkeypatch.setattr(policies, "load", lambda: {**real, "tolerances": {"arrival_time_minutes": 90}})
    _imp(s, trip, "F1_airline_lgw_tgd.eml", "F6_guest_statement.json")
    assert not s.scalars(select(TripIssue).where(TripIssue.kind == "guest_provider_disagreement")).all()


def test_no_fuzzy_merge_of_statement_and_booking(s, trip):
    _imp(s, trip, "F6_guest_statement.json", "F1_airline_lgw_tgd.eml")
    assert {i.kind for i in _items(s)} == {"statement", "flight"} and len(_items(s)) == 2


# ------------------------------------------------------------------ order, re-import
ALL = ["F1_airline_lgw_tgd.eml", "F6_guest_statement.json", "F2_hotel_kotor.eml", "F9_hotel_kotor.html",
       "F3_restaurant_kotor.eml", "F3b_restaurant_cancel.eml", "F3c_restaurant_reinstated.eml"]


def _state(s):  # noqa: ANN001, ANN202
    items = sorted((i.identity, i.status, i.current_revision,
                    {k: v for k, v in i.details.items() if k != "related"},
                    {k: {f: v.get(f) for f in ("local", "tz", "utc", "date_only")} for k, v in i.times.items()})
                   for i in _items(s))
    issues = sorted((i.kind, i.detail.get("difference_minutes")) for i in s.scalars(select(TripIssue)))
    return items, issues, len(s.scalars(select(TripSourceRecord)).all())


@pytest.mark.parametrize("order", [ALL, list(reversed(ALL)), ALL[3:] + ALL[:3]])
def test_import_order_does_not_change_the_result(order):
    def run(names):  # noqa: ANN001, ANN202
        eng = make_engine("sqlite://")
        create_schema(eng)
        with sessionmaker(eng)() as session:
            t = ingest.create_trip(session, title="t")
            _imp(session, t, *names)
            return _state(session)

    assert run(order) == run(ALL)


def test_reimporting_every_fixture_is_idempotent(s, trip):
    _imp(s, trip, *ALL)
    before = (_state(s), len(s.scalars(select(TripObservation)).all()))
    _imp(s, trip, *ALL)
    assert (_state(s), len(s.scalars(select(TripObservation)).all())) == before


# ------------------------------------------------------------------ the story
def test_the_mini_scenario_tells_one_consistent_current_picture_with_its_evidence(s, trip):
    _imp(s, trip, "F6_guest_statement.json", "F1_airline_lgw_tgd.eml", "F2_hotel_kotor.eml",
         "F3_restaurant_kotor.eml", "F3b_restaurant_cancel.eml", "F3c_restaurant_reinstated.eml")
    text = view.story(s, trip.id)
    assert "Flight DM1432 LGW -> TGD: departs 18 Oct 09:15 (Europe/London), arrives 18 Oct 12:55 " \
           "(Europe/Podgorica) - CONFIRMED" in text
    assert "Hotel Demo Kotor: 18 Oct -> 21 Oct (check-in from 14:00, check-out until 11:00) - CONFIRMED" in text
    assert "Konoba Demo: 18 Oct 20:00 (Europe/Podgorica), party of 2 - CONFIRMED" in text
    assert "Guest said: arrival in Podgorica 18 Oct around 14:00 - STATED (not a booking)" in text
    assert "the guest said around 14:00, Demo Air DM1432 arrives 12:55 - 65 min apart (tolerance 30 min" in text
    assert text.index("confirm: \"Your reservation is confirmed.\"") < text.index(
        "cancel: \"Your reservation has been cancelled.\"") < text.index(
        "reinstate: \"Your reservation has been reinstated and is confirmed.\"")
    assert view.story(s, trip.id) == text                                    # deterministic
