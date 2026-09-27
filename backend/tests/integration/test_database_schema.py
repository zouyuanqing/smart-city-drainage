"""Database bootstrap integration contracts for both spatial profiles."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import re
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from uuid import UUID

import pytest

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.core.config import HYDRAULIC_SCHEMA_HEAD

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
MIGRATIONS = BACKEND_ROOT / "alembic" / "versions"
INIT_SQL = REPO_ROOT / "docker" / "init-db.sql"
COMMON_EXTENSIONS = {"uuid-ossp", "timescaledb", "btree_gist"}
_EXTENSION_RE = re.compile(
    r"CREATE\s+EXTENSION(?:\s+IF\s+NOT\s+EXISTS)?\s+\"?([a-z0-9_-]+)\"?",
    re.IGNORECASE,
)


def _load_migration(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"database_bootstrap_{path.stem}", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render_upgrade(path: Path, profile: str) -> str:
    previous_profile = os.environ.get("HYDRAULIC_SPATIAL_BACKEND")
    os.environ["HYDRAULIC_SPATIAL_BACKEND"] = profile
    try:
        migration = _load_migration(path)
        output = io.StringIO()
        migration.op = Operations(
            MigrationContext.configure(
                dialect_name="postgresql",
                opts={"as_sql": True, "output_buffer": output, "literal_binds": True},
            )
        )
        migration.upgrade()
        return output.getvalue()
    finally:
        if previous_profile is None:
            os.environ.pop("HYDRAULIC_SPATIAL_BACKEND", None)
        else:
            os.environ["HYDRAULIC_SPATIAL_BACKEND"] = previous_profile


def _migration_extensions(profile: str) -> set[str]:
    sql = "\n".join(
        _render_upgrade(path, profile)
        for path in (
            MIGRATIONS / "0001_initial.py",
            MIGRATIONS / "0002_hydraulic_schema.py",
            MIGRATIONS / "0003_hydraulic_schema_gap.py",
        )
    )
    return {match.group(1).lower() for match in _EXTENSION_RE.finditer(sql)}


def test_bootstrap_extensions_match_migration_extensions_and_create_no_schema() -> None:
    """The init hook and Alembic converge on extensions without a second writer."""
    bootstrap = INIT_SQL.read_text(encoding="utf-8")
    upper_bootstrap = bootstrap.upper()

    assert set(_EXTENSION_RE.findall(bootstrap)) >= COMMON_EXTENSIONS | {"postgis"}
    assert _migration_extensions("portable") == COMMON_EXTENSIONS
    assert _migration_extensions("postgis") == COMMON_EXTENSIONS | {"postgis"}
    assert upper_bootstrap.count("CREATE EXTENSION") == upper_bootstrap.count(
        "CREATE EXTENSION IF NOT EXISTS"
    )
    assert "CURRENT_SETTING('HYDRAULIC.SPATIAL_BACKEND', TRUE)" in upper_bootstrap
    assert "'PORTABLE'" in upper_bootstrap
    assert "RAISE EXCEPTION" in upper_bootstrap
    for forbidden in (
        "CREATE TABLE",
        "CREATE HYPERTABLE",
        "INSERT INTO",
        "ALTER TABLE",
    ):
        assert forbidden not in upper_bootstrap


def test_database_images_are_digest_pinned_and_keep_profiles_immutable() -> None:
    """The two build recipes pin inputs and cannot silently switch profile."""
    postgis = (REPO_ROOT / "docker" / "postgres" / "postgis.Dockerfile").read_text(
        encoding="utf-8"
    )
    portable = (REPO_ROOT / "docker" / "postgres" / "portable.Dockerfile").read_text(
        encoding="utf-8"
    )
    compose = (REPO_ROOT / "docker" / "postgres" / "compose.yml").read_text(
        encoding="utf-8"
    )
    entrypoint = (
        REPO_ROOT / "docker" / "postgres" / "entrypoint-profile.sh"
    ).read_text(encoding="utf-8")

    postgis_base = re.search(r"^ARG BASE_IMAGE=(\S+)$", postgis, re.MULTILINE)
    portable_base = re.search(r"^ARG BASE_IMAGE=(\S+)$", portable, re.MULTILINE)
    assert postgis_base is not None
    assert portable_base is not None
    assert postgis_base.group(1).startswith("timescale/timescaledb-ha:pg15@sha256:")
    assert portable_base.group(1).startswith(
        "timescale/timescaledb:2.17.2-pg15@sha256:"
    )
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", postgis_base.group(1).split("@", 1)[1])
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", portable_base.group(1).split("@", 1)[1])
    assert "HYDRAULIC_SPATIAL_BACKEND=postgis" in postgis
    assert "HYDRAULIC_SPATIAL_BACKEND=portable" in portable
    assert "timescaledb-ha" not in portable
    assert "container_name" not in compose
    assert "postgres-postgis:" in compose
    assert "postgres-portable:" in compose
    assert "HYDRAULIC_SPATIAL_BACKEND: postgis" in compose
    assert "HYDRAULIC_SPATIAL_BACKEND: portable" in compose
    assert "HYDRAULIC_IMAGE_SPATIAL_BACKEND" in entrypoint
    assert "hydraulic.spatial_backend" in entrypoint
    assert "exec docker-entrypoint.sh" in entrypoint


def _load_verifier() -> ModuleType:
    path = REPO_ROOT / "scripts" / "verify_database.py"
    spec = importlib.util.spec_from_file_location("verify_database_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _valid_manifest(profile: str) -> dict[str, object]:
    extensions = {
        "btree_gist": "1.7",
        "timescaledb": "2.17.2",
        "uuid-ossp": "1.1",
    }
    spatial_columns: list[dict[str, object]] = []
    spatial_indexes: list[dict[str, object]] = []
    if profile == "postgis":
        extensions["postgis"] = "3.4.2"
        spatial_columns = [
            {
                "table_name": table,
                "column_name": "geometry",
                "format_type": f"geometry({geometry_type},4326)",
                "generated": b"s",
            }
            for table, geometry_type in (
                ("network_nodes", "Point"),
                ("pipe_segments", "LineString"),
                ("catchments", "Geometry"),
            )
        ]
        spatial_indexes = [
            {
                "table_name": table,
                "index_name": f"ix_hydraulic_{table}_geometry_gist",
                "access_method": "gist",
            }
            for table in ("network_nodes", "pipe_segments", "catchments")
        ]
    return {
        "database": {"name": "smart_drainage", "server_version": "15.17"},
        "extensions": extensions,
        "alembic_revision": HYDRAULIC_SCHEMA_HEAD,
        "schema_metadata": {
            "value": profile,
            "alembic_revision": HYDRAULIC_SCHEMA_HEAD,
        },
        "hypertable": {
            "schema": "hydraulic",
            "table": "simulation_frames",
            "time_column": "frame_time",
            "time_interval": "1 day",
        },
        "retention": {
            "table_name": "simulation_frames",
            "retention_days": 90,
            "chunk_interval": "1 day",
            "drop_after": "90 days",
            "enabled": True,
        },
        "spatial_columns": spatial_columns,
        "spatial_indexes": spatial_indexes,
    }


def test_database_verifier_accepts_postgis_and_portable_manifests() -> None:
    """The public verifier accepts the two complete profile manifests."""
    verifier = _load_verifier()

    for profile in ("postgis", "portable"):
        result = verifier.validate_manifest(
            _valid_manifest(profile),
            profile=profile,
            expected_revision=HYDRAULIC_SCHEMA_HEAD,
            retention_days=90,
        )
        assert result["ok"] is True
        assert all(check["ok"] for check in result["checks"])


def test_database_verifier_cli_serializes_bytes_manifest(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI emits a JSON manifest when PostgreSQL returns character bytes."""

    verifier = _load_verifier()
    manifest = _valid_manifest("postgis")

    async def collect_bytes_manifest(dsn: str, *, profile: str) -> dict[str, object]:
        assert dsn.startswith("postgresql://")
        assert profile == "postgis"
        return manifest

    monkeypatch.setattr(verifier, "collect_manifest", collect_bytes_manifest)

    exit_code = verifier.main(
        [
            "--profile",
            "postgis",
            "--dsn",
            "postgresql://verifier:secret@localhost/smart_drainage",
            "--compact",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["ok"] is True
    assert set(payload["manifest"]) == set(manifest)
    assert [
        column["generated"] for column in payload["manifest"]["spatial_columns"]
    ] == ["s", "s", "s"]


def test_database_verifier_normalizes_driver_values_recursively() -> None:
    """Driver scalars retain the manifest shape and become strict JSON values."""

    verifier = _load_verifier()
    source = {
        "bytes": b"text",
        "bytearray": bytearray(b"mutable"),
        "memoryview": memoryview(b"view"),
        "date": date(2026, 9, 25),
        "datetime": datetime(2026, 9, 25, 12, 30, tzinfo=timezone.utc),
        "time": time(12, 30, tzinfo=timezone.utc),
        "decimal": Decimal("1234567890.1234567890"),
        "uuid": UUID("12345678-1234-5678-1234-567812345678"),
        "nested": ({"values": [bytearray(b"deep")]},),
    }

    normalized = verifier._normalize_json_value(source, name="fixture")

    assert normalized == {
        "bytes": "text",
        "bytearray": "mutable",
        "memoryview": "view",
        "date": "2026-09-25",
        "datetime": "2026-09-25T12:30:00+00:00",
        "time": "12:30:00+00:00",
        "decimal": "1234567890.1234567890",
        "uuid": "12345678-1234-5678-1234-567812345678",
        "nested": [{"values": ["deep"]}],
    }
    assert isinstance(normalized, dict)
    assert tuple(normalized) == tuple(source)
    json.dumps(normalized, allow_nan=False)


@pytest.mark.parametrize(
    ("value", "error_type", "message"),
    [
        pytest.param(b"\xff", ValueError, "non-UTF-8", id="invalid-utf8"),
        pytest.param({1: "bad"}, TypeError, "non-string key", id="key-drift"),
        pytest.param(object(), TypeError, "unsupported type", id="unknown-type"),
        pytest.param(float("nan"), ValueError, "finite number", id="nonfinite"),
    ],
)
def test_database_verifier_rejects_unsafe_manifest_values(
    value: object, error_type: type[Exception], message: str
) -> None:
    verifier = _load_verifier()

    with pytest.raises(error_type, match=message):
        verifier._normalize_json_value(value, name="manifest")


def test_database_verifier_fails_closed_on_schema_drift() -> None:
    """Missing capabilities or a profile switch produce named failed checks."""
    verifier = _load_verifier()
    manifest = _valid_manifest("postgis")
    extensions = manifest["extensions"]
    assert isinstance(extensions, dict)
    del extensions["postgis"]
    manifest["hypertable"] = None
    manifest["spatial_indexes"] = []
    metadata = manifest["schema_metadata"]
    assert isinstance(metadata, dict)
    metadata["value"] = "portable"

    result = verifier.validate_manifest(
        manifest,
        profile="postgis",
        expected_revision=HYDRAULIC_SCHEMA_HEAD,
        retention_days=90,
    )
    failed = {check["name"] for check in result["checks"] if not check["ok"]}

    assert result["ok"] is False
    assert failed == {
        "required_extensions",
        "postgis_extension_exclusivity",
        "immutable_schema_profile",
        "simulation_frames_hypertable",
        "postgis_spatial_indexes",
    }


@pytest.mark.parametrize("profile", ("postgis", "portable"))
async def test_live_database_profile_manifest(profile: str) -> None:
    """Run against fresh-volume databases supplied by DATABASE_TEST_DSN_*."""
    dsn = os.getenv(f"DATABASE_TEST_DSN_{profile.upper()}")
    if not dsn:
        pytest.skip(f"DATABASE_TEST_DSN_{profile.upper()} is not configured")

    verifier = _load_verifier()
    manifest = await verifier.collect_manifest(dsn, profile=profile)
    json.dumps(manifest, allow_nan=False)
    result = verifier.validate_manifest(
        manifest,
        profile=profile,
        expected_revision=HYDRAULIC_SCHEMA_HEAD,
        retention_days=90,
    )

    assert result["ok"] is True, result
