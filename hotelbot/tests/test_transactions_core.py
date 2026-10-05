"""Transaction layer internals: consent, slot parsing, provider adapters,
the PostgreSQL-compatible job queue and signed provider callbacks."""

from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app.clock import FrozenClock
from app.db.models import Job, JobStatus
from app.db.session import create_schema, make_engine, make_session_factory
from app.jobs.queue import PermanentJobError, RetryableJobError, RetryPolicy, JobWorker, enqueue
from app.transactions.callbacks import sign
from app.transactions.catalog import SERVICE_CATALOG
from app.transactions.consent import ConsentDecision, classify, classify_selection, mentioned_code
from app.transactions.providers.base import (
    ProviderAuthError,
    ProviderInvalidRequest,
    ProviderTimeout,
    ProviderUnavailable,
    QuoteRequest,
    SubmitRequest,
)
from app.transactions.providers.mock import MockExternalProvider
from app.transactions.providers.webhook import WebhookExternalProvider
from app.transactions.slots import extract, parse_count, parse_when
from app.text import fold

T0 = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ consent
@pytest.mark.parametrize("text", ["yes", "Yes, book it", "da, rezervišite", "да, бронируйте", "yes ABCD"])
def test_consent_explicit(text):
    assert classify(text, "ABCD", False) == ConsentDecision.CONSENT


@pytest.mark.parametrize("text", ["ok", "ok maybe", "sounds good?", "👍", "yes but cheaper?", "maybe yes"])
def test_consent_not_consent(text):
    assert classify(text, "ABCD", False) != ConsentDecision.CONSENT


def test_changed_details_are_never_consent():
    assert classify("yes", "ABCD", True) == ConsentDecision.MODIFY


def test_selection_consent_rules():
    refs = ["ABCD", "EFGH", "transfer", "skis", "guide"]
    assert classify_selection("book the transfer and the skis", refs) == (ConsentDecision.CONSENT, False)
    assert classify_selection("book all", refs) == (ConsentDecision.CONSENT, True)
    assert classify_selection("забронируй все", refs) == (ConsentDecision.CONSENT, True)
    assert classify_selection("ok", refs)[0] == ConsentDecision.NONE            # "ok" is not consent
    assert classify_selection("book the skis but cheaper", refs)[0] == ConsentDecision.NONE
    assert classify_selection("cancel only the guide", refs)[0] == ConsentDecision.DECLINE
    assert mentioned_code("book EFGH please") == "EFGH" and mentioned_code("book it") is None


# -------------------------------------------------------------------- slots
def test_parse_when_weekdays_dayparts_and_meridiem():
    monday = date(2027, 1, 11)
    def when(text):
        return parse_when(fold(text), monday)

    assert when("Friday at 8pm").day == date(2027, 1, 15) and when("Friday at 8pm").time.hour == 20
    assert when("Saturday morning").time.hour == 9
    assert when("в воскресенье в 10:00").day == date(2027, 1, 17)
    assert when("u petak").day == date(2027, 1, 15)


def test_parse_count_multilingual():
    assert parse_count(fold("there are four of us")) == 4
    assert parse_count(fold("нас четверо")) == 4
    assert parse_count(fold("for 2 people")) == 2


def test_extract_never_invents_missing_values():
    ex = extract("I need a transfer", SERVICE_CATALOG["airport_transfer"], date(2026, 10, 5))
    assert ex.values == {} and ex.when == {}
    guide = extract("Find a Russian-speaking guide for Sunday", SERVICE_CATALOG["guide_booking"], date(2027, 1, 11))
    assert guide.values == {"language": "ru"}
    assert guide.when["start_time"].time is None                 # a day without a time stays pending


# --------------------------------------------------------------- providers
def _mock(**behavior) -> MockExternalProvider:
    return MockExternalProvider("demo", {"currency": "EUR", "pricing": {"base": 30, "per_extra_person": 5},
                                         "validity_minutes": 15, **behavior}, FrozenClock(T0))


def _submit(key: str = "txn-1") -> SubmitRequest:
    return SubmitRequest(idempotency_key=key, service_type="taxi", details={"party_size": 2}, quote_reference=None,
                         amount=Decimal("35.00"), currency="EUR", customer_reference="stay-1")


def test_mock_provider_prices_and_dedupes_by_idempotency_key():
    p = _mock()
    q = p.request_quote(QuoteRequest("taxi", {"party_size": 3}))
    assert q.amount == Decimal("40.00") and q.valid_until == T0 + timedelta(minutes=15)
    first, again = p.submit(_submit()), p.submit(_submit())
    assert first.reference == again.reference and p.active_bookings == 1


@pytest.mark.parametrize("behavior, error", [
    ("timeout", ProviderTimeout), ("unavailable", ProviderUnavailable),
    ("invalid", ProviderInvalidRequest), ("auth", ProviderAuthError),
])
def test_mock_provider_failure_modes(behavior, error):
    with pytest.raises(error):
        _mock(behavior={"submit": behavior}).submit(_submit())


