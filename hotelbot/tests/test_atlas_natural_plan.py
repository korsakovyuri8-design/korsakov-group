from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.atlas_web.app import create_app
from app.atlas_web.sessions import SessionStore


NOW = datetime(2026, 10, 17, 19, 0, tzinfo=timezone.utc)


def _import(text: str) -> dict:
    with TestClient(
        create_app(
            store=SessionStore(),
            utcnow=lambda: NOW,
        )
    ) as c:
        return c.post(
            "/demo/import",
            json={
                "text": text,
                "client_timezone": "Europe/Podgorica",
            },
        ).json()


def _plan(out: dict) -> dict:
    return next(
        i for i in out["trip"]["items"]
        if i["kind"] == "plan"
    )


def test_weekend_plan_is_accepted_without_inventing_dates():
    out = _import(
        "I want to go to Podgorica for a weekend without a Kolashin"
    )

    assert out["understood"]["outcome"] == "new"
    assert out["understood"]["kind"] == "plan"
    assert out["understood"]["status"] == "Stated"
    assert out["understood"]["route"] == "Podgorica"

    detail = _plan(out)["detail"]

    assert detail["place"] == "Podgorica"
    assert detail["duration_text"].lower() == "a weekend"
    assert detail["note"] == "without a Kolashin"
    assert detail.get("date_text") is None

    # The advanced planning layer is still present. With no exact date,
    # downstream planning questions may be deferred until the trip can be
    # anchored in time.
    assert "planning" in detail
    assert isinstance(detail["planning"], dict)

    assert out["trip"]["bookings"] == []


def test_for_before_date_never_becomes_part_of_place():
    out = _import(
        "I'm going to Podgorica for 30-31 October"
    )

    assert out["understood"]["route"] == "Podgorica"
    assert out["understood"]["when"] == "30–31 October"

    detail = _plan(out)["detail"]

    assert detail["place"] == "Podgorica"
    assert detail["date_text"] == "30–31 October"


def test_event_place_and_date_still_split():
    out = _import(
        "I'm going to Investment Conference returns to "
        "One&Only Portonovi 20-21 of October"
    )

    detail = _plan(out)["detail"]

    assert detail["event"] == "Investment Conference"
    assert detail["place"] == "One&Only Portonovi"
    assert detail["date_text"] == "20–21 October"

    assert "planning" in detail


def test_relative_time_stays_relative():
    out = _import(
        "Thinking of going to Budva next month"
    )

    detail = _plan(out)["detail"]

    assert detail["place"] == "Budva"
    assert detail["timing_text"].lower() == "next month"
    assert detail.get("date_text") is None


def test_unrelated_text_remains_unreadable():
    out = _import(
        "Hi! Your parcel 4471 ships tomorrow."
    )

    assert out["understood"]["outcome"] == "unreadable"


def test_dated_natural_plan_still_enters_advanced_planning():
    out = _import(
        "I'm going to Podgorica for 30-31 October"
    )

    detail = _plan(out)["detail"]

    assert detail["place"] == "Podgorica"
    assert detail["date_text"] == "30–31 October"

    codes = {
        n["code"]
        for n in detail["planning"]["needs"]
    }

    # Podgorica is already resolved as a city, so Atlas does not need
    # to ask what role the place plays. It still needs stay/origin facts.
    assert "place_role_unknown" not in codes
    assert "overnight_stay_unknown" in codes
    assert "arrival_origin_unknown" in codes


def test_vague_weekend_then_exact_dates_refines_same_plan():
    with TestClient(
        create_app(
            store=SessionStore(),
            utcnow=lambda: NOW,
        )
    ) as c:
        first = c.post(
            "/demo/import",
            json={
                "text":
                    "I want to go to Podgorica for a weekend "
                    "without a Kolashin",
                "client_timezone": "Europe/Podgorica",
            },
        ).json()

        sid = first["session"]
        first_plan = next(
            i for i in first["trip"]["items"]
            if i["kind"] == "plan"
        )
        first_key = first_plan["key"]

        second = c.post(
            "/demo/import",
            json={
                "session": sid,
                "text":
                    "I'm going to Podgorica for 30-31 October",
                "client_timezone": "Europe/Podgorica",
            },
        ).json()

        plans = [
            i for i in second["trip"]["items"]
            if i["kind"] == "plan"
        ]

        assert len(plans) == 1

        [plan] = plans
        detail = plan["detail"]

        # Stable semantic object: the second statement refines the first.
        assert plan["key"] == first_key

        assert detail["place"] == "Podgorica"
        assert detail["date_text"] == "30–31 October"

        # Information from the earlier statement is retained.
        assert detail["duration_text"].lower() == "a weekend"
        assert detail["note"] == "without a Kolashin"


