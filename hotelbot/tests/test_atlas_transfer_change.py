from fastapi.testclient import TestClient

from app.atlas_web.app import create_app
from app.atlas_web.sessions import SessionStore
from app.db.models import ActionStatus, ExternalTransaction, Quote
from app.journey import route_planner
from app.mobility import marketplace

from tests.test_atlas_transfer_cancel import NOW, prepare


def test_change_quote_links_old_booking_without_cancelling(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        route_planner,
        "fetch_route",
        lambda origin, destination: {
            "distance_m": 72400,
            "duration_seconds": 5460,
        },
    )

    store = SessionStore(
        database_url=f"sqlite:///{tmp_path / 'change.db'}"
    )

    with TestClient(
        create_app(store=store, utcnow=lambda: NOW)
    ) as c:
        sid, key = prepare(c)

        s = store.get(sid)
        plan = marketplace.plan_item(
            s.db, s.trip_id, key
        )
        old_txn = marketplace.current_transfer_transaction(
            s.db, s.trip_id, plan
        )

        assert old_txn is not None
        old_id = old_txn.id
        assert old_txn.action.status == ActionStatus.ACCEPTED

        changed = c.post(
            "/demo/plan/party-size",
            json={
                "session": sid,
                "plan_key": key,
                "value": 3,
            },
        )
        assert changed.status_code == 200

        # Requesting a replacement must not cancel the old booking.
        assert (
            s.db.get(ExternalTransaction, old_id).action.status
            == ActionStatus.ACCEPTED
        )

        response = c.post(
            "/demo/plan/transfer-quote",
            json={
                "session": sid,
                "plan_key": key,
            },
        )
        assert response.status_code == 200

        payload = response.json()["quote"]
        assert payload["amount"] == "70.00"

        new_quote = s.db.get(Quote, payload["id"])
        assert new_quote is not None

        # This is the missing replacement link.
        assert new_quote.replaces_transaction_id == old_id

        # Still no premature cancellation.
        assert (
            s.db.get(ExternalTransaction, old_id).action.status
            == ActionStatus.ACCEPTED
        )


def test_party_change_reopens_transport_gap(monkeypatch, tmp_path):
    monkeypatch.setattr(
        route_planner,
        "fetch_route",
        lambda origin, destination: {
            "distance_m": 72400,
            "duration_seconds": 5460,
        },
    )

    store = SessionStore(
        database_url=f"sqlite:///{tmp_path / 'party_change.db'}"
    )

    with TestClient(
        create_app(store=store, utcnow=lambda: NOW)
    ) as c:
        sid, key = prepare(c)

        before = c.get(f"/demo/trip/{sid}").json()["trip"]
        assert next(
            x for x in before["items"] if x["kind"] == "plan"
        )["detail"]["planning"]["status"] == "covered"

        changed = c.post(
            "/demo/plan/party-size",
            json={
                "session": sid,
                "plan_key": key,
                "value": 3,
            },
        )

        assert changed.status_code == 200

        plan = next(
            x for x in changed.json()["trip"]["items"]
            if x["kind"] == "plan"
        )
        detail = plan["detail"]

        assert detail["confirmed_transport"]["party_size"] == 2
        assert "arrival_transport_unconfirmed" in {
            n["code"] for n in detail["planning"]["needs"]
        }


def test_accepted_change_retires_old_sandbox_booking(
    monkeypatch, tmp_path
):
    from sqlalchemy import select
    from app.db.models import Job, JobStatus

    monkeypatch.setattr(
        route_planner,
        "fetch_route",
        lambda origin, destination: {
            "distance_m": 72400,
            "duration_seconds": 5460,
        },
    )

    store = SessionStore(
        database_url=f"sqlite:///{tmp_path / 'replacement.db'}"
    )

    with TestClient(
        create_app(store=store, utcnow=lambda: NOW)
    ) as c:
        sid, key = prepare(c)
        s = store.get(sid)
        plan = marketplace.plan_item(s.db, s.trip_id, key)

        old = marketplace.current_transfer_transaction(
            s.db, s.trip_id, plan
        )
        old_id = old.id

        changed = c.post(
            "/demo/plan/party-size",
            json={
                "session": sid,
                "plan_key": key,
                "value": 3,
            },
        )
        assert changed.status_code == 200

        body = {"session": sid, "plan_key": key}
        quote_response = c.post(
            "/demo/plan/transfer-quote", json=body
        )
        assert quote_response.status_code == 200

        quote_id = quote_response.json()["quote"]["id"]
        assert (
            s.db.get(Quote, quote_id).replaces_transaction_id
            == old_id
        )

        assert (
            s.db.get(ExternalTransaction, old_id).action.status
            == ActionStatus.ACCEPTED
        )

        confirm = {
            **body,
            "quote_id": quote_id,
            "consent": True,
        }
        response = c.post(
            "/demo/plan/transfer-confirm", json=confirm
        )

        assert response.status_code == 200
        new_txn = response.json()["transaction"]
        assert new_txn["status"] == "accepted"
        assert new_txn["id"] != old_id

        # Successful replacement must resolve the old sandbox obligation.
        assert (
            s.db.get(ExternalTransaction, old_id).action.status
            == ActionStatus.CANCELLED
        )

        cancel_job = s.db.scalar(
            select(Job).where(
                Job.idempotency_key == f"cancel:{old_id}"
            )
        )
        assert cancel_job is not None
        assert cancel_job.status == JobStatus.SUCCEEDED

        replay = c.post(
            "/demo/plan/transfer-confirm", json=confirm
        )
        assert replay.status_code == 200
        assert replay.json()["transaction"]["id"] == new_txn["id"]


