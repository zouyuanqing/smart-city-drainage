"""Schema contract tests for the eight /readyz tables added in 0004-0008.

Contract §13.2 fixes one linear revision chain with a single head.  Revisions
0001-0003 are the digest-fixed base; 0004-0008 are the additive revisions that
create the eight tables ``/readyz`` reports but the base never declared.

Every assertion here is bound to a frozen contract clause, cited by section
and line in the assertion comment, and to the live migration SQL.  No column,
constraint, index, or enum value is inferred: each one is quoted from
``docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md`` §3.4, §3.7, §3.8, or §13.2.
"""

from __future__ import annotations

import enum
import hashlib
import importlib.util
import io
import os
import uuid
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.models.db_models import Base
from app.models.hydraulic_models import (
    HYDRAULIC_SCHEMA,
    ImportSourceFormat,
    ObservationAuthRequirement,
    ObservationBindingRequirement,
    ObservationIngestStatus,
    ObservationMetric,
    ObservationQualityFlag,
    ObservationTargetType,
    ObservationTrustLevel,
    ObservationTrustPolicyResolution,
    ValidationIssueSeverity,
    ValidationRunStatus,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# Contract §13.2 positions 4-8.  The identifiers are the ones this repository
# actually uses; ``alembic_version.version_num`` is VARCHAR(32), so a longer
# name fails the live run rather than only this static check.
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

# backend/app/main.py ``_REQUIRED_HYDRAULIC_TABLES`` minus the tables that
# 0002 and 0003 already created.
READYZ_MISSING_TABLES = (
    "ab_comparisons",
    "forecast_alert_windows",
    "network_imports",
    "network_validation_issues",
    "observation_ingest_events",
    "observation_samples",
    "observation_trust_policies",
    "validation_runs",
)

# Live digests recorded as ``live_sha256`` by
# artifacts/contracts/approved-migration-0003.json for the three base revisions
# this work must leave byte-identical.
BASE_MIGRATION_DIGESTS = {
    "0001_initial.py": (
        "cd3516a92dbac312c3a661b17a6664f867847d2b239cdb1d0c8f11d9a1b6c8bb"
    ),
    "0002_hydraulic_schema.py": (
        "3c2f6539f84eedd324fd80aa1ff1b5e6c5b9f869e39d495dda3276295fbc0003"
    ),
    "0003_hydraulic_schema_gap.py": (
        "8ccc00d6634c9355f21e623d196eb3ea3700207de30e0a37c3b077bf844efb2b"
    ),
}

HASH_A = "a" * 64
HASH_B = "b" * 64

READYZ_TABLE_COLUMNS: dict[str, set[str]] = {
    "network_imports": {
        "id",
        "network_id",
        "network_revision_id",
        "network_job_id",
        "source_filename",
        "source_format",
        "source_sha256",
        "byte_size",
        "parser_version",
        "committed",
        "counts",
        "created_by",
        "created_at",
        "finished_at",
        "error_code",
        "error_message",
    },
    "validation_runs": {
        "id",
        "network_job_id",
        "network_id",
        "network_revision_id",
        "status_value",
        "counts",
        "simulation_ready",
        "topology",
        "created_by",
        "created_at",
        "completed_at",
        "error_code",
        "error_message",
    },
    "network_validation_issues": {
        "id",
        "validation_run_id",
        "severity",
        "code",
        "entity_type",
        "entity_key",
        "message",
        "source_line",
        "details",
    },
    "observation_trust_policies": {
        "id",
        "source_system",
        "policy_version",
        "auth_requirement",
        "binding_requirement",
        "trust_level",
        "valid_from",
        "valid_until",
        "policy_sha256",
        "created_at",
    },
    "observation_ingest_events": {
        "id",
        "source_system",
        "source_stream",
        "source_partition",
        "source_offset",
        "source_event_id",
        "source_observed_at",
        "received_at",
        "adapter_version",
        "trust_policy_id",
        "trust_policy_version",
        "trust_policy_sha256",
        "trust_policy_resolution",
        "trust_level",
        "auth_verified",
        "binding_verified",
        "source_payload_sha256",
        "status",
        "error_code",
        "error_message",
    },
    "observation_samples": {
        "observed_at",
        "ingest_event_id",
        "sample_index",
        "network_id",
        "target_type",
        "target_stable_key",
        "metric",
        "value",
        "unit",
        "quality_flag",
        "source_point_ref",
        "ingest_version",
        "created_at",
    },
    "ab_comparisons": {
        "id",
        "baseline_simulation_id",
        "candidate_simulation_id",
        "frame_grid",
        "metrics",
        "comparison_sha256",
        "created_by",
        "created_at",
    },
    "forecast_alert_windows": {
        "suppression_key",
        "active_alert_id",
        "cooldown_until",
        "last_candidate_at",
        "last_qualifying_at",
        "suppressed_count",
        "version",
        "created_at",
        "updated_at",
    },
}


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
    """Render one 0004-0008 revision, which is profile-independent."""

    monkeypatch.setenv("HYDRAULIC_SPATIAL_BACKEND", "portable")
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output, "literal_binds": True},
    )
    migration.op = Operations(context)
    if direction == "upgrade":
        migration.upgrade()
    else:
        migration.downgrade()
    return output.getvalue()


