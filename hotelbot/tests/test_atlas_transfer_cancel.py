from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.atlas_web.app import create_app
from app.atlas_web.sessions import SessionStore
from app.journey import route_planner


NOW = datetime(
    2026, 10, 18, 12, 0,
    tzinfo=timezone.utc,
)


def prepare(c):
    first = c.post("/demo/import", json={
        "text":
            "I'm going from Podgorica to Kotor "
            "for 30-31 October",
        "client_timezone": "Europe/Podgorica",
    }).json()

    sid = first["session"]

    key = next(
        x["key"]
        for x in first["trip"]["items"]
        if x["kind"] == "plan"
    )

    for path, value in [
        ("/demo/plan/stay-intent", False),
        ("/demo/plan/arrival-time", "12:00"),
        ("/demo/plan/party-size", 2),
    ]:
        c.post(path, json={
            "session": sid,
            "plan_key": key,
            "value": value,
        })

    c.post("/demo/plan/route", json={
        "session": sid,
        "plan_key": key,
    })

    quote = c.post(
        "/demo/plan/transfer-quote",
        json={
            "session": sid,
            "plan_key": key,
        },
    ).json()["quote"]

    confirmed = c.post(
        "/demo/plan/transfer-confirm",
        json={
            "session": sid,
            "plan_key": key,
            "quote_id": quote["id"],
            "consent": True,
        },
    )

    assert confirmed.status_code == 200

    return sid, key


def plan_of(trip):
    return next(
        x
        for x in trip["items"]
        if x["kind"] == "plan"
    )


def test_cancelled_transfer_reopens_planning_gap(monkeypatch):
    monkeypatch.setattr(
        route_planner,
        "fetch_route",
        lambda origin, destination: {
            "distance_m": 72400,
            "duration_seconds": 5460,
        },
    )

    with TestClient(
        create_app(
            store=SessionStore(),
            utcnow=lambda: NOW,
        )
    ) as c:
        sid, key = prepare(c)

        before = c.get(
            f"/demo/trip/{sid}"
        ).json()["trip"]

        before_plan = plan_of(before)

        assert (
            before_plan["detail"]["confirmed_transport"]
            is not None
        )
        assert (
            before_plan["detail"]["planning"]["status"]
            == "covered"
        )

        policy = c.post(
            "/demo/plan/transfer-cancellation",
            json={
                "session": sid,
                "plan_key": key,
            },
        )

        assert policy.status_code == 200
        cancel_state = policy.json()["cancellation"]

        assert cancel_state is not None
        assert cancel_state["cancellable"] is True

        cancelled = c.post(
            "/demo/plan/transfer-cancel",
            json={
                "session": sid,
                "plan_key": key,
                "transaction_id": cancel_state["transaction_id"],
                "consent": True,
            },
        )

        assert cancelled.status_code == 200
        assert (
            cancelled.json()["transaction"]["status"]
            == "cancelled"
        )

        after = cancelled.json()["trip"]
        after_plan = plan_of(after)

        assert (
            after_plan["detail"]["confirmed_transport"]
            is None
        )

        codes = {
            x["code"]
            for x in
            after_plan["detail"]["planning"]["needs"]
        }

        assert "arrival_transport_unconfirmed" in codes


def test_cancel_requires_explicit_consent(monkeypatch):
    monkeypatch.setattr(
        route_planner,
        "fetch_route",
        lambda origin, destination: {
            "distance_m": 72400,
            "duration_seconds": 5460,
        },
    )

    with TestClient(
        create_app(
            store=SessionStore(),
            utcnow=lambda: NOW,
        )
    ) as c:
        sid, key = prepare(c)

        response = c.post(
            "/demo/plan/transfer-cancel",
            json={
                "session": sid,
                "plan_key": key,
                "consent": False,
            },
        )

        assert response.status_code == 409

        trip = c.get(
            f"/demo/trip/{sid}"
        ).json()["trip"]

        assert (
            plan_of(trip)["detail"]
            ["confirmed_transport"]
            is not None
        )


def test_cancel_consent_bound_to_transaction(monkeypatch):
    monkeypatch.setattr(
        route_planner,
        "fetch_route",
        lambda origin, destination: {
            "distance_m": 72400,
            "duration_seconds": 5460,
        },
    )

    with TestClient(
        create_app(
            store=SessionStore(),
            utcnow=lambda: NOW,
        )
    ) as c:
        sid, key = prepare(c)

        preview = c.post(
            "/demo/plan/transfer-cancellation",
            json={"session": sid, "plan_key": key},
        )

        assert preview.status_code == 200
        state = preview.json()["cancellation"]

        assert state["transaction_id"]

        wrong = c.post(
            "/demo/plan/transfer-cancel",
            json={
                "session": sid,
                "plan_key": key,
                "transaction_id": "wrong-transaction",
                "consent": True,
            },
        )

        assert wrong.status_code == 409

        trip = c.get(f"/demo/trip/{sid}").json()["trip"]

        assert (
            plan_of(trip)["detail"]["confirmed_transport"]
            is not None
        )