def test_refused_old_cancellation_is_visible_after_change(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        route_planner,
        "fetch_route",
        lambda origin, destination: {
            "distance_m": 72400,
            "duration_seconds": 5460,
        },
    )

    store = SessionStore(
        database_url=f"sqlite:///{tmp_path / 'refusal.db'}"
    )

    with TestClient(
        create_app(store=store, utcnow=lambda: NOW)
    ) as c:
        sid, key = prepare(c)
        s = store.get(sid)
        plan = marketplace.plan_item(s.db, s.trip_id, key)

        old = marketplace.current_transfer_transaction(
            s.db, s.trip_id, plan
        )
        old_id = old.id
        old_reference = old.provider_reference

        config = dict(old.provider.config or {})
        config["behavior"] = {
            **(config.get("behavior") or {}),
            "cancel": "refused",
        }
        old.provider.config = config
        s.db.commit()

        body = {"session": sid, "plan_key": key}

        changed = c.post(
            "/demo/plan/party-size",
            json={**body, "value": 3},
        )
        assert changed.status_code == 200

        quoted = c.post(
            "/demo/plan/transfer-quote", json=body
        )
        assert quoted.status_code == 200

        quote_id = quoted.json()["quote"]["id"]
        assert (
            s.db.get(Quote, quote_id).replaces_transaction_id
            == old_id
        )

        response = c.post(
            "/demo/plan/transfer-confirm",
            json={
                **body,
                "quote_id": quote_id,
                "consent": True,
            },
        )

        assert response.status_code in (200, 202)
        result = response.json()

        new_id = result["transaction"]["id"]

        assert new_id != old_id
        assert (
            s.db.get(ExternalTransaction, new_id).action.status
            == ActionStatus.ACCEPTED
        )
        assert (
            s.db.get(ExternalTransaction, old_id).action.status
            == ActionStatus.ACCEPTED
        )

        # The new booking exists, but the overall change is
        # incomplete. Never silently report clean success.
        assert response.status_code == 202
        assert result["change"]["status"] == "needs_attention"
        assert (
            result["change"]["reason"]
            == "old_cancellation_not_confirmed"
        )

        # Reload must not hide the older active obligation.
        refreshed = c.get(f"/demo/trip/{sid}")
        assert refreshed.status_code == 200

        detail = next(
            x["detail"]
            for x in refreshed.json()["trip"]["items"]
            if x["kind"] == "plan"
        )

        assert detail["transport_change"]["status"] == "needs_attention"
        assert (
            detail["transport_change"]["old_provider_reference"]
            == old_reference
        )


def _prepare_change_with_cancel_behavior(c, store, behavior):
    sid, key = prepare(c)
    s = store.get(sid)
    plan = marketplace.plan_item(s.db, s.trip_id, key)

    old = marketplace.current_transfer_transaction(
        s.db, s.trip_id, plan
    )
    old_id = old.id

    config = dict(old.provider.config or {})
    config["behavior"] = {
        **(config.get("behavior") or {}),
        "cancel": behavior,
    }
    old.provider.config = config
    s.db.commit()

    body = {"session": sid, "plan_key": key}

    assert c.post(
        "/demo/plan/party-size",
        json={**body, "value": 3},
    ).status_code == 200

    quoted = c.post("/demo/plan/transfer-quote", json=body)
    assert quoted.status_code == 200

    quote_id = quoted.json()["quote"]["id"]

    assert (
        s.db.get(Quote, quote_id).replaces_transaction_id
        == old_id
    )

    return sid, key, old_id, quote_id


