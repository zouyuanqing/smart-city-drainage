"""Hydraulic schema contract tests.

These tests intentionally separate the PostGIS and portable DDL paths while
using the same WKT/GeoJSON codec.  They do not claim a live PostgreSQL or
Timescale gate; they verify the migration SQL that a live G1 run must execute.
"""

from __future__ import annotations

import importlib.util
import io
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import ValidationError
from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    create_engine,
    event,
    func,
    inspect,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.core.config import HYDRAULIC_SCHEMA_HEAD, Settings
from app.models.db_models import Base, RoleEnum, User
from app.models.hydraulic_models import (
    HYDRAULIC_SCHEMA,
    IdempotencyRecord,
    InitialStateSnapshot,
    Network,
    NetworkNode,
    NetworkNodeKind,
    NetworkRevision,
    NetworkTargetManifest,
    ParameterSet,
    PipeSegment,
    RainfallPoint,
    RainfallScenario,
    RainfallScenarioKind,
    ResultRetentionPolicy,
    SimulationAttempt,
    SimulationAttemptStatus,
    SimulationFrame,
    SimulationFrameRegistration,
    SimulationResult,
    SimulationRun,
)
from app.schemas.hydraulic_schemas import (
    BoundingBox,
    CatchmentCreate,
    GeometryDocument,
    NetworkNodeCreate,
    PipeSegmentCreate,
)
from app.schemas.hydraulic_schemas import RainfallPoint as RainfallPointSchema
from app.schemas.hydraulic_schemas import (
    RainfallScenarioCreate,
    ResultRetentionPolicyRead,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
MIGRATION_PATH = BACKEND_ROOT / "alembic" / "versions" / "0002_hydraulic_schema.py"
GAP_MIGRATION_PATH = (
    BACKEND_ROOT / "alembic" / "versions" / "0003_hydraulic_schema_gap.py"
)
# Contract §13.2 fixes one linear chain with a single head.  0001-0003 are the
# digest-fixed base; 0004-0008 are the additive revisions that create the eight
# /readyz tables the base never declared.
READYZ_MIGRATION_PATHS: tuple[tuple[str, Path], ...] = (
    (
        "0004_hydraulic_imports",
        BACKEND_ROOT / "alembic" / "versions" / "0004_hydraulic_imports.py",
    ),
    (
        "0005_hydraulic_observations",
        BACKEND_ROOT / "alembic" / "versions" / "0005_hydraulic_observations.py",
    ),
    (
        "0006_hydraulic_alert_windows",
        BACKEND_ROOT / "alembic" / "versions" / "0006_hydraulic_alert_windows.py",
    ),
    (
        "0007_hydraulic_ab_comparisons",
        BACKEND_ROOT / "alembic" / "versions" / "0007_hydraulic_ab_comparisons.py",
    ),
    (
        "0008_hydraulic_retention_seed",
        BACKEND_ROOT / "alembic" / "versions" / "0008_hydraulic_retention_seed.py",
    ),
)
READYZ_REQUIRED_TABLES = (
    "ab_comparisons",
    "forecast_alert_windows",
    "network_imports",
    "network_validation_issues",
    "observation_ingest_events",
    "observation_samples",
    "observation_trust_policies",
    "validation_runs",
)
HASH_A = "a" * 64
HASH_B = "b" * 64
UTC_NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("hydraulic_0002_test", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_gap_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "hydraulic_0003_test", GAP_MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render_gap_migration(
    migration: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    direction: str = "upgrade",
) -> str:
    """Render 0003, which is profile-independent by construction."""
    monkeypatch.setenv("HYDRAULIC_SPATIAL_BACKEND", "portable")
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={
            "as_sql": True,
            "output_buffer": output,
            "literal_binds": True,
        },
    )
    migration.op = Operations(context)
    if direction == "upgrade":
        migration.upgrade()
    else:
        migration.downgrade()
    return output.getvalue()


def _render_migration(
    migration: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
    direction: str = "upgrade",
) -> str:
    monkeypatch.setenv("HYDRAULIC_SPATIAL_BACKEND", profile)
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={
            "as_sql": True,
            "output_buffer": output,
            "literal_binds": True,
        },
    )
    migration.op = Operations(context)
    if direction == "upgrade":
        migration.upgrade()
    else:
        migration.downgrade()
    return output.getvalue()


def _load_readyz_migrations() -> tuple[ModuleType, ...]:
    modules: list[ModuleType] = []
    for revision, path in READYZ_MIGRATION_PATHS:
        spec = importlib.util.spec_from_file_location(
            f"hydraulic_{revision}_test", path
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    return tuple(modules)


def _render_readyz_migration(
    migration: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    direction: str = "upgrade",
) -> str:
    """Render 0004-0008, which are profile-independent by construction."""

    monkeypatch.setenv("HYDRAULIC_SPATIAL_BACKEND", "portable")
    return _render_gap_migration(migration, monkeypatch, direction)


def _render_readyz_migrations(
    monkeypatch: pytest.MonkeyPatch,
    direction: str = "upgrade",
) -> str:
    return "".join(
        _render_readyz_migration(module, monkeypatch, direction)
        for module in _load_readyz_migrations()
    )


def test_geometry_wkt_and_geojson_dual_path_emit_canonical_bbox() -> None:
    from_wkt = NetworkNodeCreate(
        stable_key="N-001",
        kind="manhole",
        geometry={"geometry_wkt": "point(121.3000 31.2000)"},
        invert_elevation_m=2.0,
        rim_elevation_m=4.0,
        max_depth_m=2.0,
    )
    from_geojson = NetworkNodeCreate(
        stable_key="N-002",
        kind="manhole",
        geometry={"geometry_geojson": ('{"type":"Point","coordinates":[121.3,31.2]}')},
    )

    assert from_wkt.geometry.geometry_wkt == "POINT (121.3 31.2)"
    assert from_wkt.geometry.geometry_geojson == (
        '{"coordinates":[121.3,31.2],"type":"Point"}'
    )
    assert from_geojson.geometry.geometry_wkt == from_wkt.geometry.geometry_wkt
    assert from_geojson.geometry.bbox == BoundingBox(
        west=121.3, south=31.2, east=121.3, north=31.2
    )
    assert from_geojson.geometry.crs == "EPSG:4326"


def test_geometry_bbox_serialization_and_entity_type_checks() -> None:
    pipe = PipeSegmentCreate(
        stable_key="P-001",
        upstream_node_id=uuid.uuid4(),
        downstream_node_id=uuid.uuid4(),
        nominal_diameter_m=0.5,
        length_m=30.0,
        roughness_manning_n=0.013,
        geometry={"geometry_wkt": "LINESTRING (121.3 31.2, 121.31 31.21)"},
    )
    assert pipe.geometry.bbox.model_dump(mode="json") == {
        "west": 121.3,
        "south": 31.2,
        "east": 121.31,
        "north": 31.21,
    }

    catchment = CatchmentCreate(
        stable_key="C-001",
        area_m2=1250.0,
        geometry={
            "geometry_geojson": json_text(
                {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [121.30, 31.20],
                            [121.31, 31.20],
                            [121.31, 31.21],
                            [121.30, 31.20],
                        ]
                    ],
                }
            )
        },
    )
    assert catchment.geometry.geometry_type == "Polygon"
    assert catchment.geometry.geometry_wkt.startswith("POLYGON ((")

    with pytest.raises(ValueError, match="catchment requires"):
        pipe.geometry.require_entity_geometry("catchment")
    with pytest.raises(ValueError, match="bbox does not match"):
        GeometryDocument(
            geometry_wkt="POINT (121.3 31.2)",
            bbox={"west": 0, "south": 0, "east": 1, "north": 1},
        )


