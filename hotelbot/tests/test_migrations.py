"""Migration safety: Alembic head == models, and Core v1 data survives the
Stay Engine upgrade. Runs on SQLite and (HOTELBOT_TEST_DATABASE_URL) PostgreSQL."""

from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext

from app.db.models import Base
from app.db.session import alembic_config, create_schema, head_revision, make_engine
from tests.conftest import TEST_DATABASE_URL

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def engine(tmp_path):
    url = TEST_DATABASE_URL if not TEST_DATABASE_URL.startswith("sqlite") else f"sqlite:///{tmp_path}/m.db"
    eng = make_engine(url)
    with eng.begin() as conn:  # clean slate
        meta = sa.MetaData()
        meta.reflect(conn)
        meta.drop_all(conn)
    yield eng
    eng.dispose()


def _upgrade(engine, rev):
    with engine.begin() as conn:
        command.upgrade(alembic_config(conn), rev)


def _diff(engine):
    def ignore_enum_vs_string(context, inspected_column, metadata_column, inspected_type, metadata_type):
        if isinstance(metadata_type, sa.Enum) and isinstance(inspected_type, sa.String):
            return False  # non-native enums are VARCHAR(32) in the database
        return None

    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": ignore_enum_vs_string})
        return compare_metadata(ctx, Base.metadata)


def test_migrations_produce_the_model_schema(engine):
    _upgrade(engine, "head")
    assert _diff(engine) == []


def test_fresh_database_is_created_and_stamped(engine):
    create_schema(engine)
    with engine.connect() as conn:
        assert MigrationContext.configure(conn).get_current_revision() == head_revision()
    assert _diff(engine) == []


def _seed_v1(conn):
    t = lambda name, *cols: sa.table(name, *(sa.column(c) for c in cols))  # noqa: E731
    conn.execute(t("hotels", "id", "slug", "name", "is_synthetic", "created_at").insert().values(
        id="h1", slug="example-hotel", name="Demo", is_synthetic=True, created_at=NOW))
    conn.execute(sa.table("hotel_knowledge_documents", *(sa.column(c) for c in (
        "id", "hotel_id", "item_key", "category", "source", "content", "keywords", "metadata", "content_hash",
        "updated_at")), sa.column("content", sa.JSON), sa.column("keywords", sa.JSON),
        sa.column("metadata", sa.JSON)).insert().values(
        id="d1", hotel_id="h1", item_key="wifi", category="amenities", source="s", content={"en": "Free"},
        keywords={}, metadata={}, content_hash="x", updated_at=NOW))
    conn.execute(t("guests", "id", "channel", "external_id", "display_name", "created_at").insert().values(
        id="g1", channel="whatsapp", external_id="38267000001", display_name="Ana", created_at=NOW))
    memory = {"guest_count": {"value": 2, "source": "guest_stated", "confirmed": False},
              "_state": {"pending_offer": {"kind": "ask_staff", "question": "sauna?"}}}
    conn.execute(sa.table("conversations", *(sa.column(c) for c in (
        "id", "hotel_id", "guest_id", "channel", "language", "status", "consecutive_failures", "created_at",
        "updated_at")), sa.column("memory", sa.JSON)).insert().values(
        id="c1", hotel_id="h1", guest_id="g1", channel="whatsapp", language="cnr", status="active",
        memory=memory, consecutive_failures=0, created_at=NOW, updated_at=NOW))
    conn.execute(sa.table("messages", *(sa.column(c) for c in (
        "id", "seq", "conversation_id", "role", "text", "language", "external_id", "created_at")),
        sa.column("metadata", sa.JSON)).insert().values(
        id="m1", seq=1, conversation_id="c1", role="guest", text="Možemo li poslije 23h?", language="cnr",
        external_id="wamid.1", metadata={}, created_at=NOW))
    reqs = sa.table("hotel_requests", *(sa.column(c) for c in (
        "id", "conversation_id", "request_type", "status", "urgency", "summary", "resolution_note", "created_at",
        "resolved_at")), sa.column("details", sa.JSON))
    conn.execute(reqs.insert().values(
        id="r1", conversation_id="c1", request_type="late_check_in", status="resolved", urgency="normal",
        summary="Late check in", details={"guest_message": "Možemo li poslije 23h?"}, resolution_note="ok",
        created_at=NOW, resolved_at=NOW))
    conn.execute(reqs.insert().values(
        id="r2", conversation_id="c1", request_type="housekeeping", status="pending", urgency="normal",
        summary="Towels", details={}, created_at=NOW))
    conn.execute(sa.table("human_handoffs", *(sa.column(c) for c in (
        "id", "conversation_id", "reason", "urgency", "status", "created_at")), sa.column("package", sa.JSON))
        .insert().values(id="hf1", conversation_id="c1", reason="complaint", urgency="high", status="open",
                         package={"guest_id": "38267000001"}, created_at=NOW))


