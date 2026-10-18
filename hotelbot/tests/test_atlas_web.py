"""Competition Release 1.0: the public Atlas demo service (app/atlas_web).

One container: the demo page, plus a small API that runs the REAL parser /
ingest / timeline / Discovery pipeline in an isolated, ephemeral session
per visitor. Gates: the whole sample path, a pasted supported email parsed
by the same pipeline, an unknown format that changes nothing, and the
privacy limits (no raw document in logs or responses, size cap, rate limit,
HTML reduced to text, sessions that expire and carry no personal data)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.atlas_web import intake
from app.atlas_web.app import create_app
from app.atlas_web.sessions import SessionStore
from tests.conftest import ROOT

FIX = ROOT / "data" / "trips" / "fixtures"

FIXED_LIVE_NOW = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def client(clock):  # noqa: ANN001, ANN201
    app = create_app(
        store=SessionStore(ttl_seconds=45 * 60, max_sessions=50, clock=clock),
        clock=clock,
        utcnow=lambda: FIXED_LIVE_NOW,
    )
    with TestClient(app) as c:
        yield c


def _body(name: str) -> str:
    """What a person copies out of their mail client: the body, no transport headers."""
    raw = (FIX / name).read_text(encoding="utf-8")
    return raw.split("\n\n", 1)[1]


def _sample(client) -> dict:  # noqa: ANN001
    r = client.get("/demo/sample")
    assert r.status_code == 200
    return r.json()


# ------------------------------------------------------------------ the page
def test_the_home_page_offers_import_first_and_the_demo_second(client):
    r = client.get("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    html = r.text
    assert html.index("Paste a booking confirmation") < html.index("Watch the 90-second demo")
    assert "Use sample bookings" in html and "Import" in html
    assert "imported documents are processed temporarily and are not retained after the demo session" in html
    assert '"scenes"' in html                                    # the playback data is embedded


def test_the_page_forbids_outbound_loads_beyond_fonts(client):
    csp = client.get("/").headers["content-security-policy"]
    assert "default-src 'self'" in csp and "img-src 'self' data:" in csp and "connect-src 'self'" in csp


# ------------------------------------------------------------------ the sample path
def test_use_sample_bookings_builds_the_trip_and_finds_the_missing_transfer(client):
    out = _sample(client)
    assert re.fullmatch(r"[A-Za-z0-9_-]{20,}", out["session"])
    trip = out["trip"]
    assert trip["route"] == ["London", "Podgorica", "Kotor"] and trip["dates"] == "18–21 October"
    assert [c["title"] for c in out["understood"]] == ["Flight DM1432", "Hotel Demo Kotor", "Dinner at Konoba Demo"]
    assert [a["kind"] for a in trip["attention"]] == ["missing_transfer"]
    assert trip["attention"][0]["lines"][0] == "You land in Podgorica at 12:55."
    assert [n["id"] for n in trip["next"]] == ["transfer"]


def test_the_whole_path_transfer_free_time_discovery_and_disruption(client):
    sid = _sample(client)["session"]
    r = client.post("/demo/import", json={"session": sid, "sample": "transfer"}).json()
    assert r["understood"]["title"] == "Transfer TGD → Kotor" and r["understood"]["outcome"] == "new"
    trip = r["trip"]
    assert trip["attention"] == []
    day = next(d for d in trip["days"] if d["date"] == "2026-10-18")
    assert ("16:30–19:30", "Free time") in [(i["time"], i["label"]) for i in day["itinerary"]]
    assert trip["activities"]["headline"] == "You have 3 hours before dinner. Here are 3 things that actually fit."
    assert [n["id"] for n in trip["next"]] == ["flight_change"]

    r = client.post("/demo/import", json={"session": sid, "sample": "flight_change"}).json()
    card = r["understood"]
    assert card["outcome"] == "updated" and "Departure moved from 09:15 to 11:15." in card["changes"]
    kinds = [a["kind"] for a in r["trip"]["attention"]]
    assert kinds == ["impossible_connection"] and r["trip"]["next"] == []
    assert "Your existing transfer may no longer fit." in r["trip"]["attention"][0]["lines"]

    again = client.get(f"/demo/trip/{sid}").json()
    assert again["trip"] == r["trip"]


# ------------------------------------------------------------------ a pasted email
def test_a_pasted_supported_email_goes_through_the_same_pipeline(client):
    r = client.post("/demo/import", json={"text": _body("F1_airline_lgw_tgd.eml")})
    assert r.status_code == 200
    out = r.json()
    card = out["understood"]
    assert card == {"outcome": "new", "provider": "Demo Air", "title": "Flight DM1432", "route": "London → Podgorica",
                    "when": "18 Oct", "status": "Confirmed", "changes": [], "kind": "flight"}
    assert out["session"] and out["trip"]["route"] == ["London", "Podgorica"]


def test_the_same_email_twice_is_recognised_not_duplicated(client):
    first = client.post("/demo/import", json={"text": _body("F2_hotel_kotor.eml")}).json()
    again = client.post("/demo/import", json={"session": first["session"], "text": _body("F2_hotel_kotor.eml")}).json()
    assert again["understood"]["outcome"] == "already_known"
    assert len(again["trip"]["bookings"]) == 1


def test_html_email_is_reduced_to_text_before_parsing(client):
    html = (FIX / "F9_hotel_kotor.html").read_text(encoding="utf-8").split("\n\n", 1)[1]
    html = html.replace("</body>", '<img src="https://tracker.example/pixel.gif"><script>alert(1)</script></body>')
    clean = intake.to_text(html)
    assert "<" not in clean and "tracker.example" not in clean and "alert" not in clean
    r = client.post("/demo/import", json={"text": html}).json()
    assert r["understood"]["outcome"] == "new" and r["understood"]["kind"] == "accommodation"


def test_an_unknown_format_changes_nothing(client):
    sid = _sample(client)["session"]
    before = client.get(f"/demo/trip/{sid}").json()
    r = client.post("/demo/import", json={"session": sid, "text": "Hi! Your parcel 4471 ships tomorrow."})
    assert r.status_code == 200
    assert r.json()["understood"] == {"outcome": "unreadable", "message": "Atlas can't read this booking format yet.",
                                      "detail": "No trip data was changed."}
    assert client.get(f"/demo/trip/{sid}").json() == before


# ------------------------------------------------------------------ privacy and limits
def test_raw_documents_are_never_logged_or_echoed(client, caplog):
    marker = "Zebra-Quartz-7781"
    text = _body("F3_restaurant_kotor.eml").replace("Party: 2", f"Party: 2\nNote: {marker}")
    with caplog.at_level(logging.DEBUG):
        r = client.post("/demo/import", json={"text": text})
        client.post("/demo/import", json={"text": f"unreadable {marker}"})
    assert marker not in r.text and marker not in caplog.text


def test_a_document_over_the_size_limit_is_refused(client):
    r = client.post("/demo/import", json={"text": "x" * (intake.MAX_DOCUMENT_BYTES + 1)})
    assert r.status_code == 413 and r.json()["message"] == "This document is too large for the demo (limit 150 KB)."


def test_requests_are_rate_limited_per_visitor(client):
    codes = [client.post("/demo/import", json={"text": "nothing"}).status_code for _ in range(40)]
    assert codes[0] == 200 and codes[-1] == 429


def test_reset_deletes_the_session(client):
    sid = _sample(client)["session"]
    assert client.post("/demo/reset", json={"session": sid}).status_code == 200
    assert client.get(f"/demo/trip/{sid}").status_code == 404


def test_sessions_expire(client, clock):
    sid = _sample(client)["session"]
    clock.t += 45 * 60 + 1
    assert client.get(f"/demo/trip/{sid}").status_code == 404


def test_the_session_token_is_random_and_holds_no_personal_data(client):
    a = client.post("/demo/import", json={"text": _body("F1_airline_lgw_tgd.eml")}).json()["session"]
    b = client.post("/demo/import", json={"text": _body("F1_airline_lgw_tgd.eml")}).json()["session"]
    assert a != b and "Ana" not in a and "DMX7Q2" not in a


def test_the_service_never_imports_an_llm_or_an_http_client():
    import ast

    for f in (ROOT / "app" / "atlas_web").rglob("*.py"):
        tree = ast.parse(f.read_text())
        mods = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module] + \
               [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        assert not [m for m in mods if m.startswith(("app.llm", "httpx", "requests", "urllib"))], f


# ------------------------------------------------------------------ stated plans
def test_a_natural_language_trip_plan_is_kept_as_stated_not_booking(client):
    text = "I'm going to Investment Conference returns to One&Only Portonovi 20-21 of October"
    r = client.post("/demo/import", json={"text": text})
    assert r.status_code == 200
    out = r.json()
    card = out["understood"]
    assert card["outcome"] == "new" and card["kind"] == "plan" and card["status"] == "Stated"
    assert card["title"] == "Investment Conference" and card["route"] == "One&Only Portonovi"
    assert card["when"] == "20–21 October"
    assert out["trip"]["bookings"] == []
    plans = [i for i in out["trip"]["items"] if i["kind"] == "plan"]
    assert len(plans) == 1
    assert plans[0]["status"] == "stated" and plans[0]["provider"] is None and plans[0]["reference"] is None
    assert plans[0]["detail"]["planning"]["status"] == "needs_planning"
    assert [n["code"] for n in plans[0]["detail"]["planning"]["needs"]] == [
        "place_role_unknown",
        "overnight_stay_unknown",
        "arrival_origin_unknown",
    ]
    assert plans[0]["detail"]["resolved_date"] == {
        "status": "resolved",
        "start_date": "2026-10-20",
        "end_date": "2026-10-21",
        "timezone": "Europe/Podgorica",
        "basis": "inferred_upcoming_date",
        "reference_date": "2026-10-17",
        "year_stated": False,
    }
    expected_detail = {
        "event": "Investment Conference",
        "place": "One&Only Portonovi",
        "date_text": "20–21 October",
        "basis": "guest",
        "resolved_place": {
            "status": "resolved",
            "entity_key": "me:oneandonly_portonovi",
            "canonical_name": "One&Only Portonovi",
            "kind": "resort",
            "address": "Portonovi Resort, Herceg Novi 85340, Montenegro",
            "locality": "Kumbor",
            "municipality": "Herceg Novi",
            "country": "Montenegro",
            "timezone": "Europe/Podgorica",
            "coordinates": {
                "lat": 42.435287,
                "lon": 18.59857,
            },
            "resolver": "atlas_montenegro_seed",
            "registry_version": "2026-10-17.2",
            "match": "exact_alias",
        },
    }
    for key, value in expected_detail.items():
        assert plans[0]["detail"][key] == value
    assert text not in str(out["trip"])          # raw guest text is not echoed by the trip view
    assert out["trip"]["attention"] == [] and out["trip"]["activities"] is None


def test_a_vague_sentence_without_an_explicit_date_is_kept_as_stated_plan(client):
    r = client.post(
        "/demo/import",
        json={"text": "I'm going to One&Only Portonovi soon"},
    )
    assert r.status_code == 200

    out = r.json()
    assert out["understood"]["outcome"] == "new"
    assert out["understood"]["kind"] == "plan"
    assert out["understood"]["status"] == "Stated"
    assert out["understood"]["route"] == "One&Only Portonovi"

    plan = next(
        item for item in out["trip"]["items"]
        if item["kind"] == "plan"
    )

    assert plan["detail"]["place"] == "One&Only Portonovi"
    assert plan["detail"]["timing_text"].lower() == "soon"
    assert plan["detail"].get("date_text") is None
    assert out["trip"]["bookings"] == []


# ------------------------------------------------------------------ live clock
def test_a_real_import_uses_live_clock_and_client_timezone(clock):
    now = datetime(2026, 10, 16, 17, 5, tzinfo=timezone.utc)
    app = create_app(store=SessionStore(ttl_seconds=45 * 60, max_sessions=50, clock=clock),
                     clock=clock, utcnow=lambda: now)
    text = "I'm going to Investment Conference returns to One&Only Portonovi 20-21 of October"
    with TestClient(app) as c:
        out = c.post("/demo/import", json={"text": text, "client_timezone": "Europe/Podgorica"}).json()
    clock_view = out["trip"]["context"]["now"]
    assert clock_view == {
        "utc": "2026-10-16T17:05:00+00:00",
        "local": "2026-10-16T19:05",
        "timezone": "Europe/Podgorica",
        "clock": "live",
    }


def test_demo_sample_keeps_the_frozen_demo_clock(client):
    now = _sample(client)["trip"]["context"]["now"]
    assert now["clock"] == "demo"
    assert now["local"] == "2026-10-18T13:00"


def test_answering_plan_place_role_closes_only_that_planning_question(client):
    text = "I'm going to Investment Conference returns to One&Only Portonovi 20-21 of October"
    first = client.post("/demo/import", json={"text": text}).json()

    sid = first["session"]
    plan = next(i for i in first["trip"]["items"] if i["kind"] == "plan")
    key = plan["key"]

    r = client.post(
        "/demo/plan/role",
        json={"session": sid, "plan_key": key, "role": "venue"},
    )
    assert r.status_code == 200
    out = r.json()

    assert out["changed"] is True

    plan = next(i for i in out["trip"]["items"] if i["kind"] == "plan")
    assert plan["detail"]["place_role"] == "venue"

    codes = [n["code"] for n in plan["detail"]["planning"]["needs"]]
    assert codes == [
        "overnight_stay_unknown",
        "arrival_origin_unknown",
    ]

    # Answering the clarification does not invent a booking.
    assert out["trip"]["bookings"] == []

    again = client.post(
        "/demo/plan/role",
        json={"session": sid, "plan_key": key, "role": "venue"},
    ).json()
    assert again["changed"] is False
