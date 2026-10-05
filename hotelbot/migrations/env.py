"""Alembic environment.

Uses a connection handed over by app.db.session (config.attributes
["connection"]) when the app migrates itself on startup, otherwise connects
to HOTELBOT_DATABASE_URL (`alembic upgrade head` from the CLI).
"""

from __future__ import annotations

from alembic import context

from app.db.models import Base

config = context.config
target_metadata = Base.metadata


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite needs table rebuilds for ALTERs
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    from app.config import get_settings
    from app.db.session import make_engine

    engine = make_engine(get_settings().database_url)
    with engine.begin() as conn:
        _run(conn)


if context.is_offline_mode():
    raise SystemExit("offline (SQL script) migrations are not supported; run against a database")
run_migrations_online()
