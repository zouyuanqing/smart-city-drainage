"""SQLAlchemy ORM models for the hydraulic twin persistence contract.

The canonical spatial payload is always ``geometry_wkt`` plus a numeric
EPSG:4326 bounding box.  When the frozen process profile is ``postgis``, the
mapped tables additionally expose a typed PostGIS geometry column; the
``portable`` profile intentionally has no PostGIS ORM dependency.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship
from sqlalchemy.types import TypeDecorator, UserDefinedType

from app.core.config import settings
from app.models.db_models import Base

HYDRAULIC_SCHEMA = "hydraulic"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_UUID = postgresql.UUID(as_uuid=True)
_JSONB = postgresql.JSONB().with_variant(JSON(), "sqlite")


class _RainfallReturnPeriod(TypeDecorator):
    """Persist numeric return periods as text while preserving int DTO values."""

    impl = String(16)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError("return_period_years cannot be boolean")
        if isinstance(value, int):
            return str(value)
        if isinstance(value, str) and value == "custom":
            return value
        raise ValueError("return_period_years must be an integer or 'custom'")

    def process_result_value(self, value: Any, dialect: Any) -> int | str | None:
        if value is None:
            return None
        if isinstance(value, str) and value == "custom":
            return value
        return int(value)


class SpatialBackend(str, enum.Enum):
    postgis = "postgis"
    portable = "portable"


_ACTIVE_SPATIAL_BACKEND = SpatialBackend(settings.HYDRAULIC_SPATIAL_BACKEND)


class PostGISGeometry(UserDefinedType):
    """Minimal dependency-free PostGIS geometry type for ORM metadata.

    Reads should prefer the canonical WKT columns.  This type exists so a
    ``postgis`` deployment can select its generated geometry without requiring
    GeoAlchemy; portable processes never register a geometry column.
    """

    cache_ok = True

    def __init__(self, geometry_type: str, srid: int = 4326) -> None:
        self.geometry_type = geometry_type
        self.srid = srid

    def get_col_spec(self, **_: Any) -> str:
        return f"geometry({self.geometry_type},{self.srid})"


def _generated_geometry_column(geometry_type: str) -> Column[Any]:
    return Column(
        "geometry",
        PostGISGeometry(geometry_type),
        Computed("ST_GeomFromText(geometry_wkt, 4326)", persisted=True),
        nullable=False,
        _omit_from_statements=True,
    )


class NetworkStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    archived = "archived"


class NetworkNodeKind(str, enum.Enum):
    junction = "junction"
    outfall = "outfall"
    manhole = "manhole"
    storage = "storage"
    other = "other"


class RainfallScenarioKind(str, enum.Enum):
    design = "design"
    custom = "custom"
    synthetic = "synthetic"


class SimulationStatus(str, enum.Enum):
    queued = "queued"
    leased = "leased"
    running = "running"
    retry_wait = "retry_wait"
    cancelling = "cancelling"
    cancelled = "cancelled"
    completed = "completed"
    failed = "failed"
    timed_out = "timed_out"


class SimulationAttemptStatus(str, enum.Enum):
    running = "running"
    discarded = "discarded"
    published = "published"
    cleanup_pending = "cleanup_pending"


class FrameQualityStatus(str, enum.Enum):
    verified = "verified"
    warning = "warning"
    failed = "failed"


class CalibrationStatus(str, enum.Enum):
    queued = "queued"
    leased = "leased"
    running = "running"
    retry_wait = "retry_wait"
    cancelling = "cancelling"
    cancelled = "cancelled"
    completed = "completed"
    failed = "failed"
    timed_out = "timed_out"


class ForecastAlertType(str, enum.Enum):
    flood_forecast = "flood_forecast"
    pipe_surcharge = "pipe_surcharge"


class ForecastAlertStatus(str, enum.Enum):
    open = "open"
    acknowledged = "acknowledged"
    resolved = "resolved"
    expired = "expired"


class ForecastAlertPhase(str, enum.Enum):
    predicted = "predicted"
    observed = "observed"


class ConfidenceMethod(str, enum.Enum):
    ensemble_exceedance_frequency = "ensemble_exceedance_frequency"
    calibrated_residual = "calibrated_residual"
    conformal = "conformal"
    calibrated_regression = "calibrated_regression"


class ForecastTransitionAction(str, enum.Enum):
    create = "create"
    acknowledge = "acknowledge"
    observe = "observe"
    resolve = "resolve"
    expire = "expire"
    refresh = "refresh"
    escalate = "escalate"


class ImportSourceFormat(str, enum.Enum):
    """Contract §3.4 ``hydraulic.network_imports.source_format``."""

    inp = "inp"
    geojson = "geojson"


class ValidationRunStatus(str, enum.Enum):
    """Contract §3.4 ``hydraulic.validation_runs.status_value``."""

    valid = "valid"
    valid_with_warnings = "valid_with_warnings"
    invalid = "invalid"


class ValidationIssueSeverity(str, enum.Enum):
    """Contract §3.4 ``hydraulic.network_validation_issues.severity``."""

    error = "error"
    warning = "warning"
    info = "info"


class ObservationAuthRequirement(str, enum.Enum):
    """Contract §3.7 ``hydraulic.observation_trust_policies.auth_requirement``."""

    authenticated = "authenticated"
    allowlisted_local = "allowlisted_local"


class ObservationBindingRequirement(str, enum.Enum):
    """Contract §3.7 ``hydraulic.observation_trust_policies.binding_requirement``."""

    active_required = "active_required"
    none = "none"


class ObservationTrustLevel(str, enum.Enum):
    """Contract §3.7 trust level shared by policies and ingest events."""

    external_verified = "external_verified"
    external_unverified = "external_unverified"
    synthetic = "synthetic"
    mock = "mock"


class ObservationTrustPolicyResolution(str, enum.Enum):
    """Contract §3.7 ``hydraulic.observation_ingest_events.trust_policy_resolution``."""

    matched = "matched"
    missing = "missing"
    expired = "expired"


class ObservationIngestStatus(str, enum.Enum):
    """Contract §3.7 ``hydraulic.observation_ingest_events.status``."""

    accepted = "accepted"
    conflict = "conflict"
    quarantined = "quarantined"


class ObservationTargetType(str, enum.Enum):
    """Contract §3.7 ``hydraulic.observation_samples.target_type``."""

    node = "node"
    pipe_segment = "pipe_segment"


class ObservationMetric(str, enum.Enum):
    """Contract §3.7 ``hydraulic.observation_samples.metric``."""

    water_depth_m = "water_depth_m"
    flow_m3s = "flow_m3s"


class ObservationQualityFlag(str, enum.Enum):
    """Contract §3.7 ``hydraulic.observation_samples.quality_flag``."""

    good = "good"
    bad = "bad"
    missing = "missing"
    stale = "stale"


def _pg_enum(enum_type: type[enum.Enum], name: str) -> Enum:
    return Enum(
        enum_type,
        values_callable=lambda cls: [item.value for item in cls],
        name=name,
        schema=HYDRAULIC_SCHEMA,
        native_enum=True,
        create_type=False,
        validate_strings=True,
    )


def _hash_column(name: str = "content_sha256") -> Mapped[str]:
    return mapped_column(String(64), nullable=False, name=name)


def _durable_job_constraints(prefix: str) -> tuple[Any, ...]:
    """Database invariants shared by every durable hydraulic job surface."""

    return (
        CheckConstraint(
            "max_attempts BETWEEN 2 AND 3",
            name=f"ck_hydraulic_{prefix}_max_attempts",
        ),
        CheckConstraint(
            "attempt BETWEEN 0 AND max_attempts",
            name=f"ck_hydraulic_{prefix}_attempt",
        ),
        CheckConstraint(
            "((status = 'retry_wait') = (next_attempt_at IS NOT NULL))",
            name=f"ck_hydraulic_{prefix}_retry_due",
        ),
        CheckConstraint(
            "((status = 'retry_wait' AND retry_backoff_seconds IS NOT NULL "
            "AND retry_backoff_seconds > 0 AND last_retry_error_code IS NOT NULL "
            "AND length(trim(last_retry_error_code)) > 0) OR "
            "(status <> 'retry_wait' AND retry_backoff_seconds IS NULL "
            "AND last_retry_error_code IS NULL))",
            name=f"ck_hydraulic_{prefix}_retry_metadata",
        ),
        CheckConstraint(
            "((status IN ('leased','running') AND lease_owner IS NOT NULL "
            "AND lease_token IS NOT NULL AND lease_until IS NOT NULL) OR "
            "(status NOT IN ('leased','running') AND lease_owner IS NULL "
            "AND lease_token IS NULL AND lease_until IS NULL))",
            name=f"ck_hydraulic_{prefix}_lease",
        ),
    )


class SchemaMetadata(Base):
    __tablename__ = "schema_metadata"
    __table_args__ = (
        CheckConstraint(
            "key = 'spatial_backend'", name="ck_hydraulic_schema_metadata_key"
        ),
        CheckConstraint(
            "value IN ('postgis','portable')",
            name="ck_hydraulic_schema_metadata_profile",
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(String(32), nullable=False)
    alembic_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=_utcnow,
    )


class Network(Base):
    __tablename__ = "networks"
    __table_args__ = (
        UniqueConstraint("code", name="uq_hydraulic_networks_code"),
        CheckConstraint(
            "status IN ('draft','active','archived')",
            name="ck_hydraulic_networks_status",
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        _pg_enum(NetworkStatus, "network_status"),
        nullable=False,
        default=NetworkStatus.draft.value,
        server_default="draft",
    )
    current_revision_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.network_revisions.id",
            name="fk_hydraulic_networks_current_revision",
            ondelete="RESTRICT",
            use_alter=True,
        ),
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        _UUID, ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=_utcnow,
    )

    current_revision: Mapped[Optional["NetworkRevision"]] = relationship(
        "NetworkRevision", foreign_keys=[current_revision_id], post_update=True
    )
    revisions: Mapped[list["NetworkRevision"]] = relationship(
        "NetworkRevision",
        foreign_keys="NetworkRevision.network_id",
        back_populates="network",
        order_by="NetworkRevision.revision_no",
    )
    nodes: Mapped[list["NetworkNode"]] = relationship(
        "NetworkNode", foreign_keys="NetworkNode.network_id", viewonly=True
    )
    pipes: Mapped[list["PipeSegment"]] = relationship(
        "PipeSegment", foreign_keys="PipeSegment.network_id", viewonly=True
    )
    catchments: Mapped[list["Catchment"]] = relationship(
        "Catchment", foreign_keys="Catchment.network_id", viewonly=True
    )
    rainfall_scenarios: Mapped[list["RainfallScenario"]] = relationship(
        "RainfallScenario", back_populates="network"
    )
    simulations: Mapped[list["SimulationRun"]] = relationship(
        "SimulationRun", back_populates="network"
    )


class NetworkRevision(Base):
    __tablename__ = "network_revisions"
    __table_args__ = (
        UniqueConstraint(
            "network_id", "revision_no", name="uq_hydraulic_network_revisions_number"
        ),
        UniqueConstraint(
            "network_id",
            "content_sha256",
            name="uq_hydraulic_network_revisions_content",
        ),
        UniqueConstraint(
            "id", "content_sha256", name="uq_hydraulic_network_revisions_id_content"
        ),
        UniqueConstraint(
            "id", "network_id", name="uq_hydraulic_network_revisions_id_network"
        ),
        CheckConstraint(
            "revision_no >= 1", name="ck_hydraulic_network_revisions_number_positive"
        ),
        CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_hydraulic_network_revisions_hash_length",
        ),
        CheckConstraint(
            "source_kind IN ('import','seed','crud')",
            name="ck_hydraulic_network_revisions_source_kind",
        ),
        CheckConstraint(
            "parent_revision_id IS NULL OR parent_revision_id <> id",
            name="ck_hydraulic_network_revisions_parent_not_self",
        ),
        ForeignKeyConstraint(
            ["parent_revision_id", "network_id"],
            [
                f"{HYDRAULIC_SCHEMA}.network_revisions.id",
                f"{HYDRAULIC_SCHEMA}.network_revisions.network_id",
            ],
            name="fk_hydraulic_network_revisions_parent_network",
            ondelete="RESTRICT",
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    network_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.networks.id",
            name="fk_hydraulic_network_revisions_network",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_revision_id: Mapped[Optional[uuid.UUID]] = mapped_column(_UUID)
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(
        _JSONB, nullable=False, default=dict
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        _UUID, ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    network: Mapped["Network"] = relationship(
        "Network", foreign_keys=[network_id], back_populates="revisions"
    )
    parent_revision: Mapped[Optional["NetworkRevision"]] = relationship(
        "NetworkRevision",
        remote_side=[id],
        foreign_keys=[parent_revision_id, network_id],
        viewonly=True,
    )
    nodes: Mapped[list["NetworkNode"]] = relationship(
        "NetworkNode",
        foreign_keys="[NetworkNode.network_revision_id, NetworkNode.network_id]",
        back_populates="revision",
        passive_deletes=True,
    )
    pipes: Mapped[list["PipeSegment"]] = relationship(
        "PipeSegment",
        foreign_keys="[PipeSegment.network_revision_id, PipeSegment.network_id]",
        back_populates="revision",
        passive_deletes=True,
    )
    catchments: Mapped[list["Catchment"]] = relationship(
        "Catchment",
        foreign_keys="[Catchment.network_revision_id, Catchment.network_id]",
        back_populates="revision",
        passive_deletes=True,
    )


_node_constraints: tuple[Any, ...] = (
    UniqueConstraint(
        "network_revision_id", "stable_key", name="uq_hydraulic_nodes_revision_key"
    ),
    UniqueConstraint(
        "network_revision_id", "id", name="uq_hydraulic_nodes_revision_id"
    ),
    CheckConstraint(
        "min_lon <= max_lon AND min_lat <= max_lat",
        name="ck_hydraulic_nodes_bbox_order",
    ),
    CheckConstraint(
        "min_lon >= -180 AND max_lon <= 180", name="ck_hydraulic_nodes_bbox_longitude"
    ),
    CheckConstraint(
        "min_lat >= -90 AND max_lat <= 90", name="ck_hydraulic_nodes_bbox_latitude"
    ),
    ForeignKeyConstraint(
        ["network_revision_id", "network_id"],
        [
            f"{HYDRAULIC_SCHEMA}.network_revisions.id",
            f"{HYDRAULIC_SCHEMA}.network_revisions.network_id",
        ],
        name="fk_hydraulic_nodes_revision_network",
        ondelete="RESTRICT",
    ),
    {"schema": HYDRAULIC_SCHEMA},
)
if _ACTIVE_SPATIAL_BACKEND is SpatialBackend.postgis:
    _node_constraints = (
        *_node_constraints[:-1],
        CheckConstraint(
            "ST_IsValid(geometry) AND NOT ST_IsEmpty(geometry)",
            name="ck_hydraulic_network_nodes_geometry_valid",
        ),
        Index("ix_hydraulic_nodes_geometry_gist", "geometry", postgresql_using="gist"),
        _node_constraints[-1],
    )


class NetworkNode(Base):
    __tablename__ = "network_nodes"
    __table_args__ = _node_constraints

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    network_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.networks.id",
            name="fk_hydraulic_nodes_network",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    network_revision_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    stable_key: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(
        _pg_enum(NetworkNodeKind, "network_node_kind"),
        nullable=False,
    )
    geometry_wkt: Mapped[str] = mapped_column(Text, nullable=False)
    min_lon: Mapped[float] = mapped_column(Float, nullable=False)
    min_lat: Mapped[float] = mapped_column(Float, nullable=False)
    max_lon: Mapped[float] = mapped_column(Float, nullable=False)
    max_lat: Mapped[float] = mapped_column(Float, nullable=False)
    invert_elevation_m: Mapped[Optional[float]] = mapped_column(Float)
    rim_elevation_m: Mapped[Optional[float]] = mapped_column(Float)
    max_depth_m: Mapped[Optional[float]] = mapped_column(Float)
    properties: Mapped[dict[str, Any]] = mapped_column(
        _JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    if _ACTIVE_SPATIAL_BACKEND is SpatialBackend.postgis:
        geometry: Mapped[str] = declared_attr(  # type: ignore[assignment]
            lambda cls: _generated_geometry_column("POINT")
        )

    network: Mapped["Network"] = relationship(
        "Network", foreign_keys=[network_id], viewonly=True
    )
    revision: Mapped["NetworkRevision"] = relationship(
        "NetworkRevision",
        foreign_keys=[network_revision_id, network_id],
        back_populates="nodes",
        viewonly=True,
    )


_pipe_constraints: tuple[Any, ...] = (
    UniqueConstraint(
        "network_revision_id", "stable_key", name="uq_hydraulic_pipes_revision_key"
    ),
    UniqueConstraint(
        "network_revision_id", "id", name="uq_hydraulic_pipes_revision_id"
    ),
    ForeignKeyConstraint(
        ["network_revision_id", "upstream_node_id"],
        [
            f"{HYDRAULIC_SCHEMA}.network_nodes.network_revision_id",
            f"{HYDRAULIC_SCHEMA}.network_nodes.id",
        ],
        name="fk_hydraulic_pipes_upstream_node_revision",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["network_revision_id", "downstream_node_id"],
        [
            f"{HYDRAULIC_SCHEMA}.network_nodes.network_revision_id",
            f"{HYDRAULIC_SCHEMA}.network_nodes.id",
        ],
        name="fk_hydraulic_pipes_downstream_node_revision",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "upstream_node_id <> downstream_node_id",
        name="ck_hydraulic_pipes_distinct_nodes",
    ),
    CheckConstraint(
        "nominal_diameter_m > 0 AND length_m > 0 AND roughness_manning_n > 0",
        name="ck_hydraulic_pipes_positive_dimensions",
    ),
    CheckConstraint(
        "min_lon <= max_lon AND min_lat <= max_lat",
        name="ck_hydraulic_pipes_bbox_order",
    ),
    CheckConstraint(
        "min_lon >= -180 AND max_lon <= 180",
        name="ck_hydraulic_pipes_bbox_longitude",
    ),
    CheckConstraint(
        "min_lat >= -90 AND max_lat <= 90",
        name="ck_hydraulic_pipes_bbox_latitude",
    ),
    ForeignKeyConstraint(
        ["network_revision_id", "network_id"],
        [
            f"{HYDRAULIC_SCHEMA}.network_revisions.id",
            f"{HYDRAULIC_SCHEMA}.network_revisions.network_id",
        ],
        name="fk_hydraulic_pipes_revision_network",
        ondelete="RESTRICT",
    ),
    {"schema": HYDRAULIC_SCHEMA},
)
if _ACTIVE_SPATIAL_BACKEND is SpatialBackend.postgis:
    _pipe_constraints = (
        *_pipe_constraints[:-1],
        CheckConstraint(
            "ST_IsValid(geometry) AND NOT ST_IsEmpty(geometry) "
            "AND ST_NPoints(geometry) >= 2",
            name="ck_hydraulic_pipe_segments_geometry_valid",
        ),
        Index("ix_hydraulic_pipes_geometry_gist", "geometry", postgresql_using="gist"),
        _pipe_constraints[-1],
    )


class PipeSegment(Base):
    __tablename__ = "pipe_segments"
    __table_args__ = _pipe_constraints

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    network_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.networks.id",
            name="fk_hydraulic_pipes_network",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    network_revision_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    stable_key: Mapped[str] = mapped_column(String(128), nullable=False)
    upstream_node_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    downstream_node_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    nominal_diameter_m: Mapped[float] = mapped_column(Float, nullable=False)
    length_m: Mapped[float] = mapped_column(Float, nullable=False)
    roughness_manning_n: Mapped[float] = mapped_column(Float, nullable=False)
    geometry_wkt: Mapped[str] = mapped_column(Text, nullable=False)
    min_lon: Mapped[float] = mapped_column(Float, nullable=False)
    min_lat: Mapped[float] = mapped_column(Float, nullable=False)
    max_lon: Mapped[float] = mapped_column(Float, nullable=False)
    max_lat: Mapped[float] = mapped_column(Float, nullable=False)
    properties: Mapped[dict[str, Any]] = mapped_column(
        _JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    if _ACTIVE_SPATIAL_BACKEND is SpatialBackend.postgis:
        geometry: Mapped[str] = declared_attr(  # type: ignore[assignment]
            lambda cls: _generated_geometry_column("LINESTRING")
        )

    network: Mapped["Network"] = relationship(
        "Network", foreign_keys=[network_id], viewonly=True
    )
    revision: Mapped["NetworkRevision"] = relationship(
        "NetworkRevision",
        foreign_keys=[network_revision_id, network_id],
        back_populates="pipes",
        viewonly=True,
    )
    upstream_node: Mapped["NetworkNode"] = relationship(
        "NetworkNode",
        foreign_keys=[network_revision_id, upstream_node_id],
        viewonly=True,
    )
    downstream_node: Mapped["NetworkNode"] = relationship(
        "NetworkNode",
        foreign_keys=[network_revision_id, downstream_node_id],
        viewonly=True,
    )


_catchment_constraints: tuple[Any, ...] = (
    UniqueConstraint(
        "network_revision_id", "stable_key", name="uq_hydraulic_catchments_revision_key"
    ),
    UniqueConstraint(
        "network_revision_id", "id", name="uq_hydraulic_catchments_revision_id"
    ),
    ForeignKeyConstraint(
        ["network_revision_id", "outfall_node_id"],
        [
            f"{HYDRAULIC_SCHEMA}.network_nodes.network_revision_id",
            f"{HYDRAULIC_SCHEMA}.network_nodes.id",
        ],
        name="fk_hydraulic_catchments_outfall_revision",
        ondelete="RESTRICT",
    ),
    CheckConstraint("area_m2 > 0", name="ck_hydraulic_catchments_positive_area"),
    CheckConstraint(
        "min_lon <= max_lon AND min_lat <= max_lat",
        name="ck_hydraulic_catchments_bbox_order",
    ),
    CheckConstraint(
        "min_lon >= -180 AND max_lon <= 180",
        name="ck_hydraulic_catchments_bbox_longitude",
    ),
    CheckConstraint(
        "min_lat >= -90 AND max_lat <= 90",
        name="ck_hydraulic_catchments_bbox_latitude",
    ),
    ForeignKeyConstraint(
        ["network_revision_id", "network_id"],
        [
            f"{HYDRAULIC_SCHEMA}.network_revisions.id",
            f"{HYDRAULIC_SCHEMA}.network_revisions.network_id",
        ],
        name="fk_hydraulic_catchments_revision_network",
        ondelete="RESTRICT",
    ),
    {"schema": HYDRAULIC_SCHEMA},
)
if _ACTIVE_SPATIAL_BACKEND is SpatialBackend.postgis:
    _catchment_constraints = (
        *_catchment_constraints[:-1],
        CheckConstraint(
            "ST_IsValid(geometry) AND NOT ST_IsEmpty(geometry) "
            "AND ST_GeometryType(geometry) IN ('ST_Polygon','ST_MultiPolygon')",
            name="ck_hydraulic_catchments_geometry_valid",
        ),
        Index(
            "ix_hydraulic_catchments_geometry_gist", "geometry", postgresql_using="gist"
        ),
        _catchment_constraints[-1],
    )


class Catchment(Base):
    __tablename__ = "catchments"
    __table_args__ = _catchment_constraints

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    network_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.networks.id",
            name="fk_hydraulic_catchments_network",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    network_revision_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    stable_key: Mapped[str] = mapped_column(String(128), nullable=False)
    area_m2: Mapped[float] = mapped_column(Float, nullable=False)
    outfall_node_id: Mapped[Optional[uuid.UUID]] = mapped_column(_UUID)
    geometry_wkt: Mapped[str] = mapped_column(Text, nullable=False)
    min_lon: Mapped[float] = mapped_column(Float, nullable=False)
    min_lat: Mapped[float] = mapped_column(Float, nullable=False)
    max_lon: Mapped[float] = mapped_column(Float, nullable=False)
    max_lat: Mapped[float] = mapped_column(Float, nullable=False)
    properties: Mapped[dict[str, Any]] = mapped_column(
        _JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    if _ACTIVE_SPATIAL_BACKEND is SpatialBackend.postgis:
        geometry: Mapped[str] = declared_attr(  # type: ignore[assignment]
            lambda cls: _generated_geometry_column("GEOMETRY")
        )

    network: Mapped["Network"] = relationship(
        "Network", foreign_keys=[network_id], viewonly=True
    )
    revision: Mapped["NetworkRevision"] = relationship(
        "NetworkRevision",
        foreign_keys=[network_revision_id, network_id],
        back_populates="catchments",
        viewonly=True,
    )
    outfall_node: Mapped[Optional["NetworkNode"]] = relationship(
        "NetworkNode",
        foreign_keys=[network_revision_id, outfall_node_id],
        viewonly=True,
    )


class RainfallScenario(Base):
    __tablename__ = "rainfall_scenarios"
    __table_args__ = (
        UniqueConstraint(
            "network_id", "id", name="uq_hydraulic_rainfall_scenarios_network_id"
        ),
        CheckConstraint(
            "kind IN ('design','custom','synthetic')", name="ck_hydraulic_rainfall_kind"
        ),
        CheckConstraint(
            "step_seconds IN (60,300,900)", name="ck_hydraulic_rainfall_step"
        ),
        CheckConstraint(
            "duration_seconds > 0 AND duration_seconds <= 259200",
            name="ck_hydraulic_rainfall_duration",
        ),
        CheckConstraint(
            "return_period_years IS NULL OR return_period_years IN "
            "('1','2','3','5','10','20','50','100','custom')",
            name="ck_hydraulic_rainfall_return_period",
        ),
        CheckConstraint("version >= 1", name="ck_hydraulic_rainfall_version"),
        CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_hydraulic_rainfall_content_hash_length",
        ),
        Index("ix_hydraulic_rainfall_network_created", "network_id", "created_at"),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    network_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.networks.id",
            name="fk_hydraulic_rainfall_network",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    root_scenario_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.rainfall_scenarios.id",
            name="fk_hydraulic_rainfall_root",
            ondelete="RESTRICT",
        ),
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(
        _pg_enum(RainfallScenarioKind, "rainfall_scenario_kind"), nullable=False
    )
    return_period_years: Mapped[Optional[int | str]] = mapped_column(
        _RainfallReturnPeriod
    )
    step_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="synthetic", server_default="synthetic"
    )
    algorithm: Mapped[str] = mapped_column(String(128), nullable=False)
    random_seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        _UUID, ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    network: Mapped["Network"] = relationship(
        "Network", back_populates="rainfall_scenarios"
    )
    points: Mapped[list["RainfallPoint"]] = relationship(
        "RainfallPoint",
        back_populates="scenario",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="RainfallPoint.point_index",
    )


class RainfallPoint(Base):
    __tablename__ = "rainfall_points"
    __table_args__ = (
        UniqueConstraint(
            "scenario_id", "valid_at", name="uq_hydraulic_rainfall_points_time"
        ),
        CheckConstraint("point_index >= 0", name="ck_hydraulic_rainfall_points_index"),
        CheckConstraint(
            "intensity_mm_h >= 0", name="ck_hydraulic_rainfall_points_intensity"
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    scenario_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.rainfall_scenarios.id",
            name="fk_hydraulic_rainfall_points_scenario",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    point_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    valid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    intensity_mm_h: Mapped[float] = mapped_column(Float, nullable=False)

    scenario: Mapped["RainfallScenario"] = relationship(
        "RainfallScenario", back_populates="points"
    )


class ParameterSet(Base):
    __tablename__ = "parameter_sets"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('default','calibrated')", name="ck_hydraulic_parameter_sets_kind"
        ),
        CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_hydraulic_parameter_sets_hash_length",
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(_JSONB, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        _UUID, ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NetworkTargetManifest(Base):
    __tablename__ = "network_target_manifests"
    __table_args__ = (
        UniqueConstraint(
            "network_revision_id",
            "schema_version",
            name="uq_hydraulic_target_manifests_revision_schema",
        ),
        UniqueConstraint(
            "id", "content_sha256", name="uq_hydraulic_target_manifests_id_hash"
        ),
        UniqueConstraint(
            "network_revision_id",
            "id",
            "content_sha256",
            name="uq_hydraulic_target_manifests_revision_id_hash",
        ),
        CheckConstraint(
            "schema_version >= 1", name="ck_hydraulic_target_manifests_version"
        ),
        CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_hydraulic_target_manifests_hash_length",
        ),
        ForeignKeyConstraint(
            ["network_revision_id", "network_id"],
            [
                f"{HYDRAULIC_SCHEMA}.network_revisions.id",
                f"{HYDRAULIC_SCHEMA}.network_revisions.network_id",
            ],
            name="fk_hydraulic_target_manifests_revision_network",
            ondelete="RESTRICT",
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    network_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    network_revision_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(_JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class InitialStateSnapshot(Base):
    __tablename__ = "initial_state_snapshots"
    __table_args__ = (
        CheckConstraint(
            "state_kind IN ('dry','warm')", name="ck_hydraulic_initial_state_kind"
        ),
        CheckConstraint(
            "((state_kind = 'dry' AND observed_as_of IS NULL) OR "
            "(state_kind = 'warm' AND observed_as_of IS NOT NULL))",
            name="ck_hydraulic_initial_state_observation",
        ),
        CheckConstraint(
            "length(content_sha256) = 64", name="ck_hydraulic_initial_state_hash_length"
        ),
        UniqueConstraint(
            "id", "content_sha256", name="uq_hydraulic_initial_states_id_hash"
        ),
        UniqueConstraint(
            "network_revision_id",
            "id",
            "content_sha256",
            name="uq_hydraulic_initial_states_revision_id_hash",
        ),
        ForeignKeyConstraint(
            ["network_revision_id", "network_id"],
            [
                f"{HYDRAULIC_SCHEMA}.network_revisions.id",
                f"{HYDRAULIC_SCHEMA}.network_revisions.network_id",
            ],
            name="fk_hydraulic_initial_states_revision_network",
            ondelete="RESTRICT",
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    network_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    network_revision_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    state_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    observed_as_of: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(_JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "scope",
            "idempotency_key",
            "user_id",
            "generation",
            name="uq_hydraulic_idempotency_generation",
        ),
        Index(
            "ix_hydraulic_idempotency_active",
            "scope",
            "idempotency_key",
            "user_id",
            unique=True,
            postgresql_where=text("retired_at IS NULL"),
            sqlite_where=text("retired_at IS NULL"),
        ),
        CheckConstraint("generation >= 1", name="ck_hydraulic_idempotency_generation"),
        CheckConstraint(
            "length(request_fingerprint) = 64",
            name="ck_hydraulic_idempotency_request_hash_length",
        ),
        CheckConstraint(
            "idempotency_key IS NOT NULL "
            "AND length(trim(idempotency_key)) BETWEEN 8 AND 128",
            name="ck_hydraulic_idempotency_key_length",
        ),
        CheckConstraint(
            "expires_at > created_at", name="ck_hydraulic_idempotency_expiry"
        ),
        CheckConstraint(
            "retired_at IS NULL OR retired_at = expires_at",
            name="ck_hydraulic_idempotency_retirement",
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    scope: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[Optional[uuid.UUID]] = mapped_column(_UUID)
    response_status: Mapped[Optional[int]] = mapped_column(Integer)
    response_body: Mapped[Optional[dict[str, Any]]] = mapped_column(_JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    retired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class NetworkJob(Base):
    __tablename__ = "network_jobs"
    __table_args__ = (
        *_durable_job_constraints("network_jobs"),
        CheckConstraint(
            "job_type IN ('import','validate','seed')",
            name="ck_hydraulic_network_jobs_type",
        ),
        CheckConstraint(
            "progress BETWEEN 0 AND 100", name="ck_hydraulic_network_jobs_progress"
        ),
        CheckConstraint(
            "phase IN ('accepted','staging','parsing','validating',"
            "'publishing','cleanup')",
            name="ck_hydraulic_network_jobs_phase",
        ),
        CheckConstraint(
            "((progress = 100) = (status = 'completed'))",
            name="ck_hydraulic_network_jobs_progress_terminal",
        ),
        CheckConstraint(
            "length(request_fingerprint) = 64",
            name="ck_hydraulic_network_jobs_request_hash_length",
        ),
        UniqueConstraint(
            "idempotency_record_id", name="uq_hydraulic_network_jobs_idempotency"
        ),
        ForeignKeyConstraint(
            ["base_network_revision_id", "network_id"],
            [
                f"{HYDRAULIC_SCHEMA}.network_revisions.id",
                f"{HYDRAULIC_SCHEMA}.network_revisions.network_id",
            ],
            name="fk_hydraulic_network_jobs_revision_network",
            ondelete="RESTRICT",
        ),
        Index("ix_hydraulic_network_jobs_retry_due", "status", "next_attempt_at"),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    job_type: Mapped[str] = mapped_column(String(16), nullable=False)
    network_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.networks.id",
            name="fk_hydraulic_network_jobs_network",
            ondelete="RESTRICT",
        ),
    )
    base_network_revision_id: Mapped[Optional[uuid.UUID]] = mapped_column(_UUID)
    status: Mapped[str] = mapped_column(
        _pg_enum(SimulationStatus, "simulation_status"),
        nullable=False,
        default=SimulationStatus.queued.value,
        server_default="queued",
    )
    progress: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    phase: Mapped[str] = mapped_column(
        String(16), nullable=False, default="accepted", server_default="accepted"
    )
    attempt: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=3, server_default="3"
    )
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    retry_backoff_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    last_retry_error_code: Mapped[Optional[str]] = mapped_column(String(128))
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_record_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.idempotency_records.id",
            name="fk_hydraulic_network_jobs_idempotency",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    parser_version: Mapped[Optional[str]] = mapped_column(String(64))
    profile_version: Mapped[Optional[str]] = mapped_column(String(64))
    seed_algorithm_version: Mapped[Optional[str]] = mapped_column(String(64))
    lease_owner: Mapped[Optional[str]] = mapped_column(String(255))
    lease_token: Mapped[Optional[uuid.UUID]] = mapped_column(_UUID)
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    fencing_token: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    deadline_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    result_artifact_id: Mapped[Optional[uuid.UUID]] = mapped_column(_UUID)
    error_code: Mapped[Optional[str]] = mapped_column(String(128))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=_utcnow,
    )

    attempts: Mapped[list["NetworkJobAttempt"]] = relationship(
        "NetworkJobAttempt",
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class NetworkJobAttempt(Base):
    __tablename__ = "network_job_attempts"
    __table_args__ = (
        UniqueConstraint(
            "job_id", "attempt_no", name="uq_hydraulic_network_job_attempts_number"
        ),
        UniqueConstraint(
            "job_id", "fencing_token", name="uq_hydraulic_network_job_attempts_fencing"
        ),
        CheckConstraint(
            "attempt_no BETWEEN 1 AND 3",
            name="ck_hydraulic_network_job_attempts_number",
        ),
        CheckConstraint(
            "fencing_token > 0",
            name="ck_hydraulic_network_job_attempts_fencing_positive",
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.network_jobs.id",
            name="fk_hydraulic_network_job_attempts_job",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    attempt_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        _pg_enum(SimulationAttemptStatus, "simulation_attempt_status"), nullable=False
    )
    workspace_ref: Mapped[Optional[str]] = mapped_column(String(1024))
    parser_result_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    error_code: Mapped[Optional[str]] = mapped_column(String(128))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    job: Mapped["NetworkJob"] = relationship("NetworkJob", back_populates="attempts")


class SimulationRun(Base):
    __tablename__ = "simulations"
    __table_args__ = (
        *_durable_job_constraints("simulations"),
        CheckConstraint(
            "progress BETWEEN 0 AND 100", name="ck_hydraulic_simulations_progress"
        ),
        CheckConstraint(
            "duration_seconds > 0 AND frame_interval_seconds > 0 "
            "AND duration_seconds % frame_interval_seconds = 0",
            name="ck_hydraulic_simulations_time_grid",
        ),
        CheckConstraint(
            "(solver_selection = 'explicit' AND requested_solver_id IS NOT NULL) "
            "OR solver_selection = 'auto'",
            name="ck_hydraulic_simulations_solver_selection",
        ),
        CheckConstraint(
            "phase IN ('accepted','validating','solving','quality',"
            "'persisting','finalizing')",
            name="ck_hydraulic_simulations_phase",
        ),
        CheckConstraint(
            "((status = 'completed') = (published_attempt_id IS NOT NULL "
            "AND published_attempt_no IS NOT NULL))",
            name="ck_hydraulic_simulations_published_attempt",
        ),
        CheckConstraint(
            "((progress = 100) = (status = 'completed'))",
            name="ck_hydraulic_simulations_progress_terminal",
        ),
        UniqueConstraint(
            "idempotency_record_id", name="uq_hydraulic_simulations_idempotency"
        ),
        UniqueConstraint(
            "id", "network_revision_id", name="uq_hydraulic_simulations_id_revision"
        ),
        UniqueConstraint(
            "id",
            "network_id",
            "network_revision_id",
            "target_manifest_id",
            "target_manifest_sha256",
            name="uq_hydraulic_simulations_id_lineage",
        ),
        UniqueConstraint(
            "id", "fencing_token", name="uq_hydraulic_simulations_id_fencing"
        ),
        UniqueConstraint(
            "id",
            "network_revision_id",
            "fencing_token",
            name="uq_hydraulic_simulations_id_revision_fencing",
        ),
        ForeignKeyConstraint(
            ["network_revision_id", "network_id"],
            [
                f"{HYDRAULIC_SCHEMA}.network_revisions.id",
                f"{HYDRAULIC_SCHEMA}.network_revisions.network_id",
            ],
            name="fk_hydraulic_simulations_revision_network",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["network_revision_id", "network_revision_content_sha256"],
            [
                f"{HYDRAULIC_SCHEMA}.network_revisions.id",
                f"{HYDRAULIC_SCHEMA}.network_revisions.content_sha256",
            ],
            name="fk_hydraulic_simulations_revision_hash",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["network_revision_id", "target_manifest_id", "target_manifest_sha256"],
            [
                f"{HYDRAULIC_SCHEMA}.network_target_manifests.network_revision_id",
                f"{HYDRAULIC_SCHEMA}.network_target_manifests.id",
                f"{HYDRAULIC_SCHEMA}.network_target_manifests.content_sha256",
            ],
            name="fk_hydraulic_simulations_target_manifest",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["id", "published_attempt_id", "published_attempt_no"],
            [
                f"{HYDRAULIC_SCHEMA}.simulation_attempts.simulation_id",
                f"{HYDRAULIC_SCHEMA}.simulation_attempts.id",
                f"{HYDRAULIC_SCHEMA}.simulation_attempts.attempt_no",
            ],
            name="fk_hydraulic_simulations_published_attempt_lineage",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["network_revision_id", "initial_state_id", "initial_state_content_sha256"],
            [
                f"{HYDRAULIC_SCHEMA}.initial_state_snapshots.network_revision_id",
                f"{HYDRAULIC_SCHEMA}.initial_state_snapshots.id",
                f"{HYDRAULIC_SCHEMA}.initial_state_snapshots.content_sha256",
            ],
            name="fk_hydraulic_simulations_initial_state",
            ondelete="RESTRICT",
        ),
        Index("ix_hydraulic_simulations_network_status", "network_id", "status"),
        Index("ix_hydraulic_simulations_retry_due", "status", "next_attempt_at"),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    requested_by: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    network_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.networks.id",
            name="fk_hydraulic_simulations_network",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    network_revision_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.network_revisions.id",
            name="fk_hydraulic_simulations_revision",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    network_revision_content_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    target_manifest_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    target_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    initial_state_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    initial_state_content_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    rainfall_scenario_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.rainfall_scenarios.id",
            name="fk_hydraulic_simulations_rainfall",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    parameter_set_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.parameter_sets.id",
            name="fk_hydraulic_simulations_parameter_set",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    idempotency_record_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.idempotency_records.id",
            name="fk_hydraulic_simulations_idempotency",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    frame_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    solver_selection: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_solver_id: Mapped[Optional[str]] = mapped_column(String(32))
    solver_id: Mapped[str] = mapped_column(String(32), nullable=False)
    solver_version: Mapped[str] = mapped_column(String(128), nullable=False)
    engine_binary_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    solver_fallback_reason: Mapped[Optional[str]] = mapped_column(Text)
    solver_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        _pg_enum(SimulationStatus, "simulation_status"),
        nullable=False,
        default=SimulationStatus.queued.value,
        server_default="queued",
    )
    phase: Mapped[str] = mapped_column(
        String(16), nullable=False, default="accepted", server_default="accepted"
    )
    progress: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    attempt: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=3, server_default="3"
    )
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    retry_backoff_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    last_retry_error_code: Mapped[Optional[str]] = mapped_column(String(128))
    lease_owner: Mapped[Optional[str]] = mapped_column(String(255))
    lease_token: Mapped[Optional[uuid.UUID]] = mapped_column(_UUID)
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    fencing_token: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    published_attempt_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.simulation_attempts.id",
            name="fk_hydraulic_simulations_published_attempt_id",
            ondelete="RESTRICT",
            use_alter=True,
        ),
    )
    published_attempt_no: Mapped[Optional[int]] = mapped_column(SmallInteger)
    cancel_requested_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    deadline_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[Optional[str]] = mapped_column(String(128))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    result_summary: Mapped[Optional[dict[str, Any]]] = mapped_column(_JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=_utcnow,
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    network: Mapped["Network"] = relationship("Network", back_populates="simulations")
    revision: Mapped["NetworkRevision"] = relationship(
        "NetworkRevision", foreign_keys=[network_revision_id]
    )
    target_manifest: Mapped["NetworkTargetManifest"] = relationship(
        "NetworkTargetManifest",
        foreign_keys=[network_revision_id, target_manifest_id, target_manifest_sha256],
        viewonly=True,
    )
    initial_state: Mapped["InitialStateSnapshot"] = relationship(
        "InitialStateSnapshot",
        foreign_keys=[
            network_revision_id,
            initial_state_id,
            initial_state_content_sha256,
        ],
        viewonly=True,
    )
    parameter_set: Mapped["ParameterSet"] = relationship("ParameterSet")
    rainfall_scenario: Mapped["RainfallScenario"] = relationship("RainfallScenario")
    attempts: Mapped[list["SimulationAttempt"]] = relationship(
        "SimulationAttempt",
        foreign_keys="SimulationAttempt.simulation_id",
        back_populates="simulation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    published_attempt: Mapped[Optional["SimulationAttempt"]] = relationship(
        "SimulationAttempt", foreign_keys=[published_attempt_id], post_update=True
    )
    results: Mapped[list["SimulationResult"]] = relationship(
        "SimulationResult",
        foreign_keys="SimulationResult.simulation_id",
        back_populates="simulation",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class SimulationAttempt(Base):
    __tablename__ = "simulation_attempts"
    __table_args__ = (
        UniqueConstraint(
            "simulation_id",
            "attempt_no",
            name="uq_hydraulic_simulation_attempts_number",
        ),
        UniqueConstraint(
            "id", "simulation_id", name="uq_hydraulic_simulation_attempts_id_simulation"
        ),
        UniqueConstraint(
            "id",
            "simulation_id",
            "attempt_no",
            name="uq_hydraulic_simulation_attempts_id_simulation_attempt",
        ),
        UniqueConstraint(
            "simulation_id",
            "fencing_token",
            name="uq_hydraulic_simulation_attempts_fencing",
        ),
        CheckConstraint(
            "attempt_no BETWEEN 1 AND 3", name="ck_hydraulic_simulation_attempts_number"
        ),
        CheckConstraint(
            "fencing_token > 0",
            name="ck_hydraulic_simulation_attempts_fencing_positive",
        ),
        ForeignKeyConstraint(
            ["simulation_id", "network_revision_id"],
            [
                f"{HYDRAULIC_SCHEMA}.simulations.id",
                f"{HYDRAULIC_SCHEMA}.simulations.network_revision_id",
            ],
            name="fk_hydraulic_simulation_attempts_simulation_revision",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["network_revision_id", "target_manifest_id", "target_manifest_sha256"],
            [
                f"{HYDRAULIC_SCHEMA}.network_target_manifests.network_revision_id",
                f"{HYDRAULIC_SCHEMA}.network_target_manifests.id",
                f"{HYDRAULIC_SCHEMA}.network_target_manifests.content_sha256",
            ],
            name="fk_hydraulic_simulation_attempts_target_manifest",
            ondelete="RESTRICT",
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    simulation_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.simulations.id",
            name="fk_hydraulic_simulation_attempts_simulation",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    network_revision_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    attempt_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        _pg_enum(SimulationAttemptStatus, "simulation_attempt_status"), nullable=False
    )
    target_manifest_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    target_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    frame_count: Mapped[Optional[int]] = mapped_column(Integer)
    first_frame_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    last_frame_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    frame_manifest_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    summary_id: Mapped[Optional[uuid.UUID]] = mapped_column(_UUID)
    quality_report_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    simulation: Mapped["SimulationRun"] = relationship(
        "SimulationRun", foreign_keys=[simulation_id], back_populates="attempts"
    )
    target_manifest: Mapped["NetworkTargetManifest"] = relationship(
        "NetworkTargetManifest",
        foreign_keys=[network_revision_id, target_manifest_id, target_manifest_sha256],
    )
    frames: Mapped[list["SimulationFrame"]] = relationship(
        "SimulationFrame",
        foreign_keys="SimulationFrame.attempt_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    registrations: Mapped[list["SimulationFrameRegistration"]] = relationship(
        "SimulationFrameRegistration",
        back_populates="attempt",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    result: Mapped[Optional["SimulationResult"]] = relationship(
        "SimulationResult",
        foreign_keys="SimulationResult.attempt_id",
        back_populates="attempt",
        uselist=False,
        passive_deletes=True,
    )


class SimulationFrameRegistration(Base):
    __tablename__ = "simulation_frame_registrations"
    __table_args__ = (
        UniqueConstraint(
            "attempt_id", "frame_time", name="uq_hydraulic_frame_registrations_time"
        ),
        CheckConstraint(
            "frame_index >= 0", name="ck_hydraulic_frame_registrations_index"
        ),
        CheckConstraint(
            "payload_bytes >= 0", name="ck_hydraulic_frame_registrations_bytes"
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    attempt_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.simulation_attempts.id",
            name="fk_hydraulic_frame_registrations_attempt",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    frame_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    frame_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    payload_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    quality_status: Mapped[str] = mapped_column(
        _pg_enum(FrameQualityStatus, "frame_quality_status"), nullable=False
    )

    attempt: Mapped["SimulationAttempt"] = relationship(
        "SimulationAttempt", back_populates="registrations"
    )


class SimulationFrame(Base):
    __tablename__ = "simulation_frames"
    __table_args__ = (
        PrimaryKeyConstraint("frame_time", "attempt_id", "frame_index"),
        CheckConstraint(
            "frame_index >= 0", name="ck_hydraulic_simulation_frames_index"
        ),
        CheckConstraint(
            "payload_bytes >= 0", name="ck_hydraulic_simulation_frames_bytes"
        ),
        CheckConstraint(
            "schema_version >= 1", name="ck_hydraulic_simulation_frames_schema_version"
        ),
        CheckConstraint(
            "length(payload_sha256) = 64",
            name="ck_hydraulic_simulation_frames_payload_hash_length",
        ),
        ForeignKeyConstraint(
            ["attempt_id", "simulation_id", "attempt_no"],
            [
                f"{HYDRAULIC_SCHEMA}.simulation_attempts.id",
                f"{HYDRAULIC_SCHEMA}.simulation_attempts.simulation_id",
                f"{HYDRAULIC_SCHEMA}.simulation_attempts.attempt_no",
            ],
            name="fk_hydraulic_simulation_frames_simulation_attempt",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["simulation_id", "network_revision_id"],
            [
                f"{HYDRAULIC_SCHEMA}.simulations.id",
                f"{HYDRAULIC_SCHEMA}.simulations.network_revision_id",
            ],
            name="fk_hydraulic_simulation_frames_simulation_revision",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["network_revision_id", "network_id"],
            [
                f"{HYDRAULIC_SCHEMA}.network_revisions.id",
                f"{HYDRAULIC_SCHEMA}.network_revisions.network_id",
            ],
            name="fk_hydraulic_simulation_frames_revision_network",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["network_revision_id", "target_manifest_id", "target_manifest_sha256"],
            [
                f"{HYDRAULIC_SCHEMA}.network_target_manifests.network_revision_id",
                f"{HYDRAULIC_SCHEMA}.network_target_manifests.id",
                f"{HYDRAULIC_SCHEMA}.network_target_manifests.content_sha256",
            ],
            name="fk_hydraulic_simulation_frames_target_manifest",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_hydraulic_simulation_frames_attempt_time", "attempt_id", "frame_time"
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    frame_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.simulation_attempts.id",
            name="fk_hydraulic_simulation_frames_attempt",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    simulation_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.simulations.id",
            name="fk_hydraulic_simulation_frames_simulation",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    attempt_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    frame_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    network_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.networks.id",
            name="fk_hydraulic_simulation_frames_network",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    network_revision_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.network_revisions.id",
            name="fk_hydraulic_simulation_frames_revision",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    target_manifest_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    target_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    solver_id: Mapped[str] = mapped_column(String(32), nullable=False)
    solver_version: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(_JSONB, nullable=False)
    payload_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    quality_status: Mapped[str] = mapped_column(
        _pg_enum(FrameQualityStatus, "frame_quality_status"), nullable=False
    )

    attempt: Mapped["SimulationAttempt"] = relationship(
        "SimulationAttempt", foreign_keys=[attempt_id], back_populates="frames"
    )
    simulation: Mapped["SimulationRun"] = relationship(
        "SimulationRun", foreign_keys=[simulation_id]
    )


class SimulationResult(Base):
    __tablename__ = "simulation_results"
    __table_args__ = (
        UniqueConstraint(
            "simulation_id", name="uq_hydraulic_simulation_results_simulation"
        ),
        UniqueConstraint("attempt_id", name="uq_hydraulic_simulation_results_attempt"),
        CheckConstraint(
            "max_mass_residual_relative IS NULL OR max_mass_residual_relative >= 0",
            name="ck_hydraulic_simulation_results_mass_residual",
        ),
        CheckConstraint(
            "max_courant IS NULL OR max_courant >= 0",
            name="ck_hydraulic_simulation_results_courant",
        ),
        CheckConstraint(
            "length(summary_sha256) = 64 AND length(quality_report_sha256) = 64",
            name="ck_hydraulic_simulation_results_hash_length",
        ),
        ForeignKeyConstraint(
            ["attempt_id", "simulation_id"],
            [
                f"{HYDRAULIC_SCHEMA}.simulation_attempts.id",
                f"{HYDRAULIC_SCHEMA}.simulation_attempts.simulation_id",
            ],
            name="fk_hydraulic_simulation_results_attempt_simulation",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["simulation_id", "network_revision_id", "fencing_token"],
            [
                f"{HYDRAULIC_SCHEMA}.simulations.id",
                f"{HYDRAULIC_SCHEMA}.simulations.network_revision_id",
                f"{HYDRAULIC_SCHEMA}.simulations.fencing_token",
            ],
            name="fk_hydraulic_simulation_results_simulation_fencing",
            ondelete="CASCADE",
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    simulation_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.simulations.id",
            name="fk_hydraulic_simulation_results_simulation",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    network_revision_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.simulation_attempts.id",
            name="fk_hydraulic_simulation_results_attempt",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(
        _JSONB, nullable=False, default=dict
    )
    summary_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    quality_report: Mapped[dict[str, Any]] = mapped_column(
        _JSONB, nullable=False, default=dict
    )
    quality_report_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    max_mass_residual_relative: Mapped[Optional[float]] = mapped_column(Float)
    max_courant: Mapped[Optional[float]] = mapped_column(Float)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    simulation: Mapped["SimulationRun"] = relationship(
        "SimulationRun", foreign_keys=[simulation_id], back_populates="results"
    )
    attempt: Mapped["SimulationAttempt"] = relationship(
        "SimulationAttempt", foreign_keys=[attempt_id], back_populates="result"
    )


class CalibrationRun(Base):
    __tablename__ = "calibration_runs"
    __table_args__ = (
        *_durable_job_constraints("calibration_runs"),
        UniqueConstraint(
            "idempotency_record_id", name="uq_hydraulic_calibration_runs_idempotency"
        ),
        UniqueConstraint(
            "id",
            "observation_dataset_sha256",
            "split_manifest_sha256",
            name="uq_hydraulic_calibration_runs_dataset_split",
        ),
        UniqueConstraint(
            "id", "fencing_token", name="uq_hydraulic_calibration_runs_id_fencing"
        ),
        CheckConstraint(
            "progress BETWEEN 0 AND 100", name="ck_hydraulic_calibration_runs_progress"
        ),
        CheckConstraint(
            "length(observation_dataset_sha256) = 64 "
            "AND length(split_manifest_sha256) = 64 "
            "AND length(calibration_config_hash) = 64",
            name="ck_hydraulic_calibration_runs_hash_length",
        ),
        CheckConstraint(
            "((progress = 100) = (status = 'completed'))",
            name="ck_hydraulic_calibration_runs_progress_terminal",
        ),
        ForeignKeyConstraint(
            ["baseline_simulation_id", "network_revision_id"],
            [
                f"{HYDRAULIC_SCHEMA}.simulations.id",
                f"{HYDRAULIC_SCHEMA}.simulations.network_revision_id",
            ],
            name="fk_hydraulic_calibration_runs_baseline_revision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["candidate_simulation_id", "network_revision_id"],
            [
                f"{HYDRAULIC_SCHEMA}.simulations.id",
                f"{HYDRAULIC_SCHEMA}.simulations.network_revision_id",
            ],
            name="fk_hydraulic_calibration_runs_candidate_revision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["network_revision_id", "network_id"],
            [
                f"{HYDRAULIC_SCHEMA}.network_revisions.id",
                f"{HYDRAULIC_SCHEMA}.network_revisions.network_id",
            ],
            name="fk_hydraulic_calibration_runs_revision_network",
            ondelete="RESTRICT",
        ),
        Index("ix_hydraulic_calibration_runs_status", "status"),
        Index("ix_hydraulic_calibration_runs_retry_due", "status", "next_attempt_at"),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    network_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    network_revision_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    baseline_simulation_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.simulations.id",
            name="fk_hydraulic_calibration_runs_baseline",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    candidate_simulation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.simulations.id",
            name="fk_hydraulic_calibration_runs_candidate",
            ondelete="RESTRICT",
        ),
    )
    observation_dataset_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    split_manifest: Mapped[dict[str, Any]] = mapped_column(_JSONB, nullable=False)
    split_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    calibration_config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(
        _JSONB, nullable=False, default=dict
    )
    improved: Mapped[Optional[bool]] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(
        _pg_enum(CalibrationStatus, "calibration_status"),
        nullable=False,
        default=CalibrationStatus.queued.value,
        server_default="queued",
    )
    progress: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    attempt: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=3, server_default="3"
    )
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    retry_backoff_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    last_retry_error_code: Mapped[Optional[str]] = mapped_column(String(128))
    lease_owner: Mapped[Optional[str]] = mapped_column(String(255))
    lease_token: Mapped[Optional[uuid.UUID]] = mapped_column(_UUID)
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    fencing_token: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    deadline_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    error_code: Mapped[Optional[str]] = mapped_column(String(128))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    requested_by: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_record_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.idempotency_records.id",
            name="fk_hydraulic_calibration_runs_idempotency",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=_utcnow,
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    baseline_simulation: Mapped["SimulationRun"] = relationship(
        "SimulationRun", foreign_keys=[baseline_simulation_id]
    )
    candidate_simulation: Mapped[Optional["SimulationRun"]] = relationship(
        "SimulationRun", foreign_keys=[candidate_simulation_id]
    )
    report: Mapped[Optional["CalibrationReport"]] = relationship(
        "CalibrationReport",
        foreign_keys="CalibrationReport.calibration_run_id",
        back_populates="run",
        uselist=False,
    )


class CalibrationReport(Base):
    __tablename__ = "calibration_reports"
    __table_args__ = (
        UniqueConstraint(
            "calibration_run_id", name="uq_hydraulic_calibration_reports_run"
        ),
        CheckConstraint(
            "length(observation_dataset_sha256) = 64 "
            "AND length(split_manifest_sha256) = 64 "
            "AND length(report_sha256) = 64",
            name="ck_hydraulic_calibration_reports_hash_length",
        ),
        ForeignKeyConstraint(
            [
                "calibration_run_id",
                "observation_dataset_sha256",
                "split_manifest_sha256",
            ],
            [
                f"{HYDRAULIC_SCHEMA}.calibration_runs.id",
                f"{HYDRAULIC_SCHEMA}.calibration_runs.observation_dataset_sha256",
                f"{HYDRAULIC_SCHEMA}.calibration_runs.split_manifest_sha256",
            ],
            name="fk_hydraulic_calibration_reports_run_dataset",
            ondelete="RESTRICT",
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    calibration_run_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.calibration_runs.id",
            name="fk_hydraulic_calibration_reports_run",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    observation_dataset_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    split_manifest: Mapped[dict[str, Any]] = mapped_column(_JSONB, nullable=False)
    split_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(
        _JSONB, nullable=False, default=dict
    )
    report_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_uri: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    run: Mapped["CalibrationRun"] = relationship(
        "CalibrationRun", foreign_keys=[calibration_run_id], back_populates="report"
    )


class ProbabilityCalibration(Base):
    __tablename__ = "probability_calibrations"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "artifact_sha256",
            name="uq_hydraulic_probability_calibrations_id_hash",
        ),
        CheckConstraint(
            "lead_min_minutes >= 0 AND lead_max_minutes >= lead_min_minutes",
            name="ck_hydraulic_probability_calibrations_lead",
        ),
        CheckConstraint(
            "magnitude_min <= magnitude_max",
            name="ck_hydraulic_probability_calibrations_magnitude",
        ),
        CheckConstraint(
            "valid_until > valid_from",
            name="ck_hydraulic_probability_calibrations_validity",
        ),
        CheckConstraint(
            "length(artifact_sha256) = 64",
            name="ck_hydraulic_probability_calibrations_artifact_hash_length",
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    artifact_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[str] = mapped_column(
        _pg_enum(ConfidenceMethod, "confidence_method"), nullable=False
    )
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_uri: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_format: Mapped[str] = mapped_column(String(32), nullable=False)
    lead_min_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    lead_max_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    magnitude_min: Mapped[float] = mapped_column(Float, nullable=False)
    magnitude_max: Mapped[float] = mapped_column(Float, nullable=False)
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    valid_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    alerts: Mapped[list["ForecastAlert"]] = relationship(
        "ForecastAlert",
        foreign_keys=(
            "[ForecastAlert.probability_calibration_id, "
            "ForecastAlert.probability_calibration_sha256]"
        ),
        back_populates="probability_calibration",
    )


class ForecastRule(Base):
    __tablename__ = "forecast_rules"
    __table_args__ = (
        UniqueConstraint(
            "alert_type", "version", name="uq_hydraulic_forecast_rules_type_version"
        ),
        UniqueConstraint(
            "id", "artifact_sha256", name="uq_hydraulic_forecast_rules_id_hash"
        ),
        CheckConstraint(
            "rule_schema_version >= 1",
            name="ck_hydraulic_forecast_rules_schema_version",
        ),
        CheckConstraint(
            "operator IN ('>','>=','<','<=')",
            name="ck_hydraulic_forecast_rules_operator",
        ),
        CheckConstraint(
            "refresh_expected_shift_steps >= 0 AND refresh_peak_delta >= 0 "
            "AND refresh_confidence_delta >= 0 AND refresh_risk_delta >= 0",
            name="ck_hydraulic_forecast_rules_refresh",
        ),
        CheckConstraint(
            "valid_until > valid_from", name="ck_hydraulic_forecast_rules_validity"
        ),
        CheckConstraint(
            "length(artifact_sha256) = 64",
            name="ck_hydraulic_forecast_rules_artifact_hash_length",
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    rule_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    alert_type: Mapped[str] = mapped_column(
        _pg_enum(ForecastAlertType, "forecast_alert_type"), nullable=False
    )
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    operator: Mapped[str] = mapped_column(String(2), nullable=False)
    threshold_value: Mapped[float] = mapped_column(Float, nullable=False)
    threshold_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    clear_value: Mapped[Optional[float]] = mapped_column(Float)
    expiry_grace_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    no_forecast_confirmations: Mapped[int] = mapped_column(Integer, nullable=False)
    clear_debounce_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    refresh_expected_shift_steps: Mapped[int] = mapped_column(Integer, nullable=False)
    refresh_peak_delta: Mapped[float] = mapped_column(Float, nullable=False)
    refresh_confidence_delta: Mapped[float] = mapped_column(Float, nullable=False)
    refresh_risk_delta: Mapped[float] = mapped_column(Float, nullable=False)
    risk_scale_m: Mapped[Optional[float]] = mapped_column(Float)
    risk_scale_ratio: Mapped[Optional[float]] = mapped_column(Float)
    artifact_uri: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    valid_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NowcastRun(Base):
    __tablename__ = "nowcast_runs"
    __table_args__ = (
        *_durable_job_constraints("nowcast_runs"),
        UniqueConstraint(
            "network_id", "scheduled_for", name="uq_hydraulic_nowcast_runs_slot"
        ),
        UniqueConstraint("id", "issued_at", name="uq_hydraulic_nowcast_runs_id_issued"),
        UniqueConstraint(
            "id", "fencing_token", name="uq_hydraulic_nowcast_runs_id_fencing"
        ),
        CheckConstraint(
            "deadline_at > scheduled_for", name="ck_hydraulic_nowcast_runs_deadline"
        ),
        CheckConstraint(
            "length(target_manifest_sha256) = 64 AND length(config_hash) = 64 "
            "AND length(flood_forecast_probability_sha256) = 64 "
            "AND length(pipe_surcharge_probability_sha256) = 64 "
            "AND length(flood_forecast_rule_sha256) = 64 "
            "AND length(pipe_surcharge_rule_sha256) = 64",
            name="ck_hydraulic_nowcast_runs_hash_length",
        ),
        CheckConstraint(
            "((status = 'completed') = (issued_at IS NOT NULL "
            "AND published_at IS NOT NULL))",
            name="ck_hydraulic_nowcast_runs_issued_terminal",
        ),
        ForeignKeyConstraint(
            ["network_revision_id", "network_id"],
            [
                f"{HYDRAULIC_SCHEMA}.network_revisions.id",
                f"{HYDRAULIC_SCHEMA}.network_revisions.network_id",
            ],
            name="fk_hydraulic_nowcast_runs_revision_network",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["network_revision_id", "target_manifest_id", "target_manifest_sha256"],
            [
                f"{HYDRAULIC_SCHEMA}.network_target_manifests.network_revision_id",
                f"{HYDRAULIC_SCHEMA}.network_target_manifests.id",
                f"{HYDRAULIC_SCHEMA}.network_target_manifests.content_sha256",
            ],
            name="fk_hydraulic_nowcast_runs_target_manifest",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "network_id",
                "simulation_id",
                "network_revision_id",
                "target_manifest_id",
                "target_manifest_sha256",
            ],
            [
                f"{HYDRAULIC_SCHEMA}.simulations.id",
                f"{HYDRAULIC_SCHEMA}.simulations.network_id",
                f"{HYDRAULIC_SCHEMA}.simulations.network_revision_id",
                f"{HYDRAULIC_SCHEMA}.simulations.target_manifest_id",
                f"{HYDRAULIC_SCHEMA}.simulations.target_manifest_sha256",
            ],
            name="fk_hydraulic_nowcast_runs_simulation_lineage",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["network_id", "rainfall_scenario_id"],
            [
                f"{HYDRAULIC_SCHEMA}.rainfall_scenarios.network_id",
                f"{HYDRAULIC_SCHEMA}.rainfall_scenarios.id",
            ],
            name="fk_hydraulic_nowcast_runs_rainfall_network",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "flood_forecast_probability_calibration_id",
                "flood_forecast_probability_sha256",
            ],
            [
                f"{HYDRAULIC_SCHEMA}.probability_calibrations.id",
                f"{HYDRAULIC_SCHEMA}.probability_calibrations.artifact_sha256",
            ],
            name="fk_hydraulic_nowcast_runs_flood_probability",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "pipe_surcharge_probability_calibration_id",
                "pipe_surcharge_probability_sha256",
            ],
            [
                f"{HYDRAULIC_SCHEMA}.probability_calibrations.id",
                f"{HYDRAULIC_SCHEMA}.probability_calibrations.artifact_sha256",
            ],
            name="fk_hydraulic_nowcast_runs_pipe_probability",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["flood_forecast_rule_id", "flood_forecast_rule_sha256"],
            [
                f"{HYDRAULIC_SCHEMA}.forecast_rules.id",
                f"{HYDRAULIC_SCHEMA}.forecast_rules.artifact_sha256",
            ],
            name="fk_hydraulic_nowcast_runs_flood_rule",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["pipe_surcharge_rule_id", "pipe_surcharge_rule_sha256"],
            [
                f"{HYDRAULIC_SCHEMA}.forecast_rules.id",
                f"{HYDRAULIC_SCHEMA}.forecast_rules.artifact_sha256",
            ],
            name="fk_hydraulic_nowcast_runs_pipe_rule",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_hydraulic_nowcast_runs_network_scheduled", "network_id", "scheduled_for"
        ),
        Index("ix_hydraulic_nowcast_runs_retry_due", "status", "next_attempt_at"),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    network_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.networks.id",
            name="fk_hydraulic_nowcast_runs_network",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    network_revision_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    target_manifest_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    target_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    forecast_origin_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    issued_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        _pg_enum(SimulationStatus, "simulation_status"),
        nullable=False,
        default=SimulationStatus.queued.value,
        server_default="queued",
    )
    attempt: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=3, server_default="3"
    )
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    retry_backoff_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    last_retry_error_code: Mapped[Optional[str]] = mapped_column(String(128))
    lease_owner: Mapped[Optional[str]] = mapped_column(String(255))
    lease_token: Mapped[Optional[uuid.UUID]] = mapped_column(_UUID)
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    fencing_token: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    simulation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.simulations.id",
            name="fk_hydraulic_nowcast_runs_simulation",
            ondelete="RESTRICT",
        ),
    )
    rainfall_scenario_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    solver_id: Mapped[str] = mapped_column(String(32), nullable=False)
    solver_version: Mapped[str] = mapped_column(String(128), nullable=False)
    model_calibration_id: Mapped[Optional[uuid.UUID]] = mapped_column(_UUID)
    flood_forecast_probability_calibration_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, nullable=False
    )
    flood_forecast_probability_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    pipe_surcharge_probability_calibration_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, nullable=False
    )
    pipe_surcharge_probability_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    flood_forecast_rule_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    flood_forecast_rule_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    pipe_surcharge_rule_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    pipe_surcharge_rule_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    cancel_requested_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    error_code: Mapped[Optional[str]] = mapped_column(String(128))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=_utcnow,
    )

    simulation: Mapped[Optional["SimulationRun"]] = relationship(
        "SimulationRun", foreign_keys=[simulation_id]
    )
    flood_probability: Mapped["ProbabilityCalibration"] = relationship(
        "ProbabilityCalibration",
        foreign_keys=[flood_forecast_probability_calibration_id],
    )
    pipe_probability: Mapped["ProbabilityCalibration"] = relationship(
        "ProbabilityCalibration",
        foreign_keys=[pipe_surcharge_probability_calibration_id],
    )
    flood_rule: Mapped["ForecastRule"] = relationship(
        "ForecastRule", foreign_keys=[flood_forecast_rule_id]
    )
    pipe_rule: Mapped["ForecastRule"] = relationship(
        "ForecastRule", foreign_keys=[pipe_surcharge_rule_id]
    )
    alerts: Mapped[list["ForecastAlert"]] = relationship(
        "ForecastAlert",
        foreign_keys="[ForecastAlert.nowcast_run_id, ForecastAlert.nowcast_issued_at]",
        back_populates="nowcast_run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ForecastAlert(Base):
    __tablename__ = "forecast_alerts"
    __table_args__ = (
        CheckConstraint(
            "target_type IN ('node','pipe_segment')",
            name="ck_hydraulic_forecast_alerts_target_type",
        ),
        CheckConstraint(
            "lead_time_minutes >= 0", name="ck_hydraulic_forecast_alerts_lead"
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_hydraulic_forecast_alerts_confidence",
        ),
        CheckConstraint(
            "risk_score >= 0 AND risk_score <= 100",
            name="ck_hydraulic_forecast_alerts_risk",
        ),
        CheckConstraint(
            "(phase = 'observed') = "
            "(observation_ingest_event_id IS NOT NULL "
            "AND observed_at IS NOT NULL)",
            name="ck_hydraulic_forecast_alerts_phase_observation",
        ),
        CheckConstraint(
            "length(target_manifest_sha256) = 64 "
            "AND length(probability_calibration_sha256) = 64 "
            "AND length(forecast_rule_sha256) = 64 "
            "AND length(suppression_key) = 64",
            name="ck_hydraulic_forecast_alerts_hash_length",
        ),
        ForeignKeyConstraint(
            ["nowcast_run_id", "nowcast_issued_at"],
            [
                f"{HYDRAULIC_SCHEMA}.nowcast_runs.id",
                f"{HYDRAULIC_SCHEMA}.nowcast_runs.issued_at",
            ],
            name="fk_hydraulic_forecast_alerts_nowcast_issued",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            [
                "network_id",
                "simulation_id",
                "network_revision_id",
                "target_manifest_id",
                "target_manifest_sha256",
            ],
            [
                f"{HYDRAULIC_SCHEMA}.simulations.id",
                f"{HYDRAULIC_SCHEMA}.simulations.network_id",
                f"{HYDRAULIC_SCHEMA}.simulations.network_revision_id",
                f"{HYDRAULIC_SCHEMA}.simulations.target_manifest_id",
                f"{HYDRAULIC_SCHEMA}.simulations.target_manifest_sha256",
            ],
            name="fk_hydraulic_forecast_alerts_simulation_lineage",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["probability_calibration_id", "probability_calibration_sha256"],
            [
                f"{HYDRAULIC_SCHEMA}.probability_calibrations.id",
                f"{HYDRAULIC_SCHEMA}.probability_calibrations.artifact_sha256",
            ],
            name="fk_hydraulic_forecast_alerts_probability_artifact",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["forecast_rule_id", "forecast_rule_sha256"],
            [
                f"{HYDRAULIC_SCHEMA}.forecast_rules.id",
                f"{HYDRAULIC_SCHEMA}.forecast_rules.artifact_sha256",
            ],
            name="fk_hydraulic_forecast_alerts_rule_artifact",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_hydraulic_forecast_alerts_risk", "network_id", "status", "risk_score"
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    network_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.networks.id",
            name="fk_hydraulic_forecast_alerts_network",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    nowcast_run_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.nowcast_runs.id",
            name="fk_hydraulic_forecast_alerts_nowcast",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    nowcast_issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    simulation_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.simulations.id",
            name="fk_hydraulic_forecast_alerts_simulation",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    network_revision_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    target_manifest_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    target_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    probability_calibration_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.probability_calibrations.id",
            name="fk_hydraulic_forecast_alerts_calibration",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    probability_calibration_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    forecast_rule_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    forecast_rule_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    alert_type: Mapped[str] = mapped_column(
        _pg_enum(ForecastAlertType, "forecast_alert_type"), nullable=False
    )
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    target_stable_key: Mapped[str] = mapped_column(String(128), nullable=False)
    expected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    lead_time_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    phase: Mapped[str] = mapped_column(
        _pg_enum(ForecastAlertPhase, "forecast_alert_phase"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        _pg_enum(ForecastAlertStatus, "forecast_alert_status"), nullable=False
    )
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_method: Mapped[str] = mapped_column(
        _pg_enum(ConfidenceMethod, "confidence_method"), nullable=False
    )
    threshold: Mapped[dict[str, Any]] = mapped_column(_JSONB, nullable=False)
    measurements: Mapped[dict[str, Any]] = mapped_column(_JSONB, nullable=False)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    risk_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    suppression_key: Mapped[str] = mapped_column(String(64), nullable=False)
    observation_ingest_event_id: Mapped[Optional[uuid.UUID]] = mapped_column(_UUID)
    observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=_utcnow,
    )

    nowcast_run: Mapped["NowcastRun"] = relationship(
        "NowcastRun",
        foreign_keys=[nowcast_run_id, nowcast_issued_at],
        back_populates="alerts",
    )
    simulation: Mapped["SimulationRun"] = relationship(
        "SimulationRun", foreign_keys=[simulation_id]
    )
    probability_calibration: Mapped["ProbabilityCalibration"] = relationship(
        "ProbabilityCalibration",
        foreign_keys=[probability_calibration_id, probability_calibration_sha256],
        back_populates="alerts",
    )
    forecast_rule: Mapped["ForecastRule"] = relationship(
        "ForecastRule", foreign_keys=[forecast_rule_id, forecast_rule_sha256]
    )
    transitions: Mapped[list["ForecastAlertTransition"]] = relationship(
        "ForecastAlertTransition",
        back_populates="alert",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ForecastAlertTransition.aggregate_version",
    )


class ForecastAlertTransition(Base):
    __tablename__ = "forecast_alert_transitions"
    __table_args__ = (
        UniqueConstraint(
            "alert_id",
            "aggregate_version",
            name="uq_hydraulic_forecast_transitions_version",
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    alert_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.forecast_alerts.id",
            name="fk_hydraulic_forecast_transitions_alert",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    from_status: Mapped[Optional[str]] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    from_phase: Mapped[Optional[str]] = mapped_column(String(32))
    to_phase: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(
        _pg_enum(ForecastTransitionAction, "forecast_transition_action"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        _UUID, ForeignKey("users.id", ondelete="RESTRICT")
    )
    idempotency_record_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.idempotency_records.id",
            name="fk_hydraulic_forecast_transitions_idempotency",
            ondelete="RESTRICT",
        ),
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    alert: Mapped["ForecastAlert"] = relationship(
        "ForecastAlert", foreign_keys=[alert_id], back_populates="transitions"
    )


class OutboxEventType(str, enum.Enum):
    created = "alert.created"
    updated = "alert.updated"
    escalated = "alert.escalated"
    observed = "alert.observed"
    resolved = "alert.resolved"
    expired = "alert.expired"


class EventOutbox(Base):
    """Durable forecast event log; ``sequence`` is the SSE Last-Event-ID."""

    __tablename__ = "event_outbox"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_hydraulic_event_outbox_event"),
        UniqueConstraint(
            "aggregate_id",
            "aggregate_version",
            "event_type",
            name="uq_hydraulic_event_outbox_aggregate_version",
        ),
        Index(
            "ix_hydraulic_event_outbox_pending",
            "sequence",
            postgresql_where=text("published_at IS NULL"),
            sqlite_where=text("published_at IS NULL"),
        ),
        Index(
            "ix_hydraulic_event_outbox_network_sequence",
            "network_id",
            "sequence",
        ),
        CheckConstraint(
            "schema_version >= 1", name="ck_hydraulic_event_outbox_schema_version"
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    # SQLite has no BIGSERIAL; the variant keeps the generated key working in
    # the portable cascade test while PostgreSQL still uses BIGSERIAL.
    sequence: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        _UUID, nullable=False, default=uuid.uuid4
    )
    network_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    event_type: Mapped[str] = mapped_column(
        _pg_enum(OutboxEventType, "outbox_event_type"), nullable=False
    )
    aggregate_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[Any] = mapped_column(_JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorkerLane(str, enum.Enum):
    general = "general"
    nowcast = "nowcast"


class WorkerSlot(Base):
    """The two frozen solve slots; the nowcast lane is never lent out."""

    __tablename__ = "worker_slots"
    __table_args__ = (
        UniqueConstraint("lane", name="uq_hydraulic_worker_slots_lane"),
        Index(
            "ix_hydraulic_worker_slots_lease",
            "lease_until",
            postgresql_where=text("busy"),
            sqlite_where=text("busy"),
        ),
        CheckConstraint("slot_id IN (1,2)", name="ck_hydraulic_worker_slots_slot_id"),
        CheckConstraint(
            "((busy = false AND worker_id IS NULL AND simulation_job_id IS NULL "
            "AND nowcast_run_id IS NULL AND lease_until IS NULL "
            "AND heartbeat_at IS NULL) "
            "OR "
            "(busy = true AND worker_id IS NOT NULL AND lease_until IS NOT NULL "
            "AND heartbeat_at IS NOT NULL AND lease_until > heartbeat_at "
            "AND ((lane = 'general' AND simulation_job_id IS NOT NULL "
            "AND nowcast_run_id IS NULL) "
            "OR (lane = 'nowcast' AND simulation_job_id IS NULL "
            "AND nowcast_run_id IS NOT NULL))))",
            name="ck_hydraulic_worker_slots_occupancy",
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    slot_id: Mapped[int] = mapped_column(
        SmallInteger, primary_key=True, autoincrement=False
    )
    lane: Mapped[str] = mapped_column(
        _pg_enum(WorkerLane, "worker_lane"), nullable=False
    )
    busy: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    worker_id: Mapped[Optional[str]] = mapped_column(String(128))
    simulation_job_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.simulations.id",
            name="fk_hydraulic_worker_slots_simulation",
            ondelete="RESTRICT",
        ),
    )
    nowcast_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.nowcast_runs.id",
            name="fk_hydraulic_worker_slots_nowcast",
            ondelete="RESTRICT",
        ),
    )
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class NotificationChannel(str, enum.Enum):
    in_app = "in_app"
    email = "email"
    webhook = "webhook"


class NotificationDeliveryStatus(str, enum.Enum):
    pending = "pending"
    sending = "sending"
    retry_wait = "retry_wait"
    delivered = "delivered"
    dead_letter = "dead_letter"
    suppressed = "suppressed"


class NotificationDelivery(Base):
    """Per-destination delivery attempt record; the fact behind browser UX."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "channel",
            "destination_id",
            "template_version",
            name="uq_hydraulic_notification_deliveries_target",
        ),
        Index(
            "ix_hydraulic_notification_deliveries_retry_due",
            "status",
            "next_attempt_at",
        ),
        Index(
            "ix_hydraulic_notification_deliveries_status_updated",
            "status",
            "updated_at",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_hydraulic_notification_deliveries_attempt_count",
        ),
        CheckConstraint(
            "((status = 'retry_wait') = (next_attempt_at IS NOT NULL))",
            name="ck_hydraulic_notification_deliveries_retry_due",
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    alert_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    channel: Mapped[str] = mapped_column(
        _pg_enum(NotificationChannel, "notification_channel"), nullable=False
    )
    destination_id: Mapped[str] = mapped_column(String(255), nullable=False)
    template_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        _pg_enum(NotificationDeliveryStatus, "notification_delivery_status"),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    attempt_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(255))
    last_error_code: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=_utcnow,
    )


