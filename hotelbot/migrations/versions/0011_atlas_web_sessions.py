"""Durable Atlas web session registry.

The browser keeps a random bearer token. Atlas persists only its SHA-256
hash and the Trip it owns.

Revision ID: 0011_atlas_web_sessions
Revises: 0010_atlas_transaction_context
"""

import sqlalchemy as sa
from alembic import op


revision = "0011_atlas_web_sessions"
down_revision = "0010_atlas_transaction_context"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "atlas_web_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "trip_id",
            sa.String(36),
            sa.ForeignKey("trips.id"),
            nullable=False,
        ),
        sa.Column("clock_mode", sa.String(16), nullable=False),
        sa.Column("client_timezone", sa.String(64), nullable=True),
        sa.Column("touched_at_epoch", sa.Float(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("ended_at", TS, nullable=True),
        sa.UniqueConstraint(
            "token_hash",
            name="uq_atlas_web_sessions_token_hash",
        ),
        sa.UniqueConstraint(
            "trip_id",
            name="uq_atlas_web_sessions_trip_id",
        ),
    )

    op.create_index(
        "ix_atlas_web_sessions_trip_id",
        "atlas_web_sessions",
        ["trip_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_atlas_web_sessions_trip_id",
        table_name="atlas_web_sessions",
    )
    op.drop_table("atlas_web_sessions")