def _rows(conn, sql):
    return [dict(r) for r in conn.execute(sa.text(sql)).mappings()]


def _check_migrated(engine):
    with engine.connect() as conn:
        [prop] = _rows(conn, "select * from properties")
        assert (prop["id"], prop["slug"], prop["property_type"]) == ("h1", "example-hotel", "hotel")
        [doc] = _rows(conn, "select property_id, item_key from knowledge_documents")
        assert doc == {"property_id": "h1", "item_key": "wifi"}
        [stay] = _rows(conn, "select * from stays")
        assert (stay["guest_id"], stay["property_id"], stay["status"]) == ("g1", "h1", "inquiry")
        facts = stay["facts"] if isinstance(stay["facts"], dict) else __import__("json").loads(stay["facts"])
        assert facts["guest_count"]["value"] == 2 and "_state" not in facts
        [conv] = _rows(conn, "select * from conversations")
        memory = conv["memory"] if isinstance(conv["memory"], dict) else __import__("json").loads(conv["memory"])
        assert conv["property_id"] == "h1" and conv["stay_id"] == stay["id"]
        assert list(memory) == ["_state"]
        actions = {a["id"]: a for a in _rows(conn, "select * from actions")}
        assert actions["r1"]["action_type"] == "late_arrival_request" and actions["r1"]["status"] == "completed"
        assert actions["r1"]["note"] == "ok" and actions["r1"]["stay_id"] == stay["id"]
        assert actions["r2"]["action_type"] == "housekeeping_request" and actions["r2"]["status"] == "submitted"
        events = _rows(conn, "select action_id, from_status, to_status from action_events order by action_id, seq")
        assert [(e["action_id"], e["from_status"], e["to_status"]) for e in events] == [
            ("r1", None, "submitted"), ("r1", "submitted", "completed"), ("r2", None, "submitted")]
        assert _rows(conn, "select id, message_count from (select conversation_id as id, count(*) as message_count "
                           "from messages group by conversation_id) x") == [{"id": "c1", "message_count": 1}]
        assert len(_rows(conn, "select * from human_handoffs")) == 1
        tables = set(sa.inspect(conn).get_table_names())
        assert not tables & {"hotels", "hotel_requests", "hotel_knowledge_documents"}


def test_core_v1_data_survives_upgrade(engine):
    _upgrade(engine, "0001_core_v1")
    with engine.begin() as conn:
        _seed_v1(conn)
    _upgrade(engine, "head")
    _check_migrated(engine)
    assert _diff(engine) == []


def test_unversioned_core_v1_database_is_detected_and_migrated_on_startup(engine):
    """Core v1 created tables with create_all() - no alembic_version table."""
    _upgrade(engine, "0001_core_v1")
    with engine.begin() as conn:
        conn.execute(sa.text("drop table alembic_version"))
        _seed_v1(conn)
    create_schema(engine)  # what the app does at startup
    _check_migrated(engine)


def test_upgraded_v1_database_serves_the_app(engine, tmp_path):
    from app.container import build_container
    from app.schemas.messages import InboundMessage
    from tests.conftest import make_settings

    _upgrade(engine, "0001_core_v1")
    with engine.begin() as conn:
        _seed_v1(conn)
    url = engine.url.render_as_string(hide_password=False)
    engine.dispose()
    c = build_container(make_settings(database_url=url), llm=None)
    reply = c.orchestrator.handle(InboundMessage(channel="whatsapp", sender_id="38267000001",
                                                 text="Is my request confirmed?"))
    # The migrated guest keeps their stay; the latest migrated action answers.
    assert reply.intent == "REQUEST_STATUS" and reply.text


