"""Stay Engine: properties, stays, generic actions.

  hotels                     -> properties (+ type, timezone, language, capabilities...)
  hotel_knowledge_documents  -> knowledge_documents (property_id)
  guests                     +  preferences
  (new)                         stays; one per existing conversation, receiving the
                                guest-stated facts previously kept in conversation.memory
  conversations              hotel_id -> property_id, + stay_id
  hotel_requests             -> actions (+ action_events audit trail)
                                pending->submitted, in_progress->in_progress,
                                resolved->completed, rejected->rejected

Forward-only: the downgrade is intentionally not provided because merging
stays and actions back into the v1 shape loses information. Back up the
database (pg_dump) before upgrading production data.

Revision ID: 0002_stay_engine
Revises: 0001_core_v1
"""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0002_stay_engine"
down_revision = "0001_core_v1"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
ENUM = sa.String(32)

# Typed column helpers for reading existing rows: untyped columns come back as
# raw strings on SQLite (datetimes, JSON) and would be re-encoded wrongly.
_TYPES = {"created_at": TS, "updated_at": TS, "resolved_at": TS, "is_synthetic": sa.Boolean(),
          "content": sa.JSON(), "keywords": sa.JSON(), "metadata": sa.JSON(), "memory": sa.JSON(),
          "details": sa.JSON()}


def _table(name, *cols):
    return sa.table(name, *(sa.column(c, _TYPES.get(c)) for c in cols))

REQUEST_TO_ACTION_TYPE = {
    "late_check_in": "late_arrival_request",
    "early_check_in": "early_checkin_request",
    "late_check_out": "late_checkout_request",
    "housekeeping": "housekeeping_request",
    "maintenance": "maintenance_request",
    "restaurant": "restaurant_booking",
    "transport": "transport_booking",
    "booking": "booking_inquiry",
    "other": "staff_question",
}
REQUEST_TO_ACTION_STATUS = {
    "pending": "submitted",
    "in_progress": "in_progress",
    "resolved": "completed",
    "rejected": "rejected",
}


def _now():
    return datetime.now(timezone.utc)


