"""Local Travel Infrastructure Layer: places, events, offerings, availability,
itinerary items; providers may be region-scoped (not tied to one property).

Additive: new tables; external_providers.property_id becomes nullable and
gains a `region` column. No existing rows are transformed.

Revision ID: 0004_local_travel
Revises: 0003_transactions
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_local_travel"
down_revision = "0003_transactions"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def _provenance() -> list[sa.Column]:
    return [
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("last_verified_at", TS),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("provider_owned", sa.Boolean(), nullable=False),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False),
    ]


def upgrade() -> None:
    with op.batch_alter_table("external_providers") as batch:
        batch.alter_column("property_id", existing_type=sa.String(36), nullable=True)
        batch.add_column(sa.Column("region", sa.String(64)))
        batch.create_unique_constraint("uq_external_providers_region_slug", ["region", "slug"])
        batch.create_index("ix_external_providers_region", ["region"])

    op.create_table(
        "places",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("region", sa.String(64), nullable=False),
        sa.Column("slug", sa.String(96), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("subcategory", sa.String(64), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("description", sa.JSON(), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("latitude", sa.Float()),
        sa.Column("longitude", sa.Float()),
        sa.Column("address", sa.String(300)),
        sa.Column("service_area_km", sa.Float()),
        sa.Column("hours", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        *_provenance(),
        sa.UniqueConstraint("region", "slug"),
    )
    for col in ("region", "category", "subcategory"):
        op.create_index(f"ix_places_{col}", "places", [col])

    op.create_table(
        "events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("region", sa.String(64), nullable=False),
        sa.Column("slug", sa.String(96), nullable=False),
        sa.Column("title", sa.JSON(), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("place_id", sa.String(36), sa.ForeignKey("places.id")),
        sa.Column("start_at", TS, nullable=False),
        sa.Column("end_at", TS),
        sa.Column("ticket_required", sa.Boolean(), nullable=False),
        sa.Column("ticket_price", sa.Numeric(12, 2)),
        sa.Column("currency", sa.String(3)),
        sa.Column("age_limit", sa.Integer()),
        sa.Column("language", sa.String(16)),
        sa.Column("booking_source", sa.String(200)),
        sa.Column("attributes", sa.JSON(), nullable=False),
        *_provenance(),
        sa.UniqueConstraint("region", "slug"),
    )
    op.create_index("ix_events_region", "events", ["region"])
    op.create_index("ix_events_start_at", "events", ["start_at"])

    op.create_table(
        "offerings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("region", sa.String(64), nullable=False),
        sa.Column("slug", sa.String(96), nullable=False),
        sa.Column("service_type", sa.String(64), nullable=False),
        sa.Column("title", sa.JSON(), nullable=False),
        sa.Column("place_id", sa.String(36), sa.ForeignKey("places.id")),
        sa.Column("provider_id", sa.String(36), sa.ForeignKey("external_providers.id")),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("price_from", sa.Numeric(12, 2)),
        sa.Column("currency", sa.String(3)),
        sa.Column("active", sa.Boolean(), nullable=False),
        *_provenance(),
        sa.UniqueConstraint("region", "slug"),
    )
    op.create_index("ix_offerings_region", "offerings", ["region"])
    op.create_index("ix_offerings_service_type", "offerings", ["service_type"])

    op.create_table(
        "availability",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("offering_id", sa.String(36), sa.ForeignKey("offerings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("starts_at", TS, nullable=False),
        sa.Column("ends_at", TS, nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("remaining", sa.Integer(), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("last_verified_at", TS),
    )
    op.create_index("ix_availability_offering_id", "availability", ["offering_id"])

    op.create_table(
        "itinerary_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("stay_id", sa.String(36), sa.ForeignKey("stays.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("starts_at", TS),
        sa.Column("place_id", sa.String(36), sa.ForeignKey("places.id")),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("events.id")),
        sa.Column("offering_id", sa.String(36), sa.ForeignKey("offerings.id")),
        sa.Column("quote_id", sa.String(36), sa.ForeignKey("quotes.id")),
        sa.Column("action_id", sa.String(36), sa.ForeignKey("actions.id")),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_index("ix_itinerary_items_stay_id", "itinerary_items", ["stay_id"])


def downgrade() -> None:
    for table in ("itinerary_items", "availability", "offerings", "events", "places"):
        op.drop_table(table)
    with op.batch_alter_table("external_providers") as batch:
        batch.drop_index("ix_external_providers_region")
        batch.drop_constraint("uq_external_providers_region_slug", type_="unique")
        batch.drop_column("region")
