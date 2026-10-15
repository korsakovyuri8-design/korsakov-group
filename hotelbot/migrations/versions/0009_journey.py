"""Journey (Iteration 7, slice 1): trips, trip source records, trip items
(with revisions), trip observations and trip issues. Additive, reversible.

Revision ID: 0009_journey
Revises: 0008_world_fabric
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_journey"
down_revision = "0008_world_fabric"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def _id() -> sa.Column:
    return sa.Column("id", sa.String(36), primary_key=True)


def upgrade() -> None:
    op.create_table(
        "trips",
        _id(),
        sa.Column("title", sa.String(200)),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_table(
        "trip_source_records",
        _id(),
        sa.Column("trip_id", sa.String(36), sa.ForeignKey("trips.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("received_at", TS, nullable=False),
        sa.Column("issued_at", TS),
        sa.Column("raw", sa.Text(), nullable=False),
        sa.Column("raw_sha256", sa.String(64), nullable=False),
        sa.Column("normalized_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("trip_id", "raw_sha256"),
    )
    op.create_index("ix_trip_source_records_trip_id", "trip_source_records", ["trip_id"])
    op.create_index("ix_trip_source_records_normalized_sha256", "trip_source_records", ["normalized_sha256"])
    op.create_table(
        "trip_items",
        _id(),
        sa.Column("trip_id", sa.String(36), sa.ForeignKey("trips.id"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("identity", sa.String(255), nullable=False),
        sa.Column("provider", sa.String(120)),
        sa.Column("booking_reference", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("times", sa.JSON(), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("revisions", sa.JSON(), nullable=False),
        sa.Column("world_entity_id", sa.String(36)),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.UniqueConstraint("trip_id", "identity"),
    )
    op.create_index("ix_trip_items_trip_id", "trip_items", ["trip_id"])
    op.create_table(
        "trip_observations",
        _id(),
        sa.Column("trip_id", sa.String(36), sa.ForeignKey("trips.id"), nullable=False),
        sa.Column("record_id", sa.String(36), sa.ForeignKey("trip_source_records.id"), nullable=False),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("trip_items.id"), nullable=False),
        sa.Column("field", sa.String(64), nullable=False),
        sa.Column("value", sa.JSON()),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("extractor", sa.String(32), nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    for col in ("trip_id", "record_id", "item_id"):
        op.create_index(f"ix_trip_observations_{col}", "trip_observations", [col])
    op.create_table(
        "trip_issues",
        _id(),
        sa.Column("trip_id", sa.String(36), sa.ForeignKey("trips.id"), nullable=False),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("trip_items.id")),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_trip_issues_trip_id", "trip_issues", ["trip_id"])
    op.create_index("ix_trip_issues_item_id", "trip_issues", ["item_id"])


def downgrade() -> None:
    for table in ("trip_issues", "trip_observations", "trip_items", "trip_source_records", "trips"):
        op.drop_table(table)
