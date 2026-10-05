from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.observability import log_event

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
CORE_V1_REVISION = "0001_core_v1"


def make_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite"):
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in database_url or database_url == "sqlite://":
            kwargs["poolclass"] = StaticPool  # share one in-memory DB across threads
        return create_engine(database_url, **kwargs)
    return create_engine(database_url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def alembic_config(connection: Connection | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


@lru_cache(maxsize=1)
def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(alembic_config())


def head_revision() -> str:
    head = _script().get_current_head()
    assert head is not None
    return head


def create_schema(engine: Engine, auto_migrate: bool = True) -> None:
    """Bring the database to the current schema.

    * empty database  -> create tables from the models, stamp Alembic head
    * existing schema -> `alembic upgrade head` (Core v1 databases, which have
                         no version table, are first stamped at 0001)
    """
    tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    if not tables:
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            MigrationContext.configure(conn).stamp(_script(), "head")
        return
    if auto_migrate:
        migrate(engine)


def migrate(engine: Engine, revision: str = "head") -> None:
    with engine.begin() as conn:
        context = MigrationContext.configure(conn)
        current = context.get_current_revision()
        if current is None and "hotels" in inspect(conn).get_table_names():
            log_event("migration_stamp_core_v1")
            command.stamp(alembic_config(conn), CORE_V1_REVISION)
            current = CORE_V1_REVISION
        if current != head_revision() or revision != "head":
            log_event("migration_upgrade", from_revision=current, to_revision=revision)
            command.upgrade(alembic_config(conn), revision)


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