def test_missing_transaction_is_not_confirmation():
    from types import SimpleNamespace
    from app.atlas_web import view_v2

    class MissingDB:
        def get(self, model, identity):
            return None

    item = SimpleNamespace(details={
        "transport_transaction": {
            "transaction_id": "missing-transaction",
            "status": "accepted",
        }
    })

    result = view_v2._live_transport_transaction(
        MissingDB(),
        item,
    )

    assert result is None


def test_repeated_cancel_is_idempotent(monkeypatch):
    from sqlalchemy import select
    from app.db.models import Job

    monkeypatch.setattr(
        route_planner,
        "fetch_route",
        lambda origin, destination: {
            "distance_m": 72400,
            "duration_seconds": 5460,
        },
    )

    store = SessionStore()

    with TestClient(
        create_app(store=store, utcnow=lambda: NOW)
    ) as c:
        sid, key = prepare(c)

        preview = c.post(
            "/demo/plan/transfer-cancellation",
            json={"session": sid, "plan_key": key},
        ).json()["cancellation"]

        transaction_id = preview["transaction_id"]

        payload = {
            "session": sid,
            "plan_key": key,
            "transaction_id": transaction_id,
            "consent": True,
        }

        first = c.post(
            "/demo/plan/transfer-cancel", json=payload
        )
        second = c.post(
            "/demo/plan/transfer-cancel", json=payload
        )

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["transaction"]["status"] == "cancelled"

        s = store.get(sid)
        jobs = list(s.db.scalars(
            select(Job).where(
                Job.idempotency_key == f"cancel:{transaction_id}"
            )
        ))

        assert len(jobs) == 1


def test_provider_refusal_preserves_booking(monkeypatch):
    from app.mobility import marketplace as mobility_marketplace

    monkeypatch.setattr(
        route_planner,
        "fetch_route",
        lambda origin, destination: {
            "distance_m": 72400,
            "duration_seconds": 5460,
        },
    )

    store = SessionStore()

    with TestClient(
        create_app(store=store, utcnow=lambda: NOW)
    ) as c:
        sid, key = prepare(c)

        s = store.get(sid)
        plan = mobility_marketplace.plan_item(
            s.db, s.trip_id, key
        )
        txn = mobility_marketplace.current_transfer_transaction(
            s.db, s.trip_id, plan
        )

        config = dict(txn.provider.config or {})
        config["behavior"] = {
            **config.get("behavior", {}),
            "cancel": "refused",
        }
        txn.provider.config = config
        s.db.commit()

        response = c.post(
            "/demo/plan/transfer-cancel",
            json={
                "session": sid,
                "plan_key": key,
                "transaction_id": txn.id,
                "consent": True,
            },
        )

        assert response.status_code == 409

        trip = c.get(
            f"/demo/trip/{sid}"
        ).json()["trip"]

        detail = plan_of(trip)["detail"]

        assert detail["confirmed_transport"] is not None
        assert (
            detail["confirmed_transport"]["status"]
            == "accepted"
        )
        assert detail["planning"]["status"] == "covered"


def test_cancel_timeout_rolls_back_without_false_success(monkeypatch, tmp_path):
    from sqlalchemy import select
    from app.db.models import Job
    from app.mobility import marketplace as mobility_marketplace

    monkeypatch.setattr(
        route_planner,
        "fetch_route",
        lambda origin, destination: {
            "distance_m": 72400,
            "duration_seconds": 5460,
        },
    )

    store = SessionStore(
        database_url=f"sqlite:///{tmp_path / 'cancel_timeout.db'}"
    )

    with TestClient(
        create_app(store=store, utcnow=lambda: NOW),
        raise_server_exceptions=False,
    ) as c:
        sid, key = prepare(c)

        s = store.get(sid)
        plan = mobility_marketplace.plan_item(
            s.db, s.trip_id, key
        )
        txn = mobility_marketplace.current_transfer_transaction(
            s.db, s.trip_id, plan
        )
        transaction_id = txn.id

        config = dict(txn.provider.config or {})
        config["behavior"] = {
            **(config.get("behavior") or {}),
            "cancel": "timeout",
        }
        txn.provider.config = config
        s.db.commit()

        response = c.post(
            "/demo/plan/transfer-cancel",
            json={
                "session": sid,
                "plan_key": key,
                "transaction_id": transaction_id,
                "consent": True,
            },
        )

        assert response.status_code == 503
        assert (
            response.json()["reason"]
            == "provider_cancellation_unverified"
        )

        trip = c.get(f"/demo/trip/{sid}").json()["trip"]
        detail = plan_of(trip)["detail"]

        assert detail["confirmed_transport"] is not None
        assert (
            detail["confirmed_transport"]["status"]
            == "accepted"
        )

        s = store.get(sid)
        pending = s.db.scalar(
            select(Job).where(
                Job.idempotency_key
                == f"cancel:{transaction_id}"
            )
        )

        assert pending is None
