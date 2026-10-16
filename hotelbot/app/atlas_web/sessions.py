"""Ephemeral demo sessions: one isolated in-memory trip per visitor.

Nothing is persisted. A session is a random token (no personal data) mapped
to its own in-memory database holding the demo world and one trip. It ends
on reset, after `ttl_seconds` without use, or when the store is full (the
least recently used goes first) - and with it everything imported."""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import create_schema, make_engine
from app.journey import ingest
from app.trip import atlas_demo


@dataclass
class DemoSession:
    token: str
    engine: Engine
    db: Session
    trip_id: str
    world: dict[str, Any]
    touched: float
    lock: threading.Lock = field(default_factory=threading.Lock)

    def close(self) -> None:
        self.db.close()
        self.engine.dispose()


class SessionStore:
    def __init__(self, *, ttl_seconds: int = 45 * 60, max_sessions: int = 200,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl, self.max, self.clock = ttl_seconds, max_sessions, clock
        self._sessions: OrderedDict[str, DemoSession] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._sessions)

    def create(self) -> DemoSession:
        engine = make_engine("sqlite://")
        create_schema(engine)
        db = sessionmaker(engine)()
        world = atlas_demo.build_world(db)
        trip = ingest.create_trip(db, title="Atlas demo trip")
        db.commit()
        s = DemoSession(secrets.token_urlsafe(24), engine, db, trip.id, world, self.clock())
        with self._lock:
            self._sweep()
            while len(self._sessions) >= self.max:
                self._sessions.popitem(last=False)[1].close()
            self._sessions[s.token] = s
        return s

    def get(self, token: str | None) -> DemoSession | None:
        if not token:
            return None
        with self._lock:
            self._sweep()
            s = self._sessions.get(token)
            if s is not None:
                s.touched = self.clock()
                self._sessions.move_to_end(token)
            return s

    def drop(self, token: str | None) -> bool:
        with self._lock:
            s = self._sessions.pop(token or "", None)
        if s is not None:
            s.close()
        return s is not None

    def _sweep(self) -> None:
        now = self.clock()
        for token in [t for t, s in self._sessions.items() if now - s.touched > self.ttl]:
            self._sessions.pop(token).close()
