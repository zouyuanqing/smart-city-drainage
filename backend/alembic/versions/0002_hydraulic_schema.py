"""Hydraulic schema, spatial profiles, and result retention.

Revision ID: 0002_hydraulic_schema
Revises: 0001_initial
Create Date: 2026-09-24
"""

from __future__ import annotations

import os
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.types import UserDefinedType

from alembic import op

revision: str = "0002_hydraulic_schema"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "hydraulic"
RESULT_RETENTION_DAYS = 90
RESULT_CHUNK_INTERVAL = "1 day"

ENUM_DEFINITIONS: dict[str, tuple[str, ...]] = {
    "network_status": ("draft", "active", "archived"),
    "network_node_kind": ("junction", "outfall", "manhole", "storage", "other"),
    "rainfall_scenario_kind": ("design", "custom", "synthetic"),
    "simulation_status": (
        "queued",
        "leased",
        "running",
        "retry_wait",
        "cancelling",
        "cancelled",
        "completed",
        "failed",
        "timed_out",
    ),
    "simulation_attempt_status": (
        "running",
        "discarded",
        "published",
        "cleanup_pending",
    ),
    "frame_quality_status": ("verified", "warning", "failed"),
    "calibration_status": (
        "queued",
        "leased",
        "running",
        "retry_wait",
        "cancelling",
        "cancelled",
        "completed",
        "failed",
        "timed_out",
    ),
    "forecast_alert_type": ("flood_forecast", "pipe_surcharge"),
    "forecast_alert_status": ("open", "acknowledged", "resolved", "expired"),
    "forecast_alert_phase": ("predicted", "observed"),
    "confidence_method": (
        "ensemble_exceedance_frequency",
        "calibrated_residual",
        "conformal",
        "calibrated_regression",
    ),
    "forecast_transition_action": (
        "create",
        "acknowledge",
        "observe",
        "resolve",
        "expire",
        "refresh",
        "escalate",
    ),
}


class PostGISGeometry(UserDefinedType):
    """Compile a fixed PostGIS geometry type without a GeoAlchemy dependency."""

    cache_ok = True

    def __init__(self, geometry_type: str, srid: int = 4326) -> None:
        self.geometry_type = geometry_type
        self.srid = srid

    def get_col_spec(self, **_: object) -> str:
        return f"geometry({self.geometry_type},{self.srid})"


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(
        *ENUM_DEFINITIONS[name],
        name=name,
        schema=SCHEMA,
        create_type=False,
    )


def _uuid() -> sa.Column:
    return sa.Column(sa.String(36), nullable=False)


def _spatial_backend() -> str:
    value = os.getenv("HYDRAULIC_SPATIAL_BACKEND", "portable").strip().lower()
    if value not in {"postgis", "portable"}:
        raise RuntimeError(
            "HYDRAULIC_SPATIAL_BACKEND must be 'postgis' or 'portable'; "
            f"received {value!r}"
        )
    return value


def _create_extensions(backend: str) -> None:
    # TimescaleDB is required by both profiles.  The portable profile never
    # creates, queries, or alters a PostGIS object.
    op.execute('CREATE EXTENSION IF NOT EXISTS "timescaledb"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "btree_gist"')
    if backend == "postgis":
        # CREATE EXTENSION is also the fail-closed capability probe.  If the
        # selected image does not provide PostGIS, the migration aborts before
        # creating business rows.
        op.execute('CREATE EXTENSION IF NOT EXISTS "postgis"')


def _create_enums() -> None:
    for name, values in ENUM_DEFINITIONS.items():
        # PostgreSQL requires enum labels to be string literals.  Building the
        # statement with ``", ".join(values)`` produces invalid SQL such as
        # ``ENUM (draft, active, archived)``.  Compile each value through the
        # PostgreSQL dialect so labels are quoted and escaped by SQLAlchemy.
        value_sql = ", ".join(
            str(
                sa.literal(value).compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
            )
            for value in values
        )
        op.execute(sa.text(f"CREATE TYPE {SCHEMA}.{name} AS ENUM ({value_sql})"))