def _render_readyz_migrations(
    monkeypatch: pytest.MonkeyPatch,
    direction: str = "upgrade",
) -> str:
    return "".join(
        _render_readyz_migration(module, monkeypatch, direction)
        for module in _load_readyz_migrations()
    )


def test_readyz_migration_chain_is_linear_and_appends_to_the_frozen_base() -> None:
    modules = _load_readyz_migrations()
    expected_down = [
        "0003_hydraulic_schema_gap",
        "0004_hydraulic_imports",
        "0005_hydraulic_observations",
        "0006_hydraulic_alert_windows",
        "0007_hydraulic_ab_comparisons",
    ]
    for module, (revision, _path), down_revision in zip(
        modules, READYZ_MIGRATION_PATHS, expected_down
    ):
        assert module.revision == revision
        assert module.down_revision == down_revision
    # Alembic's own alembic_version.version_num is VARCHAR(32); a longer
    # identifier fails the live run, not only this static check.
    for revision, _path in READYZ_MIGRATION_PATHS:
        assert len(revision) <= 32
    # 0001-0003 are digest-fixed base revisions; this work must not touch them.
    for name, digest in BASE_MIGRATION_DIGESTS.items():
        path = BACKEND_ROOT / "alembic" / "versions" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, name


def test_readyz_migrations_create_every_required_table_and_are_additive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql = _render_readyz_migrations(monkeypatch, "upgrade")
    for table in READYZ_MISSING_TABLES:
        assert f"CREATE TABLE hydraulic.{table}" in sql
    # Additive only: no base object is altered, nothing is dropped, and no
    # PostGIS-only or profile-specific type is reached for.
    assert "ALTER TABLE hydraulic.networks" not in sql
    assert "ALTER TABLE hydraulic.forecast_alerts" not in sql
    assert "ALTER TABLE hydraulic.observation_ingest_events" not in sql
    assert "ALTER TABLE public." not in sql
    assert "DROP TABLE" not in sql
    assert "DROP SCHEMA" not in sql
    assert "DROP EXTENSION" not in sql
    assert "ST_" not in sql
    assert "geometry" not in sql
    # Contract §13.2 keeps extension creation in the init SQL and in 0002.
    assert "CREATE EXTENSION" not in sql


def test_readyz_migrations_are_byte_identical_for_both_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    portable = _render_readyz_migrations(monkeypatch, "upgrade")
    monkeypatch.setenv("HYDRAULIC_SPATIAL_BACKEND", "postgis")
    postgis = _render_readyz_migrations(monkeypatch, "upgrade")
    assert portable == postgis
    assert portable == _render_readyz_migrations(monkeypatch, "upgrade")


