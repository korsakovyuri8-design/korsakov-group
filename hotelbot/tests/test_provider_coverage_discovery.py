from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.db.models import (
    CoverageArea,
    ExternalProvider,
    ExternalTransaction,
    ProviderCoverage,
    Quote,
)
from app.db.session import create_schema, make_engine, make_session_factory
from app.marketplace import discovery
from app.marketplace.provider_coverage import CoverageQuery


NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
START = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)


def make_db():
    engine = make_engine("sqlite://")
    create_schema(engine)
    return engine, make_session_factory(engine)()


def provider(
    db,
    *,
    slug="real-transfer",
    region="legacy-de",
    service_type="intercity_transfer",
    integration_type="webhook",
    synthetic=False,
):
    row = ExternalProvider(
        property_id=None,
        region=region,
        slug=slug,
        name=slug,
        provider_type="transport",
        integration_type=integration_type,
        active=True,
        services={"service_types": [service_type]},
        config={},
        profile={"synthetic": synthetic},
        policies={},
    )
    db.add(row)
    db.flush()
    return row


def route_coverage(
    db,
    provider_row,
    *,
    key="berlin-munich",
    service_type="intercity_transfer",
    origin="gn:2950159",
    destination="gn:2867714",
    mode="live_api",
    capabilities=None,
    verified_at=None,
    valid_until=None,
    verification_type="provider_contract",
):
    row = ProviderCoverage(
        provider_id=provider_row.id,
        coverage_key=key,
        service_type=service_type,
        scope_kind="route",
        area_code=None,
        origin_entity_key=origin,
        destination_entity_key=destination,
        directional=True,
        integration_mode=mode,
        capabilities=(
            capabilities
            if capabilities is not None
            else {"discover": True}
        ),
        evidence_source="provider-contract",
        evidence_ref="contract:fixture",
        evidence={"fixture": True},
        verification_type=verification_type,
        verified_at=verified_at or NOW - timedelta(days=1),
        valid_from=None,
        valid_until=valid_until or NOW + timedelta(days=30),
        active=True,
    )
    db.add(row)
    db.flush()
    return row


def route_query(
    *,
    capability="discover",
    allow_sandbox=False,
    origin="gn:2950159",
    destination="gn:2867714",
):
    return CoverageQuery(
        capability=capability,
        origin_entity_key=origin,
        destination_entity_key=destination,
        allow_sandbox=allow_sandbox,
    )


def discover_route(
    db,
    *,
    region=None,
    coverage=None,
    service_type="intercity_transfer",
):
    return discovery.discover(
        db,
        service_type=service_type,
        details={
            "pickup": "Berlin, Germany",
            "destination": "Munich, Germany",
            "pickup_time": START.isoformat(),
            "party_size": 2,
        },
        region=region,
        property_id=None,
        now=NOW,
        tz="Europe/Berlin",
        start=START,
        coverage=coverage,
    )


def test_legacy_region_discovery_is_unchanged_when_no_coverage_query():
    engine, db = make_db()
    try:
        p = provider(db)
        result = discover_route(db, region="legacy-de")
        assert [c.provider.id for c in result.candidates] == [p.id]
        assert result.candidates[0].coverage is None
    finally:
        db.close()
        engine.dispose()


def test_coverage_mode_fails_closed_even_when_legacy_region_matches():
    engine, db = make_db()
    try:
        provider(db)
        result = discover_route(
            db,
            region="legacy-de",
            coverage=route_query(),
        )
        assert result.candidates == []
        assert result.pending == []
        assert result.reason == "no_verified_provider_coverage"
    finally:
        db.close()
        engine.dispose()


def test_exact_verified_route_allows_provider_without_legacy_region():
    engine, db = make_db()
    try:
        p = provider(db, region=None)
        coverage = route_coverage(db, p)

        result = discover_route(
            db,
            coverage=route_query(),
        )

        assert len(result.candidates) == 1
        candidate = result.candidates[0]
        assert candidate.provider.id == p.id
        assert candidate.coverage is not None
        assert candidate.coverage.id == coverage.id
    finally:
        db.close()
        engine.dispose()


def test_coverage_is_an_intersection_not_an_or_bypass():
    engine, db = make_db()
    try:
        p = provider(db, region="legacy-de")
        route_coverage(db, p)

        result = discover_route(
            db,
            region="different-region",
            coverage=route_query(),
        )

        assert result.candidates == []
        assert result.reason == "not_offered"
    finally:
        db.close()
        engine.dispose()


