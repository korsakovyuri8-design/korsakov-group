"""Time source. Quote expiry, retry backoff and callback replay windows all
depend on "now", so it is injected: SystemClock in production, FrozenClock
in tests and the evaluation harness."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FrozenClock:
    def __init__(self, start: datetime | None = None) -> None:
        self._now = as_utc(start) if start else datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, **delta: float) -> datetime:
        self._now += timedelta(**delta)
        return self._now

    def set(self, value: datetime) -> None:
        self._now = as_utc(value)


def as_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes; everything we store is UTC."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
