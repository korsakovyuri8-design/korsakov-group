"""Verified provider service coverage.

Provider geography is distinct from world geography and from inventory.
A ProviderCoverage row is explicit, time-bounded evidence that one provider
can expose one service in one area or exact route.

Revision ID: 0012_provider_coverage
Revises: 0011_atlas_web_sessions
"""

import sqlalchemy as sa
from alembic import op


revision = "0012_provider_coverage"
down_revision = "0011_atlas_web_sessions"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "provider_coverages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "provider_id",
            sa.String(36),
            sa.ForeignKey("external_providers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("coverage_key", sa.String(200), nullable=False),
        sa.Column("service_type", sa.String(64), nullable=False),
        sa.Column("scope_kind", sa.String(16), nullable=False),
        sa.Column(
            "area_code",
            sa.String(64),
            sa.ForeignKey("coverage_areas.code"),
            nullable=True,
        ),
        sa.Column("origin_entity_key", sa.String(128), nullable=True),
        sa.Column("destination_entity_key", sa.String(128), nullable=True),
        sa.Column("directional", sa.Boolean(), nullable=False),
        sa.Column("integration_mode", sa.String(32), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("evidence_source", sa.String(200), nullable=False),
        sa.Column("evidence_ref", sa.String(500), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("verification_type", sa.String(32), nullable=False),
        sa.Column("verified_at", TS, nullable=False),
        sa.Column("valid_from", TS, nullable=True),
        sa.Column("valid_until", TS, nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.UniqueConstraint(
            "provider_id",
            "coverage_key",
            name="uq_provider_coverages_provider_key",
        ),
    )

    op.create_index(
        "ix_provider_coverages_lookup",
        "provider_coverages",
        ["provider_id", "service_type", "active"],
    )
    op.create_index(
        "ix_provider_coverages_route",
        "provider_coverages",
        [
            "origin_entity_key",
            "destination_entity_key",
            "service_type",
        ],
    )
    op.create_index(
        "ix_provider_coverages_area",
        "provider_coverages",
        ["area_code", "service_type"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_provider_coverages_area",
        table_name="provider_coverages",
    )
    op.drop_index(
        "ix_provider_coverages_route",
        table_name="provider_coverages",
    )
    op.drop_index(
        "ix_provider_coverages_lookup",
        table_name="provider_coverages",
    )
    op.drop_table("provider_coverages")
