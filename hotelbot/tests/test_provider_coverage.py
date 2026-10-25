from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.models import (
    CoverageArea,
    ExternalProvider,
    ProviderCoverage,
)
from app.db.session import (
    create_schema,
    head_revision,
    make_engine,
    make_session_factory,
)
from app.marketplace.provider_coverage import (
    find_verified_coverage,
)


NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    engine = make_engine("sqlite://")
    create_schema(engine)
    factory = make_session_factory(engine)
    session = factory()

    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def provider(
    db,
    *,
    slug="fixture-provider",
    active=True,
    services=None,
):
    row = ExternalProvider(
        property_id=None,
        region="legacy-region-must-not-grant-coverage",
        slug=slug,
        name="Fixture Provider",
        provider_type="transport",
        integration_type="webhook",
        active=active,
        services={
            "service_types": (
                services
                if services is not None
                else [
                    "intercity_transfer",
                    "rental",
                    "airport_transfer",
                ]
            )
        },
        config={},
        profile={},
        policies={},
    )
    db.add(row)
    db.flush()
    return row


def area(db, code="de:berlin"):
    row = CoverageArea(
        code=code,
        kind="locality",
        name=code,
        country_code="DE",
        timezone="Europe/Berlin",
        attributes={},
    )
    db.add(row)
    db.flush()
    return row


def coverage(
    db,
    provider_row,
    *,
    key="coverage-1",
    service_type="intercity_transfer",
    scope_kind="route",
    area_code=None,
    origin="gn:2950159",
    destination="gn:2867714",
    directional=True,
    integration_mode="live_api",
    capabilities=None,
    evidence_source="fixture-provider-contract",
    verification_type="provider_contract",
    verified_at=None,
    valid_from=None,
    valid_until=None,
    active=True,
):
    row = ProviderCoverage(
        provider_id=provider_row.id,
        coverage_key=key,
        service_type=service_type,
        scope_kind=scope_kind,
        area_code=area_code,
        origin_entity_key=origin,
        destination_entity_key=destination,
        directional=directional,
        integration_mode=integration_mode,
        capabilities=(
            capabilities
            if capabilities is not None
            else {"discover": True}
        ),
        evidence_source=evidence_source,
        evidence_ref="fixture:contract:1",
        evidence={"fixture": True},
        verification_type=verification_type,
        verified_at=verified_at or NOW - timedelta(days=1),
        valid_from=valid_from,
        valid_until=valid_until or NOW + timedelta(days=30),
        active=active,
    )
    db.add(row)
    db.flush()
    return row


def match(
    db,
    provider_row,
    *,
    service_type="intercity_transfer",
    capability="discover",
    area_codes=(),
    origin="gn:2950159",
    destination="gn:2867714",
    allow_sandbox=False,
):
    return find_verified_coverage(
        db,
        provider_id=provider_row.id,
        service_type=service_type,
        capability=capability,
        now=NOW,
        area_codes=area_codes,
        origin_entity_key=origin,
        destination_entity_key=destination,
        allow_sandbox=allow_sandbox,
    )


def test_migration_head_is_provider_coverage():
    assert head_revision() == "0012_provider_coverage"


def test_no_coverage_row_fails_closed_even_when_legacy_region_exists(db):
    p = provider(db)
    assert p.region == "legacy-region-must-not-grant-coverage"
    assert match(db, p) is None


def test_exact_route_coverage_matches_geonames_identity(db):
    p = provider(db)
    row = coverage(db, p)
    assert match(db, p).id == row.id


def test_route_coverage_is_directional_by_default(db):
    p = provider(db)
    coverage(db, p, directional=True)
    assert match(
        db,
        p,
        origin="gn:2867714",
        destination="gn:2950159",
    ) is None


def test_bidirectional_route_may_match_reverse_direction(db):
    p = provider(db)
    row = coverage(db, p, directional=False)
    found = match(
        db,
        p,
        origin="gn:2867714",
        destination="gn:2950159",
    )
    assert found is not None
    assert found.id == row.id


def test_route_coverage_supports_curated_stable_entity_keys(db):
    p = provider(db)
    row = coverage(
        db,
        p,
        origin="it:rome",
        destination="it:milan",
    )
    found = match(
        db,
        p,
        origin="it:rome",
        destination="it:milan",
    )
    assert found.id == row.id


def test_area_coverage_matches_non_route_service(db):
    p = provider(db)
    a = area(db)
    row = coverage(
        db,
        p,
        service_type="rental",
        scope_kind="area",
        area_code=a.code,
        origin=None,
        destination=None,
    )
    found = match(
        db,
        p,
        service_type="rental",
        area_codes={a.code},
        origin=None,
        destination=None,
    )
    assert found.id == row.id


