"""Privacy-light pageview contract. Disposable SQLite only, never production DB."""
from datetime import datetime, timezone
from uuid import UUID

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.atlas_web.app import create_app
from app.atlas_web.revenue import VIEW_RATE_LIMIT
from app.atlas_web.sessions import SessionStore
from app.db.models import RevenueClick, RevenuePageView

NOW = datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc)
VALID = {"route_slug": "podgorica-kotor", "utm_source": "google",
         "utm_medium": "organic", "utm_campaign": "montenegro_launch",
         "utm_content": "link_004"}


@pytest.fixture
def client_and_store(tmp_path):
    store = SessionStore(database_url=f"sqlite:///{tmp_path}/pageviews.sqlite")
    clock = [0.0]
    with TestClient(create_app(store=store, clock=lambda: clock[0], utcnow=lambda: NOW)) as client:
        yield client, store, clock
    store.close()


def views(store):
    with store.factory() as session:
        return list(session.scalars(sa.select(RevenuePageView)))


def test_pageview_is_201_after_commit_no_identity_or_business_fks(client_and_store):
    client, store, _ = client_and_store
    created = client.post("/revenue/view", json=VALID)
    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    view_id = created.json()["id"]
    UUID(view_id)
    [event] = views(store)
    assert event.id == view_id
    assert event.route_slug == VALID["route_slug"]
    assert event.utm_campaign == VALID["utm_campaign"]
    assert event.created_at.replace(tzinfo=timezone.utc) == NOW
    assert event.__table__.foreign_keys == set()
    assert set(event.__table__.columns.keys()) == {
        "id", "route_slug", "created_at", "utm_source", "utm_medium",
        "utm_campaign", "utm_content", "utm_term",
    }
    with store.factory() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(RevenueClick)) == 0


@pytest.mark.parametrize("invalid", [
    {"route_slug": "not-in-catalog"}, {"route_slug": None},
    {"route_slug": "/private/trip"}, {"route_slug": 9},
    {"utm_source": "name@example.com"}, {"utm_medium": "+9981234567"},
    {"utm_campaign": "personal_campaign"}, {"utm_content": "session_123"},
    {"utm_term": "https://private.example"},
    {"visitor_id": "private"}, {"ip": "127.0.0.1"}, {"url": "/trip/private"},
    {"user_agent": "secret"}, {"referer": "private"}, {"created_at": "today"},
    {"id": "client-provided"}, {"provider": "kiwitaxi"}, {"email": "private@example.com"},
])
def test_rejects_unapproved_values_and_identity_fields(client_and_store, invalid, caplog):
    client, store, _ = client_and_store
    result = client.post("/revenue/view", json={**VALID, **invalid})
    assert result.status_code == 422
    assert result.json() == {"message": "Invalid view fields."}
    assert views(store) == []
    assert "private@example.com" not in caplog.text


def test_no_read_api_and_browser_origin_protection(client_and_store):
    client, store, _ = client_and_store
    for method in (client.get, client.put, client.patch, client.delete):
        assert method("/revenue/view").status_code == 405
    for headers in (
        {"origin": "https://evil.example"},
        {"sec-fetch-site": "cross-site"},
        {"sec-fetch-site": "same-site"},
    ):
        assert client.post("/revenue/view", json=VALID, headers=headers).status_code == 403
    assert client.post("/revenue/view?token=secret", json=VALID).status_code == 400
    assert not views(store)
    assert client.post("/revenue/view", json=VALID, headers={
        "origin": "http://testserver", "sec-fetch-site": "same-origin",
        "cookie": "private=secret", "user-agent": "private-agent",
    }).status_code == 201
    assert len(views(store)) == 1


def test_json_size_duplicate_keys_and_global_budget(client_and_store):
    client, store, clock = client_and_store
    assert client.post("/revenue/view", content='{"route_slug":"a","route_slug":"b"}',
                       headers={"content-type": "application/json"}).status_code == 400
    assert client.post("/revenue/view", content=" " * 2049,
                       headers={"content-type": "application/json"}).status_code == 413
    assert client.post("/revenue/view", content="{}",
                       headers={"content-type": "text/plain"}).status_code == 415
    assert client.post("/revenue/view", content="{}",
                       headers={"content-type": "application/json", "content-encoding": "gzip"}).status_code == 415
    for _ in range(VIEW_RATE_LIMIT - 4):
        assert client.post("/revenue/view", json=VALID).status_code == 201
    assert client.post("/revenue/view", json=VALID).status_code == 429
    assert len(views(store)) == VIEW_RATE_LIMIT - 4
    clock[0] = 61.0
    assert client.post("/revenue/view", json=VALID).status_code == 201