class ResultRetentionPolicy(Base):
    __tablename__ = "result_retention_policies"
    __table_args__ = (
        UniqueConstraint("table_name", name="uq_hydraulic_result_retention_table"),
        CheckConstraint(
            "retention_days > 0", name="ck_hydraulic_result_retention_days"
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        primary_key=True,
        default=uuid.uuid4,
    )
    table_name: Mapped[str] = mapped_column(String(128), nullable=False)
    hypertable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    partition_column: Mapped[Optional[str]] = mapped_column(String(64))
    retention_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=90, server_default="90"
    )
    chunk_interval: Mapped[Optional[str]] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NetworkImport(Base):
    """Contract §3.4 job result detail for a committed SWMM/GeoJSON import."""

    __tablename__ = "network_imports"
    __table_args__ = (
        UniqueConstraint("network_job_id", name="uq_hydraulic_network_imports_job"),
        Index(
            "ix_hydraulic_network_imports_network",
            "network_id",
        ),
        Index(
            "ix_hydraulic_network_imports_network_revision",
            "network_revision_id",
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    network_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.networks.id",
            name="fk_hydraulic_network_imports_network",
            ondelete="RESTRICT",
        ),
    )
    network_revision_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.network_revisions.id",
            name="fk_hydraulic_network_imports_network_revision",
            ondelete="RESTRICT",
        ),
    )
    network_job_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.network_jobs.id",
            name="fk_hydraulic_network_imports_job",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    source_filename: Mapped[Optional[str]] = mapped_column(String(512))
    source_format: Mapped[str] = mapped_column(
        _pg_enum(ImportSourceFormat, "import_source_format"), nullable=False
    )
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    committed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    counts: Mapped[dict[str, Any]] = mapped_column(
        _JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[Optional[str]] = mapped_column(String(128))
    error_message: Mapped[Optional[str]] = mapped_column(Text)


class ValidationRun(Base):
    """Contract §3.4 immutable validation report for one network job."""

    __tablename__ = "validation_runs"
    __table_args__ = (
        UniqueConstraint("network_job_id", name="uq_hydraulic_validation_runs_job"),
        Index("ix_hydraulic_validation_runs_network", "network_id"),
        Index("ix_hydraulic_validation_runs_network_revision", "network_revision_id"),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    network_job_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.network_jobs.id",
            name="fk_hydraulic_validation_runs_job",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    network_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.networks.id",
            name="fk_hydraulic_validation_runs_network",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    network_revision_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.network_revisions.id",
            name="fk_hydraulic_validation_runs_network_revision",
            ondelete="RESTRICT",
        ),
    )
    status_value: Mapped[str] = mapped_column(
        _pg_enum(ValidationRunStatus, "validation_run_status"), nullable=False
    )
    counts: Mapped[Optional[dict[str, Any]]] = mapped_column(_JSONB)
    simulation_ready: Mapped[Optional[bool]] = mapped_column(Boolean)
    topology: Mapped[Optional[dict[str, Any]]] = mapped_column(_JSONB)
    created_by: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[Optional[str]] = mapped_column(String(128))
    error_message: Mapped[Optional[str]] = mapped_column(Text)