def test_network_import_and_validation_ddl_matches_contract_3_4(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql = _render_readyz_migration(_load_readyz_migrations()[0], monkeypatch)

    # Contract §3.4 lines 550, 573, 587: the three frozen enum domains.
    assert (
        "CREATE TYPE hydraulic.import_source_format AS ENUM ('inp', 'geojson')" in sql
    )
    assert (
        "CREATE TYPE hydraulic.validation_run_status AS ENUM "
        "('valid', 'valid_with_warnings', 'invalid')" in sql
    )
    assert (
        "CREATE TYPE hydraulic.validation_issue_severity AS ENUM "
        "('error', 'warning', 'info')" in sql
    )

    # Contract §3.4 lines 549-564: one result row per job, display-only name.
    assert "CREATE TABLE hydraulic.network_imports" in sql
    assert "CONSTRAINT pk_hydraulic_network_imports PRIMARY KEY (id)" in sql
    assert "CONSTRAINT uq_hydraulic_network_imports_job UNIQUE (network_job_id)" in sql
    assert "source_filename VARCHAR(512)" in sql
    assert "source_sha256 VARCHAR(64) NOT NULL" in sql
    assert "byte_size BIGINT NOT NULL" in sql
    assert "committed BOOLEAN DEFAULT false NOT NULL" in sql
    assert "counts JSONB DEFAULT '{}'::jsonb NOT NULL" in sql
    imports_block = sql.split("CREATE TABLE hydraulic.network_imports", 1)[1].split(
        ");", 1
    )[0]
    for name, target in (
        ("network", "hydraulic.networks (id)"),
        ("network_revision", "hydraulic.network_revisions (id)"),
        ("job", "hydraulic.network_jobs (id)"),
        ("created_by", "public.users (id)"),
    ):
        assert (
            f"CONSTRAINT fk_hydraulic_network_imports_{name} FOREIGN KEY"
            in imports_block
        ), name
        assert f"REFERENCES {target} ON DELETE RESTRICT" in imports_block, target

    # Contract §3.4 lines 566-580: exactly one immutable report per job.
    assert "CREATE TABLE hydraulic.validation_runs" in sql
    assert "CONSTRAINT pk_hydraulic_validation_runs PRIMARY KEY (id)" in sql
    assert "CONSTRAINT uq_hydraulic_validation_runs_job UNIQUE (network_job_id)" in sql
    assert "status_value hydraulic.validation_run_status NOT NULL" in sql
    assert "simulation_ready BOOLEAN" in sql
    assert "topology JSONB" in sql
    assert "REFERENCES hydraulic.network_jobs (id) ON DELETE RESTRICT" in sql

    # Contract §3.4 lines 582-595: issues are owned by, and die with, the run.
    assert "CREATE TABLE hydraulic.network_validation_issues" in sql
    assert "CONSTRAINT pk_hydraulic_network_validation_issues PRIMARY KEY (id)" in sql
    assert (
        "CONSTRAINT uq_hydraulic_network_validation_issues_finding UNIQUE "
        "(validation_run_id, code, entity_type, entity_key, source_line)" in sql
    )
    assert (
        "CONSTRAINT fk_hydraulic_network_validation_issues_run FOREIGN KEY"
        "(validation_run_id) REFERENCES hydraulic.validation_runs (id) "
        "ON DELETE CASCADE" in sql
    )
    assert "source_line INTEGER" in sql


def test_observation_ddl_matches_contract_3_7_including_the_hypertable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql = _render_readyz_migration(_load_readyz_migrations()[1], monkeypatch)

    # Contract §3.7 lines 827-828, 854, 858, 883, 885, 887.
    assert (
        "CREATE TYPE hydraulic.observation_auth_requirement AS ENUM "
        "('authenticated', 'allowlisted_local')" in sql
    )
    assert (
        "CREATE TYPE hydraulic.observation_binding_requirement AS ENUM "
        "('active_required', 'none')" in sql
    )
    assert (
        "CREATE TYPE hydraulic.observation_trust_level AS ENUM "
        "('external_verified', 'external_unverified', 'synthetic', 'mock')" in sql
    )
    assert (
        "CREATE TYPE hydraulic.observation_trust_policy_resolution AS ENUM "
        "('matched', 'missing', 'expired')" in sql
    )
    assert (
        "CREATE TYPE hydraulic.observation_ingest_status AS ENUM "
        "('accepted', 'conflict', 'quarantined')" in sql
    )
    assert (
        "CREATE TYPE hydraulic.observation_target_type AS ENUM "
        "('node', 'pipe_segment')" in sql
    )
    assert (
        "CREATE TYPE hydraulic.observation_metric AS ENUM "
        "('water_depth_m', 'flow_m3s')" in sql
    )
    assert (
        "CREATE TYPE hydraulic.observation_quality_flag AS ENUM "
        "('good', 'bad', 'missing', 'stale')" in sql
    )

    # Contract §3.7 lines 822-833: version key plus the non-overlapping window.
    assert "CREATE TABLE hydraulic.observation_trust_policies" in sql
    assert (
        "CONSTRAINT uq_hydraulic_observation_trust_policies_version UNIQUE "
        "(source_system, policy_version)" in sql
    )
    assert (
        "CONSTRAINT ex_hydraulic_observation_trust_policies_validity "
        "EXCLUDE USING gist (source_system WITH =, "
        "tstzrange(valid_from, valid_until, '[)') WITH &&)" in sql
    )
    # Contract §3.7 line 836: external_verified must declare both requirements.
    assert (
        "CONSTRAINT ck_hydraulic_observation_trust_policies_verified CHECK "
        "(trust_level <> 'external_verified' OR (auth_requirement = "
        "'authenticated' AND binding_requirement = 'active_required'))" in sql
    )

    # Contract §3.7 lines 840-864: source identity, never a timestamp or value.
    assert "CREATE TABLE hydraulic.observation_ingest_events" in sql
    assert (
        "CONSTRAINT uq_hydraulic_observation_ingest_events_event UNIQUE "
        "(source_event_id)" in sql
    )
    assert (
        "CONSTRAINT uq_hydraulic_observation_ingest_events_offset UNIQUE "
        "(source_system, source_stream, source_partition, source_offset)" in sql
    )
    assert (
        "CONSTRAINT ck_hydraulic_observation_ingest_events_missing_policy CHECK "
        "((trust_policy_resolution <> 'missing') OR (trust_policy_id IS NULL "
        "AND trust_policy_version IS NULL AND trust_policy_sha256 IS NULL))" in sql
    )
    assert (
        "CONSTRAINT ck_hydraulic_observation_ingest_events_resolved_policy "
        "CHECK ((trust_policy_resolution NOT IN ('matched','expired')) OR "
        "(trust_policy_id IS NOT NULL AND trust_policy_version IS NOT NULL "
        "AND trust_policy_sha256 IS NOT NULL))" in sql
    )

    # Contract §3.7 lines 878-894: composite key led by the partition column.
    assert "CREATE TABLE hydraulic.observation_samples" in sql
    assert (
        "CONSTRAINT pk_hydraulic_observation_samples PRIMARY KEY "
        "(observed_at, ingest_event_id, sample_index)" in sql
    )
    assert "value FLOAT NOT NULL" in sql
    assert "source_point_ref JSONB NOT NULL" in sql
    samples_block = sql.split("CREATE TABLE hydraulic.observation_samples", 1)[1].split(
        ");", 1
    )[0]
    # The frozen DDL declares no foreign key for network_id on samples.
    assert "REFERENCES hydraulic.networks" not in samples_block
    assert (
        "REFERENCES hydraulic.observation_ingest_events (id) ON DELETE CASCADE"
        in samples_block
    )
    # Contract §3.7 line 894: Timescale hypertable with 90-day retention.
    assert (
        "SELECT create_hypertable('hydraulic.observation_samples', "
        "'observed_at', chunk_time_interval => INTERVAL '1 day', "
        "if_not_exists => TRUE, migrate_data => TRUE)" in sql
    )
    assert (
        "SELECT add_retention_policy('hydraulic.observation_samples', "
        "drop_after => INTERVAL '90 days', if_not_exists => TRUE, "
        "schedule_interval => INTERVAL '1 day')" in sql
    )


def test_forecast_alert_windows_ddl_matches_contract_3_8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql = _render_readyz_migration(_load_readyz_migrations()[2], monkeypatch)

    # Contract §3.8 lines 1157-1166.
    assert "CREATE TABLE hydraulic.forecast_alert_windows" in sql
    assert (
        "CONSTRAINT pk_hydraulic_forecast_alert_windows PRIMARY KEY "
        "(suppression_key)" in sql
    )
    assert "suppression_key VARCHAR(64) NOT NULL" in sql
    assert "cooldown_until TIMESTAMP WITH TIME ZONE" in sql
    assert "last_candidate_at TIMESTAMP WITH TIME ZONE NOT NULL" in sql
    assert "suppressed_count BIGINT DEFAULT 0 NOT NULL" in sql
    assert "version BIGINT DEFAULT 0 NOT NULL" in sql
    assert (
        "CONSTRAINT fk_hydraulic_forecast_alert_windows_alert FOREIGN KEY"
        "(active_alert_id) REFERENCES hydraulic.forecast_alerts (id) "
        "ON DELETE RESTRICT" in sql
    )
    # The frozen DDL declares no CHECK constraint for this table, so none is
    # invented by the migration or mirrored by the ORM.
    assert "CONSTRAINT ck_hydraulic_forecast_alert_windows" not in sql


def test_ab_comparisons_ddl_matches_contract_3_7(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql = _render_readyz_migration(_load_readyz_migrations()[3], monkeypatch)

    # Contract §3.7 lines 955-965.
    assert "CREATE TABLE hydraulic.ab_comparisons" in sql
    assert "CONSTRAINT pk_hydraulic_ab_comparisons PRIMARY KEY (id)" in sql
    assert (
        "CONSTRAINT uq_hydraulic_ab_comparisons_grid UNIQUE "
        "(baseline_simulation_id, candidate_simulation_id, frame_grid)" in sql
    )
    assert "frame_grid JSONB NOT NULL" in sql
    assert "metrics JSONB NOT NULL" in sql
    assert "comparison_sha256 VARCHAR(64) NOT NULL" in sql
    assert (
        "CONSTRAINT fk_hydraulic_ab_comparisons_baseline_simulation FOREIGN KEY"
        "(baseline_simulation_id) REFERENCES hydraulic.simulations (id) "
        "ON DELETE RESTRICT" in sql
    )
    assert (
        "CONSTRAINT fk_hydraulic_ab_comparisons_candidate_simulation FOREIGN KEY"
        "(candidate_simulation_id) REFERENCES hydraulic.simulations (id) "
        "ON DELETE RESTRICT" in sql
    )
    assert (
        "CONSTRAINT fk_hydraulic_ab_comparisons_created_by FOREIGN KEY(created_by) "
        "REFERENCES public.users (id) ON DELETE RESTRICT" in sql
    )
    # The frozen DDL declares no CHECK constraint for this table.
    assert "CONSTRAINT ck_hydraulic_ab_comparisons" not in sql


def test_retention_seed_records_the_observation_hypertable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql = _render_readyz_migration(_load_readyz_migrations()[4], monkeypatch)

    # Contract §13.2 names the last slot the index/retention/seed revision; it
    # adds no table of its own and only records the 0005 hypertable in the
    # migration-owned retention ledger.
    assert "CREATE TABLE" not in sql
    assert "CREATE INDEX" not in sql
    assert (
        "0008 upgrade refused: observation retention ledger row already exists" in sql
    )
    assert "0008 upgrade refused: the 0002 retention seed is not intact" in sql
    assert "INSERT INTO hydraulic.result_retention_policies" in sql
    assert "'observation_samples'" in sql
    assert "SET alembic_revision = '0008_hydraulic_retention_seed'" in sql
    # The ledger write may never restate or rewrite the immutable profile.
    assert "SET value" not in sql


def test_readyz_downgrades_are_reverse_order_and_data_guarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql = _render_readyz_migrations(monkeypatch, "downgrade")

    for table in reversed(READYZ_MISSING_TABLES):
        assert f"DROP TABLE hydraulic.{table}" in sql
    # Contract §13.2: a downgrade runs only against an empty database or a
    # clone, and refuses rather than silently discarding business rows.
    assert sql.index("DROP TABLE hydraulic.observation_samples") < sql.index(
        "DROP TABLE hydraulic.observation_ingest_events"
    )
    assert sql.index("DROP TABLE hydraulic.observation_ingest_events") < sql.index(
        "DROP TABLE hydraulic.observation_trust_policies"
    )
    assert sql.index("DROP TABLE hydraulic.network_validation_issues") < sql.index(
        "DROP TABLE hydraulic.validation_runs"
    )
    for message in (
        "0004 downgrade refused: import or validation data exists",
        "0005 downgrade refused: observation data exists",
        "0006 downgrade refused: forecast alert window data exists",
        "0007 downgrade refused: A/B comparison data exists",
        "0008 downgrade refused: observation retention ledger row is not intact",
    ):
        assert message in sql
    # The 0001-0003 base survives the full rollback untouched.
    assert "DROP SCHEMA" not in sql
    assert "DROP EXTENSION" not in sql
    assert "DROP TABLE hydraulic.networks" not in sql
    assert "DROP TABLE hydraulic.forecast_alerts" not in sql
    assert "DROP TABLE hydraulic.event_outbox" not in sql
    for enum_name in (
        "import_source_format",
        "validation_run_status",
        "validation_issue_severity",
        "observation_trust_level",
        "observation_trust_policy_resolution",
        "observation_ingest_status",
    ):
        assert f"DROP TYPE IF EXISTS hydraulic.{enum_name}" in sql


def test_readyz_orm_metadata_matches_the_frozen_ddl() -> None:
    """Every /readyz table is registered in the ORM with the frozen columns."""

    assert set(READYZ_TABLE_COLUMNS) == set(READYZ_MISSING_TABLES)
    for table_name, columns in READYZ_TABLE_COLUMNS.items():
        table = Base.metadata.tables[f"{HYDRAULIC_SCHEMA}.{table_name}"]
        assert set(table.c.keys()) == columns, table_name
        assert table.schema == HYDRAULIC_SCHEMA

    # Contract §3.7 line 891: the hypertable partition column leads the key.
    samples = Base.metadata.tables[f"{HYDRAULIC_SCHEMA}.observation_samples"]
    assert [column.name for column in samples.primary_key.columns] == [
        "observed_at",
        "ingest_event_id",
        "sample_index",
    ]
    # Contract §3.8 line 1158: the window is keyed by the suppression key.
    windows = Base.metadata.tables[f"{HYDRAULIC_SCHEMA}.forecast_alert_windows"]
    assert [column.name for column in windows.primary_key.columns] == [
        "suppression_key"
    ]


def test_readyz_orm_enums_are_named_and_hydraulic_scoped() -> None:
    expected: dict[tuple[str, str], type[enum.Enum]] = {
        ("network_imports", "source_format"): ImportSourceFormat,
        ("validation_runs", "status_value"): ValidationRunStatus,
        ("network_validation_issues", "severity"): ValidationIssueSeverity,
        ("observation_trust_policies", "auth_requirement"): (
            ObservationAuthRequirement
        ),
        ("observation_trust_policies", "binding_requirement"): (
            ObservationBindingRequirement
        ),
        ("observation_samples", "target_type"): ObservationTargetType,
        ("observation_samples", "metric"): ObservationMetric,
        ("observation_samples", "quality_flag"): ObservationQualityFlag,
    }
    for (table_name, column_name), enum_type in expected.items():
        column_type = (
            Base.metadata.tables[f"{HYDRAULIC_SCHEMA}.{table_name}"].c[column_name].type
        )
        assert set(column_type.enums) == {item.value for item in enum_type}
        assert column_type.schema == HYDRAULIC_SCHEMA

    # The trust level is one shared enum domain on both tables.
    for table_name, column_name in (
        ("observation_trust_policies", "trust_level"),
        ("observation_ingest_events", "trust_level"),
    ):
        column_type = (
            Base.metadata.tables[f"{HYDRAULIC_SCHEMA}.{table_name}"].c[column_name].type
        )
        assert set(column_type.enums) == {item.value for item in ObservationTrustLevel}
        assert column_type.name == "observation_trust_level"
        assert column_type.schema == HYDRAULIC_SCHEMA

    events = Base.metadata.tables[f"{HYDRAULIC_SCHEMA}.observation_ingest_events"]
    assert set(events.c["trust_policy_resolution"].type.enums) == {
        item.value for item in ObservationTrustPolicyResolution
    }
    assert set(events.c["status"].type.enums) == {
        item.value for item in ObservationIngestStatus
    }


def test_readyz_models_are_exported_from_the_models_package() -> None:
    from app import models

    for name in (
        "NetworkImport",
        "ValidationRun",
        "NetworkValidationIssue",
        "ObservationTrustPolicy",
        "ObservationIngestEvent",
        "ObservationSample",
        "AbComparison",
        "ForecastAlertWindow",
    ):
        assert hasattr(models, name), name
    assert models.ObservationSample.__tablename__ == "observation_samples"
    assert models.ForecastAlertWindow.__tablename__ == "forecast_alert_windows"


def _live_engine() -> create_engine:
    dsn = os.getenv("HYDRAULIC_TEST_SYNC_DSN") or (
        "postgresql://drainage_admin:local-test-only@127.0.0.1:55433/smart_drainage"
    )
    return create_engine(dsn, future=True)


def _require_migrated_live_database(connection) -> None:
    present = {
        row[0]
        for row in connection.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'hydraulic'"
            )
        ).fetchall()
    }
    missing = set(READYZ_MISSING_TABLES) - present
    if missing:
        pytest.skip(f"live database is missing {sorted(missing)}")
    head = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    if head != READYZ_MIGRATION_PATHS[-1][0]:
        pytest.skip(f"live database is at {head}, not the final head")


