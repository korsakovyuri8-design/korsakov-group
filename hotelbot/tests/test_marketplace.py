"""Service marketplace: provider discovery, property relationships, inventory
holds (expiry, confirmation, concurrency), pricing, terms, commercial
metadata, provider modification and conditional acceptance."""

from __future__ import annotations

import os
import threading
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.clock import FrozenClock
from app.container import build_container
from app.db.models import (
    ExternalProvider,
    HoldStatus,
    InventoryHold,
    Offering,
    PropertyProvider,
    ProviderRelation,
)
from app.marketplace import discovery, pricing, terms
from app.places import availability
from app.places.availability import NoAvailability
from app.transactions.providers.base import SubmitRequest
from app.transactions.providers.mock import MockExternalProvider
from app.trip.concierge import split_requests
from tests.conftest import make_settings

JAN = datetime(2027, 1, 11, 9, 0, tzinfo=timezone.utc)      # Monday, ski season
LOCAL = timezone(timedelta(hours=1))                          # Europe/Podgorica in January
TZ = "Europe/Podgorica"
REGION = "zabljak-demo"


@pytest.fixture
def mk():
    container = build_container(make_settings(), llm=None, clock=FrozenClock(JAN))
    with container.session_factory() as s:
        prop_id = container.properties.get("example-hotel").property_id
        yield s, prop_id


def _discover(s, prop_id, service_type, details, *, start=None, day=None, property_scoped=True, replaces=None):
    return discovery.discover(s, service_type=service_type, details=details, region=REGION,
                              property_id=prop_id if property_scoped else None, now=JAN, tz=TZ,
                              start=start, day=day, replaces=replaces)


def _offering(s, slug) -> Offering:
    return s.scalar(select(Offering).where(Offering.slug == slug))


# --------------------------------------------------------------- transport
def test_transport_constraints_pick_a_provider_that_fits(mk):
    s, prop = mk
    at = datetime(2027, 1, 12, 9, 0, tzinfo=LOCAL)
    small = _discover(s, prop, "airport_transfer", {"party_size": 2}, start=at)
    assert small.candidates[0].provider.slug == "demo-transfers"            # default partner, cheapest
    assert small.candidates[0].amount == Decimal("35.00")
    for details in ({"party_size": 6}, {"party_size": 2, "child_seats": 1}, {"party_size": 3, "luggage": 6}):
        res = _discover(s, prop, "airport_transfer", details, start=at)
        assert [c.provider.slug for c in res.candidates] == ["demo-premium-transfers"], details
    too_big = _discover(s, prop, "airport_transfer", {"party_size": 9}, start=at)
    assert too_big.candidates == [] and too_big.reason == "vehicle_too_small"


def test_only_registry_providers_are_candidates(mk):
    s, prop = mk
    res = _discover(s, prop, "guide_booking", {"party_size": 2, "language": "ru"},
                    start=datetime(2027, 1, 17, 9, 0, tzinfo=LOCAL))
    names = {c.provider.slug for c in res.candidates}
    assert names <= {p.slug for p in s.scalars(select(ExternalProvider))}
    assert names == {"demo-durmitor-private"}


# ----------------------------------------------------------- relationships
def test_blocked_and_exclusive_relationships(mk):
    s, prop = mk
    at = datetime(2027, 1, 12, 9, 0, tzinfo=LOCAL)
    premium = s.scalar(select(ExternalProvider).where(ExternalProvider.slug == "demo-premium-transfers"))
    cheap = s.scalar(select(ExternalProvider).where(ExternalProvider.slug == "demo-transfers"))
    rel = s.scalar(select(PropertyProvider).where(PropertyProvider.provider_id == cheap.id))
    rel.relation = ProviderRelation.BLOCKED
    s.flush()
    assert [c.provider.slug for c in _discover(s, prop, "taxi", {"party_size": 2}, start=at).candidates] == \
        ["demo-premium-transfers"]
    s.add(PropertyProvider(property_id=prop, provider_id=premium.id, relation=ProviderRelation.EXCLUSIVE,
                           services={"service_types": ["taxi"]}))
    rel.relation = ProviderRelation.DEFAULT
    s.flush()
    # exclusive wins even though the default partner is cheaper and fits
    assert {c.provider.slug for c in _discover(s, prop, "taxi", {"party_size": 2}, start=at).candidates} == \
        {"demo-premium-transfers"}
    # ...but only for the listed services
    assert _discover(s, prop, "airport_transfer", {"party_size": 2}, start=at).candidates[0].provider.slug == \
        "demo-transfers"


def test_discovery_works_without_a_property(mk):
    """Direct-to-traveller: the same discovery, region providers only."""
    s, prop = mk
    res = _discover(s, prop, "rental", {"category": "car", "quantity": 1, "days": 2},
                    start=datetime(2027, 1, 16, 10, 0, tzinfo=LOCAL), property_scoped=False)
    assert res.candidates and res.candidates[0].provider.slug == "demo-car-hire"


