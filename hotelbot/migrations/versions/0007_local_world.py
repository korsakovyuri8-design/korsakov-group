"""Local world / discovery engine: normalised provenance on places, events and
offerings (source_id / source_type / source_record_id), place timezone,
contacts, price range and per-fact verification; event description,
location and active flag; traveller preferences. Additive, reversible.

Revision ID: 0007_local_world
Revises: 0006_layers
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_local_world"
down_revision = "0006_layers"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
EMPTY = sa.text("'{}'")


def _provenance(batch) -> None:  # noqa: ANN001
    batch.add_column(sa.Column("source_id", sa.String(128)))
    batch.add_column(sa.Column("source_type", sa.String(32)))
    batch.add_column(sa.Column("source_record_id", sa.String(255)))


def upgrade() -> None:
    for table in ("places", "events", "offerings"):
        with op.batch_alter_table(table) as batch:
            _provenance(batch)
            batch.create_index(f"ix_{table}_source_id", ["source_id"])
    with op.batch_alter_table("places") as batch:
        batch.add_column(sa.Column("timezone", sa.String(64)))
        batch.add_column(sa.Column("phone", sa.String(64)))
        batch.add_column(sa.Column("website", sa.String(300)))
        batch.add_column(sa.Column("price_range", sa.Integer()))
        batch.add_column(sa.Column("verification", sa.JSON(), nullable=False, server_default=EMPTY))
    with op.batch_alter_table("events") as batch:
        batch.add_column(sa.Column("description", sa.JSON(), nullable=False, server_default=EMPTY))
        batch.add_column(sa.Column("latitude", sa.Float()))
        batch.add_column(sa.Column("longitude", sa.Float()))
        batch.add_column(sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table(
        "traveler_preferences",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("guest_id", sa.String(36), sa.ForeignKey("guests.id"), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("source_message_id", sa.String(36)),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.UniqueConstraint("guest_id", "key"),
    )
    op.create_index("ix_traveler_preferences_guest_id", "traveler_preferences", ["guest_id"])


def downgrade() -> None:
    op.drop_table("traveler_preferences")
    with op.batch_alter_table("events") as batch:
        for col in ("active", "longitude", "latitude", "description"):
            batch.drop_column(col)
    with op.batch_alter_table("places") as batch:
        for col in ("verification", "price_range", "website", "phone", "timezone"):
            batch.drop_column(col)
    for table in ("places", "events", "offerings"):
        with op.batch_alter_table(table) as batch:
            batch.drop_index(f"ix_{table}_source_id")
            for col in ("source_record_id", "source_type", "source_id"):
                batch.drop_column(col)