def test_change_confirm_replay_does_not_retry_cancel(
    monkeypatch, tmp_path
):
    from app.transactions.providers.mock import MockExternalProvider

    monkeypatch.setattr(
        route_planner, "fetch_route",
        lambda origin, destination: {
            "distance_m": 72400,
            "duration_seconds": 5460,
        },
    )

    original_cancel = MockExternalProvider.cancel
    calls = []

    def tracked_cancel(self, reference, idempotency_key):
        calls.append((reference, idempotency_key))
        return original_cancel(self, reference, idempotency_key)

    monkeypatch.setattr(
        MockExternalProvider, "cancel", tracked_cancel
    )

    store = SessionStore(
        database_url=f"sqlite:///{tmp_path / 'replay.db'}"
    )

    with TestClient(
        create_app(store=store, utcnow=lambda: NOW)
    ) as c:
        sid, key, old_id, quote_id = (
            _prepare_change_with_cancel_behavior(
                c, store, "refused"
            )
        )

        confirm = {
            "session": sid,
            "plan_key": key,
            "quote_id": quote_id,
            "consent": True,
        }

        first = c.post("/demo/plan/transfer-confirm", json=confirm)
        second = c.post("/demo/plan/transfer-confirm", json=confirm)

        assert first.status_code == 202
        assert second.status_code == 202

        assert (
            first.json()["transaction"]["id"]
            == second.json()["transaction"]["id"]
        )

        # Retrying Confirm must not make another provider cancel call.
        assert len(calls) == 1

        s = store.get(sid)
        assert (
            s.db.get(ExternalTransaction, old_id).action.status
            == ActionStatus.ACCEPTED
        )


def test_change_cancel_timeout_preserves_bookings(
    monkeypatch, tmp_path
):
    from sqlalchemy import select
    from app.db.models import Job, JobStatus

    monkeypatch.setattr(
        route_planner, "fetch_route",
        lambda origin, destination: {
            "distance_m": 72400,
            "duration_seconds": 5460,
        },
    )

    store = SessionStore(
        database_url=f"sqlite:///{tmp_path / 'timeout.db'}"
    )

    with TestClient(
        create_app(store=store, utcnow=lambda: NOW),
        raise_server_exceptions=False,
    ) as c:
        sid, key, old_id, quote_id = (
            _prepare_change_with_cancel_behavior(
                c, store, "timeout"
            )
        )

        response = c.post(
            "/demo/plan/transfer-confirm",
            json={
                "session": sid,
                "plan_key": key,
                "quote_id": quote_id,
                "consent": True,
            },
        )

        # New provider accepted; old cancellation remains unverified.
        assert response.status_code == 202

        result = response.json()
        assert result["change"]["status"] == "needs_attention"

        new_id = result["transaction"]["id"]
        s = store.get(sid)

        assert (
            s.db.get(ExternalTransaction, new_id).action.status
            == ActionStatus.ACCEPTED
        )
        assert (
            s.db.get(ExternalTransaction, old_id).action.status
            == ActionStatus.ACCEPTED
        )

        pending = s.db.scalar(
            select(Job).where(
                Job.idempotency_key == f"cancel:{old_id}"
            )
        )

        assert pending is not None
        assert pending.status == JobStatus.PENDING

        refreshed = c.get(f"/demo/trip/{sid}")
        assert refreshed.status_code == 200

        detail = next(
            x["detail"]
            for x in refreshed.json()["trip"]["items"]
            if x["kind"] == "plan"
        )

        assert detail["transport_change"]["status"] == "needs_attention"


def test_unresolved_change_blocks_third_quote(monkeypatch, tmp_path):
    from sqlalchemy import func, select

    monkeypatch.setattr(
        route_planner,
        "fetch_route",
        lambda origin, destination: {
            "distance_m": 72400,
            "duration_seconds": 5460,
        },
    )

    store = SessionStore(
        database_url=f"sqlite:///{tmp_path / 'third_booking.db'}"
    )

    try:
        with TestClient(
            create_app(store=store, utcnow=lambda: NOW)
        ) as c:
            sid, key, old_id, quote_id = (
                _prepare_change_with_cancel_behavior(
                    c, store, "refused"
                )
            )

            body = {"session": sid, "plan_key": key}

            confirmed = c.post(
                "/demo/plan/transfer-confirm",
                json={
                    **body,
                    "quote_id": quote_id,
                    "consent": True,
                },
            )

            assert confirmed.status_code == 202
            assert confirmed.json()["change"]["status"] == "needs_attention"

            new_id = confirmed.json()["transaction"]["id"]
            s = store.get(sid)

            assert (
                s.db.get(ExternalTransaction, old_id).action.status
                == ActionStatus.ACCEPTED
            )
            assert (
                s.db.get(ExternalTransaction, new_id).action.status
                == ActionStatus.ACCEPTED
            )

            before = s.db.scalar(
                select(func.count()).select_from(Quote)
            )

            changed = c.post(
                "/demo/plan/party-size",
                json={**body, "value": 4},
            )
            assert changed.status_code == 200

            third = c.post(
                "/demo/plan/transfer-quote",
                json=body,
            )

            assert third.status_code == 409
            assert third.json()["reason"] == "unresolved_previous_change"

            after = s.db.scalar(
                select(func.count()).select_from(Quote)
            )

            assert after == before

    finally:
        store.close()


