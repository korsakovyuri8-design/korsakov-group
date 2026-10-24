"""GeoNames integration contracts, using isolated synthetic catalogs only.

These tests never use a production snapshot, call OSRM, or contact a provider.
The city names in collision fixtures deliberately describe synthetic places.
"""

from __future__ import annotations

import hashlib
import importlib.util
import itertools
import sqlite3
import string
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db.models import (
    ActionStatus,
    ExternalProvider,
    ExternalTransaction,
    Quote,
    QuoteStatus,
    Trip,
    TripItem,
)
from app.db.session import create_schema, make_engine, make_session_factory
from app.journey import geonames_catalog, place_resolver, route_planner
from app.mobility import marketplace
from app.mobility.sandbox import ensure_transfer_provider


NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
NORMALIZATION_POLICY = "latin-safe-country-qualified-v1"
BASE_METADATA = {
    "source": "GeoNames cities5000",
    "license": "CC BY 4.0 - attribution required",
    "source_sha256": "a" * 64,
    "countries_sha256": "b" * 64,
    "built_at": "2026-10-24T11:45:07+00:00",
    "coordinate_precision": "city_centroid",
    "normalization_policy": NORMALIZATION_POLICY,
}

SCHEMA = """
CREATE TABLE cities (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    country TEXT NOT NULL,
    country_code TEXT NOT NULL,
    timezone TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    population INTEGER NOT NULL
);
CREATE TABLE aliases (
    alias_key TEXT NOT NULL,
    city_id INTEGER NOT NULL
);
CREATE UNIQUE INDEX aliases_exact ON aliases(alias_key, city_id);
CREATE INDEX aliases_lookup ON aliases(alias_key);
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def city(
    identifier=1001,
    name="Berlin",
    country="Germany",
    country_code="DE",
    zone="Europe/Berlin",
    latitude=52.52,
    longitude=13.405,
    population=100000,
):
    return (
        identifier,
        name,
        country,
        country_code,
        zone,
        latitude,
        longitude,
        population,
    )


@contextmanager
def catalog_connection(path):
    db = sqlite3.connect(path)
    try:
        with db:
            yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def isolate_active_catalog(monkeypatch, tmp_path):
    # Running these tests on server1 must not read its active production data.
    monkeypatch.setattr(
        geonames_catalog, "ACTIVE_CATALOG", tmp_path / "not-activated.sqlite"
    )


@pytest.fixture
def catalog_factory(tmp_path):
    sequence = itertools.count()

    def create(*, cities=None, aliases=None, metadata=None, omit_metadata=()):
        rows = list(cities if cities is not None else [city()])
        alias_rows = list(
            aliases if aliases is not None else [("berlin germany", 1001)]
        )
        values = {
            **BASE_METADATA,
            "cities": str(len(rows)),
            "aliases": str(len(alias_rows)),
            **(metadata or {}),
        }
        for key in omit_metadata:
            values.pop(key, None)

        path = tmp_path / f"catalog-{next(sequence)}.sqlite"
        with catalog_connection(path) as db:
            db.executescript(SCHEMA)
            db.executemany("INSERT INTO cities VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
            db.executemany("INSERT INTO aliases VALUES (?, ?)", alias_rows)
            db.executemany("INSERT INTO metadata VALUES (?, ?)", values.items())
        return path

    return create


@pytest.fixture
def db_session():
    engine = make_engine("sqlite://")
    create_schema(engine)
    try:
        with make_session_factory(engine)() as session:
            yield session
    finally:
        engine.dispose()


def activate(monkeypatch, path):
    monkeypatch.setattr(geonames_catalog, "ACTIVE_CATALOG", path)


def route_for(origin, destination):
    return route_planner.build_route_plan(
        origin=origin,
        destination=destination,
        arrival_by_local="2026-11-20T18:00",
        estimate={"distance_m": 100000, "duration_seconds": 5400},
        generated_at=NOW,
    )


def add_plan(session, origin, destination, *, origin_text=None, destination_text=None):
    trip = Trip(title="Synthetic GeoNames integration test")
    session.add(trip)
    session.flush()
    plan = TripItem(
        trip_id=trip.id,
        kind="plan",
        identity="plan|geonames-test",
        provider=None,
        booking_reference=None,
        status="stated",
        details={
            "origin_text": origin_text or origin["canonical_name"],
            "place": destination_text or destination["canonical_name"],
            "party_size": 2,
            "route_plan": route_for(origin, destination),
        },
        times={},
        current_revision=1,
        revisions=[],
    )
    session.add(plan)
    session.flush()
    return trip, plan


def assert_no_supply(session, trip, plan):
    provider_count = session.scalar(select(func.count()).select_from(ExternalProvider))
    request = marketplace.request_from_plan_details(plan.details)
    assert marketplace._region_for_request(request) is None

    result = marketplace.compare(session, plan, now=NOW)
    assert all(mode["status"] == "unavailable" for mode in result["modes"])
    assert all(not mode["providers"] for mode in result["modes"])
    with pytest.raises(ValueError, match="no_transfer_provider_for_region"):
        marketplace.create_transfer_quote(session, trip.id, plan, now=NOW)

    assert session.scalar(select(func.count()).select_from(ExternalProvider)) == provider_count
    assert session.scalar(select(func.count()).select_from(Quote)) == 0
    assert session.scalar(select(func.count()).select_from(ExternalTransaction)) == 0


def test_qualified_city_has_stable_identity_and_complete_source_evidence(catalog_factory):
    path = catalog_factory()
    result = geonames_catalog.lookup("Berlin, Germany", catalog_path=path)
    assert result is not None
    assert result["entity_key"] == "gn:1001"
    assert result["canonical_name"] == "Berlin, Germany"
    assert result["country"] == "Germany"
    assert result["timezone"] == "Europe/Berlin"
    assert result["coordinates"] == {"lat": 52.52, "lon": 13.405}
    assert result["coordinate_precision"] == "city_centroid"
    assert result["resolver"] == "atlas_geonames_snapshot"
    assert result["registry_version"] == "geonames-cities5000:aaaaaaaaaaaa:bbbbbbbbbbbb"
    assert result["source_attribution"] == "GeoNames"
    assert result["source_license"] == "CC BY 4.0"
    assert result["source_license_url"].startswith("https://creativecommons.org/licenses/by/4.0")
    assert "geonames.org" in result["source_url"]
    assert result["source_sha256"] == BASE_METADATA["source_sha256"]
    assert result["source_countries_sha256"] == BASE_METADATA["countries_sha256"]
    assert result["source_built_at"] == BASE_METADATA["built_at"]
    assert result["source_modified"] is True


@pytest.mark.parametrize(
    "text,expected",
    [
        ("  BERLIN, Germany  ", "berlin germany"),
        ("München, Germany", "munchen germany"),
        ("İstanbul, Turkey", "istanbul turkey"),
        ("Straße, Germany", "strasse germany"),
        ("One&Only Portonovi", "one and only portonovi"),
    ],
)
def test_normalization_preserves_supported_latin_names(text, expected):
    assert geonames_catalog.normalize_alias(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "東京, Japan",
        "Berlin東京, Germany",
        "Rome東京, Italy",
        "Москва, Russia",
        "Łódź, Poland",
    ],
)
def test_losing_significant_letters_never_creates_a_match(text, catalog_factory, monkeypatch):
    path = catalog_factory(
        cities=[city(), city(1002, "Tokyo", "Japan", "JP", "Asia/Tokyo")],
        aliases=[("berlin germany", 1001), ("japan", 1002)],
    )
    activate(monkeypatch, path)
    assert geonames_catalog.normalize_alias(text) is None
    assert geonames_catalog.lookup(text, catalog_path=path) is None
    assert place_resolver.resolve_place(text) is None


def test_country_only_alias_is_rejected_even_when_unique(catalog_factory):
    path = catalog_factory(
        cities=[city(1002, "Tokyo", "Japan", "JP", "Asia/Tokyo")],
        aliases=[("japan", 1002)],
    )
    assert geonames_catalog.lookup("Japan", catalog_path=path) is None


@pytest.mark.parametrize("text", ["Berlin", "Unknown City, Germany", "Germany"])
def test_unqualified_or_unknown_city_is_unresolved(text, catalog_factory):
    assert geonames_catalog.lookup(text, catalog_path=catalog_factory()) is None


def test_distinct_aliases_for_one_city_resolve_to_the_same_identity(catalog_factory):
    path = catalog_factory(
        cities=[city(1002, "Munich")],
        aliases=[("munich germany", 1002), ("munchen germany", 1002)],
    )
    english = geonames_catalog.lookup("Munich, Germany", catalog_path=path)
    german = geonames_catalog.lookup("München, Germany", catalog_path=path)
    assert english is not None and german is not None
    assert english["entity_key"] == german["entity_key"] == "gn:1002"


def test_duplicate_rows_for_the_same_id_do_not_create_false_ambiguity(catalog_factory):
    path = catalog_factory()
    with catalog_connection(path) as db:
        db.execute("DROP INDEX aliases_exact")
        db.execute("INSERT INTO aliases VALUES ('berlin germany', 1001)")
    result = geonames_catalog.lookup("Berlin, Germany", catalog_path=path)
    assert result is not None
    assert result["entity_key"] == "gn:1001"


@pytest.mark.parametrize("second_city_exists", [False, True])
def test_multiple_ids_are_ambiguous_even_if_one_is_orphaned(
    second_city_exists, catalog_factory
):
    rows = [city(1001, "Springfield", "United States", "US", "America/New_York")]
    if second_city_exists:
        rows.append(city(1002, "Springfield", "United States", "US", "America/Chicago"))
    path = catalog_factory(
        cities=rows,
        aliases=[("springfield united states", 1001), ("springfield united states", 1002)],
    )
    assert geonames_catalog.lookup("Springfield, United States", catalog_path=path) is None


def test_single_orphan_alias_is_unresolved(catalog_factory):
    path = catalog_factory(cities=[], aliases=[("berlin germany", 1001)])
    assert geonames_catalog.lookup("Berlin, Germany", catalog_path=path) is None


def test_alias_country_must_match_city_country(catalog_factory):
    path = catalog_factory(cities=[city(country="France", country_code="FR")])
    assert geonames_catalog.lookup("Berlin, Germany", catalog_path=path) is None


@pytest.mark.parametrize("missing_key", list(BASE_METADATA))
def test_required_metadata_cannot_be_silently_defaulted(missing_key, catalog_factory):
    path = catalog_factory(omit_metadata=[missing_key])
    assert geonames_catalog.lookup("Berlin, Germany", catalog_path=path) is None


@pytest.mark.parametrize(
    "key,value",
    [
        ("source", "Unverified city source"),
        ("license", "Unknown"),
        ("source_sha256", "a" * 63),
        ("source_sha256", "z" * 64),
        ("countries_sha256", "b" * 63),
        ("countries_sha256", "z" * 64),
        ("built_at", "2026-10-24T11:45:07"),
        ("built_at", "not-a-timestamp"),
        ("coordinate_precision", "street_address"),
        ("normalization_policy", "legacy-lossy-normalization"),
    ],
)
def test_invalid_metadata_fails_closed(key, value, catalog_factory):
    path = catalog_factory(metadata={key: value})
    assert geonames_catalog.lookup("Berlin, Germany", catalog_path=path) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"identifier": 0},
        {"identifier": -1},
        {"name": ""},
        {"name": "   "},
        {"country": ""},
        {"country_code": "de"},
        {"country_code": "D1"},
        {"country_code": "DEU"},
        {"zone": "Not/A_Timezone"},
        {"zone": ""},
        {"latitude": 90.01},
        {"latitude": -90.01},
        {"longitude": 180.01},
        {"longitude": -180.01},
        {"latitude": float("inf")},
        {"longitude": float("-inf")},
        {"latitude": "NaN"},
    ],
)
def test_invalid_city_fields_are_unresolved(changes, catalog_factory):
    record = city(**changes)
    path = catalog_factory(cities=[record], aliases=[("berlin germany", record[0])])
    assert geonames_catalog.lookup("Berlin, Germany", catalog_path=path) is None


def test_missing_snapshot_is_not_created(tmp_path):
    path = tmp_path / "missing.sqlite"
    assert geonames_catalog.lookup("Berlin, Germany", catalog_path=path) is None
    assert not path.exists()


def test_corrupt_snapshot_is_not_modified(tmp_path):
    path = tmp_path / "corrupt.sqlite"
    original = b"not a SQLite database\x00\xff"
    path.write_bytes(original)
    assert geonames_catalog.lookup("Berlin, Germany", catalog_path=path) is None
    assert path.read_bytes() == original


@pytest.mark.parametrize("table", ["cities", "aliases", "metadata"])
def test_missing_schema_objects_fail_closed(table, catalog_factory):
    path = catalog_factory()
    with catalog_connection(path) as db:
        db.execute(f"DROP TABLE {table}")
    assert geonames_catalog.lookup("Berlin, Germany", catalog_path=path) is None


def test_database_open_failure_is_unresolved(catalog_factory, monkeypatch):
    path = catalog_factory()

    def denied(*args, **kwargs):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(geonames_catalog.sqlite3, "connect", denied)
    assert geonames_catalog.lookup("Berlin, Germany", catalog_path=path) is None


@pytest.mark.parametrize("operation", ["is_file", "resolve"])
def test_filesystem_permission_errors_fail_closed(operation, catalog_factory, monkeypatch):
    path = catalog_factory()
    original = getattr(Path, operation)

    def denied(candidate, *args, **kwargs):
        if candidate == path:
            raise PermissionError("synthetic permission denial")
        return original(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, operation, denied)
    assert geonames_catalog.lookup("Berlin, Germany", catalog_path=path) is None


def test_broken_snapshot_symlink_is_unresolved(tmp_path):
    path = tmp_path / "current.sqlite"
    path.symlink_to(tmp_path / "does-not-exist.sqlite")
    assert geonames_catalog.lookup("Berlin, Germany", catalog_path=path) is None


def test_lookup_is_read_only_and_does_not_create_journals(catalog_factory):
    path = catalog_factory()
    before = hashlib.sha256(path.read_bytes()).digest()
    assert geonames_catalog.lookup("Berlin, Germany", catalog_path=path) is not None
    assert hashlib.sha256(path.read_bytes()).digest() == before
    assert not list(path.parent.glob(path.name + "-*"))


def test_new_catalog_activation_does_not_reuse_stale_lookup_results(
    catalog_factory, monkeypatch, tmp_path
):
    first = catalog_factory()
    second = catalog_factory(
        cities=[city(identifier=1002)],
        aliases=[("berlin germany", 1002)],
        metadata={"source_sha256": "c" * 64},
    )
    current = tmp_path / "current.sqlite"
    current.symlink_to(first)
    activate(monkeypatch, current)
    assert geonames_catalog.lookup("Berlin, Germany")["entity_key"] == "gn:1001"

    replacement = tmp_path / "next.sqlite"
    replacement.symlink_to(second)
    replacement.replace(current)
    result = geonames_catalog.lookup("Berlin, Germany")
    assert result["entity_key"] == "gn:1002"
    assert result["source_sha256"] == "c" * 64


@pytest.mark.parametrize(
    "name,country,code,zone,expected_key",
    [
        ("Rome", "Italy", "IT", "Europe/Rome", "it:rome"),
        ("Tokyo", "Japan", "JP", "Asia/Tokyo", "jp:tokyo"),
    ],
)
def test_curated_global_city_wins_over_geonames(
    name, country, code, zone, expected_key, catalog_factory, monkeypatch
):
    path = catalog_factory(
        cities=[city(1001, name, country, code, zone)],
        aliases=[(f"{name.lower()} {country.lower()}", 1001)],
    )
    activate(monkeypatch, path)
    result = place_resolver.resolve_place(f"{name}, {country}")
    assert result["entity_key"] == expected_key
    assert result["resolver"] == "atlas_global_seed"
    assert "source_attribution" not in result


def test_curated_montenegro_payload_is_unchanged(catalog_factory, monkeypatch):
    path = catalog_factory(
        cities=[city(1001, "Podgorica", "Montenegro", "ME", "Europe/Podgorica")],
        aliases=[("podgorica montenegro", 1001)],
    )
    activate(monkeypatch, path)
    result = place_resolver.resolve_place("Podgorica, Montenegro")
    assert result == {
        "status": "resolved",
        "entity_key": "me:podgorica",
        "canonical_name": "Podgorica",
        "kind": "city",
        "address": "Podgorica, Montenegro",
        "locality": "Podgorica",
        "municipality": "Podgorica",
        "country": "Montenegro",
        "timezone": "Europe/Podgorica",
        "coordinates": {"lat": 42.44123775, "lon": 19.26308552},
        "resolver": "atlas_montenegro_seed",
        "registry_version": place_resolver.REGISTRY_VERSION,
        "match": "exact_alias",
    }


def test_curated_ambiguity_never_falls_through_to_catalog(catalog_factory, monkeypatch):
    activate(monkeypatch, catalog_factory())
    monkeypatch.setitem(
        place_resolver.INDEX, "berlin germany", {"seed:first": {}, "seed:second": {}}
    )

    def unexpected_fallback(*args, **kwargs):
        pytest.fail("Ambiguous curated matches must not consult a lower-priority source")

    monkeypatch.setattr(geonames_catalog, "lookup", unexpected_fallback)
    assert place_resolver.resolve_place("Berlin, Germany") is None


def test_route_preserves_geographic_provenance(catalog_factory, monkeypatch):
    path = catalog_factory(
        cities=[city(), city(1002, "Munich")],
        aliases=[("berlin germany", 1001), ("munich germany", 1002)],
    )
    activate(monkeypatch, path)
    origin = place_resolver.resolve_place("Berlin, Germany")
    destination = place_resolver.resolve_place("Munich, Germany")
    route = route_for(origin, destination)
    fields = (
        "coordinate_precision",
        "resolver",
        "registry_version",
        "source_attribution",
        "source_url",
        "source_license",
        "source_license_url",
        "source_sha256",
        "source_countries_sha256",
        "source_built_at",
        "source_modified",
    )
    for endpoint, resolved in (("origin", origin), ("destination", destination)):
        assert route[endpoint]["entity_key"] == resolved["entity_key"]
        for field in fields:
            assert route[endpoint][field] == resolved[field]
    assert route["timezone"] == "Europe/Berlin"
    assert route["status"] == "estimated"
    assert route["traffic_live"] is False
    assert route["provider_guarantee"] is False


@pytest.mark.parametrize("existing_sandbox", [False, True])
@pytest.mark.parametrize(
    "name,country,code,zone",
    [
        ("Kotor", "Germany", "DE", "Europe/Berlin"),
        ("Fixtureville", "Montenegro", "ME", "Europe/Podgorica"),
        ("Kotor", "Montenegro", "ME", "Europe/Podgorica"),
    ],
    ids=["foreign-name-collision", "new-montenegro-city", "curated-name-id-collision"],
)
def test_catalog_geography_never_expands_sandbox_supply(
    name, country, code, zone, existing_sandbox, catalog_factory, monkeypatch, db_session
):
    path = catalog_factory(
        cities=[city(2001, name, country, code, zone)],
        aliases=[(f"{name.lower()} {country.lower()}", 2001)],
    )
    activate(monkeypatch, path)
    if existing_sandbox:
        ensure_transfer_provider(db_session)

    qualified = f"{name}, {country}"
    if name == "Kotor" and country == "Montenegro":
        # Simulate an already-persisted GeoNames route whose display name
        # collides with a curated seed's canonical name.
        origin = geonames_catalog.lookup(qualified)
    else:
        origin = place_resolver.resolve_place(qualified)
    destination = place_resolver.resolve_place("Podgorica")
    assert origin["entity_key"] == "gn:2001"
    trip, plan = add_plan(db_session, origin, destination, origin_text=qualified)
    request = marketplace.request_from_plan_details(plan.details)
    assert request.origin_entity_key == "gn:2001"
    assert request.destination_entity_key == "me:podgorica"
    assert_no_supply(db_session, trip, plan)


def test_changed_geographic_identity_cannot_confirm_an_old_quote(db_session):
    origin = place_resolver.resolve_place("Podgorica")
    destination = place_resolver.resolve_place("Kotor")
    trip, plan = add_plan(db_session, origin, destination)
    quote, _ = marketplace.create_transfer_quote(db_session, trip.id, plan, now=NOW)
    old_route = plan.details["route_plan"]
    plan.details = {
        **plan.details,
        "route_plan": {
            **old_route,
            "origin": {**old_route["origin"], "entity_key": "gn:9999"},
        },
    }

    assert marketplace.active_transfer_quote(db_session, trip.id, plan, now=NOW) is None
    with pytest.raises(ValueError, match="no_transfer_provider_for_region"):
        marketplace.confirm_transfer_quote(
            db_session, trip.id, plan, quote.id, consent=True, now=NOW
        )
    assert quote.status == QuoteStatus.OFFERED
    assert db_session.scalar(select(func.count()).select_from(ExternalTransaction)) == 0


def test_curated_sandbox_still_works_and_cancellation_survives_scope_changes(db_session):
    origin = place_resolver.resolve_place("Podgorica")
    destination = place_resolver.resolve_place("Kotor")
    trip, plan = add_plan(db_session, origin, destination)
    request = marketplace.request_from_plan_details(plan.details)
    assert marketplace._region_for_request(request) == "montenegro-beta"

    quote, _ = marketplace.create_transfer_quote(db_session, trip.id, plan, now=NOW)
    assert quote.amount == Decimal("65")
    txn = marketplace.confirm_transfer_quote(
        db_session, trip.id, plan, quote.id, consent=True, now=NOW
    )
    assert txn.action.status == ActionStatus.ACCEPTED

    old_route = plan.details["route_plan"]
    plan.details = {
        **plan.details,
        "route_plan": {
            **old_route,
            "origin": {**old_route["origin"], "entity_key": "gn:9999"},
        },
    }
    assert marketplace._region_for_request(
        marketplace.request_from_plan_details(plan.details)
    ) is None
    cancelled = marketplace.cancel_transfer(
        db_session, trip.id, plan, transaction_id=txn.id, now=NOW
    )
    assert cancelled.id == txn.id
    assert cancelled.action.status == ActionStatus.CANCELLED


def test_builder_emits_only_safe_qualified_aliases_and_verifiable_metadata(tmp_path):
    builder_path = Path(__file__).resolve().parents[1] / "tools" / "build_geonames_catalog.py"
    spec = importlib.util.spec_from_file_location("geonames_builder_test_fixture", builder_path)
    assert spec is not None and spec.loader is not None
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    codes = [
        "".join(pair)
        for pair in itertools.product(string.ascii_uppercase, repeat=2)
        if "".join(pair) not in {"DE", "JP"}
    ][:198]
    countries = {code: f"FixtureCountry{code}" for code in codes}
    countries.update({"DE": "Germany", "JP": "Japan"})
    country_file = tmp_path / "countryInfo.txt"
    country_file.write_text(
        "\n".join(f"{code}\tXXX\t000\tXX\t{name}" for code, name in countries.items()) + "\n",
        encoding="utf-8",
    )

    def source_row(identifier, name, ascii_name, aliases, code, zone):
        return "\t".join(
            [
                str(identifier), name, ascii_name, aliases,
                "52.52", "13.405", "P", "PPL", code,
                "", "", "", "", "", "100000", "", "0", zone, "2026-10-24",
            ]
        )

    source_zip = tmp_path / "cities5000.zip"
    with zipfile.ZipFile(source_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "cities5000.txt",
            source_row(1001, "Berlin", "Berlin", "Berliner,Берлин,Unsafe東京", "DE", "Europe/Berlin")
            + "\n"
            + source_row(1002, "東京", "Tokyo", "東京,Tokyo", "JP", "Asia/Tokyo")
            + "\n",
        )

    output = tmp_path / "rebuilt.sqlite"
    city_count, alias_count = builder.build(source_zip, country_file, output, min_cities=1)
    assert city_count == 2
    assert alias_count >= 3
    with catalog_connection(output) as db:
        metadata = dict(db.execute("SELECT key, value FROM metadata"))
        aliases = {row[0] for row in db.execute("SELECT alias_key FROM aliases")}

    assert metadata["normalization_policy"] == NORMALIZATION_POLICY
    assert metadata["source_sha256"] == hashlib.sha256(source_zip.read_bytes()).hexdigest()
    assert metadata["countries_sha256"] == hashlib.sha256(country_file.read_bytes()).hexdigest()
    assert {"berlin germany", "berliner germany", "tokyo japan"} <= aliases
    assert not {"germany", "japan", "unsafe germany"} & aliases
    assert geonames_catalog.lookup("Berlin, Germany", catalog_path=output)["entity_key"] == "gn:1001"
    assert geonames_catalog.lookup("Tokyo, Japan", catalog_path=output)["entity_key"] == "gn:1002"
    assert geonames_catalog.lookup("東京, Japan", catalog_path=output) is None
