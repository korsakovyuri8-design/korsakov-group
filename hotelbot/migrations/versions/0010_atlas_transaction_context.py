"""Atlas-native transaction context.

Quotes and actions may belong either to the legacy Property/Stay world
or directly to an Atlas Trip through TransactionContext.

Revision ID: 0010_atlas_transaction_context
Revises: 0009_journey
"""

import sqlalchemy as sa
from alembic import op


revision = "0010_atlas_transaction_context"
down_revision = "0009_journey"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "transaction_contexts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column(
            "trip_id",
            sa.String(36),
            sa.ForeignKey("trips.id"),
            nullable=True,
        ),
        sa.Column(
            "guest_id",
            sa.String(36),
            sa.ForeignKey("guests.id"),
            nullable=True,
        ),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("locale", sa.String(16), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.UniqueConstraint(
            "trip_id",
            name="uq_transaction_contexts_trip_id",
        ),
    )

    op.create_index(
        "ix_transaction_contexts_trip_id",
        "transaction_contexts",
        ["trip_id"],
    )
    op.create_index(
        "ix_transaction_contexts_guest_id",
        "transaction_contexts",
        ["guest_id"],
    )

    # Legacy Action ownership becomes optional because Atlas-native actions
    # are owned by transaction_context_id instead.
    with op.batch_alter_table("actions") as batch:
        batch.alter_column(
            "property_id",
            existing_type=sa.String(36),
            existing_nullable=False,
            nullable=True,
        )
        batch.add_column(
            sa.Column(
                "transaction_context_id",
                sa.String(36),
                nullable=True,
            )
        )
        batch.create_foreign_key(
            "fk_actions_transaction_context_id",
            "transaction_contexts",
            ["transaction_context_id"],
            ["id"],
        )
        batch.create_index(
            "ix_actions_transaction_context_id",
            ["transaction_context_id"],
        )

    # Legacy Quote ownership also becomes optional. Atlas-native quotes use
    # transaction_context_id and need no fake Property / Stay / Guest.
    with op.batch_alter_table("quotes") as batch:
        batch.alter_column(
            "property_id",
            existing_type=sa.String(36),
            existing_nullable=False,
            nullable=True,
        )
        batch.alter_column(
            "stay_id",
            existing_type=sa.String(36),
            existing_nullable=False,
            nullable=True,
        )
        batch.alter_column(
            "guest_id",
            existing_type=sa.String(36),
            existing_nullable=False,
            nullable=True,
        )
        batch.add_column(
            sa.Column(
                "transaction_context_id",
                sa.String(36),
                nullable=True,
            )
        )
        batch.create_foreign_key(
            "fk_quotes_transaction_context_id",
            "transaction_contexts",
            ["transaction_context_id"],
            ["id"],
        )
        batch.create_index(
            "ix_quotes_transaction_context_id",
            ["transaction_context_id"],
        )


def downgrade() -> None:
    # Safe only while there are no Atlas-native quote/action rows, because
    # the old schema requires Property/Stay ownership.

    with op.batch_alter_table("quotes") as batch:
        batch.drop_index("ix_quotes_transaction_context_id")
        batch.drop_constraint(
            "fk_quotes_transaction_context_id",
            type_="foreignkey",
        )
        batch.drop_column("transaction_context_id")

        batch.alter_column(
            "guest_id",
            existing_type=sa.String(36),
            existing_nullable=True,
            nullable=False,
        )
        batch.alter_column(
            "stay_id",
            existing_type=sa.String(36),
            existing_nullable=True,
            nullable=False,
        )
        batch.alter_column(
            "property_id",
            existing_type=sa.String(36),
            existing_nullable=True,
            nullable=False,
        )

    with op.batch_alter_table("actions") as batch:
        batch.drop_index("ix_actions_transaction_context_id")
        batch.drop_constraint(
            "fk_actions_transaction_context_id",
            type_="foreignkey",
        )
        batch.drop_column("transaction_context_id")

        batch.alter_column(
            "property_id",
            existing_type=sa.String(36),
            existing_nullable=True,
            nullable=False,
        )

    op.drop_index(
        "ix_transaction_contexts_guest_id",
        table_name="transaction_contexts",
    )
    op.drop_index(
        "ix_transaction_contexts_trip_id",
        table_name="transaction_contexts",
    )
    op.drop_table("transaction_contexts")
