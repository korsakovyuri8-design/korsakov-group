"""Durable job queue / outbox on the application database (no Redis).

* `enqueue()` is called inside the same DB transaction as the state change
  that needs follow-up work, so the work is committed atomically with it
  (transactional outbox). An idempotency key makes enqueueing safe to repeat.
* Workers claim due jobs with `SELECT ... FOR UPDATE SKIP LOCKED` (PostgreSQL)
  so several workers never process the same job. On SQLite (tests, single
  process) the lock clause is a no-op.
* A claimed job holds a lease; if a worker dies mid-job, the lease expires
  and another worker retries it - an acknowledged request is never lost.
* Failures: retryable -> bounded exponential backoff; permanent or out of
  attempts -> DEAD, with an `on_dead` hook so the domain can react (e.g.
  mark the transaction FAILED and alert staff).
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.clock import Clock, FrozenClock, as_utc
from app.db.models import Job, JobStatus
from app.observability import log_event


class RetryableJobError(Exception):
    """Transient failure: try again later."""


class PermanentJobError(Exception):
    """Will never succeed as is: stop retrying."""


@dataclass(frozen=True)
class RetryPolicy:
    base_seconds: float = 5.0
    max_seconds: float = 300.0

    def delay(self, attempts: int) -> timedelta:
        return timedelta(seconds=min(self.base_seconds * 2 ** max(attempts - 1, 0), self.max_seconds))


DEFAULT_MAX_ATTEMPTS = {"provider_submit": 5, "provider_cancel": 5, "notify_guest": 6, "send_message": 6,
                        "process_inbound": 3}
LEASE = timedelta(minutes=5)

Handler = Callable[[Session, Job], None]
DeadHook = Callable[[Session, Job, str], None]


def enqueue(session: Session, kind: str, payload: dict[str, Any], idempotency_key: str, *, clock: Clock,
            delay: timedelta | None = None, max_attempts: int | None = None) -> Job:
    """Add a job unless one with this idempotency key already exists."""
    existing = session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
    if existing is not None:
        return existing
    job = Job(kind=kind, payload=payload, idempotency_key=idempotency_key, status=JobStatus.PENDING,
              attempts=0, max_attempts=max_attempts or DEFAULT_MAX_ATTEMPTS.get(kind, 5),
              next_attempt_at=clock.now() + (delay or timedelta()), created_at=clock.now())
    try:
        with session.begin_nested():
            session.add(job)
            session.flush()
    except IntegrityError:  # concurrent enqueue of the same key
        existing = session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
        assert existing is not None
        return existing
    log_event("job_enqueued", job_id=job.id, kind=kind, key=idempotency_key)
    return job


class JobWorker:
    def __init__(self, session_factory: sessionmaker[Session], clock: Clock,
                 retry: RetryPolicy | None = None, worker_id: str | None = None) -> None:
        self.session_factory = session_factory
        self.clock = clock
        self.retry = retry or RetryPolicy()
        self.worker_id = worker_id or f"w-{uuid.uuid4().hex[:8]}"
        self.handlers: dict[str, Handler] = {}
        self.dead_hooks: dict[str, DeadHook] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()  # one run_once at a time per worker object

    def register(self, kind: str, handler: Handler, on_dead: DeadHook | None = None) -> None:
        self.handlers[kind] = handler
        if on_dead:
            self.dead_hooks[kind] = on_dead

    # ------------------------------------------------------------------ claim
    def _claim(self, limit: int) -> list[str]:
        now = self.clock.now()
        with self.session_factory() as s, s.begin():
            q = (
                select(Job)
                .where(or_(
                    and_(Job.status == JobStatus.PENDING, Job.next_attempt_at <= now),
                    and_(Job.status == JobStatus.RUNNING, Job.locked_at <= now - LEASE),   # crashed worker
                ))
                .order_by(Job.next_attempt_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            jobs = list(s.scalars(q))
            for job in jobs:
                job.status = JobStatus.RUNNING
                job.locked_by = self.worker_id
                job.locked_at = now
                job.attempts += 1
            return [j.id for j in jobs]

    # ---------------------------------------------------------------- process
    def _process(self, job_id: str) -> None:
        error: str | None = None
        permanent = False
        with self.session_factory() as s:
            job = s.get(Job, job_id)
            if job is None or job.status != JobStatus.RUNNING or job.locked_by != self.worker_id:
                return
            handler = self.handlers.get(job.kind)
            try:
                if handler is None:
                    raise PermanentJobError(f"no handler for job kind {job.kind!r}")
                handler(s, job)
                job.status, job.completed_at, job.last_error = JobStatus.SUCCEEDED, self.clock.now(), None
                s.commit()
                log_event("job_succeeded", job_id=job.id, kind=job.kind, attempts=job.attempts)
                return
            except PermanentJobError as exc:
                error, permanent = str(exc) or type(exc).__name__, True
            except RetryableJobError as exc:
                error = str(exc) or type(exc).__name__
            except Exception as exc:  # unknown failure: retry, it may be transient
                error = f"{type(exc).__name__}: {exc}"[:500]
                logging.getLogger("hotelbot").exception("job %s failed", job_id)
            s.rollback()

        with self.session_factory() as s:
            job = s.get(Job, job_id)
            assert job is not None
            job.last_error = error
            job.locked_by = job.locked_at = None
            if permanent or job.attempts >= job.max_attempts:
                job.status = JobStatus.DEAD
                job.completed_at = self.clock.now()
                hook = self.dead_hooks.get(job.kind)
                if hook:
                    hook(s, job, error or "failed")
                log_event("job_dead", job_id=job.id, kind=job.kind, attempts=job.attempts, error=error,
                          permanent=permanent)
            else:
                job.status = JobStatus.PENDING
                job.next_attempt_at = self.clock.now() + self.retry.delay(job.attempts)
                log_event("job_retry_scheduled", job_id=job.id, kind=job.kind, attempts=job.attempts,
                          next_attempt_at=job.next_attempt_at, error=error)
            s.commit()

    # -------------------------------------------------------------------- run
    def run_once(self, limit: int = 20) -> int:
        with self._lock:
            ids = self._claim(limit)
            for job_id in ids:
                self._process(job_id)
            return len(ids)

    def drain(self, *, advance_clock: bool = False, max_rounds: int = 200) -> int:
        """Process until nothing is due. With a FrozenClock and advance_clock,
        time jumps to the next scheduled retry (deterministic tests)."""
        total = 0
        for _ in range(max_rounds):
            n = self.run_once()
            total += n
            if n:
                continue
            if not (advance_clock and isinstance(self.clock, FrozenClock)):
                break
            with self.session_factory() as s:
                nxt = s.scalar(select(Job.next_attempt_at).where(Job.status == JobStatus.PENDING)
                               .order_by(Job.next_attempt_at).limit(1))
            if nxt is None:
                break
            self.clock.set(max(as_utc(nxt), self.clock.now()))
        return total

    def start(self, interval: float = 1.0) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    if not self.run_once():
                        self._stop.wait(interval)
                except Exception:  # keep the worker alive
                    logging.getLogger("hotelbot").exception("worker loop error")
                    self._stop.wait(interval)

        self._thread = threading.Thread(target=loop, name=f"hotelbot-worker-{self.worker_id}", daemon=True)
        self._thread.start()
        log_event("worker_started", worker_id=self.worker_id)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
