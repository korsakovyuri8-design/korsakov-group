"""Atlas web sessions backed by one shared SQL database.

The browser receives a random bearer token. Atlas never persists that token
verbatim: only its SHA-256 hash is stored.

A SessionStore may be destroyed and recreated against the same database and
still recover the Trip belonging to a valid token. In-memory objects are only
a process-local cache; durable truth lives in SQL.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.atlas_web.cleanup import purge_anonymous_trip
from app.db.models import AtlasWebSession
from app.db.session import create_schema, make_engine, make_session_factory
from app.journey import ingest
from app.trip import atlas_demo


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class DemoSession:
    token: str
    session_id: str
    db: Session
    trip_id: str
    world: dict[str, Any]
    touched: float
    clock_mode: str = "live"
    client_timezone: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def close(self) -> None:
        # The Engine belongs to SessionStore, not to one browser session.
        self.db.close()


class SessionStore:
    """Durable session registry with a small process-local object cache."""

    def __init__(
        self,
        *,
        ttl_seconds: int = 45 * 60,
        max_sessions: int = 200,
        clock: Callable[[], float] = time.time,
        database_url: str = "sqlite://",
    ) -> None:
        self.ttl = ttl_seconds
        self.max = max_sessions
        self.clock = clock

        self.engine = make_engine(database_url)
        create_schema(self.engine)
        self.factory = make_session_factory(self.engine)

        self._sessions: OrderedDict[str, DemoSession] = OrderedDict()
        self._lock = threading.Lock()

        # World data belongs to the shared database, not to each traveller.
        with self.factory() as db:
            self.world = atlas_demo.build_world(db)
            db.commit()

    def __len__(self) -> int:
        return len(self._sessions)

    def create(
        self,
        *,
        clock_mode: str = "live",
        client_timezone: str | None = None,
    ) -> DemoSession:
        token = secrets.token_urlsafe(24)
        now = self.clock()

        db = self.factory()

        try:
            trip = ingest.create_trip(db, title="Atlas demo trip")

            row = AtlasWebSession(
                token_hash=_token_hash(token),
                trip_id=trip.id,
                clock_mode=clock_mode,
                client_timezone=client_timezone,
                touched_at_epoch=now,
            )

            db.add(row)
            db.commit()

            session = DemoSession(
                token=token,
                session_id=row.id,
                db=db,
                trip_id=trip.id,
                world=self.world,
                touched=now,
                clock_mode=clock_mode,
                client_timezone=client_timezone,
            )

        except Exception:
            db.rollback()
            db.close()
            raise

        with self._lock:
            self._sweep_locked(now)

            # Capacity limits only the in-process cache. Evicted sessions
            # remain durable and can be rehydrated by token later.
            while len(self._sessions) >= self.max:
                _, old = self._sessions.popitem(last=False)
                old.close()

            self._sessions[token] = session

        return session

    def get(self, token: str | None) -> DemoSession | None:
        if not token:
            return None

        now = self.clock()

        with self._lock:
            self._sweep_locked(now)

            cached = self._sessions.get(token)

            if cached is not None:
                # DemoSession.db is shared by all requests for this browser
                # session. Never touch it outside the same per-session lock
                # that protects endpoint work.
                with cached.lock:
                    row = cached.db.get(
                        AtlasWebSession,
                        cached.session_id,
                    )

                    if row is None or row.ended_at is not None:
                        self._sessions.pop(token, None)
                        cached.close()
                        return None

                    if now - row.touched_at_epoch > self.ttl:
                        row.ended_at = _utcnow()
                        cached.db.commit()
                        self._sessions.pop(token, None)
                        cached.close()
                        return None

                    row.touched_at_epoch = now
                    cached.touched = now
                    cached.db.commit()

                self._sessions.move_to_end(token)
                return cached

            db = self.factory()

            try:
                row = db.scalar(
                    select(AtlasWebSession).where(
                        AtlasWebSession.token_hash
                        == _token_hash(token),
                        AtlasWebSession.ended_at.is_(None),
                    )
                )

                if row is None:
                    db.close()
                    return None

                if now - row.touched_at_epoch > self.ttl:
                    row.ended_at = _utcnow()
                    db.commit()
                    db.close()
                    return None

                row.touched_at_epoch = now
                db.commit()

                session = DemoSession(
                    token=token,
                    session_id=row.id,
                    db=db,
                    trip_id=row.trip_id,
                    world=self.world,
                    touched=now,
                    clock_mode=row.clock_mode,
                    client_timezone=row.client_timezone,
                )

            except Exception:
                db.rollback()
                db.close()
                raise

            while len(self._sessions) >= self.max:
                _, old = self._sessions.popitem(last=False)
                old.close()

            self._sessions[token] = session
            return session

    def set_client_timezone(
        self,
        session: DemoSession,
        value: str | None,
    ) -> None:
        with session.lock:
            row = session.db.get(
                AtlasWebSession,
                session.session_id,
            )

            if row is None or row.ended_at is not None:
                return

            row.client_timezone = value
            session.client_timezone = value
            session.db.commit()

    def drop(self, token: str | None) -> bool:
        if not token:
            return False

        with self._lock:
            cached = self._sessions.pop(token, None)

            if cached is not None:
                with cached.lock:
                    row = cached.db.get(
                        AtlasWebSession,
                        cached.session_id,
                    )

                    found = row is not None

                    if row is not None:
                        purge_anonymous_trip(
                            cached.db,
                            cached.trip_id,
                        )
                        cached.db.commit()

                    cached.close()

                return found

            with self.factory() as db:
                row = db.scalar(
                    select(AtlasWebSession).where(
                        AtlasWebSession.token_hash
                        == _token_hash(token)
                    )
                )

                if row is None:
                    return False

                purge_anonymous_trip(
                    db,
                    row.trip_id,
                )
                db.commit()

                return True

    def close(self) -> None:
        """Close process resources without ending durable browser sessions."""
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()

        for session in sessions:
            with session.lock:
                session.close()

        self.engine.dispose()

    def _sweep_locked(self, now: float) -> None:
        expired = [
            token
            for token, session in self._sessions.items()
            if now - session.touched > self.ttl
        ]

        for token in expired:
            session = self._sessions.pop(token)

            with session.lock:
                row = session.db.get(
                    AtlasWebSession,
                    session.session_id,
                )

                if row is not None:
                    purge_anonymous_trip(
                        session.db,
                        session.trip_id,
                    )
                    session.db.commit()

                session.close()