def test_live_observation_trust_policy_constraints_are_enforced() -> None:
    """The exclusion and verified-policy rules are enforced by PostgreSQL."""

    engine = _live_engine()
    try:
        with engine.connect() as connection:
            _require_migrated_live_database(connection)
            if connection.execute(
                text("SELECT count(*) FROM hydraulic.observation_trust_policies")
            ).scalar():
                pytest.skip("live database already carries observation policies")
            connection.execute(
                text(
                    "INSERT INTO hydraulic.observation_trust_policies "
                    "(id, source_system, policy_version, auth_requirement, "
                    "binding_requirement, trust_level, valid_from, valid_until, "
                    "policy_sha256) VALUES (:id, 'probe-source', 1, "
                    "'authenticated', 'active_required', 'external_verified', "
                    "'2026-01-01T00:00:00+00:00', '2026-02-01T00:00:00+00:00', "
                    ":hash)"
                ).bindparams(id=uuid.uuid4(), hash=HASH_A)
            )
            # Contract §3.7 line 833: overlapping windows for one source fail.
            with pytest.raises(IntegrityError) as overlap:
                connection.execute(
                    text(
                        "INSERT INTO hydraulic.observation_trust_policies "
                        "(id, source_system, policy_version, auth_requirement, "
                        "binding_requirement, trust_level, valid_from, "
                        "valid_until, policy_sha256) VALUES (:id, 'probe-source', "
                        "2, 'authenticated', 'active_required', "
                        "'external_verified', '2026-01-15T00:00:00+00:00', "
                        "'2026-03-01T00:00:00+00:00', :hash)"
                    ).bindparams(id=uuid.uuid4(), hash=HASH_B)
                )
            connection.rollback()
            assert "exclusion" in str(overlap.value).lower()
            # Contract §3.7 line 836: verified needs auth AND an active binding.
            with pytest.raises(IntegrityError) as unverified:
                connection.execute(
                    text(
                        "INSERT INTO hydraulic.observation_trust_policies "
                        "(id, source_system, policy_version, auth_requirement, "
                        "binding_requirement, trust_level, valid_from, "
                        "valid_until, policy_sha256) VALUES (:id, 'other-source', "
                        "1, 'authenticated', 'none', 'external_verified', "
                        "'2026-01-01T00:00:00+00:00', '2026-02-01T00:00:00+00:00', "
                        ":hash)"
                    ).bindparams(id=uuid.uuid4(), hash=HASH_A)
                )
            connection.rollback()
            assert "ck_hydraulic_observation_trust_policies_verified" in str(
                unverified.value
            )
    finally:
        engine.dispose()


