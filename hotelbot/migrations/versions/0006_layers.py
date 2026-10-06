"""Discovery / transaction layers: an Offering may sell tickets for an Event
(offerings.event_id), and actions may be SUBMISSION_UNKNOWN (string status,
no DDL). Additive and reversible.

Revision ID: 0006_layers
Revises: 0005_marketplace
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_layers"
down_revision = "0005_marketplace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("offerings") as batch:
        batch.add_column(sa.Column("event_id", sa.String(36)))
        batch.create_foreign_key("fk_offerings_event_id", "events", ["event_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("offerings") as batch:
        batch.drop_constraint("fk_offerings_event_id", type_="foreignkey")
        batch.drop_column("event_id")