def test_resolved_change_allows_next_replacement(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        route_planner,
        "fetch_route",
        lambda origin, destination: {
            "distance_m": 72400,
            "duration_seconds": 5460,
        },
    )

    store = SessionStore(
        database_url=f"sqlite:///{tmp_path / 'recovery.db'}"
    )

    try:
        with TestClient(
            create_app(store=store, utcnow=lambda: NOW)
        ) as c:
            sid, key, old_id, quote_id = (
                _prepare_change_with_cancel_behavior(
                    c, store, "cancelled"
                )
            )

            body = {"session": sid, "plan_key": key}

            first_change = c.post(
                "/demo/plan/transfer-confirm",
                json={
                    **body,
                    "quote_id": quote_id,
                    "consent": True,
                },
            )

            assert first_change.status_code == 200
            assert first_change.json()["change"]["status"] == "completed"

            middle_id = first_change.json()["transaction"]["id"]
            s = store.get(sid)

            assert (
                s.db.get(ExternalTransaction, old_id).action.status
                == ActionStatus.CANCELLED
            )

            assert (
                s.db.get(ExternalTransaction, middle_id).action.status
                == ActionStatus.ACCEPTED
            )

            changed = c.post(
                "/demo/plan/party-size",
                json={**body, "value": 4},
            )
            assert changed.status_code == 200

            third_quote = c.post(
                "/demo/plan/transfer-quote",
                json=body,
            )

            assert third_quote.status_code == 200
            quote = third_quote.json()["quote"]

            assert quote["amount"] == "75.00"
            assert (
                s.db.get(Quote, quote["id"]).replaces_transaction_id
                == middle_id
            )

            # The middle booking remains active until the
            # third booking is accepted.
            assert (
                s.db.get(ExternalTransaction, middle_id).action.status
                == ActionStatus.ACCEPTED
            )

            final = c.post(
                "/demo/plan/transfer-confirm",
                json={
                    **body,
                    "quote_id": quote["id"],
                    "consent": True,
                },
            )

            assert final.status_code == 200
            assert final.json()["change"]["status"] == "completed"

            final_id = final.json()["transaction"]["id"]

            assert (
                s.db.get(ExternalTransaction, middle_id).action.status
                == ActionStatus.CANCELLED
            )

            assert (
                s.db.get(ExternalTransaction, final_id).action.status
                == ActionStatus.ACCEPTED
            )

    finally:
        store.close()


def test_in_progress_transfer_cannot_be_replaced(
    monkeypatch, tmp_path
):
    from sqlalchemy import func, select

    monkeypatch.setattr(
        route_planner,
        "fetch_route",
        lambda origin, destination: {
            "distance_m": 72400,
            "duration_seconds": 5460,
        },
    )

    store = SessionStore(
        database_url=f"sqlite:///{tmp_path / 'in_progress.db'}"
    )

    try:
        with TestClient(
            create_app(store=store, utcnow=lambda: NOW)
        ) as c:
            sid, key = prepare(c)
            s = store.get(sid)

            plan = marketplace.plan_item(
                s.db, s.trip_id, key
            )
            txn = marketplace.current_transfer_transaction(
                s.db, s.trip_id, plan
            )

            txn.action.status = ActionStatus.IN_PROGRESS
            s.db.commit()

            before = s.db.scalar(
                select(func.count()).select_from(Quote)
            )

            body = {"session": sid, "plan_key": key}

            assert c.post(
                "/demo/plan/party-size",
                json={**body, "value": 3},
            ).status_code == 200

            response = c.post(
                "/demo/plan/transfer-quote",
                json=body,
            )

            assert response.status_code == 409
            assert (
                response.json()["reason"]
                == "existing_transfer_not_replaceable"
            )

            after = s.db.scalar(
                select(func.count()).select_from(Quote)
            )

            assert after == before
            assert txn.action.status == ActionStatus.IN_PROGRESS

    finally:
        store.close()
