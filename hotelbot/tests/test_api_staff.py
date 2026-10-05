from fastapi.testclient import TestClient

from app.container import build_container
from app.main import create_app
from app.schemas.messages import InboundMessage
from tests.conftest import make_settings

S = "/api/staff"


def _chat(client, message, guest="demo-001"):
    resp = client.post("/api/chat", json={"guest_id": guest, "message": message})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_chat_endpoint_runs_the_agent(client):
    body = _chat(client, "What time is breakfast?")
    assert body["intent"] == "HOTEL_INFORMATION" and "07:30" in body["text"]
    assert body["knowledge_synthetic"] is True


def test_chat_endpoint_validates_input(client):
    assert client.post("/api/chat", json={"guest_id": "", "message": "hi"}).status_code == 422
    assert client.post("/api/chat", json={"guest_id": "x"}).status_code == 422


def test_staff_api_requires_token(client):
    assert client.get(f"{S}/conversations").status_code == 401
    assert client.get(f"{S}/conversations", headers={"X-Staff-Token": "nope"}).status_code == 401


def test_staff_api_closed_outside_dev_without_token(transport):
    container = build_container(make_settings(staff_api_token=None, env="prod"), llm=None, whatsapp=transport)
    with TestClient(create_app(container=container)) as c:
        assert c.get(f"{S}/conversations").status_code == 503


def test_list_and_view_conversations(client, staff_headers):
    _chat(client, "Hi")
    _chat(client, "Kada je doručak?")
    [conv] = client.get(f"{S}/conversations", headers=staff_headers).json()
    assert conv["guest_external_id"] == "demo-001" and conv["language"] == "cnr"
    detail = client.get(f"{S}/conversations/{conv['id']}", headers=staff_headers).json()
    assert [m["role"] for m in detail["messages"]] == ["guest", "bot", "guest", "bot"]
    assert client.get(f"{S}/conversations/missing", headers=staff_headers).status_code == 404


def test_request_queue_and_resolution_with_guest_reply(client, staff_headers, container):
    _chat(client, "Can we check in after 11pm?")
    [req] = client.get(f"{S}/requests", headers=staff_headers).json()
    assert req["request_type"] == "late_check_in" and req["status"] == "pending"

    resolved = client.post(f"{S}/requests/{req['id']}/resolve", headers=staff_headers,
                           json={"note": "Night porter informed", "reply_to_guest": "Confirmed, see you at 23:30!"})
    assert resolved.status_code == 200 and resolved.json()["status"] == "resolved"
    assert client.get(f"{S}/requests", headers=staff_headers).json() == []
    assert container.dev_outbox.outbox("demo-001")[-1]["text"] == "Confirmed, see you at 23:30!"

    conv_id = req["conversation_id"]
    roles = [m["role"] for m in client.get(f"{S}/conversations/{conv_id}", headers=staff_headers).json()["messages"]]
    assert roles[-1] == "staff"


def test_handoff_lifecycle(client, staff_headers):
    _chat(client, "I want to talk to a real person")
    [handoff] = client.get(f"{S}/handoffs", headers=staff_headers).json()
    assert handoff["status"] == "open" and handoff["package"]["reason"] == "human_requested"

    # Bot is silent while staff own the conversation; staff can reply.
    assert _chat(client, "Hello?")["text"] is None
    sent = client.post(f"{S}/conversations/{handoff['conversation_id']}/messages", headers=staff_headers,
                       json={"text": "Hi, this is Marko from reception.", "staff_name": "Marko"})
    assert sent.status_code == 201 and sent.json()["role"] == "staff"

    accepted = client.post(f"{S}/handoffs/{handoff['id']}/accept", headers=staff_headers).json()
    assert accepted["status"] == "accepted"
    client.post(f"{S}/handoffs/{handoff['id']}/resolve", headers=staff_headers, json={})
    assert client.get(f"{S}/handoffs", params={"handoff_status": "open"}, headers=staff_headers).json() == []

    # Bot is back.
    assert "07:30" in _chat(client, "What time is breakfast?")["text"]


def test_closing_conversation_starts_a_new_one(client, staff_headers):
    _chat(client, "I want to talk to a human")
    [handoff] = client.get(f"{S}/handoffs", headers=staff_headers).json()
    client.post(f"{S}/handoffs/{handoff['id']}/resolve", headers=staff_headers, json={"close_conversation": True})
    _chat(client, "Hi again")
    assert len(client.get(f"{S}/conversations", headers=staff_headers).json()) == 2


def test_staff_message_to_whatsapp_guest_uses_whatsapp_transport(client, staff_headers, container, transport):
    reply = container.orchestrator.handle(
        InboundMessage(channel="whatsapp", sender_id="38267000009", text="I want to talk to a real person",
                       external_id="w9")
    )
    client.post(f"{S}/conversations/{reply.conversation_id}/messages", headers=staff_headers, json={"text": "Hello!"})
    assert transport.outbox("38267000009")[-1]["text"] == "Hello!"


def test_demo_endpoints_can_be_disabled(transport):
    container = build_container(make_settings(enable_demo_endpoints=False), llm=None, whatsapp=transport)
    with TestClient(create_app(container=container)) as c:
        assert c.post("/api/chat", json={"guest_id": "x", "message": "hi"}).status_code == 404


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