class NetworkValidationIssue(Base):
    """Contract §3.4 one row per finding inside a validation run."""

    __tablename__ = "network_validation_issues"
    __table_args__ = (
        UniqueConstraint(
            "validation_run_id",
            "code",
            "entity_type",
            "entity_key",
            "source_line",
            name="uq_hydraulic_network_validation_issues_finding",
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    validation_run_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.validation_runs.id",
            name="fk_hydraulic_network_validation_issues_run",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    severity: Mapped[str] = mapped_column(
        _pg_enum(ValidationIssueSeverity, "validation_issue_severity"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(128), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    source_line: Mapped[Optional[int]] = mapped_column(Integer)
    details: Mapped[Optional[dict[str, Any]]] = mapped_column(_JSONB)


class ObservationTrustPolicy(Base):
    """Contract §3.7 versioned, non-overlapping trust window per source system.

    The ``EXCLUDE USING gist`` validity-window constraint is a migration-only
    DDL object: it needs the ``btree_gist`` operator class that the portable
    ORM has no way to express, so it is asserted by the schema tests against
    the rendered 0005 SQL instead of being mirrored here.
    """

    __tablename__ = "observation_trust_policies"
    __table_args__ = (
        UniqueConstraint(
            "source_system",
            "policy_version",
            name="uq_hydraulic_observation_trust_policies_version",
        ),
        # Contract §3.7: an external_verified policy must declare both the
        # authentication and the active-binding requirement.
        CheckConstraint(
            "trust_level <> 'external_verified' OR "
            "(auth_requirement = 'authenticated' "
            "AND binding_requirement = 'active_required')",
            name="ck_hydraulic_observation_trust_policies_verified",
        ),
        Index(
            "ix_hydraulic_observation_trust_policies_source",
            "source_system",
            "valid_from",
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    source_system: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    auth_requirement: Mapped[str] = mapped_column(
        _pg_enum(ObservationAuthRequirement, "observation_auth_requirement"),
        nullable=False,
    )
    binding_requirement: Mapped[str] = mapped_column(
        _pg_enum(ObservationBindingRequirement, "observation_binding_requirement"),
        nullable=False,
    )
    trust_level: Mapped[str] = mapped_column(
        _pg_enum(ObservationTrustLevel, "observation_trust_level"), nullable=False
    )
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    valid_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    policy_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ObservationIngestEvent(Base):
    """Contract §3.7 source-identity anchored ingest event."""

    __tablename__ = "observation_ingest_events"
    __table_args__ = (
        CheckConstraint(
            "(trust_policy_resolution <> 'missing') OR "
            "(trust_policy_id IS NULL AND trust_policy_version IS NULL "
            "AND trust_policy_sha256 IS NULL)",
            name="ck_hydraulic_observation_ingest_events_missing_policy",
        ),
        CheckConstraint(
            "(trust_policy_resolution NOT IN ('matched','expired')) OR "
            "(trust_policy_id IS NOT NULL AND trust_policy_version IS NOT NULL "
            "AND trust_policy_sha256 IS NOT NULL)",
            name="ck_hydraulic_observation_ingest_events_resolved_policy",
        ),
        UniqueConstraint(
            "source_event_id", name="uq_hydraulic_observation_ingest_events_event"
        ),
        UniqueConstraint(
            "source_system",
            "source_stream",
            "source_partition",
            "source_offset",
            name="uq_hydraulic_observation_ingest_events_offset",
        ),
        Index(
            "ix_hydraulic_observation_ingest_events_trust_policy",
            "trust_policy_id",
        ),
        Index(
            "ix_hydraulic_observation_ingest_events_observed",
            "source_observed_at",
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    source_system: Mapped[str] = mapped_column(String(128), nullable=False)
    source_stream: Mapped[str] = mapped_column(String(128), nullable=False)
    source_partition: Mapped[str] = mapped_column(String(128), nullable=False)
    source_offset: Mapped[str] = mapped_column(String(128), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    adapter_version: Mapped[str] = mapped_column(String(64), nullable=False)
    trust_policy_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.observation_trust_policies.id",
            name="fk_hydraulic_observation_ingest_events_trust_policy",
            ondelete="RESTRICT",
        ),
    )
    trust_policy_version: Mapped[Optional[int]] = mapped_column(Integer)
    trust_policy_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    trust_policy_resolution: Mapped[str] = mapped_column(
        _pg_enum(
            ObservationTrustPolicyResolution,
            "observation_trust_policy_resolution",
        ),
        nullable=False,
    )
    trust_level: Mapped[str] = mapped_column(
        _pg_enum(ObservationTrustLevel, "observation_trust_level"), nullable=False
    )
    auth_verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    binding_verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        _pg_enum(ObservationIngestStatus, "observation_ingest_status"),
        nullable=False,
    )
    error_code: Mapped[Optional[str]] = mapped_column(String(128))
    error_message: Mapped[Optional[str]] = mapped_column(Text)


class ObservationSample(Base):
    """Contract §3.7 Timescale hypertable partitioned by ``observed_at``."""

    __tablename__ = "observation_samples"
    __table_args__ = (
        PrimaryKeyConstraint("observed_at", "ingest_event_id", "sample_index"),
        Index("ix_hydraulic_observation_samples_ingest_event", "ingest_event_id"),
        Index(
            "ix_hydraulic_observation_samples_network_observed",
            "network_id",
            "observed_at",
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    ingest_event_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.observation_ingest_events.id",
            name="fk_hydraulic_observation_samples_ingest_event",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    sample_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    network_id: Mapped[uuid.UUID] = mapped_column(_UUID, nullable=False)
    target_type: Mapped[str] = mapped_column(
        _pg_enum(ObservationTargetType, "observation_target_type"), nullable=False
    )
    target_stable_key: Mapped[str] = mapped_column(String(128), nullable=False)
    metric: Mapped[str] = mapped_column(
        _pg_enum(ObservationMetric, "observation_metric"), nullable=False
    )
    value: Mapped[float] = mapped_column(Float(asdecimal=False), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    quality_flag: Mapped[str] = mapped_column(
        _pg_enum(ObservationQualityFlag, "observation_quality_flag"),
        nullable=False,
    )
    source_point_ref: Mapped[dict[str, Any]] = mapped_column(_JSONB, nullable=False)
    ingest_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AbComparison(Base):
    """Contract §3.7 deterministic baseline/candidate A/B comparison record."""

    __tablename__ = "ab_comparisons"
    __table_args__ = (
        UniqueConstraint(
            "baseline_simulation_id",
            "candidate_simulation_id",
            "frame_grid",
            name="uq_hydraulic_ab_comparisons_grid",
        ),
        Index(
            "ix_hydraulic_ab_comparisons_candidate",
            "candidate_simulation_id",
        ),
        {"schema": HYDRAULIC_SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, default=uuid.uuid4)
    baseline_simulation_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.simulations.id",
            name="fk_hydraulic_ab_comparisons_baseline_simulation",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    candidate_simulation_id: Mapped[uuid.UUID] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.simulations.id",
            name="fk_hydraulic_ab_comparisons_candidate_simulation",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    frame_grid: Mapped[dict[str, Any]] = mapped_column(_JSONB, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(_JSONB, nullable=False)
    comparison_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        _UUID, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ForecastAlertWindow(Base):
    """Contract §3.8 per-suppression-key alert window and cooldown state."""

    __tablename__ = "forecast_alert_windows"
    __table_args__ = (
        Index("ix_hydraulic_forecast_alert_windows_active_alert", "active_alert_id"),
        Index("ix_hydraulic_forecast_alert_windows_cooldown_until", "cooldown_until"),
        {"schema": HYDRAULIC_SCHEMA},
    )

    suppression_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    active_alert_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        _UUID,
        ForeignKey(
            f"{HYDRAULIC_SCHEMA}.forecast_alerts.id",
            name="fk_hydraulic_forecast_alert_windows_alert",
            ondelete="RESTRICT",
        ),
    )
    cooldown_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_candidate_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_qualifying_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    suppressed_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


__all__ = [
    "HYDRAULIC_SCHEMA",
    "AbComparison",
    "Base",
    "CalibrationReport",
    "CalibrationRun",
    "CalibrationStatus",
    "Catchment",
    "ConfidenceMethod",
    "EventOutbox",
    "ForecastAlert",
    "ForecastAlertPhase",
    "ForecastAlertStatus",
    "ForecastAlertTransition",
    "ForecastAlertType",
    "ForecastAlertWindow",
    "ForecastRule",
    "ForecastTransitionAction",
    "FrameQualityStatus",
    "IdempotencyRecord",
    "ImportSourceFormat",
    "InitialStateSnapshot",
    "Network",
    "NetworkImport",
    "NetworkNode",
    "NetworkJob",
    "NetworkJobAttempt",
    "NetworkNodeKind",
    "NetworkRevision",
    "NetworkStatus",
    "NetworkTargetManifest",
    "NetworkValidationIssue",
    "NotificationChannel",
    "NotificationDelivery",
    "NotificationDeliveryStatus",
    "NowcastRun",
    "ObservationAuthRequirement",
    "ObservationBindingRequirement",
    "ObservationIngestEvent",
    "ObservationIngestStatus",
    "ObservationMetric",
    "ObservationQualityFlag",
    "ObservationSample",
    "ObservationTargetType",
    "ObservationTrustLevel",
    "ObservationTrustPolicy",
    "ObservationTrustPolicyResolution",
    "OutboxEventType",
    "ParameterSet",
    "PipeSegment",
    "PostGISGeometry",
    "ProbabilityCalibration",
    "RainfallPoint",
    "RainfallScenario",
    "RainfallScenarioKind",
    "ResultRetentionPolicy",
    "SchemaMetadata",
    "SimulationAttempt",
    "SimulationAttemptStatus",
    "SimulationFrame",
    "SimulationFrameRegistration",
    "SimulationResult",
    "SimulationRun",
    "SimulationStatus",
    "SpatialBackend",
    "ValidationIssueSeverity",
    "ValidationRun",
    "ValidationRunStatus",
    "WorkerLane",
    "WorkerSlot",
]