def json_text(value: object) -> str:
    import json

    return json.dumps(value, separators=(",", ":"))


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"geometry_wkt": "POINT (121.3 31.2); DROP TABLE users"}, "invalid WKT"),
        ({"geometry_wkt": "POINT (181.3 31.2)"}, "longitude"),
        ({"geometry_wkt": "LINESTRING (121.3 31.2, 121.3 31.2)"}, "distinct"),
        (
            {"geometry_geojson": '{"type":"Point","coordinates":[NaN,31.2]}'},
            "non-finite",
        ),
        (
            {
                "geometry_geojson": (
                    '{"type":"Polygon",' '"coordinates":[[[0,0],[1,0],[0,0]]]}'
                )
            },
            "four",
        ),
        (
            {
                "geometry_geojson": (
                    '{"type":"Point","coordinates":[121.3,31.2],'
                    '"crs":{"type":"name","properties":{"name":"EPSG:3857"}}}'
                )
            },
            "CRS",
        ),
        (
            {"geometry_geojson": ('{"type":"Point","coordinates":[121.3,31.2,10.0]}')},
            "exactly two",
        ),
        (
            {"geometry_wkt": "POINT Z (121.3 31.2 10.0)"},
            "expected WKT token",
        ),
        (
            {"geometry_wkt": "SRID=3857;POINT (121.3 31.2)"},
            "invalid WKT",
        ),
        (
            {
                "geometry_geojson": (
                    '{"type":"Polygon","coordinates":[[[0,0],[2,2],[0,2],'
                    "[2,0],[0,0]]]}"
                )
            },
            "self-intersect",
        ),
    ],
)
def test_invalid_geometry_is_rejected(payload: dict[str, str], message: str) -> None:
    with pytest.raises((ValidationError, ValueError), match=message):
        GeometryDocument.model_validate(payload)


def test_geometry_dual_input_must_agree() -> None:
    with pytest.raises(ValidationError, match="disagree"):
        GeometryDocument(
            geometry_wkt="POINT (121.3 31.2)",
            geometry_geojson='{"type":"Point","coordinates":[121.4,31.2]}',
        )


def test_schema_datetimes_reject_naive_values() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        RainfallPointSchema(
            point_index=0,
            valid_at=datetime(2026, 9, 24, 12, 0),
            intensity_mm_h=0.0,
        )


def test_rainfall_scenario_contract_checks_grid_and_period() -> None:
    valid = RainfallScenarioCreate(
        network_id=uuid.uuid4(),
        name="50-year storm",
        kind="design",
        return_period_years=50,
        step_seconds=300,
        duration_seconds=600,
        algorithm="synthetic-design-chicago-v1",
        random_seed=42,
        points=(
            RainfallPointSchema(point_index=0, valid_at=UTC_NOW, intensity_mm_h=0.0),
            RainfallPointSchema(
                point_index=1,
                valid_at=UTC_NOW + timedelta(minutes=5),
                intensity_mm_h=12.5,
            ),
            RainfallPointSchema(
                point_index=2,
                valid_at=UTC_NOW + timedelta(minutes=10),
                intensity_mm_h=0.0,
            ),
        ),
        content_sha256=HASH_A,
    )
    assert len(valid.points) == 3

    with pytest.raises(ValidationError, match="point count"):
        RainfallScenarioCreate(
            **{
                **valid.model_dump(),
                "duration_seconds": 1200,
            }
        )
    with pytest.raises(ValidationError, match="return_period_years"):
        RainfallScenarioCreate(**{**valid.model_dump(), "return_period_years": None})


def test_orm_keeps_legacy_tables_and_registers_hydraulic_schema() -> None:
    expected_legacy = {
        "users",
        "devices",
        "camera_streams",
        "alerts",
        "model_versions",
        "inference_results",
    }
    actual_legacy = {
        name
        for name in Base.metadata.tables
        if not name.startswith(f"{HYDRAULIC_SCHEMA}.")
    }
    assert actual_legacy == expected_legacy
    assert {
        f"{HYDRAULIC_SCHEMA}.networks",
        f"{HYDRAULIC_SCHEMA}.network_nodes",
        f"{HYDRAULIC_SCHEMA}.pipe_segments",
        f"{HYDRAULIC_SCHEMA}.catchments",
        f"{HYDRAULIC_SCHEMA}.rainfall_scenarios",
        f"{HYDRAULIC_SCHEMA}.simulations",
        f"{HYDRAULIC_SCHEMA}.simulation_frames",
        f"{HYDRAULIC_SCHEMA}.simulation_results",
        f"{HYDRAULIC_SCHEMA}.calibration_runs",
        f"{HYDRAULIC_SCHEMA}.probability_calibrations",
        f"{HYDRAULIC_SCHEMA}.nowcast_runs",
        f"{HYDRAULIC_SCHEMA}.forecast_alerts",
    }.issubset(Base.metadata.tables)


