from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.atlas_web.app import create_app
from app.atlas_web.sessions import SessionStore
from app.journey import route_planner


NOW = datetime(2026, 10, 18, 0, 0, tzinfo=timezone.utc)


def test_active_quote_can_be_read_again_without_creating_another(monkeypatch):
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
        first = c.post("/demo/import", json={
            "text":
                "I'm going from Podgorica to Kotor "
                "for 30-31 October",
            "client_timezone": "Europe/Podgorica",
        }).json()

        sid = first["session"]
        plan = next(
            x for x in first["trip"]["items"]
            if x["kind"] == "plan"
        )
        key = plan["key"]

        c.post("/demo/plan/stay-intent", json={
            "session": sid,
            "plan_key": key,
            "value": False,
        })

        c.post("/demo/plan/arrival-time", json={
            "session": sid,
            "plan_key": key,
            "value": "18:00",
        })

        c.post("/demo/plan/party-size", json={
            "session": sid,
            "plan_key": key,
            "value": 2,
        })

        c.post("/demo/plan/route", json={
            "session": sid,
            "plan_key": key,
        })

        created = c.post(
            "/demo/plan/transfer-quote",
            json={
                "session": sid,
                "plan_key": key,
            },
        ).json()["quote"]

        restored = c.post(
            "/demo/plan/transport-state",
            json={
                "session": sid,
                "plan_key": key,
            },
        )

        assert restored.status_code == 200
        quote = restored.json()["quote"]

        assert quote is not None
        assert quote["id"] == created["id"]
        assert quote["amount"] == created["amount"]
        assert quote["currency"] == created["currency"]

        # Reading state created no second quote.
        restored_again = c.post(
            "/demo/plan/transport-state",
            json={
                "session": sid,
                "plan_key": key,
            },
        ).json()

        assert restored_again["quote"]["id"] == created["id"]


def test_confirmed_quote_is_no_longer_returned_as_active(monkeypatch):
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

        c.post("/demo/plan/stay-intent", json={
            "session": sid,
            "plan_key": key,
            "value": False,
        })
        c.post("/demo/plan/arrival-time", json={
            "session": sid,
            "plan_key": key,
            "value": "18:00",
        })
        c.post("/demo/plan/party-size", json={
            "session": sid,
            "plan_key": key,
            "value": 2,
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

        restored = c.post(
            "/demo/plan/transport-state",
            json={
                "session": sid,
                "plan_key": key,
            },
        ).json()

        assert restored["quote"] is None

        trip = c.get(f"/demo/trip/{sid}").json()["trip"]
        plan = next(
            x for x in trip["items"]
            if x["kind"] == "plan"
        )

        assert plan["detail"]["confirmed_transport"] is not None