def test_two_different_explicit_dates_in_same_city_are_two_plans():
    with TestClient(
        create_app(
            store=SessionStore(),
            utcnow=lambda: NOW,
        )
    ) as c:
        first = c.post(
            "/demo/import",
            json={
                "text":
                    "I'm going to Podgorica for 30-31 October",
                "client_timezone": "Europe/Podgorica",
            },
        ).json()

        second = c.post(
            "/demo/import",
            json={
                "session": first["session"],
                "text":
                    "I'm going to Podgorica for 7-8 November",
                "client_timezone": "Europe/Podgorica",
            },
        ).json()

        plans = [
            i for i in second["trip"]["items"]
            if i["kind"] == "plan"
        ]

        assert len(plans) == 2
        assert {
            p["detail"]["date_text"]
            for p in plans
        } == {"30–31 October", "7–8 November"}


def test_plan_answers_survive_a_later_text_refinement():
    with TestClient(
        create_app(
            store=SessionStore(),
            utcnow=lambda: NOW,
        )
    ) as c:
        first = c.post(
            "/demo/import",
            json={
                "text":
                    "I'm going to Podgorica for 30-31 October",
                "client_timezone": "Europe/Podgorica",
            },
        ).json()

        sid = first["session"]
        plan = next(
            i for i in first["trip"]["items"]
            if i["kind"] == "plan"
        )
        key = plan["key"]

        assert c.post(
            "/demo/plan/stay-intent",
            json={
                "session": sid,
                "plan_key": key,
                "value": True,
            },
        ).status_code == 200

        assert c.post(
            "/demo/plan/origin",
            json={
                "session": sid,
                "plan_key": key,
                "origin": "Kotor",
            },
        ).status_code == 200

        assert c.post(
            "/demo/plan/arrival-time",
            json={
                "session": sid,
                "plan_key": key,
                "value": "18:00",
            },
        ).status_code == 200

        assert c.post(
            "/demo/plan/party-size",
            json={
                "session": sid,
                "plan_key": key,
                "value": 2,
            },
        ).status_code == 200

        refined = c.post(
            "/demo/import",
            json={
                "session": sid,
                "text":
                    "I want to go to Podgorica for 30-31 October",
                "client_timezone": "Europe/Podgorica",
            },
        ).json()

        plans = [
            i for i in refined["trip"]["items"]
            if i["kind"] == "plan"
        ]

        assert len(plans) == 1

        [plan] = plans
        detail = plan["detail"]

        assert plan["key"] == key
        assert detail["overnight_stay"] is True
        assert detail["origin_text"] == "Kotor"
        assert detail["arrival_time"] == "18:00"
        assert detail["party_size"] == 2



def test_directional_plan_keeps_origin_and_destination():
    out = _import(
        "I'm going from Podgorica to Kotor"
    )

    assert out["understood"]["outcome"] == "new"
    assert out["understood"]["kind"] == "plan"
    assert out["understood"]["status"] == "Stated"
    assert out["understood"]["route"] == "Kotor"

    detail = _plan(out)["detail"]

    assert detail["origin_text"] == "Podgorica"
    assert detail["place"] == "Kotor"
    assert detail.get("date_text") is None
    assert out["trip"]["bookings"] == []


def test_directional_plan_can_include_exact_dates():
    out = _import(
        "I'm going from Podgorica to Kotor for 30-31 October"
    )

    detail = _plan(out)["detail"]

    assert detail["origin_text"] == "Podgorica"
    assert detail["place"] == "Kotor"
    assert detail["date_text"] == "30–31 October"


def test_directional_plan_without_date_asks_for_date_first():
    out = _import("I'm going from Podgorica to Kotor")

    plan = _plan(out)
    detail = plan["detail"]

    assert detail["origin_text"] == "Podgorica"
    assert detail["place"] == "Kotor"

    assert [
        n["code"]
        for n in detail["planning"]["needs"]
    ] == ["date_unknown"]


def test_date_answer_updates_same_directional_plan():
    with TestClient(
        create_app(
            store=SessionStore(),
            utcnow=lambda: NOW,
        )
    ) as c:
        first = c.post(
            "/demo/import",
            json={
                "text": "I'm going from Podgorica to Kotor",
                "client_timezone": "Europe/Podgorica",
            },
        ).json()

        sid = first["session"]
        plan = next(
            i for i in first["trip"]["items"]
            if i["kind"] == "plan"
        )
        key = plan["key"]

        second = c.post(
            "/demo/plan/date",
            json={
                "session": sid,
                "plan_key": key,
                "value": "30-31 October",
            },
        )

        assert second.status_code == 200
        out = second.json()

        [plan] = [
            i for i in out["trip"]["items"]
            if i["kind"] == "plan"
        ]

        assert plan["key"] == key
        assert plan["detail"]["date_text"] == "30-31 October"
        assert plan["detail"]["origin_text"] == "Podgorica"

        codes = {
            n["code"]
            for n in plan["detail"]["planning"]["needs"]
        }

        assert "date_unknown" not in codes
        assert "overnight_stay_unknown" in codes
        assert "arrival_time_unknown" in codes
