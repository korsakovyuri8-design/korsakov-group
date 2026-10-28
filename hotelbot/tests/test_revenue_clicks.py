from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.atlas_web.app import create_app
from app.atlas_web.revenue import (
    MAX_BODY_BYTES, RATE_LIMIT, ROUTE_SLUGS, UTM_VALUES, UTM_CONTENT_PATTERN,
    RevenueAccessLogFilter,
)
from app.atlas_web.sessions import SessionStore
from app.db.models import Base, RevenueClick, Trip
from app.db.session import make_engine
from tests.conftest import TEST_DATABASE_URL

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
PAYLOAD = {"provider": "kiwitaxi", "placement": "route_landing", "route_slug": "podgorica-kotor",
           "utm_source": "instagram", "utm_medium": "organic", "utm_campaign": "montenegro_launch",
           "utm_content": "reel_01", "utm_term": "airport_transfer"}


@pytest.fixture
def revenue(tmp_path):
    url = TEST_DATABASE_URL
    if url.startswith("sqlite"):
        url = f"sqlite:///{tmp_path}/revenue.db"
    else:
        # Explicit test URL only. Never reads HOTELBOT_DATABASE_URL.
        engine = make_engine(url)
        with engine.begin() as connection:
            meta = sa.MetaData()
            meta.reflect(connection)
            meta.drop_all(connection)
        engine.dispose()
    store = SessionStore(database_url=url)
    clock = [0.0]
    with TestClient(create_app(store=store, clock=lambda: clock[0], utcnow=lambda: NOW)) as client:
        yield client, store, clock
    store.close()


def rows(store):
    with store.factory() as db:
        return list(db.scalars(sa.select(RevenueClick)))


def snapshot(store):
    with store.engine.connect() as conn:
        return {t.name: [dict(row) for row in conn.execute(sa.select(t)).mappings()]
                for t in Base.metadata.sorted_tables if t.name != "revenue_clicks"}


def test_activation_persists_once_with_server_id_time_and_no_business_changes(revenue):
    client, store, _ = revenue
    session = store.create()
    before = snapshot(store)
    result = client.post("/revenue/click", json=PAYLOAD)
    assert result.status_code == 201
    assert result.json()["ok"] is True
    UUID(result.json()["id"])
    [row] = rows(store)
    assert row.id == result.json()["id"]
    assert row.created_at.replace(tzinfo=timezone.utc) == NOW
    assert {k: getattr(row, k) for k in PAYLOAD} == PAYLOAD
    assert snapshot(store) == before
    trip_id = session.trip_id
    assert client.post("/demo/reset", json={"session": session.token}).status_code == 200
    with store.factory() as db:
        assert db.get(Trip, trip_id) is None
        assert db.get(RevenueClick, row.id) is not None
    assert not RevenueClick.__table__.foreign_keys
    assert set(RevenueClick.__table__.columns.keys()) == {*PAYLOAD, "id", "created_at"}


@pytest.mark.parametrize("provider", ["kiwitaxi", "discovercars", "viator", "concierge"])
@pytest.mark.parametrize("placement", ["route_landing", "missing_transport", "revenue_panel"])
def test_all_supported_providers_and_placements(revenue, provider, placement):
    client, store, _ = revenue
    data = {"provider": provider, "placement": placement}
    if placement == "route_landing":
        data["route_slug"] = "podgorica-kotor"
    assert client.post("/revenue/click", json=data).status_code == 201
    assert len(rows(store)) == 1


@pytest.mark.parametrize("slug", sorted(ROUTE_SLUGS))
def test_known_route_slugs(revenue, slug):
    client, _, _ = revenue
    assert client.post("/revenue/click", json={**PAYLOAD, "route_slug": slug}).status_code == 201


@pytest.mark.parametrize("patch", [
    {"provider": "fake"}, {"provider": 1}, {"placement": "impression"},
    {"route_slug": "unknown-route"}, {"route_slug": "Podgorica-Kotor"},
    {"route_slug": " podgorica-kotor "}, {"route_slug": "../private"},
    {"route_slug": ""}, {"route_slug": None}, {"route_slug": "x" * 81},
    {"session": "private-token"}, {"email": "guest@example.com"}, {"ip": "127.0.0.1"},
    {"destination_url": "https://example.com"}, {"booking_status": "confirmed"},
    {"commission": 10}, {"id": "client-id"}, {"created_at": "2026-10-28"},
    {"utm_source": "guest@example.com"}, {"utm_medium": "+38267000000"},
    {"utm_campaign": "john_smith"}, {"utm_content": "token_123abc"},
    {"utm_term": "private-session-id"}, {"utm_content": "reel_123456789"},
    {"utm_campaign": "https://example.com/?secret=123"}, {"utm_source": "instagram\n"},
    {"utm_source": "a" * 49}, {"utm_medium": ["organic"]}, {"utm_term": 42},
])
def test_invalid_fields_are_rejected_without_echo_or_persistence(revenue, patch, caplog):
    client, store, _ = revenue
    response = client.post("/revenue/click", json={**PAYLOAD, **patch})
    assert response.status_code == 422
    assert response.json() == {"message": "Invalid activation fields."}
    assert rows(store) == []
    assert "guest@example.com" not in caplog.text
    assert "private-token" not in caplog.text