def test_intercity_transfer_never_uses_area_only_coverage(db):
    p = provider(db)
    a = area(db)
    coverage(
        db,
        p,
        service_type="intercity_transfer",
        scope_kind="area",
        area_code=a.code,
        origin=None,
        destination=None,
    )
    assert match(
        db,
        p,
        service_type="intercity_transfer",
        area_codes={a.code},
    ) is None


def test_exact_route_outranks_area_when_both_are_valid(db):
    p = provider(db, services=["airport_transfer"])
    a = area(db)

    coverage(
        db,
        p,
        key="z-area",
        service_type="airport_transfer",
        scope_kind="area",
        area_code=a.code,
        origin=None,
        destination=None,
    )
    route = coverage(
        db,
        p,
        key="a-route",
        service_type="airport_transfer",
    )

    found = match(
        db,
        p,
        service_type="airport_transfer",
        area_codes={a.code},
    )
    assert found.id == route.id


@pytest.mark.parametrize(
    "change",
    [
        {"active": False},
        {"evidence_source": ""},
        {"verification_type": "unknown"},
        {"integration_mode": "unknown"},
        {"verified_at": NOW + timedelta(minutes=1)},
        {
            "verified_at": NOW - timedelta(days=2),
            "valid_until": NOW - timedelta(seconds=1),
        },
        {
            "valid_from": NOW + timedelta(days=1),
            "valid_until": NOW + timedelta(days=2),
        },
        {
            "verified_at": NOW + timedelta(days=2),
            "valid_until": NOW + timedelta(days=1),
        },
    ],
)
def test_invalid_or_stale_evidence_fails_closed(db, change):
    p = provider(db)
    coverage(db, p, **change)
    assert match(db, p) is None


def test_missing_capability_is_never_inferred(db):
    p = provider(db)
    coverage(
        db,
        p,
        capabilities={"discover": True},
    )
    assert match(db, p, capability="quote") is None


def test_affiliate_and_lead_do_not_gain_transaction_capabilities(db):
    for index, mode in enumerate(("affiliate", "lead")):
        p = provider(db, slug=f"provider-{mode}")
        coverage(
            db,
            p,
            key=f"coverage-{index}",
            integration_mode=mode,
            capabilities={
                "discover": True,
                "quote": True,
                "book": True,
            },
        )
        assert match(db, p, capability="discover") is not None
        assert match(db, p, capability="quote") is None
        assert match(db, p, capability="book") is None


def test_sandbox_requires_synthetic_evidence_and_explicit_opt_in(db):
    p = provider(db)
    coverage(
        db,
        p,
        integration_mode="sandbox",
        verification_type="synthetic_fixture",
        capabilities={"discover": True},
    )
    assert match(db, p, allow_sandbox=False) is None
    assert match(db, p, allow_sandbox=True) is not None


def test_synthetic_evidence_cannot_authorize_non_sandbox_coverage(db):
    p = provider(db)
    coverage(
        db,
        p,
        integration_mode="live_api",
        verification_type="synthetic_fixture",
    )
    assert match(db, p) is None


def test_sandbox_mode_rejects_real_evidence_shape(db):
    p = provider(db)
    coverage(
        db,
        p,
        integration_mode="sandbox",
        verification_type="provider_contract",
    )
    assert match(db, p, allow_sandbox=True) is None


def test_inactive_provider_and_undeclared_service_fail_closed(db):
    inactive = provider(db, slug="inactive", active=False)
    coverage(db, inactive)
    assert match(db, inactive) is None

    p = provider(
        db,
        slug="wrong-service",
        services=["rental"],
    )
    coverage(db, p, service_type="intercity_transfer")
    assert match(db, p) is None


def test_malformed_scope_shapes_fail_closed(db):
    p = provider(db)

    coverage(
        db,
        p,
        key="bad-route",
        scope_kind="route",
        area_code="de:berlin",
    )
    coverage(
        db,
        p,
        key="bad-area",
        service_type="rental",
        scope_kind="area",
        area_code=None,
        origin=None,
        destination=None,
    )

    assert match(db, p) is None
    assert match(
        db,
        p,
        service_type="rental",
        area_codes={"de:berlin"},
        origin=None,
        destination=None,
    ) is None


def test_rows_are_queryable_for_audit_without_changing_discovery(db):
    p = provider(db)
    row = coverage(db, p)

    stored = db.scalar(
        select(ProviderCoverage).where(
            ProviderCoverage.id == row.id
        )
    )

    assert stored is not None
    assert stored.evidence_source == "fixture-provider-contract"
    assert stored.evidence == {"fixture": True}
    assert stored.valid_until > stored.verified_at