def test_orm_constraints_cascades_and_named_enums() -> None:
    node_table = NetworkNode.__table__
    node_kind = node_table.c.kind.type
    assert set(node_kind.enums) == {item.value for item in NetworkNodeKind}
    assert node_kind.name == "network_node_kind"
    assert node_kind.schema == HYDRAULIC_SCHEMA

    rainfall_table = RainfallScenario.__table__
    rainfall_kind = rainfall_table.c.kind.type
    assert set(rainfall_kind.enums) == {item.value for item in RainfallScenarioKind}
    assert rainfall_kind.name == "rainfall_scenario_kind"

    def ondelete(table_name: str, constraint_name: str) -> str | None:
        table = Base.metadata.tables[f"{HYDRAULIC_SCHEMA}.{table_name}"]
        constraint = next(
            item
            for item in table.constraints
            if isinstance(item, __import__("sqlalchemy").ForeignKeyConstraint)
            and item.name == constraint_name
        )
        return constraint.ondelete

    assert (
        ondelete("rainfall_points", "fk_hydraulic_rainfall_points_scenario")
        == "CASCADE"
    )
    assert (
        ondelete("simulation_frames", "fk_hydraulic_simulation_frames_attempt")
        == "CASCADE"
    )
    assert (
        ondelete("simulation_results", "fk_hydraulic_simulation_results_attempt")
        == "CASCADE"
    )
    assert (
        ondelete("network_nodes", "fk_hydraulic_nodes_revision_network") == "RESTRICT"
    )

    frame_table = SimulationFrame.__table__
    assert [column.name for column in frame_table.primary_key.columns] == [
        "frame_time",
        "attempt_id",
        "frame_index",
    ]
    registration_table = SimulationFrameRegistration.__table__
    assert [column.name for column in registration_table.primary_key.columns] == [
        "attempt_id",
        "frame_index",
    ]
    check_names = {
        constraint.name for constraint in SimulationRun.__table__.constraints
    }
    assert {
        "ck_hydraulic_simulations_progress",
        "ck_hydraulic_simulations_attempt",
        "ck_hydraulic_simulations_time_grid",
    }.issubset(check_names)

    retention_default = ResultRetentionPolicy.__table__.c.retention_days.default
    assert retention_default is not None and retention_default.arg == 90

    result_retention = ResultRetentionPolicyRead(
        table_name="simulation_frames",
        hypertable=True,
        partition_column="frame_time",
        retention_days=90,
        chunk_interval="1 day",
        enabled=True,
    )
    assert result_retention.retention_days == 90


def test_orm_binds_lineage_durable_jobs_and_forecast_artifacts() -> None:
    simulation = SimulationRun.__table__
    attempt = SimulationAttempt.__table__
    frame = SimulationFrame.__table__
    result = SimulationResult.__table__

    assert {
        "network_revision_content_sha256",
        "target_manifest_id",
        "target_manifest_sha256",
        "snapshot_content_sha256",
        "initial_state_id",
        "initial_state_content_sha256",
        "parameter_set_id",
        "max_attempts",
        "next_attempt_at",
        "retry_backoff_seconds",
        "last_retry_error_code",
        "lease_owner",
        "lease_token",
        "lease_until",
        "fencing_token",
    }.issubset(simulation.c.keys())
    assert {
        "network_revision_id",
        "target_manifest_id",
        "target_manifest_sha256",
    }.issubset(attempt.c.keys())
    assert {"network_id", "target_manifest_id", "solver_id", "solver_version"}.issubset(
        frame.c.keys()
    )
    assert {
        "network_revision_id",
        "fencing_token",
        "summary_sha256",
        "quality_report_sha256",
    }.issubset(result.c.keys())

    def constraint_by_name(table: object, name: str, kind: type[object]) -> object:
        return next(
            item
            for item in table.constraints
            if item.name == name and isinstance(item, kind)
        )

    assert tuple(
        element.parent.name
        for element in constraint_by_name(
            frame,
            "fk_hydraulic_simulation_frames_simulation_attempt",
            ForeignKeyConstraint,
        ).elements
    ) == ("attempt_id", "simulation_id", "attempt_no")
    assert tuple(
        element.parent.name
        for element in constraint_by_name(
            result,
            "fk_hydraulic_simulation_results_simulation_fencing",
            ForeignKeyConstraint,
        ).elements
    ) == ("simulation_id", "network_revision_id", "fencing_token")

    for table, prefix in (
        (simulation, "simulations"),
        (Base.metadata.tables[f"{HYDRAULIC_SCHEMA}.network_jobs"], "network_jobs"),
        (
            Base.metadata.tables[f"{HYDRAULIC_SCHEMA}.calibration_runs"],
            "calibration_runs",
        ),
        (Base.metadata.tables[f"{HYDRAULIC_SCHEMA}.nowcast_runs"], "nowcast_runs"),
    ):
        check_names = {
            item.name for item in table.constraints if isinstance(item, CheckConstraint)
        }
        assert {
            f"ck_hydraulic_{prefix}_retry_due",
            f"ck_hydraulic_{prefix}_retry_metadata",
            f"ck_hydraulic_{prefix}_lease",
        }.issubset(check_names)

    idempotency_checks = {
        item.name
        for item in IdempotencyRecord.__table__.constraints
        if isinstance(item, CheckConstraint)
    }
    assert {
        "ck_hydraulic_idempotency_expiry",
        "ck_hydraulic_idempotency_retirement",
    }.issubset(idempotency_checks)

    nowcast_columns = Base.metadata.tables[f"{HYDRAULIC_SCHEMA}.nowcast_runs"].c.keys()
    assert {
        "target_manifest_id",
        "target_manifest_sha256",
        "flood_forecast_rule_id",
        "flood_forecast_rule_sha256",
        "pipe_surcharge_rule_id",
        "pipe_surcharge_rule_sha256",
        "config_hash",
    }.issubset(nowcast_columns)
    assert f"{HYDRAULIC_SCHEMA}.forecast_rules" in Base.metadata.tables