@pytest.mark.parametrize("body,status", [("{", 400), ('{"provider":"kiwitaxi","provider":"viator"}', 400),
                                        ("[]", 422), ("null", 422), ("123", 422), ('"text"', 422),
                                        ("[" * 1500, 400), (b"\xff", 400)])
def test_invalid_json(revenue, body, status):
    client, store, _ = revenue
    assert client.post("/revenue/click", content=body, headers={"content-type": "application/json"}).status_code == status
    assert not rows(store)


@pytest.mark.parametrize("media", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data", ""])
def test_json_only(revenue, media):
    client, _, _ = revenue
    assert client.post("/revenue/click", content=json.dumps(PAYLOAD), headers={"content-type": media}).status_code == 415


def test_size_limit_including_chunked_body_and_bad_headers(revenue):
    client, store, _ = revenue
    headers = {"content-type": "application/json"}
    assert client.post("/revenue/click", content=" " * (MAX_BODY_BYTES + 1), headers=headers).status_code == 413
    assert client.post("/revenue/click", content=iter([b" " * 1024] * 3), headers=headers).status_code == 413
    assert client.post("/revenue/click", content="{}", headers={**headers, "content-length": "-1"}).status_code == 400
    assert client.post("/revenue/click", json=PAYLOAD, headers={"content-encoding": "gzip"}).status_code == 415
    assert not rows(store)


def test_rate_limit_has_bounded_global_identity_free_window(revenue):
    client, store, clock = revenue
    for _ in range(RATE_LIMIT):
        assert client.post("/revenue/click", json=PAYLOAD).status_code == 201
    refused = client.post("/revenue/click", json=PAYLOAD, headers={"x-forwarded-for": "different-client"})
    assert refused.status_code == 429
    assert refused.headers["retry-after"] == "60"
    assert len(rows(store)) == RATE_LIMIT
    clock[0] = 60.0
    assert client.post("/revenue/click", json=PAYLOAD).status_code == 201


def test_db_failure_is_not_success_and_does_not_log_parameters(revenue, monkeypatch, caplog):
    client, store, _ = revenue
    def fail(*args, **kwargs):
        raise OperationalError("secret SQL", {"email": "guest@example.com"}, Exception("secret details"))
    monkeypatch.setattr(store.factory.class_, "commit", fail)
    response = client.post("/revenue/click", json=PAYLOAD)
    assert response.status_code == 503
    assert response.json() == {"message": "Telemetry unavailable."}
    assert not rows(store)
    assert "secret" not in caplog.text and "guest@example.com" not in caplog.text


def test_origin_query_privacy_no_read_or_mutation_api(revenue):
    client, store, _ = revenue
    for method in [client.get, client.put, client.patch, client.delete]:
        assert method("/revenue/click").status_code == 405
    assert client.options("/revenue/click", headers={"origin": "https://evil.example", "access-control-request-method": "POST"}).status_code == 405
    for headers in [{"origin": "https://evil.example"}, {"origin": "null"}, {"sec-fetch-site": "cross-site"}, {"sec-fetch-site": "same-site"}]:
        response = client.post("/revenue/click", json=PAYLOAD, headers=headers)
        assert response.status_code == 403
        assert "access-control-allow-origin" not in response.headers
    assert client.post("/revenue/click?email=secret", json=PAYLOAD).status_code == 400
    assert not rows(store)
    accepted = client.post("/revenue/click", json=PAYLOAD, headers={"origin": "http://testserver", "sec-fetch-site": "same-origin", "cookie": "session=private", "referer": "https://example.com/private", "user-agent": "private-agent"})
    assert accepted.status_code == 201
    assert accepted.headers["cache-control"] == "no-store"
    assert len(rows(store)) == 1


def test_uvicorn_access_log_filters_path_without_formatting_secrets():
    log_filter = RevenueAccessLogFilter()
    for path in ["/revenue/click", "/revenue/click?email=secret", "/revenue/click/"]:
        record = logging.LogRecord("uvicorn.access", logging.INFO, "", 1, "%s %s %s %s %s", ("IP", "POST", path, "1.1", 201), None)
        assert log_filter.filter(record) is False
    record.args = ("IP", "GET", "/health", "1.1", 200)
    assert log_filter.filter(record) is True


def test_frontend_backend_catalog_and_attribution_contract():
    frontend = Path(__file__).resolve().parents[2] / "atlas-frontend/src/lib/revenue"
    routes = json.loads((frontend / "transferRoutes.json").read_text())
    assert {route["slug"] for route in routes} == ROUTE_SLUGS
    policy = json.loads((frontend / "attributionPolicy.json").read_text())
    assert policy.pop("utm_content_pattern") == UTM_CONTENT_PATTERN
    assert policy == UTM_VALUES