def test_transaction_data_survives_local_travel_upgrade(engine):
    """0003 -> 0004: provider rows keep their property scope and gain an empty region."""
    _upgrade(engine, "0001_core_v1")
    with engine.begin() as conn:
        _seed_v1(conn)
    _upgrade(engine, "0003_transactions")
    with engine.begin() as conn:
        [prop] = _rows(conn, "select id from properties")
        conn.execute(sa.text(
            "insert into external_providers (id, property_id, slug, name, provider_type, integration_type, active,"
            " services, config, created_at) values ('p1', :pid, 'demo-transfers', 'Demo', 'transport', 'mock',"
            " :active, :services, :config, :now)"),
            {"pid": prop["id"], "active": True, "services": '["taxi"]', "config": "{}", "now": NOW})
    _upgrade(engine, "head")
    with engine.connect() as conn:
        [row] = _rows(conn, "select id, property_id, slug, region from external_providers")
        assert row == {"id": "p1", "property_id": prop["id"], "slug": "demo-transfers", "region": None}
        tables = set(sa.inspect(conn).get_table_names())
        assert {"places", "events", "offerings", "availability", "itinerary_items"} <= tables
    assert _diff(engine) == []


def test_marketplace_upgrade_keeps_providers_and_offerings(engine):
    """0004 -> 0005: existing providers/offerings gain empty marketplace fields."""
    _upgrade(engine, "0004_local_travel")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "insert into external_providers (id, property_id, region, slug, name, provider_type, integration_type,"
            " active, services, config, created_at) values ('p1', null, 'r1', 'skis', 'Skis', 'ski_rental', 'mock',"
            " :t, :services, :config, :now)"), {"t": True, "services": '{"service_types": ["ski_rental"]}',
                                                 "config": "{}", "now": NOW})
        conn.execute(sa.text(
            "insert into offerings (id, region, slug, service_type, title, provider_id, attributes, active, source,"
            " confidence, provider_owned, is_synthetic) values ('o1', 'r1', 'set', 'ski_rental', :title, 'p1',"
            " :attrs, :t, 'test', 1.0, :f, :t)"), {"title": '{"en": "Ski set"}', "attrs": "{}", "t": True, "f": False})
    _upgrade(engine, "head")
    with engine.connect() as conn:
        [prov] = _rows(conn, "select slug, profile, policies, commission_type from external_providers")
        [off] = _rows(conn, "select slug, pricing, policies, guest_price from offerings")
        assert prov["slug"] == "skis" and prov["commission_type"] is None
        assert off["slug"] == "set" and off["guest_price"] is None
        assert {"inventory_holds", "property_providers"} <= set(sa.inspect(conn).get_table_names())
    assert _diff(engine) == []


def test_local_world_upgrade_keeps_places_and_events(engine):
    """0006 -> 0007: places/events gain provenance and per-fact verification;
    traveller preferences get their own table; downgrade is clean."""
    _upgrade(engine, "0006_layers")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "insert into places (id, region, slug, name, category, subcategory, description, tags, attributes, hours,"
            " active, source, confidence, provider_owned, is_synthetic, updated_at) values ('pl1', 'r1', 'cafe',"
            " 'Cafe', 'FOOD', 'cafe', :j, :j, :j, :j, :t, 'test', 1.0, :f, :t, :now)"),
            {"j": "{}", "t": True, "f": False, "now": NOW})
        conn.execute(sa.text(
            "insert into events (id, region, slug, title, category, tags, start_at, ticket_required, attributes,"
            " source, confidence, provider_owned, is_synthetic) values ('e1', 'r1', 'gig', :title,"
            " 'concert', :j, :now, :f, :j, 'test', 1.0, :f, :t)"), {"title": '{"en": "Gig"}', "j": "{}", "now": NOW, "t": True,
                                                     "f": False})
    _upgrade(engine, "0007_local_world")
    with engine.connect() as conn:
        [place] = _rows(conn, "select slug, source_id, timezone, price_range from places")
        [event] = _rows(conn, "select slug, active, latitude from events")
        assert place["slug"] == "cafe" and place["source_id"] is None and place["price_range"] is None
        assert event["slug"] == "gig" and event["latitude"] is None
        assert "traveler_preferences" in sa.inspect(conn).get_table_names()
    assert _diff(engine) == []
    with engine.begin() as conn:
        command.downgrade(alembic_config(conn), "0006_layers")
    with engine.connect() as conn:
        assert "traveler_preferences" not in sa.inspect(conn).get_table_names()
        assert _rows(conn, "select slug from places")[0]["slug"] == "cafe"
