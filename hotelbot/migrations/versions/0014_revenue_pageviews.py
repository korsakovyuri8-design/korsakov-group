"""Add anonymous public-route view events; independent of guests and trips."""

import sqlalchemy as sa
from alembic import op

revision = "0014_revenue_pageviews"
down_revision = "0013_revenue_clicks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "revenue_page_views",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("route_slug", sa.String(80), nullable=False),
        *(sa.Column(f"utm_{name}", sa.String(48), nullable=True)
          for name in ("source", "medium", "campaign", "content", "term")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_revenue_page_views_created_at", "revenue_page_views", ["created_at"])
    op.create_index("ix_revenue_page_views_route_created", "revenue_page_views", ["route_slug", "created_at"])


def downgrade() -> None:
    # Destructive; only exercise on isolated test databases. Retain telemetry in production.
    op.drop_table("revenue_page_views")