def test_wrong_or_stale_route_never_enters_discovery():
    engine, db = make_db()
    try:
        wrong = provider(db, slug="wrong", region=None)
        route_coverage(
            db,
            wrong,
            destination="gn:999999",
        )

        stale = provider(db, slug="stale", region=None)
        route_coverage(
            db,
            stale,
            key="stale",
            valid_until=NOW - timedelta(seconds=1),
            verified_at=NOW - timedelta(days=2),
        )

        result = discover_route(
            db,
            coverage=route_query(),
        )

        assert result.candidates == []
        assert result.reason == "no_verified_provider_coverage"
    finally:
        db.close()
        engine.dispose()


def test_affiliate_can_be_discovered_but_cannot_gain_quote_authority():
    engine, db = make_db()
    try:
        p = provider(db, region=None)
        route_coverage(
            db,
            p,
            mode="affiliate",
            capabilities={
                "discover": True,
                "quote": True,
                "book": True,
            },
        )

        visible = discover_route(
            db,
            coverage=route_query(capability="discover"),
        )
        assert [c.provider.id for c in visible.candidates] == [p.id]

        quote = discover_route(
            db,
            coverage=route_query(capability="quote"),
        )
        assert quote.candidates == []
        assert quote.reason == "no_verified_provider_coverage"
    finally:
        db.close()
        engine.dispose()


def test_sandbox_is_hidden_unless_explicitly_allowed():
    engine, db = make_db()
    try:
        p = provider(
            db,
            slug="synthetic",
            region=None,
            integration_type="mock",
            synthetic=True,
        )
        route_coverage(
            db,
            p,
            mode="sandbox",
            verification_type="synthetic_fixture",
        )

        hidden = discover_route(
            db,
            coverage=route_query(allow_sandbox=False),
        )
        assert hidden.candidates == []

        shown = discover_route(
            db,
            coverage=route_query(allow_sandbox=True),
        )
        assert [c.provider.id for c in shown.candidates] == [p.id]
    finally:
        db.close()
        engine.dispose()


def test_discovery_only_reads_coverage_and_creates_no_transactions():
    engine, db = make_db()
    try:
        p = provider(db, region=None)
        route_coverage(db, p)

        result = discover_route(
            db,
            coverage=route_query(),
        )

        assert result.candidates
        assert db.scalar(select(func.count()).select_from(Quote)) == 0
        assert db.scalar(
            select(func.count()).select_from(ExternalTransaction)
        ) == 0
    finally:
        db.close()
        engine.dispose()


def test_area_coverage_can_scope_non_route_service():
    engine, db = make_db()
    try:
        p = provider(
            db,
            slug="rental",
            region=None,
            service_type="rental",
        )

        area = CoverageArea(
            code="de:berlin",
            kind="locality",
            name="Berlin",
            country_code="DE",
            timezone="Europe/Berlin",
            attributes={},
        )
        db.add(area)
        db.flush()

        coverage = ProviderCoverage(
            provider_id=p.id,
            coverage_key="berlin-rental",
            service_type="rental",
            scope_kind="area",
            area_code=area.code,
            origin_entity_key=None,
            destination_entity_key=None,
            directional=True,
            integration_mode="live_api",
            capabilities={"discover": True},
            evidence_source="provider-contract",
            evidence_ref="contract:rental",
            evidence={"fixture": True},
            verification_type="provider_contract",
            verified_at=NOW - timedelta(days=1),
            valid_from=None,
            valid_until=NOW + timedelta(days=30),
            active=True,
        )
        db.add(coverage)
        db.flush()

        result = discovery.discover(
            db,
            service_type="rental",
            details={
                "category": "car",
                "quantity": 1,
                "start_time": START.isoformat(),
            },
            region=None,
            property_id=None,
            now=NOW,
            tz="Europe/Berlin",
            start=START,
            coverage=CoverageQuery(
                capability="discover",
                area_codes=frozenset({"de:berlin"}),
            ),
        )

        assert len(result.candidates) == 1
        assert result.candidates[0].provider.id == p.id
        assert result.candidates[0].coverage.id == coverage.id
    finally:
        db.close()
        engine.dispose()
