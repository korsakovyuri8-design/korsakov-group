from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.atlas_web.app import create_app
from app.atlas_web.sessions import SessionStore
from app.db.models import (
    ExternalProvider,
    ExternalTransaction,
    Trip,
)
from app.journey import route_planner


NOW = datetime(2026, 10, 18, tzinfo=timezone.utc)


def _route(origin, destination):
    return {
        "distance_m": 72400,
        "duration_seconds": 5460,
    }


def test_reset_physically_deletes_plain_anonymous_trip(tmp_path):
    url = f"sqlite:///{tmp_path / 'atlas.db'}"
    store = SessionStore(database_url=url)

    try:
        with TestClient(
            create_app(
                store=store,
                utcnow=lambda: NOW,
            )
        ) as client:
            out = client.post(
                "/demo/import",
                json={
                    "text":
                        "I'm going from Podgorica to Kotor "
                        "for 30-31 October",
                },
            ).json()

            token = out["session"]
            session = store.get(token)
            assert session is not None
            trip_id = session.trip_id

            assert client.post(
                "/demo/reset",
                json={"session": token},
            ).status_code == 200

            assert client.get(
                f"/demo/trip/{token}"
            ).status_code == 404

        with store.factory() as db:
            assert db.get(Trip, trip_id) is None

    finally:
        store.close()


def test_reset_deletes_synthetic_transaction_trip(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        route_planner,
        "fetch_route",
        _route,
    )

    url = f"sqlite:///{tmp_path / 'atlas.db'}"
    store = SessionStore(database_url=url)

    try:
        with TestClient(
            create_app(
                store=store,
                utcnow=lambda: NOW,
            )
        ) as client:
            first = client.post(
                "/demo/import",
                json={
                    "text":
                        "I'm going from Podgorica to Kotor "
                        "for 30-31 October",
                },
            ).json()

            token = first["session"]
            key = next(
                x["key"]
                for x in first["trip"]["items"]
                if x["kind"] == "plan"
            )

            client.post(
                "/demo/plan/stay-intent",
                json={
                    "session": token,
                    "plan_key": key,
                    "value": False,
                },
            )

            client.post(
                "/demo/plan/arrival-time",
                json={
                    "session": token,
                    "plan_key": key,
                    "value": "18:00",
                },
            )

            client.post(
                "/demo/plan/party-size",
                json={
                    "session": token,
                    "plan_key": key,
                    "value": 2,
                },
            )

            client.post(
                "/demo/plan/route",
                json={
                    "session": token,
                    "plan_key": key,
                },
            )

            quote = client.post(
                "/demo/plan/transfer-quote",
                json={
                    "session": token,
                    "plan_key": key,
                },
            ).json()["quote"]

            client.post(
                "/demo/plan/transfer-confirm",
                json={
                    "session": token,
                    "plan_key": key,
                    "quote_id": quote["id"],
                    "consent": True,
                },
            )

            session = store.get(token)
            assert session is not None
            trip_id = session.trip_id

            assert client.post(
                "/demo/reset",
                json={"session": token},
            ).status_code == 200

        with store.factory() as db:
            assert db.get(Trip, trip_id) is None
            assert list(
                db.scalars(select(ExternalTransaction))
            ) == []

    finally:
        store.close()


def test_reset_retains_real_world_obligation(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        route_planner,
        "fetch_route",
        _route,
    )

    url = f"sqlite:///{tmp_path / 'atlas.db'}"
    store = SessionStore(database_url=url)

    try:
        with TestClient(
            create_app(
                store=store,
                utcnow=lambda: NOW,
            )
        ) as client:
            first = client.post(
                "/demo/import",
                json={
                    "text":
                        "I'm going from Podgorica to Kotor "
                        "for 30-31 October",
                },
            ).json()

            token = first["session"]
            key = next(
                x["key"]
                for x in first["trip"]["items"]
                if x["kind"] == "plan"
            )

            client.post(
                "/demo/plan/stay-intent",
                json={
                    "session": token,
                    "plan_key": key,
                    "value": False,
                },
            )
            client.post(
                "/demo/plan/arrival-time",
                json={
                    "session": token,
                    "plan_key": key,
                    "value": "18:00",
                },
            )
            client.post(
                "/demo/plan/party-size",
                json={
                    "session": token,
                    "plan_key": key,
                    "value": 2,
                },
            )
            client.post(
                "/demo/plan/route",
                json={
                    "session": token,
                    "plan_key": key,
                },
            )

            quote = client.post(
                "/demo/plan/transfer-quote",
                json={
                    "session": token,
                    "plan_key": key,
                },
            ).json()["quote"]

            client.post(
                "/demo/plan/transfer-confirm",
                json={
                    "session": token,
                    "plan_key": key,
                    "quote_id": quote["id"],
                    "consent": True,
                },
            )

            session = store.get(token)
            assert session is not None
            trip_id = session.trip_id

            transaction = session.db.scalar(
                select(ExternalTransaction)
            )
            assert transaction is not None

            provider = session.db.get(
                ExternalProvider,
                transaction.provider_id,
            )
            assert provider is not None

            # Simulate a future real provider. Cleanup policy must
            # conservatively retain this obligation.
            provider.profile = {
                **(provider.profile or {}),
                "synthetic": False,
            }
            session.db.commit()

            assert client.post(
                "/demo/reset",
                json={"session": token},
            ).status_code == 200

            assert client.get(
                f"/demo/trip/{token}"
            ).status_code == 404

        with store.factory() as db:
            assert db.get(Trip, trip_id) is not None
            assert db.scalar(
                select(ExternalTransaction)
            ) is not None

    finally:
        store.close()


class ExpiryClock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


def test_ttl_physically_deletes_anonymous_trip(tmp_path):
    clock = ExpiryClock()
    url = f"sqlite:///{tmp_path / 'atlas.db'}"

    store = SessionStore(
        database_url=url,
        ttl_seconds=60,
        clock=clock,
    )

    try:
        created = store.create()
        token = created.token
        trip_id = created.trip_id

        clock.t += 61

        assert store.get(token) is None

        with store.factory() as db:
            assert db.get(Trip, trip_id) is None

    finally:
        store.close()
