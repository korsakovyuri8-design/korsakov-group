"""Core v1 schema (baseline, as shipped in iteration 1 / tag hotelbot-core-v1).

Core v1 databases were created with metadata.create_all() and have no
alembic_version table; app.db.session stamps them at this revision before
upgrading.

Revision ID: 0001_core_v1
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_core_v1"
down_revision = None
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
ENUM = sa.String(32)  # non-native enums are stored as VARCHAR(32)


def upgrade() -> None:
    op.create_table(
        "hotels",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_table(
        "hotel_knowledge_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("hotel_id", sa.String(36), sa.ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_key", sa.String(128), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("keywords", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.UniqueConstraint("hotel_id", "item_key"),
    )
    op.create_table(
        "guests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(200)),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("channel", "external_id"),
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("hotel_id", sa.String(36), sa.ForeignKey("hotels.id"), nullable=False),
        sa.Column("guest_id", sa.String(36), sa.ForeignKey("guests.id"), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("language", sa.String(8)),
        sa.Column("status", ENUM, nullable=False),
        sa.Column("memory", sa.JSON(), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_index("ix_conversations_guest_id", "conversations", ["guest_id"])
    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("role", ENUM, nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("language", sa.String(8)),
        sa.Column("intent", sa.String(32)),
        sa.Column("external_id", sa.String(255), unique=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_table(
        "hotel_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("request_type", ENUM, nullable=False),
        sa.Column("status", ENUM, nullable=False),
        sa.Column("urgency", ENUM, nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("resolution_note", sa.Text()),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("resolved_at", TS),
    )
    op.create_index("ix_hotel_requests_conversation_id", "hotel_requests", ["conversation_id"])
    op.create_table(
        "human_handoffs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("urgency", ENUM, nullable=False),
        sa.Column("status", ENUM, nullable=False),
        sa.Column("package", sa.JSON(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("resolved_at", TS),
    )
    op.create_index("ix_human_handoffs_conversation_id", "human_handoffs", ["conversation_id"])


def downgrade() -> None:
    for table in ("human_handoffs", "hotel_requests", "messages", "conversations", "guests",
                  "hotel_knowledge_documents", "hotels"):
        op.drop_table(table)
