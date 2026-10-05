import hashlib
import hmac
import json

import httpx
import pytest

from app.whatsapp.meta import MetaWhatsAppTransport
from tests.conftest import APP_SECRET, VERIFY_TOKEN

URL = "/webhooks/whatsapp"


def _payload(text: str | None = "What time is breakfast?", wamid: str = "wamid.A1", sender: str = "38267000001",
             msg_type: str = "text") -> dict:
    message = {"from": sender, "id": wamid, "timestamp": "1700000000", "type": msg_type}
    if msg_type == "text":
        message["text"] = {"body": text}
    else:
        message[msg_type] = {"id": "media-1"}
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "38220000000", "phone_number_id": "PNID"},
            "contacts": [{"profile": {"name": "Ana"}, "wa_id": sender}],
            "messages": [message],
        }}]}],
    }


def _post(client, payload, secret=APP_SECRET, signature=None):
    body = json.dumps(payload).encode()
    sig = signature or "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(URL, content=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})


def test_verification_handshake(client):
    ok = client.get(URL, params={"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "12345"})
    assert ok.status_code == 200 and ok.text == "12345"
    bad = client.get(URL, params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "12345"})
    assert bad.status_code == 403
    assert client.get(URL).status_code == 403


def test_inbound_text_message_is_answered_via_transport(client, transport):
    resp = _post(client, _payload())
    assert resp.status_code == 200 and resp.json()["messages"] == 1
    [sent] = transport.outbox()
    assert sent["to"] == "38267000001" and "07:30" in sent["text"]


def test_montenegrin_message_answered_in_montenegrin(client, transport):
    _post(client, _payload("Da li imate parking?"))
    assert "parking" in transport.outbox()[0]["text"].lower()
    assert "Besplatan" in transport.outbox()[0]["text"]


def test_meta_retry_is_answered_once(client, transport):
    _post(client, _payload(wamid="wamid.SAME"))
    _post(client, _payload(wamid="wamid.SAME"))
    assert len(transport.outbox()) == 1


def test_invalid_signature_rejected(client, transport):
    assert _post(client, _payload(), signature="sha256=deadbeef").status_code == 401
    assert _post(client, _payload(), secret="other-secret").status_code == 401
    body = json.dumps(_payload()).encode()
    assert client.post(URL, content=body).status_code == 401  # missing header
    assert transport.outbox() == []


@pytest.mark.parametrize("payload", [{"no": "object"}, {"object": "page", "entry": []}])
def test_malformed_payload_rejected(client, payload):
    assert _post(client, payload).status_code == 400


def test_non_json_body_rejected(client):
    body = b"not json"
    sig = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert client.post(URL, content=body, headers={"X-Hub-Signature-256": sig}).status_code == 400


def test_status_callbacks_are_acknowledged_and_ignored(client, transport):
    payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "statuses": [{"id": "wamid.X", "status": "delivered"}]}}]}]}
    resp = _post(client, payload)
    assert resp.status_code == 200 and resp.json()["messages"] == 0
    assert transport.outbox() == []


def test_non_text_message_gets_polite_reply(client, transport):
    _post(client, _payload(text=None, msg_type="image"))
    assert "text messages" in transport.outbox()[0]["text"]


def test_handed_off_conversation_gets_no_bot_reply(client, transport):
    _post(client, _payload("I want to talk to a human", wamid="w1"))
    _post(client, _payload("Hello? Anyone?", wamid="w2"))
    assert len(transport.outbox()) == 1


def test_meta_transport_wire_format():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"messages": [{"id": "wamid.OUT"}]})

    t = MetaWhatsAppTransport("TOKEN", "PNID", "v21.0", 5, client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = t.send_text("38267000001", "Zdravo")
    assert result.ok and result.message_id == "wamid.OUT"
    assert seen["url"] == "https://graph.facebook.com/v21.0/PNID/messages"
    assert seen["auth"] == "Bearer TOKEN"
    assert seen["body"]["to"] == "38267000001" and seen["body"]["text"]["body"] == "Zdravo"


def test_meta_transport_reports_failures():
    t = MetaWhatsAppTransport("T", "P", "v21.0", 5,
                              client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401))))
    result = t.send_text("1", "x")
    assert not result.ok and result.error == "HTTP 401"
