import hashlib
import hmac
import json

import httpx
import pytest
from sqlalchemy import select

from app.actions.executors import StaffQueueExecutor, WebhookExecutor, default_executors
from app.actions.service import ALLOWED_TRANSITIONS, ActionService, InvalidTransition
from app.db.models import Action, ActionEvent, ActionStatus, Property
from app.db.session import create_schema, make_engine, make_session_factory

S = ActionStatus


@pytest.fixture
def session():
    engine = make_engine("sqlite://")
    create_schema(engine)
    with make_session_factory(engine)() as s:
        s.add(Property(id="p1", slug="p", name="P"))
        s.flush()
        yield s


def _service(session, client=None):
    return ActionService(session, default_executors(client))


def _propose(service, executor="staff", action_type="late_checkout_request"):
    return service.propose(property_id="p1", action_type=action_type, summary="Late checkout", executor=executor)


def _events(session, action):
    return [(e.from_status, e.to_status, e.actor) for e in
            session.scalars(select(ActionEvent).where(ActionEvent.action_id == action.id).order_by(ActionEvent.seq))]


def test_staff_executor_submits_and_records_history(session):
    service = _service(session)
    action = service.submit(_propose(service))
    assert action.status == S.SUBMITTED
    assert _events(session, action) == [(None, S.PROPOSED, "agent"), (S.PROPOSED, S.SUBMITTED, "executor:staff")]


def test_full_lifecycle_records_every_step(session):
    service = _service(session)
    action = service.submit(_propose(service))
    service.transition(action, S.ACCEPTED, "staff:ana", "ok until 13:00")
    service.transition(action, S.IN_PROGRESS, "staff:ana")
    service.transition(action, S.COMPLETED, "staff:ana")
    assert [e[1] for e in _events(session, action)] == [S.PROPOSED, S.SUBMITTED, S.ACCEPTED, S.IN_PROGRESS, S.COMPLETED]
    assert action.closed_at is not None and action.note == "ok until 13:00"


@pytest.mark.parametrize("frm,to", [
    (S.PROPOSED, S.ACCEPTED),       # cannot be accepted before anyone received it
    (S.PROPOSED, S.COMPLETED),
    (S.COMPLETED, S.SUBMITTED),     # terminal states are final
    (S.REJECTED, S.ACCEPTED),
    (S.FAILED, S.COMPLETED),
    (S.CANCELLED, S.IN_PROGRESS),
    (S.ACCEPTED, S.SUBMITTED),      # no going backwards
    (S.ACCEPTED, S.REJECTED),
])
def test_invalid_transitions_are_refused(session, frm, to):
    service = _service(session)
    action = _propose(service)
    action.status = frm
    with pytest.raises(InvalidTransition):
        service.transition(action, to, "staff")
    assert action.status == frm


def test_terminal_states_have_no_exits():
    for terminal in (S.COMPLETED, S.REJECTED, S.FAILED, S.CANCELLED):
        assert terminal not in ALLOWED_TRANSITIONS


def test_only_proposed_actions_can_be_submitted(session):
    service = _service(session)
    action = service.submit(_propose(service))
    with pytest.raises(InvalidTransition):
        service.submit(action)


def _webhook(response=None, exc=None, capture=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture["request"] = request
        if exc:
            raise exc
        return response

    return httpx.Client(transport=httpx.MockTransport(handler))


CONFIG = {"url": "https://partner.test/actions"}


@pytest.mark.parametrize(
    "response,expected,ref",
    [
        (httpx.Response(200, json={"status": "accepted", "reference": "T-1"}), [S.SUBMITTED, S.ACCEPTED], "T-1"),
        (httpx.Response(202, json={"status": "received"}), [S.SUBMITTED], None),
        (httpx.Response(200, json={"status": "rejected", "message": "fully booked"}), [S.SUBMITTED, S.REJECTED], None),
    ],
)
def test_webhook_outcomes(session, response, expected, ref):
    service = _service(session, _webhook(response))
    action = service.submit(_propose(service, "webhook", "transport_booking"), CONFIG)
    assert [e[1] for e in _events(session, action)][1:] == expected
    assert action.external_ref == ref


@pytest.mark.parametrize(
    "response,exc,error",
    [
        (None, httpx.ReadTimeout("slow"), "timeout"),
        (None, httpx.ConnectError("down"), "transport error: ConnectError"),
        (httpx.Response(500), None, "HTTP 500"),
        (httpx.Response(200, content=b"<html>"), None, "malformed response"),
        (httpx.Response(200, json={"status": "maybe"}), None, "malformed response"),
    ],
)
def test_webhook_failures_never_look_like_success(session, response, exc, error):
    service = _service(session, _webhook(response, exc))
    action = service.submit(_propose(service, "webhook", "transport_booking"), CONFIG)
    assert action.status == S.FAILED and action.error == error
    assert _events(session, action)[-1][:2] == (S.PROPOSED, S.FAILED)


def test_webhook_signs_body_when_secret_configured(session, monkeypatch):
    monkeypatch.setenv("PARTNER_SECRET", "s3cret")
    capture: dict = {}
    service = _service(session, _webhook(httpx.Response(200, json={"status": "received"}), capture=capture))
    service.submit(_propose(service, "webhook", "transport_booking"), {**CONFIG, "secret_env": "PARTNER_SECRET"})
    req = capture["request"]
    expected = "sha256=" + hmac.new(b"s3cret", req.content, hashlib.sha256).hexdigest()
    assert req.headers["X-HotelBot-Signature"] == expected
    assert json.loads(req.content)["action_type"] == "transport_booking"


def test_unknown_executor_and_crashing_executor_fail_safely(session):
    service = _service(session)
    action = service.submit(_propose(service, "carrier-pigeon"))
    assert action.status == S.FAILED and "unknown executor" in action.error

    class Boom(StaffQueueExecutor):
        def submit(self, action, config):
            raise RuntimeError("bug")

    from app.actions.executors import ExecutorRegistry
    service = ActionService(session, ExecutorRegistry([Boom()]))
    action = service.submit(_propose(service))
    assert action.status == S.FAILED and action.error == "executor error: RuntimeError"


def test_webhook_without_url_fails(session):
    action = Action(id="a", action_type="transport_booking", summary="x", params={}, property_id="p1")
    assert WebhookExecutor().submit(action, {}).status == S.FAILED