# ------------------------------------------------------------------ rentals
def test_rental_category_and_minimum_duration(mk):
    s, prop = mk
    at = datetime(2027, 1, 16, 10, 0, tzinfo=LOCAL)
    assert _discover(s, prop, "rental", {"category": "scooter", "quantity": 1}, start=at).reason == \
        "category_unsupported"
    assert _discover(s, prop, "rental", {"category": "car", "quantity": 1, "hours": 5}, start=at).reason == \
        "below_minimum_duration"


def test_rental_sizes_are_required_and_mapped_to_variants(mk):
    s, prop = mk
    sat = date(2027, 1, 16)
    pending = _discover(s, prop, "rental", {"category": "ski", "quantity": 2}, day=sat)
    assert not pending.candidates and pending.pending[0].missing == ["heights"]
    res = _discover(s, prop, "rental", {"category": "ski", "quantity": 2, "heights": [180, 170]}, day=sat)
    best = res.candidates[0]
    assert best.need == {"180": 1, "170": 1}
    assert best.resolved["start_time"].startswith("2027-01-16T08:30")    # the shop's opening, shown to the guest
    assert best.amount == Decimal("50.00")
    four = _discover(s, prop, "rental", {"category": "ski", "quantity": 4, "heights": [170, 171, 172, 173]}, day=sat)
    assert not four.candidates and four.reason == "no_capacity"


def test_multi_day_rental_needs_every_day(mk):
    s, prop = mk
    car = _offering(s, "car-compact")
    w = availability.window(s, car, datetime(2027, 1, 16, 10, 0, tzinfo=LOCAL).astimezone(timezone.utc), days=2,
                            tz=TZ)
    assert w is not None and (w[1] - w[0]) > timedelta(days=1)
    # the season ends 2027-04-30: a 3-day rental from the 29th cannot be covered
    late = availability.window(s, car, datetime(2027, 4, 29, 10, 0, tzinfo=timezone.utc), days=3, tz=TZ)
    assert availability.check(s, car, late[0], late[1], {None: 1}, JAN) == "not_offered_at_that_time"


# ------------------------------------------------------------------- guides
def test_guide_matching_language_party_and_formats(mk):
    s, prop = mk
    sunday = datetime(2027, 1, 17, 9, 0, tzinfo=LOCAL)
    assert _discover(s, prop, "guide_booking", {"party_size": 2, "language": "it"}, start=sunday).reason == \
        "language_unavailable"
    assert _discover(s, prop, "guide_booking", {"party_size": 8, "language": "ru"}, start=sunday).reason == \
        "party_too_large"
    sat = date(2027, 1, 16)
    res = _discover(s, prop, "guide_booking", {"party_size": 2, "language": "en", "activity": "hiking"}, day=sat)
    opts = discovery.options(res, by_format=True)
    assert {o.format for o in opts} == {"private", "group"}
    assert opts[0].provider.slug == "demo-peak-guides"                       # preferred by the property


# -------------------------------------------------------------------- holds
def _ski_window(s):
    mali = _offering(s, "skis-mali")
    start, end, _ = availability.window(s, mali, None, date(2027, 1, 16), tz=TZ)
    return mali, start, end


def test_hold_blocks_and_expiry_releases(mk):
    s, _ = mk
    mali, start, end = _ski_window(s)
    need = {"170": 1}
    availability.hold(s, mali, start, end, need, now=JAN, expires_at=JAN + timedelta(minutes=15))
    assert availability.check(s, mali, start, end, need, JAN) == "no_capacity"
    with pytest.raises(NoAvailability):
        availability.hold(s, mali, start, end, need, now=JAN, expires_at=JAN + timedelta(minutes=15))
    later = JAN + timedelta(minutes=16)     # the quote (and its hold) expired: free again, no sweeper needed
    assert availability.check(s, mali, start, end, need, later) is None


def test_confirmed_hold_survives_expiry_and_release_frees_it(mk):
    s, _ = mk
    mali, start, end = _ski_window(s)
    need = {"180": 1}
    holds = availability.hold(s, mali, start, end, need, now=JAN, expires_at=JAN + timedelta(minutes=15),
                              quote_id=None)
    for h in holds:
        h.quote_id = None
    s.flush()
    holds[0].status, holds[0].expires_at, holds[0].transaction_id = HoldStatus.CONFIRMED, None, "txn-1"
    s.flush()
    much_later = JAN + timedelta(days=1)
    assert availability.check(s, mali, start, end, need, much_later) == "no_capacity"
    # a change of THAT booking may reuse its own stock
    assert availability.check(s, mali, start, end, need, much_later, ignore_transaction="txn-1") is None
    availability.release_transaction(s, "txn-1")
    s.flush()
    assert availability.check(s, mali, start, end, need, much_later) is None


