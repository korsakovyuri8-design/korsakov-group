"""External Service Transaction Layer: providers, quotes, transactions,
provider callback log, durable job queue.

Additive only (new tables; no existing data is transformed), so it is safe
to apply on a live 0002 database. Downgrade drops the new tables - that
discards transaction history, so back up first anyway (docs/OPERATIONS.md).

Revision ID: 0003_transactions
Revises: 0002_stay_engine
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_transactions"
down_revision = "0002_stay_engine"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
ENUM = sa.String(32)


def upgrade() -> None:
    op.create_table(
        "external_providers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("property_id", sa.String(36), sa.ForeignKey("properties.id"), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("provider_type", sa.String(32), nullable=False),
        sa.Column("integration_type", sa.String(32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("services", sa.JSON(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("property_id", "slug"),
    )
    op.create_index("ix_external_providers_property_id", "external_providers", ["property_id"])

    op.create_table(
        "quotes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(8), nullable=False),
        sa.Column("property_id", sa.String(36), sa.ForeignKey("properties.id"), nullable=False),
        sa.Column("stay_id", sa.String(36), sa.ForeignKey("stays.id"), nullable=False),
        sa.Column("guest_id", sa.String(36), sa.ForeignKey("guests.id"), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id")),
        sa.Column("provider_id", sa.String(36), sa.ForeignKey("external_providers.id"), nullable=False),
        sa.Column("service_type", sa.String(64), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("conditions", sa.Text()),
        sa.Column("valid_until", TS, nullable=False),
        sa.Column("provider_reference", sa.String(255)),
        sa.Column("status", ENUM, nullable=False),
        sa.Column("consent", sa.JSON(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("decided_at", TS),
    )
    for col in ("code", "property_id", "stay_id", "guest_id"):
        op.create_index(f"ix_quotes_{col}", "quotes", [col])

    op.create_table(
        "external_transactions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("action_id", sa.String(36), sa.ForeignKey("actions.id"), nullable=False, unique=True),
        sa.Column("provider_id", sa.String(36), sa.ForeignKey("external_providers.id"), nullable=False),
        sa.Column("quote_id", sa.String(36), sa.ForeignKey("quotes.id"), nullable=False, unique=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("provider_reference", sa.String(255)),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("submit_attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("submitted_at", TS),
    )
    op.create_index("ix_external_transactions_provider_id", "external_transactions", ["provider_id"])
    op.create_index("ix_external_transactions_provider_reference", "external_transactions", ["provider_reference"])

    op.create_table(
        "provider_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider_id", sa.String(36), sa.ForeignKey("external_providers.id"), nullable=False),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("provider_reference", sa.String(255)),
        sa.Column("action_id", sa.String(36), sa.ForeignKey("actions.id")),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("received_at", TS, nullable=False),
        sa.UniqueConstraint("provider_id", "event_id"),
    )
    op.create_index("ix_provider_events_provider_id", "provider_events", ["provider_id"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False, unique=True),
        sa.Column("status", ENUM, nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", TS, nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("locked_by", sa.String(64)),
        sa.Column("locked_at", TS),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("completed_at", TS),
    )
    op.create_index("ix_jobs_due", "jobs", ["status", "next_attempt_at"])


def downgrade() -> None:
    for table in ("jobs", "provider_events", "external_transactions", "quotes", "external_providers"):
        op.drop_table(table)