def test_live_ingest_event_policy_resolution_checks_are_enforced() -> None:
    """Contract §3.7 lines 861-864, proven against real constraints."""

    engine = _live_engine()
    insert = (
        "INSERT INTO hydraulic.observation_ingest_events "
        "(id, source_system, source_stream, source_partition, source_offset, "
        "source_event_id, source_observed_at, adapter_version, trust_policy_id, "
        "trust_policy_version, trust_policy_sha256, trust_policy_resolution, "
        "trust_level, auth_verified, binding_verified, source_payload_sha256, "
        "status) VALUES (:id, 'probe', 'stream', 'partition', :offset, "
        ":event_hash, '2026-01-01T00:00:00+00:00', 'v1', :policy_id, "
        ":policy_version, :policy_hash, :resolution, 'external_unverified', "
        "false, false, :payload_hash, :status)"
    )
    try:
        with engine.connect() as connection:
            _require_migrated_live_database(connection)
            if connection.execute(
                text("SELECT count(*) FROM hydraulic.observation_ingest_events")
            ).scalar():
                pytest.skip("live database already carries ingest events")

            # Contract §3.7 line 870: no matching policy means all three policy
            # identity columns NULL, resolution=missing, unverified, quarantined.
            # This is the shape the constraint must ACCEPT.
            connection.execute(
                text(insert).bindparams(
                    id=uuid.uuid4(),
                    offset="0",
                    event_hash=HASH_A,
                    policy_id=None,
                    policy_version=None,
                    policy_hash=None,
                    resolution="missing",
                    payload_hash=HASH_B,
                    status="quarantined",
                )
            )
            connection.rollback()

            # A "missing" resolution that still names a policy is rejected.
            with pytest.raises(IntegrityError) as missing:
                connection.execute(
                    text(insert).bindparams(
                        id=uuid.uuid4(),
                        offset="1",
                        event_hash=HASH_A,
                        policy_id=uuid.uuid4(),
                        policy_version=1,
                        policy_hash=HASH_B,
                        resolution="missing",
                        payload_hash=HASH_B,
                        status="quarantined",
                    )
                )
            connection.rollback()
            assert "ck_hydraulic_observation_ingest_events_missing_policy" in str(
                missing.value
            )

            # A "matched" resolution without the full identity triple fails.
            with pytest.raises(IntegrityError) as matched:
                connection.execute(
                    text(insert).bindparams(
                        id=uuid.uuid4(),
                        offset="2",
                        event_hash=HASH_A,
                        policy_id=None,
                        policy_version=1,
                        policy_hash=HASH_B,
                        resolution="matched",
                        payload_hash=HASH_B,
                        status="accepted",
                    )
                )
            connection.rollback()
            assert "ck_hydraulic_observation_ingest_events_resolved_policy" in str(
                matched.value
            )
    finally:
        engine.dispose()


