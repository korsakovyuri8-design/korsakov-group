"""Service marketplace: inventory holds, property-provider relationships,
offering pricing/policies, commercial metadata, quote terms, and the
PENDING_CONDITION action state (a string value - no DDL needed for it).

Additive. Existing provider/offering/quote rows get empty JSON objects.

Revision ID: 0005_marketplace
Revises: 0004_local_travel
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_marketplace"
down_revision = "0004_local_travel"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
EMPTY = sa.text("'{}'")


def _json(name: str) -> sa.Column:
    return sa.Column(name, sa.JSON(), nullable=False, server_default=EMPTY)


def upgrade() -> None:
    with op.batch_alter_table("external_providers") as batch:
        batch.add_column(_json("profile"))
        batch.add_column(_json("policies"))
        batch.add_column(sa.Column("commission_type", sa.String(32)))
        batch.add_column(sa.Column("commission_value", sa.Numeric(12, 2)))

    with op.batch_alter_table("offerings") as batch:
        batch.add_column(_json("pricing"))
        batch.add_column(_json("policies"))
        batch.add_column(sa.Column("commission_type", sa.String(32)))
        batch.add_column(sa.Column("commission_value", sa.Numeric(12, 2)))
        batch.add_column(sa.Column("partner_price", sa.Numeric(12, 2)))
        batch.add_column(sa.Column("guest_price", sa.Numeric(12, 2)))

    with op.batch_alter_table("quotes") as batch:
        batch.add_column(_json("terms"))
        batch.add_column(_json("commercial"))
        batch.add_column(sa.Column("offering_id", sa.String(36)))
        batch.add_column(sa.Column("replaces_transaction_id", sa.String(36)))
        batch.create_foreign_key("fk_quotes_offering_id", "offerings", ["offering_id"], ["id"])

    op.create_table(
        "property_providers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("property_id", sa.String(36), sa.ForeignKey("properties.id"), nullable=False),
        sa.Column("provider_id", sa.String(36), sa.ForeignKey("external_providers.id"), nullable=False),
        sa.Column("relation", sa.String(32), nullable=False),
        sa.Column("services", sa.JSON(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("property_id", "provider_id"),
    )
    op.create_index("ix_property_providers_property_id", "property_providers", ["property_id"])
    op.create_index("ix_property_providers_provider_id", "property_providers", ["provider_id"])

    op.create_table(
        "inventory_holds",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("offering_id", sa.String(36), sa.ForeignKey("offerings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("variant", sa.String(64)),
        sa.Column("starts_at", TS, nullable=False),
        sa.Column("ends_at", TS, nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("expires_at", TS),
        sa.Column("quote_id", sa.String(36), sa.ForeignKey("quotes.id")),
        sa.Column("transaction_id", sa.String(36)),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_inventory_holds_window", "inventory_holds", ["offering_id", "starts_at", "ends_at"])
    op.create_index("ix_inventory_holds_quote_id", "inventory_holds", ["quote_id"])
    op.create_index("ix_inventory_holds_transaction_id", "inventory_holds", ["transaction_id"])


def downgrade() -> None:
    op.drop_table("inventory_holds")
    op.drop_table("property_providers")
    with op.batch_alter_table("quotes") as batch:
        batch.drop_constraint("fk_quotes_offering_id", type_="foreignkey")
        for col in ("replaces_transaction_id", "offering_id", "commercial", "terms"):
            batch.drop_column(col)
    with op.batch_alter_table("offerings") as batch:
        for col in ("guest_price", "partner_price", "commission_value", "commission_type", "policies", "pricing"):
            batch.drop_column(col)
    with op.batch_alter_table("external_providers") as batch:
        for col in ("commission_value", "commission_type", "policies", "profile"):
            batch.drop_column(col)
