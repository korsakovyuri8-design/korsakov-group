"""Stay Engine staff API: actions lifecycle with state-derived guest
notifications, stays verification, properties/capabilities."""

S = "/api/staff"


def _chat(client, message, guest="demo-001", prop=None):
    body = {"guest_id": guest, "message": message}
    if prop:
        body["property"] = prop
    resp = client.post("/api/chat", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_action_transition_notifies_guest_with_state_template(client, staff_headers, container):
    _chat(client, "Can I stay until 3pm?")
    [action] = client.get(f"{S}/actions", headers=staff_headers).json()
    assert action["action_type"] == "late_checkout_request" and action["status"] == "submitted"

    accepted = client.post(f"{S}/actions/{action['id']}/transition", headers=staff_headers,
                           json={"to": "accepted", "staff_name": "Ana", "note": "until 13:00"}).json()
    assert accepted["action"]["status"] == "accepted"
    assert accepted["guest_notification"] == "Your request (late check-out) has been accepted by Demo Mountain Hotel (synthetic data)."
    assert container.dev_outbox.outbox("demo-001")[-1]["text"] == accepted["guest_notification"]
    assert [e["to_status"] for e in accepted["action"]["events"]] == ["proposed", "submitted", "accepted"]
    assert accepted["action"]["events"][-1]["actor"] == "staff:Ana"

    # The guest asking again gets the stored state, not a guess.
    assert "has been accepted" in _chat(client, "Is my late checkout confirmed?")["text"]

    done = client.post(f"{S}/actions/{action['id']}/transition", headers=staff_headers,
                       json={"to": "completed"}).json()
    assert done["guest_notification"] == "Your request (late check-out) has been completed."


def test_invalid_transition_is_409_and_changes_nothing(client, staff_headers):
    _chat(client, "Can I stay until 3pm?")
    [action] = client.get(f"{S}/actions", headers=staff_headers).json()
    client.post(f"{S}/actions/{action['id']}/transition", headers=staff_headers, json={"to": "completed"})
    resp = client.post(f"{S}/actions/{action['id']}/transition", headers=staff_headers, json={"to": "accepted"})
    assert resp.status_code == 409
    assert client.get(f"{S}/actions/{action['id']}", headers=staff_headers).json()["status"] == "completed"


def test_transition_without_notification(client, staff_headers, container):
    _chat(client, "Can I stay until 3pm?")
    [action] = client.get(f"{S}/actions", headers=staff_headers).json()
    before = len(container.dev_outbox.outbox())
    out = client.post(f"{S}/actions/{action['id']}/transition", headers=staff_headers,
                      json={"to": "in_progress", "notify_guest": False}).json()
    assert out["guest_notification"] is None and len(container.dev_outbox.outbox()) == before


def test_v1_requests_view_tracks_actions(client, staff_headers):
    _chat(client, "Can I stay until 3pm?")
    [action] = client.get(f"{S}/actions", headers=staff_headers).json()
    client.post(f"{S}/actions/{action['id']}/transition", headers=staff_headers, json={"to": "accepted"})
    assert client.get(f"{S}/requests", headers=staff_headers).json() == []
    [req] = client.get(f"{S}/requests", params={"request_status": "in_progress"}, headers=staff_headers).json()
    assert req["request_type"] == "late_check_out" and req["status"] == "in_progress"


def test_staff_verifies_stay_without_touching_guest_facts(client, staff_headers):
    _chat(client, "We are 2 adults arriving 20 December")
    [stay] = client.get(f"{S}/stays", headers=staff_headers).json()
    assert stay["status"] == "inquiry" and stay["party_size"] is None
    assert stay["facts"]["guest_count"]["confirmed"] is False
    updated = client.patch(f"{S}/stays/{stay['id']}", headers=staff_headers, json={
        "status": "booked", "booking_reference": "BK-123", "party_size": 3,
        "arrival_at": "2026-12-20T15:00:00+01:00"}).json()
    assert (updated["status"], updated["booking_reference"], updated["party_size"]) == ("booked", "BK-123", 3)
    assert updated["facts"]["guest_count"]["value"] == 2  # guest statement kept, separately
    assert client.patch(f"{S}/stays/{stay['id']}", headers=staff_headers, json={"party_size": 0}).status_code == 422


def test_properties_endpoint_exposes_capabilities(client, staff_headers):
    [prop] = client.get(f"{S}/properties", headers=staff_headers).json()
    assert prop["property_type"] == "hotel" and prop["is_synthetic"] is True
    assert "late_arrival_request" in prop["capabilities"]["actions"]
    assert "breakfast" in prop["capabilities"]["knowledge"]


def test_chat_unknown_property_is_404(client):
    resp = client.post("/api/chat", json={"guest_id": "x", "message": "hi", "property": "nope"})
    assert resp.status_code == 404


def test_conversation_view_links_stay(client, staff_headers):
    body = _chat(client, "Hi")
    [conv] = client.get(f"{S}/conversations", headers=staff_headers).json()
    assert conv["stay_id"] == body["stay_id"] and conv["property_id"]