def test_sqlite_cascade_path_preserves_legacy_tables() -> None:
    legacy_runtime_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(legacy_runtime_engine)
    assert set(inspect(legacy_runtime_engine).get_table_names()) == {
        "users",
        "devices",
        "camera_streams",
        "alerts",
        "model_versions",
        "inference_results",
    }
    legacy_runtime_engine.dispose()

    engine = create_engine("sqlite:///:memory:").execution_options(
        schema_translate_map={"hydraulic": None, "public": None}
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    schema_test_tables = [
        Base.metadata.tables["users"],
        *[
            table
            for table_name, table in Base.metadata.tables.items()
            if table_name.startswith(f"{HYDRAULIC_SCHEMA}.")
        ],
    ]
    Base.metadata.create_all(engine, tables=schema_test_tables)

    with Session(engine, expire_on_commit=False) as session:
        user = User(
            username="schema-user",
            email="schema-user@example.test",
            hashed_password="not-used",
            role=RoleEnum.operator,
        )
        session.add(user)
        session.flush()

        network = Network(code="SH-SCHEMA", name="Schema test", created_by=user.id)
        session.add(network)
        session.flush()
        revision = NetworkRevision(
            network_id=network.id,
            revision_no=1,
            source_kind="crud",
            content_sha256=HASH_A,
            parser_version="test-v1",
            manifest={"counts": {"nodes": 1, "pipes": 0, "catchments": 0}},
            created_by=user.id,
        )
        session.add(revision)
        session.flush()
        network.current_revision_id = revision.id

        other_network = Network(
            code="SH-OTHER", name="Other network", created_by=user.id
        )
        session.add(other_network)
        session.flush()
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                session.add(
                    NetworkNode(
                        network_id=other_network.id,
                        network_revision_id=revision.id,
                        stable_key="N-WRONG-NETWORK",
                        kind=NetworkNodeKind.manhole.value,
                        geometry_wkt="POINT (121.3 31.2)",
                        min_lon=121.3,
                        min_lat=31.2,
                        max_lon=121.3,
                        max_lat=31.2,
                        properties={},
                    )
                )
                session.flush()

        target_manifest = NetworkTargetManifest(
            network_id=network.id,
            network_revision_id=revision.id,
            schema_version=1,
            content_sha256=HASH_B,
            manifest={"nodes": [], "pipes": [], "catchments": []},
        )
        initial_state = InitialStateSnapshot(
            network_id=network.id,
            network_revision_id=revision.id,
            state_kind="dry",
            content_sha256=HASH_A,
            snapshot={"nodes": {}, "pipes": {}, "catchments": {}},
        )
        parameter_set = ParameterSet(
            name="schema-default",
            kind="default",
            parameters={},
            content_sha256=HASH_B,
            created_by=user.id,
        )
        session.add_all([target_manifest, initial_state, parameter_set])
        session.flush()

        upstream = NetworkNode(
            network_id=network.id,
            network_revision_id=revision.id,
            stable_key="N-CASCADE-A",
            kind=NetworkNodeKind.junction.value,
            geometry_wkt="POINT (121.3 31.2)",
            min_lon=121.3,
            min_lat=31.2,
            max_lon=121.3,
            max_lat=31.2,
            properties={},
        )
        downstream = NetworkNode(
            network_id=network.id,
            network_revision_id=revision.id,
            stable_key="N-CASCADE-B",
            kind=NetworkNodeKind.outfall.value,
            geometry_wkt="POINT (121.31 31.21)",
            min_lon=121.31,
            min_lat=31.21,
            max_lon=121.31,
            max_lat=31.21,
            properties={},
        )
        session.add_all([upstream, downstream])
        session.flush()
        session.add(
            PipeSegment(
                network_id=network.id,
                network_revision_id=revision.id,
                stable_key="P-CASCADE",
                upstream_node_id=upstream.id,
                downstream_node_id=downstream.id,
                nominal_diameter_m=0.5,
                length_m=1.0,
                roughness_manning_n=0.013,
                geometry_wkt="LINESTRING (121.3 31.2, 121.31 31.21)",
                min_lon=121.3,
                min_lat=31.2,
                max_lon=121.31,
                max_lat=31.21,
                properties={},
            )
        )

        scenario = RainfallScenario(
            network_id=network.id,
            version=1,
            name="synthetic",
            kind=RainfallScenarioKind.synthetic.value,
            step_seconds=300,
            duration_seconds=300,
            source_type="synthetic",
            algorithm="synthetic-v1",
            random_seed=1,
            content_sha256=HASH_B,
            created_by=user.id,
            points=[
                RainfallPoint(
                    point_index=0,
                    valid_at=UTC_NOW,
                    intensity_mm_h=1.0,
                )
            ],
        )
        session.add(scenario)
        session.flush()

        idempotency = IdempotencyRecord(
            scope="simulation.submit",
            idempotency_key="schema-test-key",
            user_id=user.id,
            request_fingerprint=HASH_A,
            created_at=UTC_NOW,
            expires_at=UTC_NOW + timedelta(hours=24),
        )
        session.add(idempotency)
        session.flush()
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                session.add(
                    IdempotencyRecord(
                        scope="simulation.submit",
                        idempotency_key="schema-test-key",
                        user_id=user.id,
                        generation=2,
                        request_fingerprint=HASH_B,
                        expires_at=idempotency.expires_at,
                    )
                )
                session.flush()
        simulation = SimulationRun(
            requested_by=user.id,
            network_id=network.id,
            network_revision_id=revision.id,
            network_revision_content_sha256=HASH_A,
            target_manifest_id=target_manifest.id,
            target_manifest_sha256=HASH_B,
            snapshot_content_sha256=HASH_A,
            initial_state_id=initial_state.id,
            initial_state_content_sha256=HASH_A,
            rainfall_scenario_id=scenario.id,
            parameter_set_id=parameter_set.id,
            idempotency_record_id=idempotency.id,
            start_at=UTC_NOW,
            duration_seconds=300,
            frame_interval_seconds=300,
            solver_selection="auto",
            solver_id="py_mw",
            solver_version="test-v1",
            solver_config_hash=HASH_B,
            request_fingerprint=HASH_A,
            fencing_token=1,
        )
        session.add(simulation)
        session.flush()
        attempt = SimulationAttempt(
            simulation_id=simulation.id,
            network_revision_id=revision.id,
            attempt_no=1,
            fencing_token=1,
            status=SimulationAttemptStatus.running.value,
            target_manifest_id=target_manifest.id,
            target_manifest_sha256=HASH_B,
        )
        session.add(attempt)
        session.flush()

        stale_attempt = SimulationAttempt(
            simulation_id=simulation.id,
            network_revision_id=revision.id,
            attempt_no=2,
            fencing_token=2,
            status=SimulationAttemptStatus.running.value,
            target_manifest_id=target_manifest.id,
            target_manifest_sha256=HASH_B,
        )
        session.add(stale_attempt)
        session.flush()
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                session.add(
                    SimulationFrame(
                        frame_time=UTC_NOW + timedelta(minutes=5),
                        attempt_id=stale_attempt.id,
                        simulation_id=simulation.id,
                        attempt_no=1,
                        frame_index=0,
                        network_id=network.id,
                        network_revision_id=revision.id,
                        target_manifest_id=target_manifest.id,
                        target_manifest_sha256=HASH_B,
                        solver_id="py_mw",
                        solver_version="test-v1",
                        schema_version=1,
                        payload={"nodes": {}},
                        payload_bytes=2,
                        payload_sha256=HASH_B,
                        quality_status="verified",
                    )
                )
                session.flush()
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                session.add(
                    SimulationResult(
                        simulation_id=simulation.id,
                        network_revision_id=revision.id,
                        attempt_id=attempt.id,
                        fencing_token=0,
                        summary={},
                        summary_sha256=HASH_B,
                        quality_report={},
                        quality_report_sha256=HASH_A,
                    )
                )
                session.flush()

        session.add_all(
            [
                SimulationFrameRegistration(
                    attempt_id=attempt.id,
                    frame_index=0,
                    frame_time=UTC_NOW,
                    payload_bytes=2,
                    payload_sha256=HASH_B,
                    quality_status="verified",
                ),
                SimulationFrame(
                    frame_time=UTC_NOW,
                    attempt_id=attempt.id,
                    simulation_id=simulation.id,
                    attempt_no=1,
                    frame_index=0,
                    network_id=network.id,
                    network_revision_id=revision.id,
                    target_manifest_id=target_manifest.id,
                    target_manifest_sha256=HASH_B,
                    solver_id="py_mw",
                    solver_version="test-v1",
                    schema_version=1,
                    payload={"nodes": {}},
                    payload_bytes=2,
                    payload_sha256=HASH_B,
                    quality_status="verified",
                ),
                SimulationResult(
                    simulation_id=simulation.id,
                    network_revision_id=revision.id,
                    attempt_id=attempt.id,
                    fencing_token=1,
                    summary={"peak_depth_m": 0.1},
                    summary_sha256=HASH_B,
                    quality_report={"mass_residual_relative": 0.0},
                    quality_report_sha256=HASH_A,
                    max_mass_residual_relative=0.0,
                    max_courant=0.4,
                ),
            ]
        )
        session.commit()

        attempt_id = attempt.id
        session.delete(attempt)
        session.commit()
        frame_count = session.scalar(
            select(func.count())
            .select_from(SimulationFrame)
            .where(SimulationFrame.attempt_id == attempt_id)
        )
        registration_count = session.scalar(
            select(func.count())
            .select_from(SimulationFrameRegistration)
            .where(SimulationFrameRegistration.attempt_id == attempt_id)
        )
        result_count = session.scalar(
            select(func.count())
            .select_from(SimulationResult)
            .where(SimulationResult.attempt_id == attempt_id)
        )
        assert (frame_count, registration_count, result_count) == (0, 0, 0)

        session.delete(simulation)
        session.commit()
        scenario_id = scenario.id
        session.delete(scenario)
        session.commit()
        remaining_points = session.scalar(
            select(func.count())
            .select_from(RainfallPoint)
            .where(RainfallPoint.scenario_id == scenario_id)
        )
        assert remaining_points == 0

    engine.dispose()


@pytest.mark.asyncio
async def test_schema_readiness_checks_alembic_head_and_spatial_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import database as database_module

    class FakeResult:
        def __init__(self, row: dict[str, str] | None) -> None:
            self.row = row

        def mappings(self) -> "FakeResult":
            return self

        def first(self) -> dict[str, str] | None:
            return self.row

    class FakeConnection:
        async def execute(self, statement: object) -> FakeResult:
            sql = str(statement)
            if "alembic_version" in sql:
                return FakeResult({"version_num": HYDRAULIC_SCHEMA_HEAD})
            return FakeResult(
                {
                    "value": database_module.settings.HYDRAULIC_SPATIAL_BACKEND,
                    "alembic_revision": HYDRAULIC_SCHEMA_HEAD,
                }
            )

    class ConnectionContext:
        async def __aenter__(self) -> FakeConnection:
            return FakeConnection()

        async def __aexit__(self, *_: object) -> None:
            return None

    class FakeEngine:
        def connect(self) -> ConnectionContext:
            return ConnectionContext()

    monkeypatch.setattr(database_module, "get_engine", lambda: FakeEngine())
    result = await database_module.check_hydraulic_schema_readiness()
    assert result["ready"] is True
    assert result["alembic_revision"] == HYDRAULIC_SCHEMA_HEAD
    assert (
        result["spatial_backend"] == database_module.settings.HYDRAULIC_SPATIAL_BACKEND
    )


def test_postgis_migration_path_adds_generated_geometry_and_retention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    sql = _render_migration(migration, monkeypatch, "postgis")

    assert 'CREATE EXTENSION IF NOT EXISTS "postgis"' in sql
    assert "geometry(POINT,4326)" in sql
    assert "geometry(LINESTRING,4326)" in sql
    assert "geometry(GEOMETRY,4326)" in sql
    assert "ST_GeomFromText(geometry_wkt, 4326)" in sql
    assert "ST_GeometryType(geometry) IN ('ST_Polygon','ST_MultiPolygon')" in sql
    assert "USING gist" in sql
    assert "create_hypertable('hydraulic.simulation_frames', 'frame_time'" in sql
    assert "add_retention_policy('hydraulic.simulation_frames'" in sql
    assert "drop_after => INTERVAL '90 days'" in sql
    assert "INSERT INTO hydraulic.result_retention_policies" in sql
    assert "DEFAULT uuid_generate_v4()" in sql
    assert "('simulation_frames', true, 'frame_time', 90, '1 day', true)" in sql

    frame_ddl = sql.split("CREATE TABLE hydraulic.simulation_frames", 1)[1].split(
        ");", 1
    )[0]
    assert "PRIMARY KEY (frame_time, attempt_id, frame_index)" in frame_ddl
    assert "UNIQUE (attempt_id, frame_index)" not in frame_ddl
    assert "CONSTRAINT fk_hydraulic_simulations_target_manifest" in sql
    assert "CONSTRAINT fk_hydraulic_simulation_frames_simulation_attempt" in sql
    assert "CONSTRAINT fk_hydraulic_simulation_results_simulation_fencing" in sql
    assert "CONSTRAINT fk_hydraulic_nowcast_runs_simulation_lineage" in sql
    assert "CONSTRAINT fk_hydraulic_forecast_alerts_rule_artifact" in sql
    assert "CONSTRAINT ck_hydraulic_simulations_retry_due" in sql
    assert "CONSTRAINT ck_hydraulic_calibration_runs_retry_metadata" in sql
    assert "CONSTRAINT ck_hydraulic_nowcast_runs_lease" in sql
    assert "CONSTRAINT ck_hydraulic_idempotency_expiry" in sql
    assert "CONSTRAINT ck_hydraulic_idempotency_retirement" in sql


def test_portable_migration_path_has_no_postgis_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    sql = _render_migration(migration, monkeypatch, "portable")

    assert 'CREATE EXTENSION IF NOT EXISTS "timescaledb"' in sql
    assert 'CREATE EXTENSION IF NOT EXISTS "postgis"' not in sql
    assert "ST_GeomFromText" not in sql
    assert "ST_GeometryType" not in sql
    assert "geometry_wkt" in sql
    assert "min_lon <= max_lon" in sql
    assert "create_hypertable" in sql
    assert "drop_after => INTERVAL '90 days'" in sql
    assert "INSERT INTO hydraulic.schema_metadata" in sql
    assert "('spatial_backend', 'portable', '0002_hydraulic_schema')" in sql
    assert "trg_hydraulic_schema_metadata_immutable" in sql
    assert "CONSTRAINT ck_hydraulic_network_jobs_retry_due" in sql


def test_orm_and_both_profile_migrations_have_identical_check_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orm_checks = {
        constraint.name
        for table_name, table in Base.metadata.tables.items()
        if table_name.startswith(f"{HYDRAULIC_SCHEMA}.")
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    migration = _load_migration()
    gap_migration = _load_gap_migration()
    portable_sql = (
        _render_migration(migration, monkeypatch, "portable")
        + (_render_gap_migration(gap_migration, monkeypatch))
        + _render_readyz_migrations(monkeypatch, "upgrade")
    )
    portable_checks = set(
        re.findall(r"CONSTRAINT (ck_hydraulic_[A-Za-z0-9_]+) CHECK", portable_sql)
    )
    assert portable_checks == orm_checks

    postgis_sql = (
        _render_migration(migration, monkeypatch, "postgis")
        + (_render_gap_migration(gap_migration, monkeypatch))
        + _render_readyz_migrations(monkeypatch, "upgrade")
    )
    postgis_checks = set(
        re.findall(r"CONSTRAINT (ck_hydraulic_[A-Za-z0-9_]+) CHECK", postgis_sql)
    )
    geometry_checks = {
        "ck_hydraulic_network_nodes_geometry_valid",
        "ck_hydraulic_pipe_segments_geometry_valid",
        "ck_hydraulic_catchments_geometry_valid",
    }
    assert postgis_checks - geometry_checks == portable_checks
    assert geometry_checks.issubset(postgis_checks)

    environment = os.environ.copy()
    environment["HYDRAULIC_SPATIAL_BACKEND"] = "postgis"
    code = (
        "from sqlalchemy import CheckConstraint; "
        "from app.models.db_models import Base; "
        "print('\\n'.join(sorted(c.name for t in Base.metadata.tables.values() "
        "for c in t.constraints if isinstance(c, CheckConstraint) and c.name)))"
    )
    completed = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=BACKEND_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert set(completed.stdout.splitlines()) == postgis_checks


def test_migration_creates_only_new_enums_and_never_alters_legacy_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    sql = _render_migration(migration, monkeypatch, "postgis")

    assert (
        "CREATE TYPE hydraulic.network_status AS ENUM ('draft', 'active', 'archived')"
        in sql
    )
    assert "CREATE TYPE hydraulic.simulation_status AS ENUM ('queued'," in sql
    assert "CREATE TYPE hydraulic.forecast_alert_status AS ENUM ('open'," in sql
    assert "CREATE TYPE hydraulic.network_status AS ENUM (draft," not in sql
    assert "ALTER TYPE public.alert_type" not in sql
    assert "ALTER TABLE public." not in sql
    assert "DROP TABLE public." not in sql


def test_migration_invalid_profile_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    monkeypatch.setenv("HYDRAULIC_SPATIAL_BACKEND", "mysql")
    with pytest.raises(RuntimeError, match="HYDRAULIC_SPATIAL_BACKEND"):
        migration._spatial_backend()


def test_downgrade_is_reverse_order_empty_database_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_migration()
    sql = _render_migration(migration, monkeypatch, "portable", direction="downgrade")

    assert "hydraulic downgrade refused: hydraulic data exists" in sql
    assert "FROM hydraulic.networks LIMIT 1" in sql
    assert "FROM hydraulic.parameter_sets LIMIT 1" in sql
    assert "FROM hydraulic.probability_calibrations LIMIT 1" in sql
    assert "FROM hydraulic.forecast_rules LIMIT 1" in sql
    assert "DROP TABLE hydraulic.forecast_alert_transitions" in sql
    assert "DROP TABLE hydraulic.simulation_frames" in sql
    assert "DROP TABLE hydraulic.networks" in sql
    assert "DROP TYPE IF EXISTS hydraulic.simulation_status" in sql
    assert "DROP FUNCTION IF EXISTS hydraulic.guard_current_revision_network()" in sql
    assert "DROP FUNCTION IF EXISTS hydraulic.guard_schema_metadata()" in sql
    assert "DROP SCHEMA IF EXISTS hydraulic" in sql
    assert "DROP EXTENSION" not in sql
    assert "ALTER TABLE public." not in sql


def test_postgis_orm_metadata_is_process_profile_specific() -> None:
    portable_columns = {
        table_name: set(
            Base.metadata.tables[f"{HYDRAULIC_SCHEMA}.{table_name}"].c.keys()
        )
        for table_name in ("network_nodes", "pipe_segments", "catchments")
    }
    assert all("geometry" not in columns for columns in portable_columns.values())

    environment = os.environ.copy()
    environment["HYDRAULIC_SPATIAL_BACKEND"] = "postgis"
    code = (
        "from sqlalchemy.dialects import postgresql; "
        "from app.models.hydraulic_models import NetworkNode, PipeSegment, Catchment; "
        "tables = [NetworkNode.__table__, PipeSegment.__table__, Catchment.__table__]; "
        "values = [str(table.c.geometry.computed is not None) + ',' + "
        "str(all(token not in "
        "str(table.insert().compile(dialect=postgresql.dialect())) "
        "for token in ('(geometry)', ', geometry)'))) for table in tables]; "
        "print(','.join(values))"
    )
    completed = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=BACKEND_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "True,True,True,True,True,True"


def test_alembic_is_the_only_schema_writer_and_declares_the_runtime_head() -> None:
    settings = Settings(_env_file=None)
    assert settings.HYDRAULIC_SCHEMA_REVISION == HYDRAULIC_SCHEMA_HEAD
    with pytest.raises(ValidationError):
        Settings(_env_file=None, HYDRAULIC_SCHEMA_REVISION="0001_initial")

    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    bootstrap = (PROJECT_ROOT / "docker" / "init-db.sql").read_text(encoding="utf-8")
    main_source = (BACKEND_ROOT / "app" / "main.py").read_text(encoding="utf-8")
    dockerfile = (PROJECT_ROOT / "docker" / "backend" / "Dockerfile.cpu").read_text(
        encoding="utf-8"
    )

    assert 'command: ["alembic", "upgrade", "head"]' in compose
    assert "condition: service_completed_successfully" in compose
    assert f"HYDRAULIC_SCHEMA_REVISION: {HYDRAULIC_SCHEMA_HEAD}" in compose
    assert "CREATE TABLE" not in bootstrap.upper()
    assert "create_all" not in main_source
    assert "create_all_tables" not in main_source
    assert "/readyz" in dockerfile


def test_gap_migration_is_additive_and_profile_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0003 adds only the three tables /readyz was missing and never touches 0002."""

    gap = _load_gap_migration()
    # 0003 is no longer the head: contract §13.2 appends 0004-0008 on top of it.
    # What stays frozen is the base edge, not the head position.
    assert gap.revision == "0003_hydraulic_schema_gap"
    assert gap.down_revision == "0002_hydraulic_schema"
    assert gap.revision != HYDRAULIC_SCHEMA_HEAD
    readyz_chain = [revision for revision, _ in READYZ_MIGRATION_PATHS]
    assert readyz_chain[0] and readyz_chain[-1] == HYDRAULIC_SCHEMA_HEAD
    assert "0003_hydraulic_schema_gap" in {
        module.down_revision for module in _load_readyz_migrations()
    }

    portable = _render_gap_migration(gap, monkeypatch, "upgrade")
    monkeypatch.setenv("HYDRAULIC_SPATIAL_BACKEND", "postgis")
    postgis = _render_gap_migration(gap, monkeypatch, "upgrade")
    assert portable == postgis
    # The profile-independent DDL must not reach for a PostGIS object.
    assert "ST_" not in portable
    assert "geometry" not in portable
    assert "CREATE EXTENSION" not in portable

    assert "CREATE TABLE hydraulic.event_outbox" in portable
    assert "CREATE TABLE hydraulic.worker_slots" in portable
    assert "CREATE TABLE hydraulic.notification_deliveries" in portable
    assert "sequence BIGSERIAL NOT NULL" in portable
    assert "slot_id SMALLINT NOT NULL" in portable
    assert "ALTER TABLE" not in portable
    assert "DROP TABLE" not in portable
    assert "ALTER TABLE public." not in portable
    assert "DROP TABLE public." not in portable


def test_gap_migration_declares_the_frozen_keys_and_indexes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql = _render_gap_migration(_load_gap_migration(), monkeypatch, "upgrade")

    # event_outbox: SSE cursor PK, unique event identity, aggregate version key.
    assert "CONSTRAINT pk_hydraulic_event_outbox PRIMARY KEY (sequence)" in sql
    assert "CONSTRAINT uq_hydraulic_event_outbox_event UNIQUE (event_id)" in sql
    assert (
        "CONSTRAINT uq_hydraulic_event_outbox_aggregate_version UNIQUE "
        "(aggregate_id, aggregate_version, event_type)" in sql
    )
    assert (
        "ix_hydraulic_event_outbox_pending ON hydraulic.event_outbox (sequence) "
        "WHERE published_at IS NULL" in sql
    )
    assert (
        "ix_hydraulic_event_outbox_network_sequence ON hydraulic.event_outbox "
        "(network_id, sequence)" in sql
    )

    # worker_slots: two frozen lanes, each bound to exactly one owner table.
    assert "CONSTRAINT pk_hydraulic_worker_slots PRIMARY KEY (slot_id)" in sql
    assert "CONSTRAINT uq_hydraulic_worker_slots_lane UNIQUE (lane)" in sql
    assert (
        "CONSTRAINT ck_hydraulic_worker_slots_slot_id CHECK (slot_id IN (1,2))" in sql
    )
    assert (
        "CONSTRAINT fk_hydraulic_worker_slots_simulation "
        "FOREIGN KEY(simulation_job_id) "
        "REFERENCES hydraulic.simulations (id) ON DELETE RESTRICT" in sql
    )
    assert (
        "CONSTRAINT fk_hydraulic_worker_slots_nowcast FOREIGN KEY(nowcast_run_id) "
        "REFERENCES hydraulic.nowcast_runs (id) ON DELETE RESTRICT" in sql
    )
    assert (
        "ix_hydraulic_worker_slots_lease ON hydraulic.worker_slots (lease_until) "
        "WHERE busy" in sql
    )
    assert "VALUES (1, 'general', false, NULL, NULL, NULL, NULL, NULL)" in sql
    assert "VALUES (2, 'nowcast', false, NULL, NULL, NULL, NULL, NULL)" in sql

    # notification_deliveries: one row per destination and template version.
    assert "CONSTRAINT pk_hydraulic_notification_deliveries PRIMARY KEY (id)" in sql
    assert (
        "CONSTRAINT uq_hydraulic_notification_deliveries_target UNIQUE "
        "(event_id, channel, destination_id, template_version)" in sql
    )
    assert (
        "ix_hydraulic_notification_deliveries_retry_due ON "
        "hydraulic.notification_deliveries (status, next_attempt_at)" in sql
    )
    assert (
        "ix_hydraulic_notification_deliveries_status_updated ON "
        "hydraulic.notification_deliveries (status, updated_at)" in sql
    )
    # The frozen DDL declares no foreign key for event_id/alert_id.
    assert (
        "FOREIGN KEY"
        not in sql.split("CREATE TABLE hydraulic.notification_deliveries", 1)[1].split(
            ");", 1
        )[0]
    )


def test_gap_migration_restamps_schema_metadata_under_the_0002_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql = _render_gap_migration(_load_gap_migration(), monkeypatch, "upgrade")

    assert "0003 upgrade refused: hydraulic.schema_metadata is not a single row" in sql
    assert "0003 upgrade refused: hydraulic.schema_metadata is not stamped by " in sql
    assert (
        "SET alembic_revision = '0003_hydraulic_schema_gap', "
        "updated_at = clock_timestamp() "
        "WHERE key = 'spatial_backend' AND alembic_revision = '0002_hydraulic_schema'"
        in sql
    )
    # The trigger is dropped for the single UPDATE and reinstalled afterwards,
    # and the immutable profile value is never part of the statement.
    statements = [
        line.strip()
        for line in sql.splitlines()
        if "schema_metadata" in line or "guard_schema_metadata" in line
    ]
    drop = next(i for i, s in enumerate(statements) if s.startswith("DROP TRIGGER"))
    update = next(
        i
        for i, s in enumerate(statements)
        if s.startswith("UPDATE hydraulic.schema_metadata")
    )
    create = [
        i
        for i, s in enumerate(statements)
        if s.startswith("CREATE TRIGGER trg_hydraulic_schema_metadata_immutable")
    ]
    assert drop < update
    assert create and min(create) > update
    assert "SET value" not in sql


def test_gap_downgrade_is_reverse_order_and_data_guarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql = _render_gap_migration(_load_gap_migration(), monkeypatch, "downgrade")

    assert "0003 downgrade refused: gap table data exists" in sql
    assert "FROM hydraulic.event_outbox LIMIT 1" in sql
    assert "FROM hydraulic.notification_deliveries LIMIT 1" in sql
    assert "FROM hydraulic.worker_slots WHERE busy" in sql
    assert "0003 downgrade refused: worker slot seed is not intact" in sql
    assert "DROP TABLE hydraulic.notification_deliveries" in sql
    assert "DROP TABLE hydraulic.worker_slots" in sql
    assert "DROP TABLE hydraulic.event_outbox" in sql
    assert "DROP TYPE IF EXISTS hydraulic.outbox_event_type" in sql
    assert "DROP TYPE IF EXISTS hydraulic.worker_lane" in sql
    assert "DROP TYPE IF EXISTS hydraulic.notification_channel" in sql
    assert "DROP TYPE IF EXISTS hydraulic.notification_delivery_status" in sql
    # 0002 and the shared schema survive the rollback untouched.
    assert "DROP SCHEMA" not in sql
    assert "DROP EXTENSION" not in sql
    assert "DROP TABLE hydraulic.networks" not in sql
    assert (
        "SET alembic_revision = '0002_hydraulic_schema', "
        "updated_at = clock_timestamp() "
        "WHERE key = 'spatial_backend' AND "
        "alembic_revision = '0003_hydraulic_schema_gap'" in sql
    )


def test_gap_orm_matches_the_migration() -> None:
    from app.models.hydraulic_models import (
        EventOutbox,
        NotificationChannel,
        NotificationDelivery,
        NotificationDeliveryStatus,
        OutboxEventType,
        WorkerLane,
        WorkerSlot,
    )

    assert EventOutbox.__table__.c.sequence.primary_key is True
    assert {c.name for c in EventOutbox.__table__.primary_key.columns} == {"sequence"}
    assert [c.name for c in NotificationDelivery.__table__.primary_key.columns] == [
        "id"
    ]
    assert [c.name for c in WorkerSlot.__table__.primary_key.columns] == ["slot_id"]

    assert set(EventOutbox.__table__.c.event_type.type.enums) == {
        item.value for item in OutboxEventType
    }
    assert set(WorkerSlot.__table__.c.lane.type.enums) == {
        item.value for item in WorkerLane
    }
    assert set(NotificationDelivery.__table__.c.channel.type.enums) == {
        item.value for item in NotificationChannel
    }
    assert set(NotificationDelivery.__table__.c.status.type.enums) == {
        item.value for item in NotificationDeliveryStatus
    }

    fk_names = {
        constraint.name
        for constraint in WorkerSlot.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert fk_names == {
        "fk_hydraulic_worker_slots_simulation",
        "fk_hydraulic_worker_slots_nowcast",
    }
    assert NotificationDelivery.__table__.c.attempt_count.server_default is not None
    assert WorkerSlot.__table__.c.busy.server_default is not None


def test_public_model_exports_remain_compatible() -> None:
    import app.models as models

    for legacy_name in (
        "Base",
        "User",
        "Device",
        "CameraStream",
        "Alert",
        "ModelVersion",
        "InferenceResult",
    ):
        assert hasattr(models, legacy_name)
    assert models.Network.__tablename__ == "networks"
    assert models.ForecastAlert.__tablename__ == "forecast_alerts"
