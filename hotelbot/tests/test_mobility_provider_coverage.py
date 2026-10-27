from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.db.models import (
    ExternalProvider,
    ExternalTransaction,
    ProviderCoverage,
    Quote,
    TransactionContext,
    Trip,
    TripItem,
)
from app.db.session import (
    create_schema,
    make_engine,
    make_session_factory,
)
from app.mobility import marketplace


NOW = datetime(
    2026, 10, 25, 17, 0,
    tzinfo=timezone.utc,
)


def make_db():
    engine = make_engine("sqlite://")
    create_schema(engine)
    factory = make_session_factory(engine)
    return engine, factory


def count(db, model):
    return db.scalar(
        select(func.count()).select_from(model)
    )


def add_provider(
    db,
    *,
    slug="verified-transfer",
    name="Verified Transfer",
):
    row = ExternalProvider(
        property_id=None,
        region="legacy-region-must-not-authorize",
        slug=slug,
        name=name,
        provider_type="transport",
        integration_type="webhook",
        active=True,
        services={
            "service_types": ["intercity_transfer"]
        },
        config={
            "base_url": "https://provider.invalid/api",
        },
        profile={},
        policies={},
    )
    db.add(row)
    db.flush()
    return row


def add_coverage(
    db,
    provider,
    *,
    key="rome-milan",
    origin="it:rome",
    destination="it:milan",
    valid_until=None,
):
    row = ProviderCoverage(
        provider_id=provider.id,
        coverage_key=key,
        service_type="intercity_transfer",
        scope_kind="route",
        area_code=None,
        origin_entity_key=origin,
        destination_entity_key=destination,
        directional=True,
        integration_mode="live_api",
        capabilities={
            "discover": True,
            "quote": False,
            "book": False,
        },
        evidence_source="provider-contract",
        evidence_ref="contract:test",
        evidence={"fixture": True},
        verification_type="provider_contract",
        verified_at=NOW - timedelta(days=1),
        valid_from=None,
        valid_until=(
            valid_until
            if valid_until is not None
            else NOW + timedelta(days=30)
        ),
        active=True,
    )
    db.add(row)
    db.flush()
    return row


def add_plan(
    db,
    *,
    origin="Rome, Italy",
    destination="Milan, Italy",
    origin_key="it:rome",
    destination_key="it:milan",
    timezone_name="Europe/Rome",
):
    trip = Trip(
        title="Phase 2.2.C mobility test"
    )
    db.add(trip)
    db.flush()

    plan = TripItem(
        trip_id=trip.id,
        kind="plan",
        identity=(
            "plan|phase22c|"
            + origin
            + "|"
            + destination
        ),
        provider=None,
        booking_reference=None,
        status="stated",
        details={
            "origin_text": origin,
            "place": destination,
            "party_size": 2,
            "route_plan": {
                "origin": {
                    "name": origin,
                    "entity_key": origin_key,
                },
                "destination": {
                    "name": destination,
                    "entity_key": destination_key,
                },
                "arrive_by_local": "2026-11-20T18:00",
                "leave_by_local": "2026-11-20T15:00",
                "timezone": timezone_name,
            },
        },
        times={},
        current_revision=1,
        revisions=[],
    )
    db.add(plan)
    db.flush()

    return trip, plan


def private_transfer(result):
    return next(
        mode
        for mode in result["modes"]
        if mode["mode"] == "private_transfer"
    )