def test_error_classification_retryable_vs_permanent():
    assert ProviderTimeout("x").retryable and ProviderUnavailable("x").retryable
    assert not ProviderInvalidRequest("x").retryable and not ProviderAuthError("x").retryable


class _Recorder:
    def __init__(self, responder):
        self.requests: list[httpx.Request] = []
        self.responder = responder

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responder(request)


def _webhook(responder, **config) -> tuple[WebhookExternalProvider, _Recorder]:
    rec = _Recorder(responder)
    cfg = {"base_url": "https://provider.test/api", "secret_env": "TEST_PROVIDER_SECRET", **config}
    return WebhookExternalProvider("p", cfg, FrozenClock(T0), httpx.Client(transport=httpx.MockTransport(rec))), rec


def test_webhook_contract_signs_and_sends_idempotency_key(monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_SECRET", "s3cret")
    p, rec = _webhook(lambda r: httpx.Response(202, json={"status": "received", "reference": "R-1"}))
    out = p.submit(_submit("txn-42"))
    assert out.status == "received" and out.reference == "R-1"
    req = rec.requests[0]
    assert req.url.path == "/api/bookings" and req.headers["Idempotency-Key"] == "txn-42"
    ts = req.headers["X-HotelBot-Timestamp"]
    assert req.headers["X-HotelBot-Signature"] == sign("s3cret", ts, req.content)
    assert "s3cret" not in req.content.decode()


def test_webhook_quote_parsing(monkeypatch):
    monkeypatch.setenv("TEST_PROVIDER_SECRET", "s3cret")
    p, _ = _webhook(lambda r: httpx.Response(200, json={
        "amount": "44.5", "currency": "eur", "description": "Transfer", "valid_until": "2026-10-05T12:15:00+00:00"}))
    q = p.request_quote(QuoteRequest("airport_transfer", {"party_size": 2}))
    assert q.amount == Decimal("44.50") and q.currency == "EUR"


@pytest.mark.parametrize("response, error", [
    (lambda r: httpx.Response(500), ProviderUnavailable),
    (lambda r: httpx.Response(429), ProviderUnavailable),
    (lambda r: httpx.Response(400), ProviderInvalidRequest),
    (lambda r: httpx.Response(401), ProviderAuthError),
    (lambda r: httpx.Response(200, text="<html>"), ProviderUnavailable),
    (lambda r: httpx.Response(200, json={"status": "maybe"}), ProviderUnavailable),
])
def test_webhook_error_classification(monkeypatch, response, error):
    monkeypatch.setenv("TEST_PROVIDER_SECRET", "s3cret")
    p, _ = _webhook(response)
    with pytest.raises(error):
        p.submit(_submit())


def test_webhook_timeout_and_missing_secret(monkeypatch):
    def boom(request):
        raise httpx.ReadTimeout("slow", request=request)

    monkeypatch.setenv("TEST_PROVIDER_SECRET", "s3cret")
    with pytest.raises(ProviderTimeout):
        _webhook(boom)[0].submit(_submit())
    monkeypatch.delenv("TEST_PROVIDER_SECRET")
    with pytest.raises(ProviderAuthError):
        _webhook(lambda r: httpx.Response(200, json={}))[0].submit(_submit())


# ---------------------------------------------------------------- job queue
@pytest.fixture
def queue(tmp_path):
    # A file database (or PostgreSQL): real per-session transactions.
    url = os.environ.get("HOTELBOT_TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'queue.db'}"
    engine = make_engine(url)
    if not url.startswith("sqlite"):
        from sqlalchemy import MetaData

        md = MetaData()
        md.reflect(engine)
        md.drop_all(engine)
    create_schema(engine)
    factory = make_session_factory(engine)
    clock = FrozenClock(T0)
    yield factory, clock
    engine.dispose()


def _add(factory, clock, kind="k", key="key-1", **kw) -> str:
    with factory() as s:
        job = enqueue(s, kind, {"n": 1}, key, clock=clock, **kw)
        s.commit()
        return job.id


def test_enqueue_is_idempotent(queue):
    factory, clock = queue
    assert _add(factory, clock) == _add(factory, clock)
    with factory() as s:
        assert len(list(s.scalars(select(Job)))) == 1


def test_retry_backoff_then_success(queue):
    factory, clock = queue
    calls = []

    def flaky(session, job):
        calls.append(clock.now())
        if len(calls) < 3:
            raise RetryableJobError("provider timeout")

    worker = JobWorker(factory, clock, RetryPolicy(base_seconds=5, max_seconds=300))
    worker.register("k", flaky)
    job_id = _add(factory, clock)
    assert worker.run_once() == 1
    with factory() as s:
        job = s.get(Job, job_id)
        assert job.status == JobStatus.PENDING and job.attempts == 1 and "timeout" in job.last_error
    assert worker.run_once() == 0                       # not due yet
    worker.drain(advance_clock=True)
    assert [round((c - T0).total_seconds()) for c in calls] == [0, 5, 15]   # 5 s, then 10 s backoff
    with factory() as s:
        assert s.get(Job, job_id).status == JobStatus.SUCCEEDED


def test_backoff_is_bounded():
    policy = RetryPolicy(base_seconds=5, max_seconds=300)
    assert policy.delay(1) == timedelta(seconds=5)
    assert policy.delay(20) == timedelta(seconds=300)


def test_permanent_error_goes_dead_and_runs_hook(queue):
    factory, clock = queue
    dead = []
    worker = JobWorker(factory, clock)

    def bad(session, job):
        raise PermanentJobError("invalid request")

    worker.register("k", bad, on_dead=lambda s, job, err: dead.append((job.id, err)))
    job_id = _add(factory, clock)
    worker.drain(advance_clock=True)
    with factory() as s:
        job = s.get(Job, job_id)
        assert job.status == JobStatus.DEAD and job.attempts == 1
    assert dead == [(job_id, "invalid request")]


def test_max_attempts_then_dead(queue):
    factory, clock = queue
    worker = JobWorker(factory, clock)

    def always(session, job):
        raise RetryableJobError("down")

    worker.register("k", always)
    job_id = _add(factory, clock, max_attempts=3)
    worker.drain(advance_clock=True)
    with factory() as s:
        job = s.get(Job, job_id)
        assert job.status == JobStatus.DEAD and job.attempts == 3


def test_handler_side_effects_roll_back_on_failure(queue):
    factory, clock = queue
    worker = JobWorker(factory, clock)

    def half_done(session, job):
        enqueue(session, "other", {}, "side-effect", clock=clock)
        raise RetryableJobError("fail after writing")

    worker.register("k", half_done)
    _add(factory, clock)
    worker.run_once()
    with factory() as s:
        assert s.scalar(select(Job).where(Job.idempotency_key == "side-effect")) is None


def test_crashed_worker_lease_is_reclaimed(queue):
    factory, clock = queue
    job_id = _add(factory, clock)
    crashed = JobWorker(factory, clock, worker_id="crashed")
    assert crashed._claim(10) == [job_id]             # claimed, never processed
    rescuer = JobWorker(factory, clock, worker_id="rescuer")
    rescuer.register("k", lambda s, j: None)
    assert rescuer.run_once() == 0                    # lease still valid
    clock.advance(minutes=6)
    assert rescuer.run_once() == 1
    with factory() as s:
        assert s.get(Job, job_id).status == JobStatus.SUCCEEDED


@pytest.mark.skipif(not os.environ.get("HOTELBOT_TEST_DATABASE_URL", "").startswith("postgresql"),
                    reason="needs PostgreSQL (FOR UPDATE SKIP LOCKED)")
def test_concurrent_workers_never_run_a_job_twice(queue):
    factory, clock = queue
    for i in range(40):
        _add(factory, clock, key=f"k-{i}")
    seen: list[str] = []
    lock = threading.Lock()

    def handler(session, job):
        with lock:
            seen.append(job.id)

    workers = [JobWorker(factory, clock, worker_id=f"w{i}") for i in range(4)]
    for w in workers:
        w.register("k", handler)
    threads = [threading.Thread(target=lambda w=w: [w.run_once(limit=5) for _ in range(10)]) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(seen) == 40 and len(set(seen)) == 40


# ---------------------------------------------------------------- callbacks
def test_callback_signature_and_replay_window(client, container, chat, monkeypatch):
    monkeypatch.setenv("DEMO_TRANSFERS_CALLBACK_SECRET", "cb-secret")
    url = "/api/providers/example-hotel/demo-transfers/callbacks"
    body = json.dumps({"event_id": "e1", "reference": "nope", "event": "accepted"}).encode()
    now = int(container.clock.now().timestamp())
    bad = client.post(url, content=body, headers={"X-Provider-Timestamp": str(now),
                                                  "X-Provider-Signature": sign("wrong", now, body)})
    assert bad.status_code == 401
    stale = now - 3600
    old = client.post(url, content=body, headers={"X-Provider-Timestamp": str(stale),
                                                  "X-Provider-Signature": sign("cb-secret", stale, body)})
    assert old.status_code == 401
    ok = client.post(url, content=body, headers={"X-Provider-Timestamp": str(now),
                                                 "X-Provider-Signature": sign("cb-secret", now, body)})
    assert ok.status_code == 404 and ok.json()["status"] == "unknown_reference"
    dup = client.post(url, content=body, headers={"X-Provider-Timestamp": str(now),
                                                  "X-Provider-Signature": sign("cb-secret", now, body)})
    assert dup.status_code == 200 and dup.json()["status"] == "duplicate"