def _create_network_tables() -> None:
    op.create_table(
        "schema_metadata",
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("value", sa.String(32), nullable=False),
        sa.Column("alembic_revision", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "key = 'spatial_backend'", name="ck_hydraulic_schema_metadata_key"
        ),
        sa.CheckConstraint(
            "value IN ('postgis','portable')",
            name="ck_hydraulic_schema_metadata_profile",
        ),
        sa.PrimaryKeyConstraint("key", name="pk_hydraulic_schema_metadata"),
        schema=SCHEMA,
    )

    op.create_table(
        "networks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "status", _enum("network_status"), nullable=False, server_default="draft"
        ),
        # Added as a named foreign key immediately after network_revisions exists.
        sa.Column("current_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('draft','active','archived')",
            name="ck_hydraulic_networks_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_networks"),
        sa.UniqueConstraint("code", name="uq_hydraulic_networks_code"),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["public.users.id"],
            name="fk_hydraulic_networks_created_by",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "network_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("parent_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(64), nullable=False),
        sa.Column("manifest", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "revision_no >= 1", name="ck_hydraulic_network_revisions_number_positive"
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_hydraulic_network_revisions_hash_length",
        ),
        sa.CheckConstraint(
            "source_kind IN ('import','seed','crud')",
            name="ck_hydraulic_network_revisions_source_kind",
        ),
        sa.CheckConstraint(
            "parent_revision_id IS NULL OR parent_revision_id <> id",
            name="ck_hydraulic_network_revisions_parent_not_self",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_network_revisions"),
        sa.UniqueConstraint(
            "network_id", "revision_no", name="uq_hydraulic_network_revisions_number"
        ),
        sa.UniqueConstraint(
            "network_id",
            "content_sha256",
            name="uq_hydraulic_network_revisions_content",
        ),
        sa.UniqueConstraint(
            "id", "content_sha256", name="uq_hydraulic_network_revisions_id_content"
        ),
        sa.UniqueConstraint(
            "id", "network_id", name="uq_hydraulic_network_revisions_id_network"
        ),
        sa.ForeignKeyConstraint(
            ["network_id"],
            [f"{SCHEMA}.networks.id"],
            name="fk_hydraulic_network_revisions_network",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_revision_id", "network_id"],
            [
                f"{SCHEMA}.network_revisions.id",
                f"{SCHEMA}.network_revisions.network_id",
            ],
            name="fk_hydraulic_network_revisions_parent_network",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["public.users.id"],
            name="fk_hydraulic_network_revisions_created_by",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )

    op.create_foreign_key(
        "fk_hydraulic_networks_current_revision",
        "networks",
        "network_revisions",
        ["current_revision_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.execute(
        f"CREATE OR REPLACE FUNCTION {SCHEMA}.guard_current_revision_network() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "IF NEW.current_revision_id IS NOT NULL AND NOT EXISTS ("
        f"SELECT 1 FROM {SCHEMA}.network_revisions "
        "WHERE id = NEW.current_revision_id AND network_id = NEW.id"
        ") THEN RAISE EXCEPTION 'network current revision must belong to its network'; "
        "END IF; RETURN NEW; END $$"
    )
    op.execute(
        f"CREATE TRIGGER trg_hydraulic_networks_current_revision_network "
        f"BEFORE INSERT OR UPDATE OF current_revision_id ON {SCHEMA}.networks "
        f"FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.guard_current_revision_network()"
    )

    _create_spatial_tables()


def _create_spatial_tables() -> None:
    op.create_table(
        "network_nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stable_key", sa.String(128), nullable=False),
        sa.Column("kind", _enum("network_node_kind"), nullable=False),
        sa.Column("geometry_wkt", sa.Text(), nullable=False),
        sa.Column("min_lon", sa.Float(), nullable=False),
        sa.Column("min_lat", sa.Float(), nullable=False),
        sa.Column("max_lon", sa.Float(), nullable=False),
        sa.Column("max_lat", sa.Float(), nullable=False),
        sa.Column("invert_elevation_m", sa.Float(), nullable=True),
        sa.Column("rim_elevation_m", sa.Float(), nullable=True),
        sa.Column("max_depth_m", sa.Float(), nullable=True),
        sa.Column(
            "properties", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "min_lon <= max_lon AND min_lat <= max_lat",
            name="ck_hydraulic_nodes_bbox_order",
        ),
        sa.CheckConstraint(
            "min_lon >= -180 AND max_lon <= 180",
            name="ck_hydraulic_nodes_bbox_longitude",
        ),
        sa.CheckConstraint(
            "min_lat >= -90 AND max_lat <= 90",
            name="ck_hydraulic_nodes_bbox_latitude",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_network_nodes"),
        sa.UniqueConstraint(
            "network_revision_id", "stable_key", name="uq_hydraulic_nodes_revision_key"
        ),
        sa.UniqueConstraint(
            "network_revision_id", "id", name="uq_hydraulic_nodes_revision_id"
        ),
        sa.ForeignKeyConstraint(
            ["network_id"],
            [f"{SCHEMA}.networks.id"],
            name="fk_hydraulic_nodes_network",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["network_revision_id", "network_id"],
            [
                f"{SCHEMA}.network_revisions.id",
                f"{SCHEMA}.network_revisions.network_id",
            ],
            name="fk_hydraulic_nodes_revision_network",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "pipe_segments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stable_key", sa.String(128), nullable=False),
        sa.Column("upstream_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("downstream_node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nominal_diameter_m", sa.Float(), nullable=False),
        sa.Column("length_m", sa.Float(), nullable=False),
        sa.Column("roughness_manning_n", sa.Float(), nullable=False),
        sa.Column("geometry_wkt", sa.Text(), nullable=False),
        sa.Column("min_lon", sa.Float(), nullable=False),
        sa.Column("min_lat", sa.Float(), nullable=False),
        sa.Column("max_lon", sa.Float(), nullable=False),
        sa.Column("max_lat", sa.Float(), nullable=False),
        sa.Column(
            "properties", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "upstream_node_id <> downstream_node_id",
            name="ck_hydraulic_pipes_distinct_nodes",
        ),
        sa.CheckConstraint(
            "nominal_diameter_m > 0 AND length_m > 0 AND roughness_manning_n > 0",
            name="ck_hydraulic_pipes_positive_dimensions",
        ),
        sa.CheckConstraint(
            "min_lon <= max_lon AND min_lat <= max_lat",
            name="ck_hydraulic_pipes_bbox_order",
        ),
        sa.CheckConstraint(
            "min_lon >= -180 AND max_lon <= 180",
            name="ck_hydraulic_pipes_bbox_longitude",
        ),
        sa.CheckConstraint(
            "min_lat >= -90 AND max_lat <= 90",
            name="ck_hydraulic_pipes_bbox_latitude",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_pipe_segments"),
        sa.UniqueConstraint(
            "network_revision_id", "stable_key", name="uq_hydraulic_pipes_revision_key"
        ),
        sa.UniqueConstraint(
            "network_revision_id", "id", name="uq_hydraulic_pipes_revision_id"
        ),
        sa.ForeignKeyConstraint(
            ["network_id"],
            [f"{SCHEMA}.networks.id"],
            name="fk_hydraulic_pipes_network",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["network_revision_id", "network_id"],
            [
                f"{SCHEMA}.network_revisions.id",
                f"{SCHEMA}.network_revisions.network_id",
            ],
            name="fk_hydraulic_pipes_revision_network",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["network_revision_id", "upstream_node_id"],
            [
                f"{SCHEMA}.network_nodes.network_revision_id",
                f"{SCHEMA}.network_nodes.id",
            ],
            name="fk_hydraulic_pipes_upstream_node_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["network_revision_id", "downstream_node_id"],
            [
                f"{SCHEMA}.network_nodes.network_revision_id",
                f"{SCHEMA}.network_nodes.id",
            ],
            name="fk_hydraulic_pipes_downstream_node_revision",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "catchments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stable_key", sa.String(128), nullable=False),
        sa.Column("area_m2", sa.Float(), nullable=False),
        sa.Column("outfall_node_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("geometry_wkt", sa.Text(), nullable=False),
        sa.Column("min_lon", sa.Float(), nullable=False),
        sa.Column("min_lat", sa.Float(), nullable=False),
        sa.Column("max_lon", sa.Float(), nullable=False),
        sa.Column("max_lat", sa.Float(), nullable=False),
        sa.Column(
            "properties", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("area_m2 > 0", name="ck_hydraulic_catchments_positive_area"),
        sa.CheckConstraint(
            "min_lon <= max_lon AND min_lat <= max_lat",
            name="ck_hydraulic_catchments_bbox_order",
        ),
        sa.CheckConstraint(
            "min_lon >= -180 AND max_lon <= 180",
            name="ck_hydraulic_catchments_bbox_longitude",
        ),
        sa.CheckConstraint(
            "min_lat >= -90 AND max_lat <= 90",
            name="ck_hydraulic_catchments_bbox_latitude",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_catchments"),
        sa.UniqueConstraint(
            "network_revision_id",
            "stable_key",
            name="uq_hydraulic_catchments_revision_key",
        ),
        sa.UniqueConstraint(
            "network_revision_id", "id", name="uq_hydraulic_catchments_revision_id"
        ),
        sa.ForeignKeyConstraint(
            ["network_id"],
            [f"{SCHEMA}.networks.id"],
            name="fk_hydraulic_catchments_network",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["network_revision_id", "network_id"],
            [
                f"{SCHEMA}.network_revisions.id",
                f"{SCHEMA}.network_revisions.network_id",
            ],
            name="fk_hydraulic_catchments_revision_network",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["network_revision_id", "outfall_node_id"],
            [
                f"{SCHEMA}.network_nodes.network_revision_id",
                f"{SCHEMA}.network_nodes.id",
            ],
            name="fk_hydraulic_catchments_outfall_revision",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )


def _create_rainfall_tables() -> None:
    op.create_table(
        "rainfall_scenarios",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("root_scenario_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", _enum("rainfall_scenario_kind"), nullable=False),
        sa.Column("return_period_years", sa.String(length=16), nullable=True),
        sa.Column("step_seconds", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "source_type", sa.String(16), nullable=False, server_default="synthetic"
        ),
        sa.Column("algorithm", sa.String(128), nullable=False),
        sa.Column("random_seed", sa.BigInteger(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "kind IN ('design','custom','synthetic')", name="ck_hydraulic_rainfall_kind"
        ),
        sa.CheckConstraint(
            "step_seconds IN (60,300,900)", name="ck_hydraulic_rainfall_step"
        ),
        sa.CheckConstraint(
            "duration_seconds > 0 AND duration_seconds <= 259200",
            name="ck_hydraulic_rainfall_duration",
        ),
        sa.CheckConstraint(
            "return_period_years IS NULL OR return_period_years IN "
            "('1','2','3','5','10','20','50','100','custom')",
            name="ck_hydraulic_rainfall_return_period",
        ),
        sa.CheckConstraint("version >= 1", name="ck_hydraulic_rainfall_version"),
        sa.CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_hydraulic_rainfall_content_hash_length",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_rainfall_scenarios"),
        sa.UniqueConstraint(
            "network_id", "id", name="uq_hydraulic_rainfall_scenarios_network_id"
        ),
        sa.ForeignKeyConstraint(
            ["network_id"],
            [f"{SCHEMA}.networks.id"],
            name="fk_hydraulic_rainfall_network",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["root_scenario_id"],
            [f"{SCHEMA}.rainfall_scenarios.id"],
            name="fk_hydraulic_rainfall_root",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["public.users.id"],
            name="fk_hydraulic_rainfall_created_by",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_hydraulic_rainfall_network_created",
        "rainfall_scenarios",
        ["network_id", "created_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "rainfall_points",
        sa.Column("scenario_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("point_index", sa.Integer(), nullable=False),
        sa.Column("valid_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("intensity_mm_h", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "point_index >= 0", name="ck_hydraulic_rainfall_points_index"
        ),
        sa.CheckConstraint(
            "intensity_mm_h >= 0", name="ck_hydraulic_rainfall_points_intensity"
        ),
        sa.PrimaryKeyConstraint(
            "scenario_id", "point_index", name="pk_hydraulic_rainfall_points"
        ),
        sa.UniqueConstraint(
            "scenario_id", "valid_at", name="uq_hydraulic_rainfall_points_time"
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            [f"{SCHEMA}.rainfall_scenarios.id"],
            name="fk_hydraulic_rainfall_points_scenario",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )


def _create_lineage_tables() -> None:
    op.create_table(
        "parameter_sets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column(
            "parameters", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "kind IN ('default','calibrated')", name="ck_hydraulic_parameter_sets_kind"
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_hydraulic_parameter_sets_hash_length",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_parameter_sets"),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["public.users.id"],
            name="fk_hydraulic_parameter_sets_created_by",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "network_target_manifests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("manifest", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "schema_version >= 1", name="ck_hydraulic_target_manifests_version"
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_hydraulic_target_manifests_hash_length",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_target_manifests"),
        sa.UniqueConstraint(
            "network_revision_id",
            "schema_version",
            name="uq_hydraulic_target_manifests_revision_schema",
        ),
        sa.UniqueConstraint(
            "id", "content_sha256", name="uq_hydraulic_target_manifests_id_hash"
        ),
        sa.UniqueConstraint(
            "network_revision_id",
            "id",
            "content_sha256",
            name="uq_hydraulic_target_manifests_revision_id_hash",
        ),
        sa.ForeignKeyConstraint(
            ["network_revision_id", "network_id"],
            [
                f"{SCHEMA}.network_revisions.id",
                f"{SCHEMA}.network_revisions.network_id",
            ],
            name="fk_hydraulic_target_manifests_revision_network",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "initial_state_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state_kind", sa.String(16), nullable=False),
        sa.Column("observed_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "state_kind IN ('dry','warm')", name="ck_hydraulic_initial_state_kind"
        ),
        sa.CheckConstraint(
            "((state_kind = 'dry' AND observed_as_of IS NULL) OR "
            "(state_kind = 'warm' AND observed_as_of IS NOT NULL))",
            name="ck_hydraulic_initial_state_observation",
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_hydraulic_initial_state_hash_length",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_initial_state_snapshots"),
        sa.UniqueConstraint(
            "id", "content_sha256", name="uq_hydraulic_initial_states_id_hash"
        ),
        sa.UniqueConstraint(
            "network_revision_id",
            "id",
            "content_sha256",
            name="uq_hydraulic_initial_states_revision_id_hash",
        ),
        sa.ForeignKeyConstraint(
            ["network_revision_id", "network_id"],
            [
                f"{SCHEMA}.network_revisions.id",
                f"{SCHEMA}.network_revisions.network_id",
            ],
            name="fk_hydraulic_initial_states_revision_network",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )


def _create_simulation_tables() -> None:
    op.create_table(
        "idempotency_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "generation >= 1", name="ck_hydraulic_idempotency_generation"
        ),
        sa.CheckConstraint(
            "length(request_fingerprint) = 64",
            name="ck_hydraulic_idempotency_request_hash_length",
        ),
        sa.CheckConstraint(
            "length(trim(idempotency_key)) BETWEEN 8 AND 128",
            name="ck_hydraulic_idempotency_key_length",
        ),
        sa.CheckConstraint(
            "expires_at = created_at + INTERVAL '24 hours'",
            name="ck_hydraulic_idempotency_expiry",
        ),
        sa.CheckConstraint(
            "retired_at IS NULL OR retired_at = expires_at",
            name="ck_hydraulic_idempotency_retirement",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_idempotency_records"),
        sa.UniqueConstraint(
            "scope",
            "idempotency_key",
            "user_id",
            "generation",
            name="uq_hydraulic_idempotency_generation",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["public.users.id"],
            name="fk_hydraulic_idempotency_user",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_hydraulic_idempotency_active",
        "idempotency_records",
        ["scope", "idempotency_key", "user_id"],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
        schema=SCHEMA,
    )

    op.create_table(
        "network_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(16), nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "base_network_revision_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "status",
            _enum("simulation_status"),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("progress", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("phase", sa.String(16), nullable=False, server_default="accepted"),
        sa.Column("attempt", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column(
            "max_attempts", sa.SmallInteger(), nullable=False, server_default="3"
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_backoff_seconds", sa.Integer(), nullable=True),
        sa.Column("last_retry_error_code", sa.String(128), nullable=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "idempotency_record_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("parser_version", sa.String(64), nullable=True),
        sa.Column("profile_version", sa.String(64), nullable=True),
        sa.Column("seed_algorithm_version", sa.String(64), nullable=True),
        sa.Column("lease_owner", sa.String(255), nullable=True),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_artifact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "max_attempts BETWEEN 2 AND 3",
            name="ck_hydraulic_network_jobs_max_attempts",
        ),
        sa.CheckConstraint(
            "attempt BETWEEN 0 AND max_attempts",
            name="ck_hydraulic_network_jobs_attempt",
        ),
        sa.CheckConstraint(
            "((status = 'retry_wait') = (next_attempt_at IS NOT NULL))",
            name="ck_hydraulic_network_jobs_retry_due",
        ),
        sa.CheckConstraint(
            "((status = 'retry_wait' AND retry_backoff_seconds IS NOT NULL "
            "AND retry_backoff_seconds > 0 AND last_retry_error_code IS NOT NULL "
            "AND length(trim(last_retry_error_code)) > 0) OR "
            "(status <> 'retry_wait' AND retry_backoff_seconds IS NULL "
            "AND last_retry_error_code IS NULL))",
            name="ck_hydraulic_network_jobs_retry_metadata",
        ),
        sa.CheckConstraint(
            "((status IN ('leased','running') AND lease_owner IS NOT NULL "
            "AND lease_token IS NOT NULL AND lease_until IS NOT NULL) OR "
            "(status NOT IN ('leased','running') AND lease_owner IS NULL "
            "AND lease_token IS NULL AND lease_until IS NULL))",
            name="ck_hydraulic_network_jobs_lease",
        ),
        sa.CheckConstraint(
            "job_type IN ('import','validate','seed')",
            name="ck_hydraulic_network_jobs_type",
        ),
        sa.CheckConstraint(
            "progress BETWEEN 0 AND 100", name="ck_hydraulic_network_jobs_progress"
        ),
        sa.CheckConstraint(
            "phase IN ('accepted','staging','parsing','validating',"
            "'publishing','cleanup')",
            name="ck_hydraulic_network_jobs_phase",
        ),
        sa.CheckConstraint(
            "((progress = 100) = (status = 'completed'))",
            name="ck_hydraulic_network_jobs_progress_terminal",
        ),
        sa.CheckConstraint(
            "length(request_fingerprint) = 64",
            name="ck_hydraulic_network_jobs_request_hash_length",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_network_jobs"),
        sa.UniqueConstraint(
            "idempotency_record_id", name="uq_hydraulic_network_jobs_idempotency"
        ),
        sa.ForeignKeyConstraint(
            ["network_id"],
            [f"{SCHEMA}.networks.id"],
            name="fk_hydraulic_network_jobs_network",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["base_network_revision_id", "network_id"],
            [
                f"{SCHEMA}.network_revisions.id",
                f"{SCHEMA}.network_revisions.network_id",
            ],
            name="fk_hydraulic_network_jobs_revision_network",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["idempotency_record_id"],
            [f"{SCHEMA}.idempotency_records.id"],
            name="fk_hydraulic_network_jobs_idempotency",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["public.users.id"],
            name="fk_hydraulic_network_jobs_created_by",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_hydraulic_network_jobs_retry_due",
        "network_jobs",
        ["status", "next_attempt_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "network_job_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_no", sa.SmallInteger(), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("status", _enum("simulation_attempt_status"), nullable=False),
        sa.Column("workspace_ref", sa.String(1024), nullable=True),
        sa.Column("parser_result_sha256", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attempt_no BETWEEN 1 AND 3",
            name="ck_hydraulic_network_job_attempts_number",
        ),
        sa.CheckConstraint(
            "fencing_token > 0",
            name="ck_hydraulic_network_job_attempts_fencing_positive",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_network_job_attempts"),
        sa.UniqueConstraint(
            "job_id", "attempt_no", name="uq_hydraulic_network_job_attempts_number"
        ),
        sa.UniqueConstraint(
            "job_id",
            "fencing_token",
            name="uq_hydraulic_network_job_attempts_fencing",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            [f"{SCHEMA}.network_jobs.id"],
            name="fk_hydraulic_network_job_attempts_job",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "simulations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_revision_content_sha256", sa.String(64), nullable=False),
        sa.Column("target_manifest_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("snapshot_content_sha256", sa.String(64), nullable=False),
        sa.Column("initial_state_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("initial_state_content_sha256", sa.String(64), nullable=False),
        sa.Column(
            "rainfall_scenario_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("parameter_set_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "idempotency_record_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("frame_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("solver_selection", sa.String(16), nullable=False),
        sa.Column("requested_solver_id", sa.String(32), nullable=True),
        sa.Column("solver_id", sa.String(32), nullable=False),
        sa.Column("solver_version", sa.String(128), nullable=False),
        sa.Column("engine_binary_sha256", sa.String(64), nullable=True),
        sa.Column("solver_fallback_reason", sa.Text(), nullable=True),
        sa.Column("solver_config_hash", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "status",
            _enum("simulation_status"),
            nullable=False,
            server_default="queued",
        ),
        sa.Column(
            "phase",
            sa.String(16),
            nullable=False,
            server_default="accepted",
        ),
        sa.Column("progress", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("attempt", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column(
            "max_attempts", sa.SmallInteger(), nullable=False, server_default="3"
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_backoff_seconds", sa.Integer(), nullable=True),
        sa.Column("last_retry_error_code", sa.String(128), nullable=True),
        sa.Column("lease_owner", sa.String(255), nullable=True),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("published_attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("published_attempt_no", sa.SmallInteger(), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result_summary", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "progress BETWEEN 0 AND 100", name="ck_hydraulic_simulations_progress"
        ),
        sa.CheckConstraint(
            "duration_seconds > 0 AND frame_interval_seconds > 0 "
            "AND duration_seconds % frame_interval_seconds = 0",
            name="ck_hydraulic_simulations_time_grid",
        ),
        sa.CheckConstraint(
            "max_attempts BETWEEN 2 AND 3",
            name="ck_hydraulic_simulations_max_attempts",
        ),
        sa.CheckConstraint(
            "attempt BETWEEN 0 AND max_attempts",
            name="ck_hydraulic_simulations_attempt",
        ),
        sa.CheckConstraint(
            "((status = 'retry_wait') = (next_attempt_at IS NOT NULL))",
            name="ck_hydraulic_simulations_retry_due",
        ),
        sa.CheckConstraint(
            "((status = 'retry_wait' AND retry_backoff_seconds IS NOT NULL "
            "AND retry_backoff_seconds > 0 AND last_retry_error_code IS NOT NULL "
            "AND length(trim(last_retry_error_code)) > 0) OR "
            "(status <> 'retry_wait' AND retry_backoff_seconds IS NULL "
            "AND last_retry_error_code IS NULL))",
            name="ck_hydraulic_simulations_retry_metadata",
        ),
        sa.CheckConstraint(
            "((status IN ('leased','running') AND lease_owner IS NOT NULL "
            "AND lease_token IS NOT NULL AND lease_until IS NOT NULL) OR "
            "(status NOT IN ('leased','running') AND lease_owner IS NULL "
            "AND lease_token IS NULL AND lease_until IS NULL))",
            name="ck_hydraulic_simulations_lease",
        ),
        sa.CheckConstraint(
            "(solver_selection = 'explicit' AND requested_solver_id IS NOT NULL) OR "
            "(solver_selection = 'auto')",
            name="ck_hydraulic_simulations_solver_selection",
        ),
        sa.CheckConstraint(
            "phase IN ('accepted','validating','solving','quality',"
            "'persisting','finalizing')",
            name="ck_hydraulic_simulations_phase",
        ),
        sa.CheckConstraint(
            "((status = 'completed') = (published_attempt_id IS NOT NULL "
            "AND published_attempt_no IS NOT NULL))",
            name="ck_hydraulic_simulations_published_attempt",
        ),
        sa.CheckConstraint(
            "((progress = 100) = (status = 'completed'))",
            name="ck_hydraulic_simulations_progress_terminal",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_simulations"),
        sa.UniqueConstraint(
            "idempotency_record_id", name="uq_hydraulic_simulations_idempotency"
        ),
        sa.UniqueConstraint(
            "id", "network_revision_id", name="uq_hydraulic_simulations_id_revision"
        ),
        sa.UniqueConstraint(
            "id",
            "network_id",
            "network_revision_id",
            "target_manifest_id",
            "target_manifest_sha256",
            name="uq_hydraulic_simulations_id_lineage",
        ),
        sa.UniqueConstraint(
            "id", "fencing_token", name="uq_hydraulic_simulations_id_fencing"
        ),
        sa.UniqueConstraint(
            "id",
            "network_revision_id",
            "fencing_token",
            name="uq_hydraulic_simulations_id_revision_fencing",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["public.users.id"],
            name="fk_hydraulic_simulations_requested_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["network_id"],
            [f"{SCHEMA}.networks.id"],
            name="fk_hydraulic_simulations_network",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["network_revision_id"],
            [f"{SCHEMA}.network_revisions.id"],
            name="fk_hydraulic_simulations_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["network_revision_id", "network_id"],
            [
                f"{SCHEMA}.network_revisions.id",
                f"{SCHEMA}.network_revisions.network_id",
            ],
            name="fk_hydraulic_simulations_revision_network",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["network_revision_id", "network_revision_content_sha256"],
            [
                f"{SCHEMA}.network_revisions.id",
                f"{SCHEMA}.network_revisions.content_sha256",
            ],
            name="fk_hydraulic_simulations_revision_hash",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["network_revision_id", "target_manifest_id", "target_manifest_sha256"],
            [
                f"{SCHEMA}.network_target_manifests.network_revision_id",
                f"{SCHEMA}.network_target_manifests.id",
                f"{SCHEMA}.network_target_manifests.content_sha256",
            ],
            name="fk_hydraulic_simulations_target_manifest",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["network_revision_id", "initial_state_id", "initial_state_content_sha256"],
            [
                f"{SCHEMA}.initial_state_snapshots.network_revision_id",
                f"{SCHEMA}.initial_state_snapshots.id",
                f"{SCHEMA}.initial_state_snapshots.content_sha256",
            ],
            name="fk_hydraulic_simulations_initial_state",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rainfall_scenario_id"],
            [f"{SCHEMA}.rainfall_scenarios.id"],
            name="fk_hydraulic_simulations_rainfall",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parameter_set_id"],
            [f"{SCHEMA}.parameter_sets.id"],
            name="fk_hydraulic_simulations_parameter_set",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["idempotency_record_id"],
            [f"{SCHEMA}.idempotency_records.id"],
            name="fk_hydraulic_simulations_idempotency",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_hydraulic_simulations_network_status",
        "simulations",
        ["network_id", "status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_hydraulic_simulations_retry_due",
        "simulations",
        ["status", "next_attempt_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "simulation_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("simulation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_manifest_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("attempt_no", sa.SmallInteger(), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("status", _enum("simulation_attempt_status"), nullable=False),
        sa.Column("frame_count", sa.Integer(), nullable=True),
        sa.Column("first_frame_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_frame_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("frame_manifest_sha256", sa.String(64), nullable=True),
        sa.Column("summary_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("quality_report_sha256", sa.String(64), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attempt_no BETWEEN 1 AND 3",
            name="ck_hydraulic_simulation_attempts_number",
        ),
        sa.CheckConstraint(
            "fencing_token > 0",
            name="ck_hydraulic_simulation_attempts_fencing_positive",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_simulation_attempts"),
        sa.UniqueConstraint(
            "simulation_id",
            "attempt_no",
            name="uq_hydraulic_simulation_attempts_number",
        ),
        sa.UniqueConstraint(
            "id", "simulation_id", name="uq_hydraulic_simulation_attempts_id_simulation"
        ),
        sa.UniqueConstraint(
            "id",
            "simulation_id",
            "attempt_no",
            name="uq_hydraulic_simulation_attempts_id_simulation_attempt",
        ),
        sa.UniqueConstraint(
            "simulation_id",
            "fencing_token",
            name="uq_hydraulic_simulation_attempts_fencing",
        ),
        sa.ForeignKeyConstraint(
            ["simulation_id"],
            [f"{SCHEMA}.simulations.id"],
            name="fk_hydraulic_simulation_attempts_simulation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["simulation_id", "network_revision_id"],
            [
                f"{SCHEMA}.simulations.id",
                f"{SCHEMA}.simulations.network_revision_id",
            ],
            name="fk_hydraulic_simulation_attempts_simulation_revision",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["network_revision_id", "target_manifest_id", "target_manifest_sha256"],
            [
                f"{SCHEMA}.network_target_manifests.network_revision_id",
                f"{SCHEMA}.network_target_manifests.id",
                f"{SCHEMA}.network_target_manifests.content_sha256",
            ],
            name="fk_hydraulic_simulation_attempts_target_manifest",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_hydraulic_simulations_published_attempt_lineage",
        "simulations",
        "simulation_attempts",
        ["id", "published_attempt_id", "published_attempt_no"],
        ["simulation_id", "id", "attempt_no"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )

    op.create_table(
        "simulation_frame_registrations",
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("frame_index", sa.Integer(), nullable=False),
        sa.Column("frame_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_bytes", sa.BigInteger(), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("quality_status", _enum("frame_quality_status"), nullable=False),
        sa.CheckConstraint(
            "frame_index >= 0", name="ck_hydraulic_frame_registrations_index"
        ),
        sa.CheckConstraint(
            "payload_bytes >= 0", name="ck_hydraulic_frame_registrations_bytes"
        ),
        sa.PrimaryKeyConstraint(
            "attempt_id", "frame_index", name="pk_hydraulic_frame_registrations"
        ),
        sa.UniqueConstraint(
            "attempt_id", "frame_time", name="uq_hydraulic_frame_registrations_time"
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            [f"{SCHEMA}.simulation_attempts.id"],
            name="fk_hydraulic_frame_registrations_attempt",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "simulation_frames",
        sa.Column("frame_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("simulation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_no", sa.SmallInteger(), nullable=False),
        sa.Column("frame_index", sa.Integer(), nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_manifest_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("solver_id", sa.String(32), nullable=False),
        sa.Column("solver_version", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("payload_bytes", sa.BigInteger(), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("quality_status", _enum("frame_quality_status"), nullable=False),
        sa.CheckConstraint(
            "frame_index >= 0", name="ck_hydraulic_simulation_frames_index"
        ),
        sa.CheckConstraint(
            "payload_bytes >= 0", name="ck_hydraulic_simulation_frames_bytes"
        ),
        sa.CheckConstraint(
            "schema_version >= 1", name="ck_hydraulic_simulation_frames_schema_version"
        ),
        sa.CheckConstraint(
            "length(payload_sha256) = 64",
            name="ck_hydraulic_simulation_frames_payload_hash_length",
        ),
        # Every hypertable unique/primary key includes the time partition key.
        # Cross-partition (attempt_id, frame_index) uniqueness belongs to the
        # ordinary PostgreSQL registration table above.
        sa.PrimaryKeyConstraint(
            "frame_time",
            "attempt_id",
            "frame_index",
            name="pk_hydraulic_simulation_frames",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            [f"{SCHEMA}.simulation_attempts.id"],
            name="fk_hydraulic_simulation_frames_attempt",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["simulation_id"],
            [f"{SCHEMA}.simulations.id"],
            name="fk_hydraulic_simulation_frames_simulation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["network_id"],
            [f"{SCHEMA}.networks.id"],
            name="fk_hydraulic_simulation_frames_network",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["network_revision_id"],
            [f"{SCHEMA}.network_revisions.id"],
            name="fk_hydraulic_simulation_frames_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id", "simulation_id", "attempt_no"],
            [
                f"{SCHEMA}.simulation_attempts.id",
                f"{SCHEMA}.simulation_attempts.simulation_id",
                f"{SCHEMA}.simulation_attempts.attempt_no",
            ],
            name="fk_hydraulic_simulation_frames_simulation_attempt",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["simulation_id", "network_revision_id"],
            [
                f"{SCHEMA}.simulations.id",
                f"{SCHEMA}.simulations.network_revision_id",
            ],
            name="fk_hydraulic_simulation_frames_simulation_revision",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["network_revision_id", "network_id"],
            [
                f"{SCHEMA}.network_revisions.id",
                f"{SCHEMA}.network_revisions.network_id",
            ],
            name="fk_hydraulic_simulation_frames_revision_network",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["network_revision_id", "target_manifest_id", "target_manifest_sha256"],
            [
                f"{SCHEMA}.network_target_manifests.network_revision_id",
                f"{SCHEMA}.network_target_manifests.id",
                f"{SCHEMA}.network_target_manifests.content_sha256",
            ],
            name="fk_hydraulic_simulation_frames_target_manifest",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_hydraulic_simulation_frames_attempt_time",
        "simulation_frames",
        ["attempt_id", "frame_time"],
        schema=SCHEMA,
    )

    op.create_table(
        "simulation_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("simulation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("summary", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("summary_sha256", sa.String(64), nullable=False),
        sa.Column(
            "quality_report", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
        sa.Column("quality_report_sha256", sa.String(64), nullable=False),
        sa.Column("max_mass_residual_relative", sa.Float(), nullable=True),
        sa.Column("max_courant", sa.Float(), nullable=True),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "max_mass_residual_relative IS NULL OR max_mass_residual_relative >= 0",
            name="ck_hydraulic_simulation_results_mass_residual",
        ),
        sa.CheckConstraint(
            "max_courant IS NULL OR max_courant >= 0",
            name="ck_hydraulic_simulation_results_courant",
        ),
        sa.CheckConstraint(
            "length(summary_sha256) = 64 AND length(quality_report_sha256) = 64",
            name="ck_hydraulic_simulation_results_hash_length",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_simulation_results"),
        sa.UniqueConstraint(
            "simulation_id", name="uq_hydraulic_simulation_results_simulation"
        ),
        sa.UniqueConstraint(
            "attempt_id", name="uq_hydraulic_simulation_results_attempt"
        ),
        sa.ForeignKeyConstraint(
            ["simulation_id"],
            [f"{SCHEMA}.simulations.id"],
            name="fk_hydraulic_simulation_results_simulation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            [f"{SCHEMA}.simulation_attempts.id"],
            name="fk_hydraulic_simulation_results_attempt",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id", "simulation_id"],
            [
                f"{SCHEMA}.simulation_attempts.id",
                f"{SCHEMA}.simulation_attempts.simulation_id",
            ],
            name="fk_hydraulic_simulation_results_attempt_simulation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["simulation_id", "network_revision_id", "fencing_token"],
            [
                f"{SCHEMA}.simulations.id",
                f"{SCHEMA}.simulations.network_revision_id",
                f"{SCHEMA}.simulations.fencing_token",
            ],
            name="fk_hydraulic_simulation_results_simulation_fencing",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )


def _create_calibration_tables() -> None:
    op.create_table(
        "calibration_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "baseline_simulation_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "candidate_simulation_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("observation_dataset_sha256", sa.String(64), nullable=False),
        sa.Column("split_manifest", postgresql.JSONB(), nullable=False),
        sa.Column("split_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("calibration_config_hash", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("improved", sa.Boolean(), nullable=True),
        sa.Column(
            "status",
            _enum("calibration_status"),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("progress", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("attempt", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column(
            "max_attempts", sa.SmallInteger(), nullable=False, server_default="3"
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_backoff_seconds", sa.Integer(), nullable=True),
        sa.Column("last_retry_error_code", sa.String(128), nullable=True),
        sa.Column("lease_owner", sa.String(255), nullable=True),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "idempotency_record_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "progress BETWEEN 0 AND 100", name="ck_hydraulic_calibration_runs_progress"
        ),
        sa.CheckConstraint(
            "max_attempts BETWEEN 2 AND 3",
            name="ck_hydraulic_calibration_runs_max_attempts",
        ),
        sa.CheckConstraint(
            "attempt BETWEEN 0 AND max_attempts",
            name="ck_hydraulic_calibration_runs_attempt",
        ),
        sa.CheckConstraint(
            "((status = 'retry_wait') = (next_attempt_at IS NOT NULL))",
            name="ck_hydraulic_calibration_runs_retry_due",
        ),
        sa.CheckConstraint(
            "((status = 'retry_wait' AND retry_backoff_seconds IS NOT NULL "
            "AND retry_backoff_seconds > 0 AND last_retry_error_code IS NOT NULL "
            "AND length(trim(last_retry_error_code)) > 0) OR "
            "(status <> 'retry_wait' AND retry_backoff_seconds IS NULL "
            "AND last_retry_error_code IS NULL))",
            name="ck_hydraulic_calibration_runs_retry_metadata",
        ),
        sa.CheckConstraint(
            "((status IN ('leased','running') AND lease_owner IS NOT NULL "
            "AND lease_token IS NOT NULL AND lease_until IS NOT NULL) OR "
            "(status NOT IN ('leased','running') AND lease_owner IS NULL "
            "AND lease_token IS NULL AND lease_until IS NULL))",
            name="ck_hydraulic_calibration_runs_lease",
        ),
        sa.CheckConstraint(
            "length(observation_dataset_sha256) = 64 "
            "AND length(split_manifest_sha256) = 64 "
            "AND length(calibration_config_hash) = 64",
            name="ck_hydraulic_calibration_runs_hash_length",
        ),
        sa.CheckConstraint(
            "((progress = 100) = (status = 'completed'))",
            name="ck_hydraulic_calibration_runs_progress_terminal",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_calibration_runs"),
        sa.UniqueConstraint(
            "idempotency_record_id", name="uq_hydraulic_calibration_runs_idempotency"
        ),
        sa.UniqueConstraint(
            "id",
            "observation_dataset_sha256",
            "split_manifest_sha256",
            name="uq_hydraulic_calibration_runs_dataset_split",
        ),
        sa.UniqueConstraint(
            "id", "fencing_token", name="uq_hydraulic_calibration_runs_id_fencing"
        ),
        sa.ForeignKeyConstraint(
            ["baseline_simulation_id"],
            [f"{SCHEMA}.simulations.id"],
            name="fk_hydraulic_calibration_runs_baseline",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["baseline_simulation_id", "network_revision_id"],
            [
                f"{SCHEMA}.simulations.id",
                f"{SCHEMA}.simulations.network_revision_id",
            ],
            name="fk_hydraulic_calibration_runs_baseline_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_simulation_id"],
            [f"{SCHEMA}.simulations.id"],
            name="fk_hydraulic_calibration_runs_candidate",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_simulation_id", "network_revision_id"],
            [
                f"{SCHEMA}.simulations.id",
                f"{SCHEMA}.simulations.network_revision_id",
            ],
            name="fk_hydraulic_calibration_runs_candidate_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["network_revision_id", "network_id"],
            [
                f"{SCHEMA}.network_revisions.id",
                f"{SCHEMA}.network_revisions.network_id",
            ],
            name="fk_hydraulic_calibration_runs_revision_network",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["public.users.id"],
            name="fk_hydraulic_calibration_runs_requested_by",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["idempotency_record_id"],
            [f"{SCHEMA}.idempotency_records.id"],
            name="fk_hydraulic_calibration_runs_idempotency",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_hydraulic_calibration_runs_status",
        "calibration_runs",
        ["status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_hydraulic_calibration_runs_retry_due",
        "calibration_runs",
        ["status", "next_attempt_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "calibration_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("calibration_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("observation_dataset_sha256", sa.String(64), nullable=False),
        sa.Column("split_manifest", postgresql.JSONB(), nullable=False),
        sa.Column("split_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("report_sha256", sa.String(64), nullable=False),
        sa.Column("artifact_uri", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "length(observation_dataset_sha256) = 64 "
            "AND length(split_manifest_sha256) = 64 "
            "AND length(report_sha256) = 64",
            name="ck_hydraulic_calibration_reports_hash_length",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_calibration_reports"),
        sa.UniqueConstraint(
            "calibration_run_id", name="uq_hydraulic_calibration_reports_run"
        ),
        sa.ForeignKeyConstraint(
            ["calibration_run_id"],
            [f"{SCHEMA}.calibration_runs.id"],
            name="fk_hydraulic_calibration_reports_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "calibration_run_id",
                "observation_dataset_sha256",
                "split_manifest_sha256",
            ],
            [
                f"{SCHEMA}.calibration_runs.id",
                f"{SCHEMA}.calibration_runs.observation_dataset_sha256",
                f"{SCHEMA}.calibration_runs.split_manifest_sha256",
            ],
            name="fk_hydraulic_calibration_reports_run_dataset",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )


def _create_forecast_tables() -> None:
    op.create_table(
        "probability_calibrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_schema_version", sa.Integer(), nullable=False),
        sa.Column("method", _enum("confidence_method"), nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("artifact_uri", sa.Text(), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("artifact_format", sa.String(32), nullable=False),
        sa.Column("lead_min_minutes", sa.Integer(), nullable=False),
        sa.Column("lead_max_minutes", sa.Integer(), nullable=False),
        sa.Column("magnitude_min", sa.Float(), nullable=False),
        sa.Column("magnitude_max", sa.Float(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "lead_min_minutes >= 0 AND lead_max_minutes >= lead_min_minutes",
            name="ck_hydraulic_probability_calibrations_lead",
        ),
        sa.CheckConstraint(
            "magnitude_min <= magnitude_max",
            name="ck_hydraulic_probability_calibrations_magnitude",
        ),
        sa.CheckConstraint(
            "valid_until > valid_from",
            name="ck_hydraulic_probability_calibrations_validity",
        ),
        sa.CheckConstraint(
            "length(artifact_sha256) = 64",
            name="ck_hydraulic_probability_calibrations_artifact_hash_length",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_probability_calibrations"),
        sa.UniqueConstraint(
            "id",
            "artifact_sha256",
            name="uq_hydraulic_probability_calibrations_id_hash",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "forecast_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_schema_version", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("alert_type", _enum("forecast_alert_type"), nullable=False),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("operator", sa.String(2), nullable=False),
        sa.Column("threshold_value", sa.Float(), nullable=False),
        sa.Column("threshold_unit", sa.String(32), nullable=False),
        sa.Column("clear_value", sa.Float(), nullable=True),
        sa.Column("expiry_grace_minutes", sa.Integer(), nullable=False),
        sa.Column("no_forecast_confirmations", sa.Integer(), nullable=False),
        sa.Column("clear_debounce_minutes", sa.Integer(), nullable=False),
        sa.Column("refresh_expected_shift_steps", sa.Integer(), nullable=False),
        sa.Column("refresh_peak_delta", sa.Float(), nullable=False),
        sa.Column("refresh_confidence_delta", sa.Float(), nullable=False),
        sa.Column("refresh_risk_delta", sa.Float(), nullable=False),
        sa.Column("risk_scale_m", sa.Float(), nullable=True),
        sa.Column("risk_scale_ratio", sa.Float(), nullable=True),
        sa.Column("artifact_uri", sa.Text(), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "rule_schema_version >= 1",
            name="ck_hydraulic_forecast_rules_schema_version",
        ),
        sa.CheckConstraint(
            "operator IN ('>','>=','<','<=')",
            name="ck_hydraulic_forecast_rules_operator",
        ),
        sa.CheckConstraint(
            "refresh_expected_shift_steps >= 0 AND refresh_peak_delta >= 0 "
            "AND refresh_confidence_delta >= 0 AND refresh_risk_delta >= 0",
            name="ck_hydraulic_forecast_rules_refresh",
        ),
        sa.CheckConstraint(
            "valid_until > valid_from", name="ck_hydraulic_forecast_rules_validity"
        ),
        sa.CheckConstraint(
            "length(artifact_sha256) = 64",
            name="ck_hydraulic_forecast_rules_artifact_hash_length",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_forecast_rules"),
        sa.UniqueConstraint(
            "alert_type", "version", name="uq_hydraulic_forecast_rules_type_version"
        ),
        sa.UniqueConstraint(
            "id", "artifact_sha256", name="uq_hydraulic_forecast_rules_id_hash"
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "nowcast_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_manifest_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_manifest_sha256", sa.String(64), nullable=False),
        sa.Column("simulation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "rainfall_scenario_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("model_version", sa.String(128), nullable=False),
        sa.Column("solver_id", sa.String(32), nullable=False),
        sa.Column("solver_version", sa.String(128), nullable=False),
        sa.Column("model_calibration_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "flood_forecast_probability_calibration_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("flood_forecast_probability_sha256", sa.String(64), nullable=False),
        sa.Column(
            "pipe_surcharge_probability_calibration_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("pipe_surcharge_probability_sha256", sa.String(64), nullable=False),
        sa.Column(
            "flood_forecast_rule_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("flood_forecast_rule_sha256", sa.String(64), nullable=False),
        sa.Column(
            "pipe_surcharge_rule_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("pipe_surcharge_rule_sha256", sa.String(64), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("forecast_origin_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            _enum("simulation_status"),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("attempt", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column(
            "max_attempts", sa.SmallInteger(), nullable=False, server_default="3"
        ),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_backoff_seconds", sa.Integer(), nullable=True),
        sa.Column("last_retry_error_code", sa.String(128), nullable=True),
        sa.Column("lease_owner", sa.String(255), nullable=True),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "max_attempts BETWEEN 2 AND 3",
            name="ck_hydraulic_nowcast_runs_max_attempts",
        ),
        sa.CheckConstraint(
            "attempt BETWEEN 0 AND max_attempts",
            name="ck_hydraulic_nowcast_runs_attempt",
        ),
        sa.CheckConstraint(
            "((status = 'retry_wait') = (next_attempt_at IS NOT NULL))",
            name="ck_hydraulic_nowcast_runs_retry_due",
        ),
        sa.CheckConstraint(
            "((status = 'retry_wait' AND retry_backoff_seconds IS NOT NULL "
            "AND retry_backoff_seconds > 0 AND last_retry_error_code IS NOT NULL "
            "AND length(trim(last_retry_error_code)) > 0) OR "
            "(status <> 'retry_wait' AND retry_backoff_seconds IS NULL "
            "AND last_retry_error_code IS NULL))",
            name="ck_hydraulic_nowcast_runs_retry_metadata",
        ),
        sa.CheckConstraint(
            "((status IN ('leased','running') AND lease_owner IS NOT NULL "
            "AND lease_token IS NOT NULL AND lease_until IS NOT NULL) OR "
            "(status NOT IN ('leased','running') AND lease_owner IS NULL "
            "AND lease_token IS NULL AND lease_until IS NULL))",
            name="ck_hydraulic_nowcast_runs_lease",
        ),
        sa.CheckConstraint(
            "deadline_at = scheduled_for + INTERVAL '180 seconds'",
            name="ck_hydraulic_nowcast_runs_deadline",
        ),
        sa.CheckConstraint(
            "length(target_manifest_sha256) = 64 AND length(config_hash) = 64 "
            "AND length(flood_forecast_probability_sha256) = 64 "
            "AND length(pipe_surcharge_probability_sha256) = 64 "
            "AND length(flood_forecast_rule_sha256) = 64 "
            "AND length(pipe_surcharge_rule_sha256) = 64",
            name="ck_hydraulic_nowcast_runs_hash_length",
        ),
        sa.CheckConstraint(
            "((status = 'completed') = (issued_at IS NOT NULL "
            "AND published_at IS NOT NULL))",
            name="ck_hydraulic_nowcast_runs_issued_terminal",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_nowcast_runs"),
        sa.UniqueConstraint(
            "network_id", "scheduled_for", name="uq_hydraulic_nowcast_runs_slot"
        ),
        sa.UniqueConstraint(
            "id", "issued_at", name="uq_hydraulic_nowcast_runs_id_issued"
        ),
        sa.UniqueConstraint(
            "id", "fencing_token", name="uq_hydraulic_nowcast_runs_id_fencing"
        ),
        sa.ForeignKeyConstraint(
            ["network_id"],
            [f"{SCHEMA}.networks.id"],
            name="fk_hydraulic_nowcast_runs_network",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["network_revision_id"],
            [f"{SCHEMA}.network_revisions.id"],
            name="fk_hydraulic_nowcast_runs_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["network_revision_id", "network_id"],
            [
                f"{SCHEMA}.network_revisions.id",
                f"{SCHEMA}.network_revisions.network_id",
            ],
            name="fk_hydraulic_nowcast_runs_revision_network",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["network_revision_id", "target_manifest_id", "target_manifest_sha256"],
            [
                f"{SCHEMA}.network_target_manifests.network_revision_id",
                f"{SCHEMA}.network_target_manifests.id",
                f"{SCHEMA}.network_target_manifests.content_sha256",
            ],
            name="fk_hydraulic_nowcast_runs_target_manifest",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["simulation_id"],
            [f"{SCHEMA}.simulations.id"],
            name="fk_hydraulic_nowcast_runs_simulation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "network_id",
                "simulation_id",
                "network_revision_id",
                "target_manifest_id",
                "target_manifest_sha256",
            ],
            [
                f"{SCHEMA}.simulations.id",
                f"{SCHEMA}.simulations.network_id",
                f"{SCHEMA}.simulations.network_revision_id",
                f"{SCHEMA}.simulations.target_manifest_id",
                f"{SCHEMA}.simulations.target_manifest_sha256",
            ],
            name="fk_hydraulic_nowcast_runs_simulation_lineage",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rainfall_scenario_id"],
            [f"{SCHEMA}.rainfall_scenarios.id"],
            name="fk_hydraulic_nowcast_runs_rainfall",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["network_id", "rainfall_scenario_id"],
            [
                f"{SCHEMA}.rainfall_scenarios.network_id",
                f"{SCHEMA}.rainfall_scenarios.id",
            ],
            name="fk_hydraulic_nowcast_runs_rainfall_network",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "flood_forecast_probability_calibration_id",
                "flood_forecast_probability_sha256",
            ],
            [
                f"{SCHEMA}.probability_calibrations.id",
                f"{SCHEMA}.probability_calibrations.artifact_sha256",
            ],
            name="fk_hydraulic_nowcast_runs_flood_probability",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "pipe_surcharge_probability_calibration_id",
                "pipe_surcharge_probability_sha256",
            ],
            [
                f"{SCHEMA}.probability_calibrations.id",
                f"{SCHEMA}.probability_calibrations.artifact_sha256",
            ],
            name="fk_hydraulic_nowcast_runs_pipe_probability",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["flood_forecast_rule_id", "flood_forecast_rule_sha256"],
            [
                f"{SCHEMA}.forecast_rules.id",
                f"{SCHEMA}.forecast_rules.artifact_sha256",
            ],
            name="fk_hydraulic_nowcast_runs_flood_rule",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["pipe_surcharge_rule_id", "pipe_surcharge_rule_sha256"],
            [
                f"{SCHEMA}.forecast_rules.id",
                f"{SCHEMA}.forecast_rules.artifact_sha256",
            ],
            name="fk_hydraulic_nowcast_runs_pipe_rule",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_hydraulic_nowcast_runs_network_scheduled",
        "nowcast_runs",
        ["network_id", "scheduled_for"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_hydraulic_nowcast_runs_retry_due",
        "nowcast_runs",
        ["status", "next_attempt_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "forecast_alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nowcast_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nowcast_issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("simulation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("network_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_manifest_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_manifest_sha256", sa.String(64), nullable=False),
        sa.Column(
            "probability_calibration_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("probability_calibration_sha256", sa.String(64), nullable=False),
        sa.Column("forecast_rule_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("forecast_rule_sha256", sa.String(64), nullable=False),
        sa.Column("alert_type", _enum("forecast_alert_type"), nullable=False),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_stable_key", sa.String(128), nullable=False),
        sa.Column("expected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lead_time_minutes", sa.Integer(), nullable=False),
        sa.Column("phase", _enum("forecast_alert_phase"), nullable=False),
        sa.Column("status", _enum("forecast_alert_status"), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("confidence_method", _enum("confidence_method"), nullable=False),
        sa.Column("threshold", postgresql.JSONB(), nullable=False),
        sa.Column("measurements", postgresql.JSONB(), nullable=False),
        sa.Column("risk_score", sa.Float(), nullable=False),
        sa.Column("risk_policy_version", sa.String(64), nullable=False),
        sa.Column("suppression_key", sa.String(64), nullable=False),
        sa.Column(
            "observation_ingest_event_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "target_type IN ('node','pipe_segment')",
            name="ck_hydraulic_forecast_alerts_target_type",
        ),
        sa.CheckConstraint(
            "lead_time_minutes >= 0", name="ck_hydraulic_forecast_alerts_lead"
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_hydraulic_forecast_alerts_confidence",
        ),
        sa.CheckConstraint(
            "risk_score >= 0 AND risk_score <= 100",
            name="ck_hydraulic_forecast_alerts_risk",
        ),
        sa.CheckConstraint(
            "(phase = 'observed') = "
            "(observation_ingest_event_id IS NOT NULL AND observed_at IS NOT NULL)",
            name="ck_hydraulic_forecast_alerts_phase_observation",
        ),
        sa.CheckConstraint(
            "length(target_manifest_sha256) = 64 "
            "AND length(probability_calibration_sha256) = 64 "
            "AND length(forecast_rule_sha256) = 64 "
            "AND length(suppression_key) = 64",
            name="ck_hydraulic_forecast_alerts_hash_length",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_forecast_alerts"),
        sa.ForeignKeyConstraint(
            ["network_id"],
            [f"{SCHEMA}.networks.id"],
            name="fk_hydraulic_forecast_alerts_network",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["nowcast_run_id"],
            [f"{SCHEMA}.nowcast_runs.id"],
            name="fk_hydraulic_forecast_alerts_nowcast",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["nowcast_run_id", "nowcast_issued_at"],
            [
                f"{SCHEMA}.nowcast_runs.id",
                f"{SCHEMA}.nowcast_runs.issued_at",
            ],
            name="fk_hydraulic_forecast_alerts_nowcast_issued",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["simulation_id"],
            [f"{SCHEMA}.simulations.id"],
            name="fk_hydraulic_forecast_alerts_simulation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "network_id",
                "simulation_id",
                "network_revision_id",
                "target_manifest_id",
                "target_manifest_sha256",
            ],
            [
                f"{SCHEMA}.simulations.id",
                f"{SCHEMA}.simulations.network_id",
                f"{SCHEMA}.simulations.network_revision_id",
                f"{SCHEMA}.simulations.target_manifest_id",
                f"{SCHEMA}.simulations.target_manifest_sha256",
            ],
            name="fk_hydraulic_forecast_alerts_simulation_lineage",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["probability_calibration_id"],
            [f"{SCHEMA}.probability_calibrations.id"],
            name="fk_hydraulic_forecast_alerts_calibration",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["probability_calibration_id", "probability_calibration_sha256"],
            [
                f"{SCHEMA}.probability_calibrations.id",
                f"{SCHEMA}.probability_calibrations.artifact_sha256",
            ],
            name="fk_hydraulic_forecast_alerts_probability_artifact",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["forecast_rule_id", "forecast_rule_sha256"],
            [
                f"{SCHEMA}.forecast_rules.id",
                f"{SCHEMA}.forecast_rules.artifact_sha256",
            ],
            name="fk_hydraulic_forecast_alerts_rule_artifact",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_hydraulic_forecast_alerts_risk",
        "forecast_alerts",
        ["network_id", "status", "risk_score"],
        schema=SCHEMA,
    )

    op.create_table(
        "forecast_alert_transitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alert_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(32), nullable=True),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("from_phase", sa.String(32), nullable=True),
        sa.Column("to_phase", sa.String(32), nullable=False),
        sa.Column("action", _enum("forecast_transition_action"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "idempotency_record_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_forecast_alert_transitions"),
        sa.UniqueConstraint(
            "alert_id",
            "aggregate_version",
            name="uq_hydraulic_forecast_transitions_version",
        ),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            [f"{SCHEMA}.forecast_alerts.id"],
            name="fk_hydraulic_forecast_transitions_alert",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["public.users.id"],
            name="fk_hydraulic_forecast_transitions_actor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["idempotency_record_id"],
            [f"{SCHEMA}.idempotency_records.id"],
            name="fk_hydraulic_forecast_transitions_idempotency",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )


def _create_result_retention(backend: str) -> None:
    op.create_table(
        "result_retention_policies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("table_name", sa.String(128), nullable=False),
        sa.Column("hypertable", sa.Boolean(), nullable=False),
        sa.Column("partition_column", sa.String(64), nullable=True),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("chunk_interval", sa.String(64), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "retention_days > 0", name="ck_hydraulic_result_retention_days"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hydraulic_result_retention_policies"),
        sa.UniqueConstraint("table_name", name="uq_hydraulic_result_retention_table"),
        schema=SCHEMA,
    )
    op.execute(
        "SELECT create_hypertable("
        f"'{SCHEMA}.simulation_frames', 'frame_time', "
        f"chunk_time_interval => INTERVAL '{RESULT_CHUNK_INTERVAL}', "
        "if_not_exists => TRUE, migrate_data => TRUE)"
    )
    op.execute(
        "SELECT add_retention_policy("
        f"'{SCHEMA}.simulation_frames', "
        f"drop_after => INTERVAL '{RESULT_RETENTION_DAYS} days', "
        "if_not_exists => TRUE, schedule_interval => INTERVAL '1 day')"
    )
    policy_table = sa.table(
        "result_retention_policies",
        sa.column("table_name", sa.String),
        sa.column("hypertable", sa.Boolean),
        sa.column("partition_column", sa.String),
        sa.column("retention_days", sa.Integer),
        sa.column("chunk_interval", sa.String),
        sa.column("enabled", sa.Boolean),
        schema=SCHEMA,
    )
    op.bulk_insert(
        policy_table,
        [
            {
                "table_name": "simulation_frames",
                "hypertable": True,
                "partition_column": "frame_time",
                "retention_days": RESULT_RETENTION_DAYS,
                "chunk_interval": RESULT_CHUNK_INTERVAL,
                "enabled": True,
            }
        ],
    )


def _add_postgis_geometry() -> None:
    for table_name, geometry_type, valid_predicate in (
        ("network_nodes", "POINT", "ST_IsValid(geometry) AND NOT ST_IsEmpty(geometry)"),
        (
            "pipe_segments",
            "LINESTRING",
            "ST_IsValid(geometry) AND NOT ST_IsEmpty(geometry) AND ST_NPoints(geometry) >= 2",
        ),
        (
            "catchments",
            "GEOMETRY",
            "ST_IsValid(geometry) AND NOT ST_IsEmpty(geometry) "
            "AND ST_GeometryType(geometry) IN ('ST_Polygon','ST_MultiPolygon')",
        ),
    ):
        op.add_column(
            table_name,
            sa.Column(
                "geometry",
                PostGISGeometry(geometry_type=geometry_type, srid=4326),
                sa.Computed(f"ST_GeomFromText(geometry_wkt, {4326})", persisted=True),
                nullable=False,
            ),
            schema=SCHEMA,
        )
        op.create_check_constraint(
            f"ck_hydraulic_{table_name}_geometry_valid",
            table_name,
            valid_predicate,
            schema=SCHEMA,
        )
        op.create_index(
            f"ix_hydraulic_{table_name}_geometry_gist",
            table_name,
            ["geometry"],
            postgresql_using="gist",
            schema=SCHEMA,
        )


def _insert_schema_metadata(backend: str) -> None:
    metadata_table = sa.table(
        "schema_metadata",
        sa.column("key", sa.String),
        sa.column("value", sa.String),
        sa.column("alembic_revision", sa.String),
        schema=SCHEMA,
    )
    op.bulk_insert(
        metadata_table,
        [
            {
                "key": "spatial_backend",
                "value": backend,
                "alembic_revision": revision,
            }
        ],
    )


def _protect_schema_metadata() -> None:
    op.execute(
        f"CREATE OR REPLACE FUNCTION {SCHEMA}.guard_schema_metadata() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "RAISE EXCEPTION 'hydraulic schema metadata is immutable'; "
        "END $$"
    )
    op.execute(
        f"DROP TRIGGER IF EXISTS trg_hydraulic_schema_metadata_immutable "
        f"ON {SCHEMA}.schema_metadata"
    )
    op.execute(
        f"CREATE TRIGGER trg_hydraulic_schema_metadata_immutable "
        f"BEFORE UPDATE OR DELETE ON {SCHEMA}.schema_metadata "
        "FOR EACH ROW EXECUTE FUNCTION "
        f"{SCHEMA}.guard_schema_metadata()"
    )


def upgrade() -> None:
    backend = _spatial_backend()
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    _create_extensions(backend)
    _create_enums()
    _create_network_tables()
    _create_rainfall_tables()
    _create_lineage_tables()
    _create_simulation_tables()
    _create_calibration_tables()
    _create_forecast_tables()
    _create_result_retention(backend)
    if backend == "postgis":
        _add_postgis_geometry()
    _insert_schema_metadata(backend)
    _protect_schema_metadata()


def _assert_hydraulic_schema_empty() -> None:
    # A network row is not the only possible hydraulic data root: parameter
    # sets and forecast artifacts can exist independently.  Refuse rollback
    # when any business table contains a row, while deliberately excluding the
    # migration-owned metadata and retention-policy bookkeeping tables.
    business_tables = (
        "networks",
        "network_revisions",
        "network_nodes",
        "pipe_segments",
        "catchments",
        "rainfall_scenarios",
        "rainfall_points",
        "parameter_sets",
        "network_target_manifests",
        "initial_state_snapshots",
        "idempotency_records",
        "network_jobs",
        "network_job_attempts",
        "simulations",
        "simulation_attempts",
        "simulation_frame_registrations",
        "simulation_frames",
        "simulation_results",
        "calibration_runs",
        "calibration_reports",
        "probability_calibrations",
        "forecast_rules",
        "nowcast_runs",
        "forecast_alerts",
        "forecast_alert_transitions",
    )
    checks = " OR ".join(
        f"EXISTS (SELECT 1 FROM {SCHEMA}.{table_name} LIMIT 1)"
        for table_name in business_tables
    )
    op.execute(
        "DO $$ BEGIN "
        f"IF {checks} THEN "
        "RAISE EXCEPTION 'hydraulic downgrade refused: hydraulic data exists'; "
        "END IF; END $$;"
    )


def _drop_tables_in_reverse_order() -> None:
    for table_name in (
        "network_job_attempts",
        "network_jobs",
        "forecast_alert_transitions",
        "forecast_alerts",
        "nowcast_runs",
        "forecast_rules",
        "probability_calibrations",
        "calibration_reports",
        "calibration_runs",
        "simulation_results",
        "simulation_frames",
        "simulation_frame_registrations",
    ):
        op.drop_table(table_name, schema=SCHEMA)

    op.drop_constraint(
        "fk_hydraulic_simulations_published_attempt_lineage",
        "simulations",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_table("simulation_attempts", schema=SCHEMA)
    op.drop_table("simulations", schema=SCHEMA)
    op.drop_table("idempotency_records", schema=SCHEMA)
    op.drop_table("rainfall_points", schema=SCHEMA)
    op.drop_table("rainfall_scenarios", schema=SCHEMA)
    op.drop_table("initial_state_snapshots", schema=SCHEMA)
    op.drop_table("network_target_manifests", schema=SCHEMA)
    op.drop_table("parameter_sets", schema=SCHEMA)
    op.drop_table("catchments", schema=SCHEMA)
    op.drop_table("pipe_segments", schema=SCHEMA)
    op.drop_table("network_nodes", schema=SCHEMA)
    op.drop_constraint(
        "fk_hydraulic_networks_current_revision",
        "networks",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_table("network_revisions", schema=SCHEMA)
    op.drop_table("networks", schema=SCHEMA)
    op.drop_table("result_retention_policies", schema=SCHEMA)
    op.drop_table("schema_metadata", schema=SCHEMA)


def _drop_enums() -> None:
    for name in reversed(tuple(ENUM_DEFINITIONS)):
        op.execute(sa.text(f"DROP TYPE IF EXISTS {SCHEMA}.{name}"))


def downgrade() -> None:
    # Production rollback is intentionally fail-closed.  Destructive downgrade
    # is reserved for an empty database or a disposable clone.
    _assert_hydraulic_schema_empty()
    _drop_tables_in_reverse_order()
    _drop_enums()
    # Triggers are removed with their tables, but their PL/pgSQL functions
    # remain schema-owned dependencies and must be dropped explicitly before
    # the schema itself.  Without this, PostgreSQL rejects DROP SCHEMA.
    op.execute(
        f"DROP FUNCTION IF EXISTS {SCHEMA}.guard_current_revision_network()"
    )
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.guard_schema_metadata()")
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
    # Extensions are shared with 0001/legacy deployment and are intentionally
    # retained.  This migration never drops or mutates a pre-existing table.
