"""Anonymous outbound activations; no relationship to private trip state."""

import sqlalchemy as sa
from alembic import op

revision = "0013_revenue_clicks"
down_revision = "0012_provider_coverage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "revenue_clicks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("placement", sa.String(32), nullable=False),
        sa.Column("route_slug", sa.String(80), nullable=True),
        *(sa.Column(f"utm_{key}", sa.String(48), nullable=True)
          for key in ("source", "medium", "campaign", "content", "term")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("provider IN ('kiwitaxi', 'discovercars', 'viator', 'concierge')", name="ck_revenue_clicks_provider"),
        sa.CheckConstraint("placement IN ('missing_transport', 'route_landing', 'revenue_panel')", name="ck_revenue_clicks_placement"),
    )
    op.create_index("ix_revenue_clicks_created_at", "revenue_clicks", ["created_at"])
    op.create_index("ix_revenue_clicks_provider_placement_route", "revenue_clicks", ["provider", "placement", "route_slug"])


def downgrade() -> None:
    # Destructive to telemetry; production rollback should normally retain this table.
    op.drop_table("revenue_clicks")