def test_live_observation_samples_is_a_90_day_hypertable() -> None:
    engine = _live_engine()
    try:
        with engine.connect() as connection:
            _require_migrated_live_database(connection)
            hypertables = {
                (row[0], row[1])
                for row in connection.execute(
                    text(
                        "SELECT hypertable_schema, hypertable_name "
                        "FROM timescaledb_information.hypertables"
                    )
                ).fetchall()
            }
            # /readyz requires exactly these two hypertables.
            assert (
                HYDRAULIC_SCHEMA,
                "observation_samples",
            ) in hypertables
            ledger = connection.execute(
                text(
                    "SELECT hypertable, partition_column, retention_days, "
                    "chunk_interval, enabled "
                    "FROM hydraulic.result_retention_policies "
                    "WHERE table_name = 'observation_samples'"
                )
            ).fetchall()
            assert ledger == [(True, "observed_at", 90, "1 day", True)]
            job = connection.execute(
                text(
                    "SELECT proc_name, schedule_interval, config "
                    "FROM timescaledb_information.jobs "
                    "WHERE hypertable_name = 'observation_samples'"
                )
            ).fetchall()
            assert len(job) == 1
            assert job[0][0] == "policy_retention"
            assert job[0][2]["drop_after"] == "90 days"
    finally:
        engine.dispose()


def test_live_both_profiles_expose_every_readyz_table() -> None:
    """The /readyz table set is complete on the migrated database."""

    engine = _live_engine()
    try:
        with engine.connect() as connection:
            _require_migrated_live_database(connection)
            present = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'hydraulic'"
                    )
                ).fetchall()
            }
            assert set(READYZ_MISSING_TABLES) <= present
            stamped = connection.execute(
                text(
                    "SELECT value, alembic_revision "
                    "FROM hydraulic.schema_metadata WHERE key = 'spatial_backend'"
                )
            ).fetchall()
            # Contract §3.1: the immutable profile mirrors the declared head.
            assert stamped[0][1] == READYZ_MIGRATION_PATHS[-1][0]
    finally:
        engine.dispose()
