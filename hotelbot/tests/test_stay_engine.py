"""Property / Stay / Capability behaviour through the real orchestrator."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.capabilities.registry import CapabilitySpec, legacy_capabilities
from app.container import build_container
from app.db.models import Action, ActionStatus, Conversation, Guest, Property, PropertyType, Stay, StayStatus
from app.knowledge.ingest import load_pack
from app.schemas.messages import InboundMessage
from tests.conftest import ROOT, make_settings

APARTMENT = str(ROOT / "data" / "properties" / "demo_apartment.yaml")


def _container(response=None, exc=None, **settings):
    def handler(request):
        if exc:
            raise exc
        return response or httpx.Response(200, json={"status": "accepted", "reference": "TR-1"})

    return build_container(make_settings(extra_pack_paths=[APARTMENT], **settings), llm=None,
                           http_client=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.fixture
def multi():
    return _container()


def _say(c, text, guest="g", prop=None, channel="demo"):
    return c.orchestrator.handle(InboundMessage(channel=channel, sender_id=guest, text=text, property_slug=prop))


def _all(c, model):
    with c.session_factory() as s:
        return list(s.scalars(select(model)))


# ------------------------------------------------------------- properties
def test_properties_are_generic(multi):
    props = {p.slug: p for p in _all(multi, Property)}
    assert props["example-hotel"].property_type == PropertyType.HOTEL
    assert props["demo-apartment"].property_type == PropertyType.VACATION_RENTAL
    assert props["demo-apartment"].timezone == "Europe/Podgorica"


def test_v1_pack_format_still_loads_with_legacy_capabilities(tmp_path):
    v1 = tmp_path / "v1.yaml"
    v1.write_text(
        "pack: {hotel_slug: old, hotel_name: Old Hotel, source: s}\n"
        "items: [{key: wifi, category: a, content: {en: Free wifi.}}]\n", encoding="utf-8")
    pack = load_pack(v1)
    assert pack.pack.property_slug == "old" and pack.pack.hotel_name == "Old Hotel"
    assert pack.capabilities is None
    c = build_container(make_settings(knowledge_path=str(v1), hotel_slug="old"), llm=None)
    registry = c.properties.get().capabilities
    assert registry.spec == legacy_capabilities()


def test_core_does_not_hardcode_the_emergency_number(tmp_path):
    pack = tmp_path / "p.yaml"
    pack.write_text("pack: {property_slug: us, property_name: US Inn, source: s}\n"
                    "items: [{key: wifi, category: a, content: {en: Free wifi.}}]\n", encoding="utf-8")
    c = build_container(make_settings(knowledge_path=str(pack), hotel_slug="us"), llm=None)
    reply = _say(c, "Fire! Help!")
    assert "112" not in reply.text and "local emergency number" in reply.text


# ----------------------------------------------------------- capabilities
@pytest.mark.parametrize("spec,error", [
    ({"actions": {"teleport": {}}, "integrations": ["human_staff"]}, "unknown action types"),
    ({"actions": {"transport_booking": {"executor": "webhook"}}}, "requires config.url"),
    ({"actions": {"housekeeping_request": {}}}, "human_staff"),
    ({"integrations": ["carrier_pigeon"]}, "unknown integrations"),
    ({"actions": {"housekeeping_request": {"executor": "magic"}}, "integrations": ["human_staff"]}, "unknown executor"),
])
def test_capability_spec_validation(spec, error):
    with pytest.raises(ValidationError, match=error):
        CapabilitySpec.model_validate(spec)


def test_registry_describes_available_and_unavailable(multi):
    desc = multi.properties.get("demo-apartment").capabilities.describe()
    assert desc["actions"]["transport_booking"] == "webhook"
    assert "housekeeping_request" in desc["unavailable_actions"]
    assert "wifi" in desc["knowledge"]


def test_unavailable_capability_is_explained_not_faked(multi):
    reply = _say(multi, "Book me a table for tonight", prop="demo-apartment")
    assert reply.actions == [] and "can't arrange" in reply.text
    assert _all(multi, Action) == []


def test_same_request_is_actioned_where_capability_exists(multi):
    reply = _say(multi, "Book me a table for tonight", guest="h")
    assert [a.detail["action_type"] for a in reply.actions] == ["restaurant_booking"]


def test_integration_acceptance_is_reported_as_accepted_not_completed(multi):
    reply = _say(multi, "Can you book me a taxi to the airport tomorrow at 6?", prop="demo-apartment")
    [action] = _all(multi, Action)
    assert action.status == ActionStatus.ACCEPTED and action.external_ref == "TR-1"
    assert "accepted" in reply.text and "completed" not in reply.text


@pytest.mark.parametrize("exc,response", [(httpx.ReadTimeout("slow"), None), (None, httpx.Response(503))])
def test_integration_failure_falls_back_to_staff_honestly(exc, response):
    c = _container(response=response, exc=exc)
    reply = _say(c, "Can you book me a taxi to the airport tomorrow at 6?", prop="demo-apartment")
    statuses = sorted((a.executor, a.status.value) for a in _all(c, Action))
    assert statuses == [("staff", "submitted"), ("webhook", "failed")]
    assert "couldn't submit" in reply.text and "not confirmed" in reply.text


def test_per_property_failure_policy(multi):
    # The apartment escalates after ONE failed local recommendation.
    reply = _say(multi, "What do you recommend to visit nearby?", prop="demo-apartment")
    assert reply.handed_off
    # The hotel uses the default of two.
    assert not _say(multi, "Do you have a sauna?", guest="h2").handed_off


# ------------------------------------------------------------------- stays
def test_one_stay_per_guest_and_property(multi):
    _say(multi, "Hi")
    _say(multi, "Hi", prop="demo-apartment")
    _say(multi, "Breakfast?")
    stays = _all(multi, Stay)
    assert len(stays) == 2 and {s.status for s in stays} == {StayStatus.INQUIRY}
    assert len(_all(multi, Guest)) == 1
    convs = _all(multi, Conversation)
    assert {c.stay_id for c in convs} == {s.id for s in stays}


def test_stay_facts_do_not_leak_across_properties(multi):
    _say(multi, "We are 2 adults arriving 20 December", prop="demo-apartment")
    _say(multi, "Do you have parking?")
    by_prop = {s.property_id: s for s in _all(multi, Stay)}
    hotel_id = multi.properties.get().property_id
    apartment_id = multi.properties.get("demo-apartment").property_id
    assert by_prop[apartment_id].facts["guest_count"]["value"] == 2
    assert by_prop[hotel_id].facts == {}


def test_finished_stay_starts_fresh(multi):
    _say(multi, "We are 3 people")
    with multi.session_factory() as s:
        stay = s.scalars(select(Stay)).one()
        stay.status = StayStatus.CHECKED_OUT
        s.commit()
    _say(multi, "Hello again")
    stays = sorted(_all(multi, Stay), key=lambda s: s.created_at)
    assert len(stays) == 2 and stays[1].facts == {}


def test_stale_verified_departure_starts_fresh(multi):
    _say(multi, "We are 3 people")
    with multi.session_factory() as s:
        stay = s.scalars(select(Stay)).one()
        stay.departure_at = datetime.now(timezone.utc) - timedelta(days=10)
        s.commit()
    _say(multi, "Hello again")
    assert len(_all(multi, Stay)) == 2


def test_guest_statements_never_become_authoritative_stay_data(multi):
    _say(multi, "We are 2 adults, arriving 20.12. and leaving 23.12.")
    [stay] = _all(multi, Stay)
    assert stay.facts["guest_count"] == {**stay.facts["guest_count"], "confirmed": False, "source": "guest_stated"}
    assert stay.party_size is None and stay.arrival_at is None and stay.status == StayStatus.INQUIRY


def test_language_preference_is_global_and_reused_for_new_stay(multi):
    _say(multi, "Kada je doručak?")
    reply = _say(multi, "ok", prop="demo-apartment")  # new stay, no language signal
    assert reply.language == "cnr"
    [guest] = _all(multi, Guest)
    assert guest.preferences["language"]["value"] == "cnr"


def test_whatsapp_number_routes_to_property(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.test_api_whatsapp import _payload, _post

    pack = load_pack(APARTMENT).model_dump(mode="json", by_alias=False)
    pack["pack"]["whatsapp_phone_number_id"] = "PNID"
    import yaml
    path = tmp_path / "apt.yaml"
    path.write_text(yaml.safe_dump(pack, allow_unicode=True), encoding="utf-8")
    c = build_container(make_settings(extra_pack_paths=[str(path)]), llm=None)
    with TestClient(create_app(container=c)) as client:
        _post(client, _payload("Is there parking?"))  # payload metadata.phone_number_id == "PNID"
    assert "no private parking" in c.dev_outbox.outbox()[0]["text"]