def upgrade() -> None:
    bind = op.get_bind()

    # ---------------------------------------------------------- properties
    properties = op.create_table(
        "properties",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("property_type", ENUM, nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("default_language", sa.String(16), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    hotels = _table("hotels", "id", "slug", "name", "is_synthetic", "created_at")
    rows = bind.execute(sa.select(hotels)).mappings().all()
    if rows:
        op.bulk_insert(properties, [
            {"id": r["id"], "slug": r["slug"], "name": r["name"], "property_type": "hotel", "timezone": "UTC",
             "default_language": "en", "active": True, "is_synthetic": bool(r["is_synthetic"]),
             "capabilities": {}, "metadata": {}, "created_at": r["created_at"]}
            for r in rows
        ])

    # ------------------------------------------------- knowledge documents
    knowledge = op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("property_id", sa.String(36), sa.ForeignKey("properties.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_key", sa.String(128), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("keywords", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.UniqueConstraint("property_id", "item_key"),
    )
    op.create_index("ix_knowledge_documents_property_id", "knowledge_documents", ["property_id"])
    old_docs = _table("hotel_knowledge_documents", "id", "hotel_id", "item_key", "category", "source", "content",
                      "keywords", "metadata", "content_hash", "updated_at")
    docs = bind.execute(sa.select(old_docs)).mappings().all()
    if docs:
        op.bulk_insert(knowledge, [{**{k: d[k] for k in d.keys() if k != "hotel_id"}, "property_id": d["hotel_id"]}
                                   for d in docs])
    op.drop_table("hotel_knowledge_documents")

    # --------------------------------------------------------------- guests
    with op.batch_alter_table("guests") as batch:
        batch.add_column(sa.Column("preferences", sa.JSON(), nullable=True))
    guests = sa.table("guests", sa.column("id"), sa.column("preferences", sa.JSON()))
    bind.execute(guests.update().values(preferences={}))
    with op.batch_alter_table("guests") as batch:
        batch.alter_column("preferences", existing_type=sa.JSON(), nullable=False)

    # ---------------------------------------------------------------- stays
    stays = op.create_table(
        "stays",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("guest_id", sa.String(36), sa.ForeignKey("guests.id"), nullable=False),
        sa.Column("property_id", sa.String(36), sa.ForeignKey("properties.id"), nullable=False),
        sa.Column("status", ENUM, nullable=False),
        sa.Column("booking_reference", sa.String(128)),
        sa.Column("arrival_at", TS),
        sa.Column("departure_at", TS),
        sa.Column("party_size", sa.Integer()),
        sa.Column("source_channel", sa.String(32)),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_index("ix_stays_guest_id", "stays", ["guest_id"])
    op.create_index("ix_stays_property_id", "stays", ["property_id"])

    # -------------------------------------------------------- conversations
    with op.batch_alter_table("conversations") as batch:
        batch.add_column(sa.Column("property_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("stay_id", sa.String(36), nullable=True))
    conversations = _table("conversations", "id", "hotel_id", "guest_id", "channel", "memory", "created_at",
                           "updated_at", "property_id", "stay_id")
    conv_stay: dict[str, tuple[str, str]] = {}
    for c in bind.execute(sa.select(conversations)).mappings().all():
        memory = c["memory"] or {}
        facts = {k: v for k, v in memory.items() if not k.startswith("_")}
        state = {k: v for k, v in memory.items() if k.startswith("_")}
        stay_id = str(uuid.uuid4())
        bind.execute(stays.insert().values(
            id=stay_id, guest_id=c["guest_id"], property_id=c["hotel_id"], status="inquiry",
            source_channel=c["channel"], facts=facts, metadata={"migrated_from_conversation": c["id"]},
            created_at=c["created_at"], updated_at=c["updated_at"],
        ))
        bind.execute(conversations.update().where(conversations.c.id == c["id"]).values(
            property_id=c["hotel_id"], stay_id=stay_id, memory=state))
        conv_stay[c["id"]] = (c["hotel_id"], stay_id)
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("hotel_id")
        batch.alter_column("property_id", existing_type=sa.String(36), nullable=False)
        batch.alter_column("language", existing_type=sa.String(8), type_=sa.String(16))
        batch.create_foreign_key("fk_conversations_property_id", "properties", ["property_id"], ["id"])
        batch.create_foreign_key("fk_conversations_stay_id", "stays", ["stay_id"], ["id"])
        batch.create_index("ix_conversations_property_id", ["property_id"])
        batch.create_index("ix_conversations_stay_id", ["stay_id"])
    with op.batch_alter_table("messages") as batch:
        batch.alter_column("language", existing_type=sa.String(8), type_=sa.String(16))

    # -------------------------------------------------------------- actions
    actions = op.create_table(
        "actions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("property_id", sa.String(36), sa.ForeignKey("properties.id"), nullable=False),
        sa.Column("stay_id", sa.String(36), sa.ForeignKey("stays.id")),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id")),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("status", ENUM, nullable=False),
        sa.Column("urgency", ENUM, nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("executor", sa.String(64), nullable=False),
        sa.Column("external_ref", sa.String(255)),
        sa.Column("error", sa.Text()),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("closed_at", TS),
    )
    op.create_index("ix_actions_property_id", "actions", ["property_id"])
    op.create_index("ix_actions_stay_id", "actions", ["stay_id"])
    op.create_index("ix_actions_conversation_id", "actions", ["conversation_id"])
    events = op.create_table(
        "action_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("action_id", sa.String(36), sa.ForeignKey("actions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("from_status", ENUM),
        sa.Column("to_status", ENUM, nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("detail", sa.Text()),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_action_events_action_id", "action_events", ["action_id"])

    requests = _table("hotel_requests", "id", "conversation_id", "request_type", "status", "urgency", "summary",
                      "details", "resolution_note", "created_at", "resolved_at")
    for r in bind.execute(sa.select(requests)).mappings().all():
        property_id, stay_id = conv_stay[r["conversation_id"]]
        status = REQUEST_TO_ACTION_STATUS.get(r["status"], "submitted")
        bind.execute(actions.insert().values(
            id=r["id"], property_id=property_id, stay_id=stay_id, conversation_id=r["conversation_id"],
            action_type=REQUEST_TO_ACTION_TYPE.get(r["request_type"], "staff_question"), status=status,
            urgency=r["urgency"], summary=r["summary"], params=r["details"] or {}, result={}, executor="staff",
            note=r["resolution_note"], created_at=r["created_at"], updated_at=r["resolved_at"] or r["created_at"],
            closed_at=r["resolved_at"],
        ))
        history = [(None, "submitted")] + ([("submitted", status)] if status != "submitted" else [])
        for seq, (frm, to) in enumerate(history, start=1):
            bind.execute(events.insert().values(
                id=str(uuid.uuid4()), action_id=r["id"], seq=seq, from_status=frm, to_status=to,
                actor="migration:0002", detail="migrated from hotel_requests", created_at=_now(),
            ))
    op.drop_table("hotel_requests")
    op.drop_table("hotels")


def downgrade() -> None:
    raise NotImplementedError("0002_stay_engine is forward-only; restore a pre-upgrade backup to roll back")
