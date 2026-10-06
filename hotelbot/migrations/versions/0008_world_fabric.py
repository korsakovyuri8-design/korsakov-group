"""World data fabric: canonical entities, source entities, entity links,
aliases, identifiers, match reviews, fact assertions, corrections, change
history, sources (licence + health), coverage areas, marketplace identity
links and snapshots; places/events gain canonical id, resolution and
quality, places a geohash for the spatial index. Additive, reversible.

Revision ID: 0008_world_fabric
Revises: 0007_local_world
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_world_fabric"
down_revision = "0007_local_world"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
EMPTY = sa.text("'{}'")
EMPTY_LIST = sa.text("'[]'")


def _id() -> sa.Column:
    return sa.Column("id", sa.String(36), primary_key=True)


def upgrade() -> None:
    op.create_table(
        "world_sources",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_class", sa.String(32), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("format", sa.String(64), nullable=False),
        sa.Column("license", sa.String(200), nullable=False),
        sa.Column("attribution_required", sa.Boolean(), nullable=False),
        sa.Column("attribution_text", sa.String(300)),
        sa.Column("redistribution", sa.String(32), nullable=False),
        sa.Column("retention_days", sa.Integer()),
        sa.Column("cache_raw", sa.Boolean(), nullable=False),
        sa.Column("default_confidence", sa.Float(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("last_attempt_at", TS),
        sa.Column("last_success_at", TS),
        sa.Column("last_full_sync_at", TS),
        sa.Column("last_error", sa.Text()),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("records_seen", sa.Integer(), nullable=False),
        sa.Column("records_changed", sa.Integer(), nullable=False),
        sa.Column("cursor", sa.JSON(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_table(
        "canonical_entities",
        _id(),
        sa.Column("entity_type", sa.String(16), nullable=False),
        sa.Column("canonical_name", sa.String(300), nullable=False),
        sa.Column("canonical_slug", sa.String(120), nullable=False),
        sa.Column("region", sa.String(64)),
        sa.Column("country_code", sa.String(2)),
        sa.Column("latitude", sa.Float()),
        sa.Column("longitude", sa.Float()),
        sa.Column("geohash", sa.String(12)),
        sa.Column("starts_at", TS),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("deactivated_at", TS),
        sa.Column("deactivation_reason", sa.String(200)),
        sa.Column("merged_into_id", sa.String(36), sa.ForeignKey("canonical_entities.id")),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )
    for col in ("entity_type", "canonical_slug", "region", "geohash", "starts_at"):
        op.create_index(f"ix_canonical_entities_{col}", "canonical_entities", [col])
    op.create_table(
        "source_entities",
        _id(),
        sa.Column("source_id", sa.String(128), sa.ForeignKey("world_sources.id"), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_record_id", sa.String(255), nullable=False),
        sa.Column("entity_type", sa.String(16), nullable=False),
        sa.Column("source_url", sa.String(500)),
        sa.Column("raw", sa.JSON()),
        sa.Column("raw_hash", sa.String(64), nullable=False),
        sa.Column("normalized", sa.JSON(), nullable=False),
        sa.Column("issues", sa.JSON(), nullable=False),
        sa.Column("latitude", sa.Float()),
        sa.Column("longitude", sa.Float()),
        sa.Column("geohash", sa.String(12)),
        sa.Column("observed_at", TS),
        sa.Column("first_seen_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
        sa.Column("last_synced_at", TS, nullable=False),
        sa.Column("active_at_source", sa.Boolean(), nullable=False),
        sa.Column("tombstoned_at", TS),
        sa.UniqueConstraint("source_id", "source_record_id"),
    )
    op.create_index("ix_source_entities_source_id", "source_entities", ["source_id"])
    op.create_index("ix_source_entities_geohash", "source_entities", ["geohash"])
    op.create_table(
        "entity_links",
        _id(),
        sa.Column("source_entity_id", sa.String(36), sa.ForeignKey("source_entities.id"), nullable=False),
        sa.Column("canonical_entity_id", sa.String(36), sa.ForeignKey("canonical_entities.id"), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("method", sa.String(32), nullable=False),
        sa.Column("score", sa.Float()),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("decided_by", sa.String(64), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("ended_at", TS),
        sa.Column("end_reason", sa.String(200)),
    )
    for col in ("source_entity_id", "canonical_entity_id", "active"):
        op.create_index(f"ix_entity_links_{col}", "entity_links", [col])
    op.create_table(
        "entity_aliases",
        _id(),
        sa.Column("canonical_entity_id", sa.String(36), sa.ForeignKey("canonical_entities.id"), nullable=False),
        sa.Column("source_entity_id", sa.String(36), sa.ForeignKey("source_entities.id")),
        sa.Column("alias", sa.String(300), nullable=False),
        sa.Column("folded", sa.String(300), nullable=False),
        sa.Column("language", sa.String(16)),
        sa.Column("kind", sa.String(16), nullable=False),
    )
    op.create_index("ix_entity_aliases_canonical_entity_id", "entity_aliases", ["canonical_entity_id"])
    op.create_index("ix_entity_aliases_folded", "entity_aliases", ["folded"])
    op.create_table(
        "entity_identifiers",
        _id(),
        sa.Column("source_entity_id", sa.String(36), sa.ForeignKey("source_entities.id"), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("value", sa.String(300), nullable=False),
    )
    op.create_index("ix_entity_identifiers_source_entity_id", "entity_identifiers", ["source_entity_id"])
    op.create_index("ix_entity_identifiers_kind_value", "entity_identifiers", ["kind", "value"])
    op.create_table(
        "match_reviews",
        _id(),
        sa.Column("source_entity_id", sa.String(36), sa.ForeignKey("source_entities.id"), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("resolution", sa.String(32)),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("resolved_at", TS),
    )
    op.create_index("ix_match_reviews_source_entity_id", "match_reviews", ["source_entity_id"])
    op.create_table(
        "fact_assertions",
        _id(),
        sa.Column("canonical_entity_id", sa.String(36), sa.ForeignKey("canonical_entities.id"), nullable=False),
        sa.Column("source_entity_id", sa.String(36), sa.ForeignKey("source_entities.id"), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("source_class", sa.String(32), nullable=False),
        sa.Column("field_name", sa.String(64), nullable=False),
        sa.Column("value", sa.JSON()),
        sa.Column("observed_at", TS, nullable=False),
        sa.Column("valid_from", TS),
        sa.Column("valid_until", TS),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("verification_type", sa.String(32), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("superseded_at", TS),
        sa.Column("superseded_reason", sa.String(64)),
    )
    op.create_index("ix_fact_assertions_entity_field", "fact_assertions", ["canonical_entity_id", "field_name"])
    op.create_index("ix_fact_assertions_source_entity_id", "fact_assertions", ["source_entity_id"])
    op.create_index("ix_fact_assertions_source_id", "fact_assertions", ["source_id"])
    op.create_table(
        "world_corrections",
        _id(),
        sa.Column("canonical_entity_id", sa.String(36), sa.ForeignKey("canonical_entities.id"), nullable=False),
        sa.Column("field_name", sa.String(64), nullable=False),
        sa.Column("value", sa.JSON()),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_ref", sa.String(128), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("assertion_id", sa.String(36), sa.ForeignKey("fact_assertions.id"), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("withdrawn_at", TS),
    )
    op.create_index("ix_world_corrections_canonical_entity_id", "world_corrections", ["canonical_entity_id"])
    op.create_table(
        "world_changes",
        _id(),
        sa.Column("canonical_entity_id", sa.String(36)),
        sa.Column("source_entity_id", sa.String(36)),
        sa.Column("source_id", sa.String(128)),
        sa.Column("change_type", sa.String(32), nullable=False),
        sa.Column("field_name", sa.String(64)),
        sa.Column("old_value", sa.JSON()),
        sa.Column("new_value", sa.JSON()),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("detected_at", TS, nullable=False),
    )
    for col in ("canonical_entity_id", "source_entity_id", "detected_at"):
        op.create_index(f"ix_world_changes_{col}", "world_changes", [col])
    op.create_table(
        "coverage_areas",
        _id(),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("country_code", sa.String(2)),
        sa.Column("parent_code", sa.String(64)),
        sa.Column("timezone", sa.String(64)),
        sa.Column("min_lat", sa.Float()), sa.Column("min_lon", sa.Float()),
        sa.Column("max_lat", sa.Float()), sa.Column("max_lon", sa.Float()),
        sa.Column("center_lat", sa.Float()), sa.Column("center_lon", sa.Float()),
        sa.Column("radius_km", sa.Float()),
        sa.Column("attributes", sa.JSON(), nullable=False),
    )
    op.create_table(
        "entity_marketplace_links",
        _id(),
        sa.Column("canonical_entity_id", sa.String(36), sa.ForeignKey("canonical_entities.id"), nullable=False),
        sa.Column("provider_id", sa.String(36), sa.ForeignKey("external_providers.id")),
        sa.Column("offering_id", sa.String(36), sa.ForeignKey("offerings.id")),
        sa.Column("established_by", sa.String(32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    for col in ("canonical_entity_id", "provider_id", "offering_id"):
        op.create_index(f"ix_entity_marketplace_links_{col}", "entity_marketplace_links", [col])
    op.create_table(
        "world_snapshots",
        _id(),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("source_versions", sa.JSON(), nullable=False),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
    )
    with op.batch_alter_table("places") as batch:
        batch.add_column(sa.Column("canonical_entity_id", sa.String(36)))
        batch.add_column(sa.Column("geohash", sa.String(12)))
        batch.add_column(sa.Column("resolution", sa.JSON(), nullable=False, server_default=EMPTY))
        batch.add_column(sa.Column("quality", sa.JSON(), nullable=False, server_default=EMPTY))
        batch.create_foreign_key("fk_places_canonical_entity_id", "canonical_entities",
                                 ["canonical_entity_id"], ["id"])
        batch.create_index("ix_places_canonical_entity_id", ["canonical_entity_id"])
        batch.create_index("ix_places_geohash", ["geohash"])
    with op.batch_alter_table("events") as batch:
        batch.add_column(sa.Column("canonical_entity_id", sa.String(36)))
        batch.add_column(sa.Column("resolution", sa.JSON(), nullable=False, server_default=EMPTY))
        batch.create_foreign_key("fk_events_canonical_entity_id", "canonical_entities",
                                 ["canonical_entity_id"], ["id"])
        batch.create_index("ix_events_canonical_entity_id", ["canonical_entity_id"])


def downgrade() -> None:
    with op.batch_alter_table("events") as batch:
        batch.drop_index("ix_events_canonical_entity_id")
        batch.drop_constraint("fk_events_canonical_entity_id", type_="foreignkey")
        batch.drop_column("resolution")
        batch.drop_column("canonical_entity_id")
    with op.batch_alter_table("places") as batch:
        batch.drop_index("ix_places_geohash")
        batch.drop_index("ix_places_canonical_entity_id")
        batch.drop_constraint("fk_places_canonical_entity_id", type_="foreignkey")
        for col in ("quality", "resolution", "geohash", "canonical_entity_id"):
            batch.drop_column(col)
    for table in ("world_snapshots", "entity_marketplace_links", "coverage_areas", "world_changes",
                  "world_corrections", "fact_assertions", "match_reviews", "entity_identifiers", "entity_aliases",
                  "entity_links", "source_entities", "canonical_entities", "world_sources"):
        op.drop_table(table)
