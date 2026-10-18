from __future__ import annotations

import hashlib

from sqlalchemy import select

from app.atlas_web.sessions import SessionStore
from app.db.models import AtlasWebSession, Trip


class Clock:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


def test_session_rehydrates_after_store_restart(tmp_path):
    clock = Clock()
    path = tmp_path / "atlas.db"
    url = f"sqlite:///{path}"

    first = SessionStore(
        database_url=url,
        clock=clock,
    )

    created = first.create(
        clock_mode="live",
        client_timezone="Europe/Podgorica",
    )

    token = created.token
    trip_id = created.trip_id

    first.close()

    second = SessionStore(
        database_url=url,
        clock=clock,
    )

    restored = second.get(token)

    assert restored is not None
    assert restored.trip_id == trip_id
    assert restored.client_timezone == "Europe/Podgorica"
    assert restored.db.get(Trip, trip_id) is not None

    row = restored.db.scalar(
        select(AtlasWebSession).where(
            AtlasWebSession.trip_id == trip_id
        )
    )

    assert row is not None
    assert row.token_hash == hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()

    assert row.token_hash != token

    second.close()


def test_ttl_survives_store_restart(tmp_path):
    clock = Clock()
    path = tmp_path / "atlas.db"
    url = f"sqlite:///{path}"

    first = SessionStore(
        database_url=url,
        ttl_seconds=45 * 60,
        clock=clock,
    )

    token = first.create().token
    first.close()

    clock.t += 45 * 60 + 1

    second = SessionStore(
        database_url=url,
        ttl_seconds=45 * 60,
        clock=clock,
    )

    assert second.get(token) is None

    second.close()


def test_default_app_store_uses_configured_database_url(
    monkeypatch,
):
    from types import SimpleNamespace

    from app.atlas_web import app as atlas_app

    seen = {}

    class FakeStore:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(
        atlas_app,
        "SessionStore",
        FakeStore,
    )
    monkeypatch.setattr(
        atlas_app,
        "get_settings",
        lambda: SimpleNamespace(
            database_url="sqlite:///configured-atlas.db",
        ),
    )
    monkeypatch.setattr(
        atlas_app,
        "_page_template",
        lambda: "",
    )

    atlas_app.create_app()

    assert seen == {
        "database_url": "sqlite:///configured-atlas.db",
    }