@pytest.mark.skipif(not os.environ.get("HOTELBOT_TEST_DATABASE_URL", "").startswith("postgresql"),
                    reason="needs PostgreSQL (row locks)")
def test_two_guests_cannot_hold_the_last_item_concurrently():
    container = build_container(make_settings(), llm=None, clock=FrozenClock(JAN))
    results: list[str] = []
    barrier = threading.Barrier(2)

    def attempt() -> None:
        with container.session_factory() as s:
            mali, start, end = _ski_window(s)
            barrier.wait()
            try:
                availability.hold(s, mali, start, end, {"160": 1}, now=JAN, expires_at=JAN + timedelta(minutes=15))
                s.commit()
                results.append("held")
            except NoAvailability:
                s.rollback()
                results.append("refused")

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["held", "refused"]
    with container.session_factory() as s:
        assert len(list(s.scalars(select(InventoryHold)))) == 1


# ------------------------------------------------------- pricing and terms
def test_pricing_models():
    night = {"party_size": 3, "pickup_time": "2027-01-12T23:30:00+01:00"}
    assert pricing.compute({"model": "per_booking", "amount": 30, "per_extra_person": 5, "night_surcharge": 10},
                           night, tz=TZ) == Decimal("50.00")
    assert pricing.compute({"model": "per_item_day", "amount": 45}, {"quantity": 1, "days": 3}) == Decimal("135.00")
    assert pricing.compute({"model": "per_hour", "amount": 40, "min_hours": 2}, {"hours": 1}) == Decimal("80.00")
    assert pricing.compute({"model": "per_person", "amount": 35}, {"party_size": 4}) == Decimal("140.00")
    assert pricing.compute({"model": "per_booking", "amount": 60, "child_seat": 5},
                           {"party_size": 1, "child_seats": 2}) == Decimal("70.00")
    assert pricing.compute({}, {}) is None


def test_commission_never_changes_the_guest_price(mk):
    s, _ = mk
    car = _offering(s, "car-compact")
    provider = s.get(ExternalProvider, car.provider_id)
    snap = pricing.commission(provider, Decimal("90.00"))
    assert snap["guest_price"] == "90.00" and snap["commission"] == "15.00"     # fixed 15 per booking
    pct = pricing.commission(s.scalar(select(ExternalProvider).where(ExternalProvider.slug == "demo-transfers")),
                             Decimal("45.00"))
    assert pct == {"guest_price": "45.00", "commission_type": "percent", "commission": "4.50"}


def test_terms_merge_and_render():
    merged = terms.merged({"id_required": True, "free_cancellation_hours": 24, "deposit": {"amount": 200}},
                          {"deposit": {"amount": 100, "currency": "EUR"}},
                          {"weather_dependent": True, "meeting_point": "lake", "format": "private"})
    assert merged["deposit"] == {"amount": 100, "currency": "EUR"}             # offering overrides provider
    assert "format" not in merged                                             # only material terms
    text = terms.render(merged, "en")
    assert text.startswith("Important terms: depends on weather") and "deposit 100 EUR" in text
    assert "Важные условия" in terms.render(merged, "ru")


# ----------------------------------------------------------- mock provider
def test_mock_modification_keeps_the_reference_and_conditional_acceptance():
    p = MockExternalProvider("demo", {"currency": "EUR"}, FrozenClock(JAN))
    first = p.submit(SubmitRequest(idempotency_key="txn-a", service_type="taxi", details={"party_size": 2},
                                   quote_reference=None, amount=Decimal("35"), currency="EUR",
                                   customer_reference="stay"))
    changed = p.modify(SubmitRequest(idempotency_key="txn-b", service_type="taxi", details={"party_size": 3},
                                     quote_reference=None, amount=Decimal("40"), currency="EUR",
                                     customer_reference="stay", modifies_reference=first.reference))
    assert changed.status == "accepted" and changed.reference == first.reference and p.active_bookings == 1
    weather = p.submit(SubmitRequest(idempotency_key="txn-c", service_type="guide_booking",
                                     details={"weather_dependent": True}, quote_reference=None,
                                     amount=Decimal("160"), currency="EUR", customer_reference="stay"))
    assert weather.status == "accepted_conditional"


# --------------------------------------------------------- multi-service
def test_split_requests_one_sentence_three_services():
    parts = split_requests("We arrive Friday evening. There are four of us. Get us a transfer, two sets of skis "
                           "for Saturday, and I'd like a Russian-speaking guide for Durmitor on Sunday.")
    assert parts[2:] == ["Get us a transfer", "two sets of skis for Saturday",
                         "and I'd like a Russian-speaking guide for Durmitor on Sunday."]
    assert split_requests("Нужен трансфер, лыжи на субботу и гид на воскресенье.") == \
        ["Нужен трансфер", "лыжи на субботу", "и гид на воскресенье."]
