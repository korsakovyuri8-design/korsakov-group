from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.atlas_web.app import create_app
from app.atlas_web.sessions import SessionStore
from app.db.models import (
    ExternalTransaction,
    TripObservation,
    TripSourceRecord,
)
from app.journey import route_planner


NOW = datetime(2026, 10, 18, 0, 0, tzinfo=timezone.utc)


def _fake_route(origin, destination):
    return {
        "distance_m": 72400,
        "duration_seconds": 5460,
    }


def test_atlas_web_does_not_retain_raw_source_body():
    store = SessionStore()

    try:
        with TestClient(
            create_app(
                store=store,
                utcnow=lambda: NOW,
            )
        ) as client:
            text = (
                "I'm going from Podgorica to Kotor "
                "for 30-31 October"
            )

            out = client.post(
                "/demo/import",
                json={
                    "text": text,
                    "client_timezone": "Europe/Podgorica",
                },
            ).json()

            session = store.get(out["session"])
            assert session is not None

            record = session.db.scalar(
                select(TripSourceRecord).where(
                    TripSourceRecord.trip_id
                    == session.trip_id
                )
            )

            assert record is not None
            assert record.raw == ""
            assert len(record.raw_sha256) == 64
            assert len(record.normalized_sha256) == 64

            observations = session.db.scalar(
                select(func.count())
                .select_from(TripObservation)
                .where(
                    TripObservation.trip_id
                    == session.trip_id
                )
            )

            assert observations
            assert observations > 0

    finally:
        store.close()


def test_confirmed_transfer_survives_store_restart(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        route_planner,
        "fetch_route",
        _fake_route,
    )

    path = tmp_path / "atlas.db"
    database_url = f"sqlite:///{path}"

    first_store = SessionStore(
        database_url=database_url,
    )

    with TestClient(
        create_app(
            store=first_store,
            utcnow=lambda: NOW,
        )
    ) as client:
        first = client.post(
            "/demo/import",
            json={
                "text":
                    "I'm going from Podgorica to Kotor "
                    "for 30-31 October",
                "client_timezone": "Europe/Podgorica",
            },
        ).json()

        token = first["session"]

        plan = next(
            item
            for item in first["trip"]["items"]
            if item["kind"] == "plan"
        )

        plan_key = plan["key"]

        assert client.post(
            "/demo/plan/stay-intent",
            json={
                "session": token,
                "plan_key": plan_key,
                "value": False,
            },
        ).status_code == 200

        assert client.post(
            "/demo/plan/arrival-time",
            json={
                "session": token,
                "plan_key": plan_key,
                "value": "18:00",
            },
        ).status_code == 200

        assert client.post(
            "/demo/plan/party-size",
            json={
                "session": token,
                "plan_key": plan_key,
                "value": 2,
            },
        ).status_code == 200

        assert client.post(
            "/demo/plan/route",
            json={
                "session": token,
                "plan_key": plan_key,
            },
        ).status_code == 200

        quote_response = client.post(
            "/demo/plan/transfer-quote",
            json={
                "session": token,
                "plan_key": plan_key,
            },
        )

        assert quote_response.status_code == 200
        quote = quote_response.json()["quote"]
        assert quote["amount"] == "65.00"

        confirmed_response = client.post(
            "/demo/plan/transfer-confirm",
            json={
                "session": token,
                "plan_key": plan_key,
                "quote_id": quote["id"],
                "consent": True,
            },
        )

        assert confirmed_response.status_code == 200

        confirmed = confirmed_response.json()["transaction"]

        assert confirmed["status"] == "accepted"
        assert confirmed["provider_reference"].startswith(
            "MOCK-"
        )

    # Simulates the Python process disappearing.
    first_store.close()

    # A completely new store opens the same durable database.
    second_store = SessionStore(
        database_url=database_url,
    )

    try:
        with TestClient(
            create_app(
                store=second_store,
                utcnow=lambda: NOW,
            )
        ) as client:
            response = client.get(
                f"/demo/trip/{token}"
            )

            assert response.status_code == 200

            trip = response.json()["trip"]

            plan = next(
                item
                for item in trip["items"]
                if item["kind"] == "plan"
            )

            assert plan["key"] == plan_key

            transport = (
                plan["detail"]["confirmed_transport"]
            )

            assert transport is not None
            assert transport["status"] == "accepted"
            assert (
                transport["provider"]
                == "Atlas Sandbox Transfer"
            )
            assert transport["sandbox"] is True
            assert transport["provider_reference"].startswith(
                "MOCK-"
            )

            restored = second_store.get(token)
            assert restored is not None

            transaction_count = restored.db.scalar(
                select(func.count()).select_from(
                    ExternalTransaction
                )
            )

            assert transaction_count == 1

            state = client.post(
                "/demo/plan/transport-state",
                json={
                    "session": token,
                    "plan_key": plan_key,
                },
            )

            assert state.status_code == 200

            # Confirmed quote is not resurrected as an
            # active unbooked quote after restart.
            assert state.json()["quote"] is None

    finally:
        second_store.close()