def test_verified_foreign_provider_enters_comparison_without_quote():
    engine, factory = make_db()

    try:
        with factory() as db:
            provider = add_provider(db)
            coverage = add_coverage(
                db,
                provider,
            )
            trip, plan = add_plan(db)

            result = marketplace.compare(
                db,
                plan,
                now=NOW,
            )

            transfer = private_transfer(result)

            assert transfer["status"] == "available"
            assert len(transfer["providers"]) == 1

            shown = transfer["providers"][0]
            assert shown["id"] == provider.id
            assert shown["sandbox"] is False
            assert (
                shown["coverage"]["coverage_key"]
                == coverage.coverage_key
            )
            assert (
                shown["coverage"]["integration_mode"]
                == "live_api"
            )

            assert count(db, Quote) == 0
            assert count(db, TransactionContext) == 0
            assert count(db, ExternalTransaction) == 0

            # Phase 2.2.C deliberately keeps real-provider quote
            # execution disabled. Visibility != quote authority.
            with pytest.raises(
                ValueError,
                match="no_transfer_provider_for_region",
            ):
                marketplace.create_transfer_quote(
                    db,
                    trip.id,
                    plan,
                    now=NOW,
                )

            assert count(db, Quote) == 0
            assert count(db, ExternalTransaction) == 0

    finally:
        engine.dispose()


def test_missing_or_stale_coverage_keeps_foreign_route_unavailable():
    engine, factory = make_db()

    try:
        with factory() as db:
            provider = add_provider(db)
            _, plan = add_plan(db)

            no_coverage = marketplace.compare(
                db,
                plan,
                now=NOW,
            )

            assert (
                private_transfer(no_coverage)["status"]
                == "unavailable"
            )

            add_coverage(
                db,
                provider,
                valid_until=NOW - timedelta(seconds=1),
            )

            stale = marketplace.compare(
                db,
                plan,
                now=NOW,
            )

            assert (
                private_transfer(stale)["status"]
                == "unavailable"
            )
            assert (
                private_transfer(stale)["providers"]
                == []
            )

    finally:
        engine.dispose()


def test_forged_route_entity_keys_fail_closed():
    engine, factory = make_db()

    try:
        with factory() as db:
            provider = add_provider(db)
            add_coverage(db, provider)

            _, plan = add_plan(
                db,
                origin_key="it:milan",
                destination_key="it:rome",
            )

            result = marketplace.compare(
                db,
                plan,
                now=NOW,
            )

            transfer = private_transfer(result)

            assert transfer["status"] == "unavailable"
            assert transfer["providers"] == []
            assert (
                transfer["reason"]
                == "route_identity_unverified"
            )

    finally:
        engine.dispose()


def test_verified_real_provider_and_legacy_sandbox_coexist():
    engine, factory = make_db()

    try:
        with factory() as db:
            provider = add_provider(
                db,
                slug="verified-me-transfer",
                name="Verified ME Transfer",
            )
            add_coverage(
                db,
                provider,
                key="podgorica-kotor",
                origin="me:podgorica",
                destination="me:kotor",
            )

            trip, plan = add_plan(
                db,
                origin="Podgorica",
                destination="Kotor",
                origin_key="me:podgorica",
                destination_key="me:kotor",
                timezone_name="Europe/Podgorica",
            )

            result = marketplace.compare(
                db,
                plan,
                now=NOW,
            )

            transfer = private_transfer(result)

            slugs = [
                item["slug"]
                for item in transfer["providers"]
            ]

            assert slugs[0] == provider.slug
            assert "atlas-sandbox-transfer" in slugs

            verified = transfer["providers"][0]
            assert verified["sandbox"] is False
            assert verified["coverage"] is not None

            sandbox = next(
                item
                for item in transfer["providers"]
                if item["slug"]
                == "atlas-sandbox-transfer"
            )

            assert sandbox["sandbox"] is True
            assert sandbox["coverage"] is None

            # Existing quote path is intentionally still sandbox-only.
            quote, provider_name = (
                marketplace.create_transfer_quote(
                    db,
                    trip.id,
                    plan,
                    now=NOW,
                )
            )

            assert (
                provider_name
                == "Atlas Sandbox Transfer"
            )
            assert quote.amount == 65
            assert count(db, Quote) == 1
            assert count(db, ExternalTransaction) == 0

    finally:
        engine.dispose()
