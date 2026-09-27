#!/usr/bin/env python3
"""Verify the deployed PostgreSQL/Timescale database for either spatial profile.

The verifier reads a live database through asyncpg and emits a JSON manifest. It
never prints the DSN or password. Alembic remains the only business-schema
writer; this script only checks the resulting database state.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime, time, timezone
from decimal import Decimal
from math import isfinite
from pathlib import Path
from typing import Any, Mapping, Sequence, cast
from uuid import UUID
from urllib.parse import urlsplit


COMMON_EXTENSIONS = frozenset({"btree_gist", "timescaledb", "uuid-ossp"})
SPATIAL_TABLES = ("network_nodes", "pipe_segments", "catchments")
POSTGIS_COLUMNS = {
    "network_nodes": "geometry(Point,4326)",
    "pipe_segments": "geometry(LineString,4326)",
    "catchments": "geometry(Geometry,4326)",
}
DEFAULT_REVISION = "0008_hydraulic_retention_seed"
DEFAULT_RETENTION_DAYS = 90


def _check(
    name: str,
    ok: bool,
    *,
    expected: object,
    actual: object,
    message: str,
) -> dict[str, object]:
    return {
        "name": name,
        "ok": bool(ok),
        "expected": expected,
        "actual": actual,
        "message": message,
    }


def _as_dict(value: object, *, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"manifest field {name!r} must be an object")
    return dict(value)


def _as_optional_dict(value: object, *, name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    return _as_dict(value, name=name)


def _as_optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _normalize_json_value(value: object, *, name: str) -> object:
    """Return a strict JSON-safe copy without changing manifest structure."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError(f"manifest field {name!r} must be a finite number")
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        try:
            return bytes(value).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"manifest field {name!r} contains non-UTF-8 bytes"
            ) from exc
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"manifest field {name!r} has non-string key {key!r}")
            normalized[key] = _normalize_json_value(item, name=f"{name}.{key}")
        return normalized
    if isinstance(value, (list, tuple)):
        return [
            _normalize_json_value(item, name=f"{name}[{index}]")
            for index, item in enumerate(value)
        ]
    raise TypeError(
        f"manifest field {name!r} has unsupported type {type(value).__name__}"
    )


def _normalize_interval_days(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        match = value.strip().lower().split()[0] if value.strip() else ""
        if match.isdigit():
            return int(match)
    return None


def validate_manifest(
    manifest: Mapping[str, object],
    *,
    profile: str,
    expected_revision: str = DEFAULT_REVISION,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> dict[str, object]:
    """Return deterministic checks for a collected database manifest."""

    if profile not in {"postgis", "portable"}:
        raise ValueError("profile must be 'postgis' or 'portable'")
    if retention_days <= 0:
        raise ValueError("retention_days must be positive")

    manifest = cast(
        Mapping[str, object],
        _normalize_json_value(manifest, name="manifest"),
    )
    checks: list[dict[str, object]] = []
    database = _as_dict(manifest.get("database"), name="database")
    checks.append(
        _check(
            "database_identity",
            bool(database.get("name")) and bool(database.get("server_version")),
            expected={"name": "<database>", "server_version": "<version>"},
            actual={
                "name": database.get("name"),
                "server_version": database.get("server_version"),
            },
            message="connected to a named PostgreSQL server",
        )
    )

    raw_extensions = _as_dict(manifest.get("extensions"), name="extensions")
    extensions = {
        str(name).lower(): str(version)
        for name, version in raw_extensions.items()
        if version is not None
    }
    expected_extensions = set(COMMON_EXTENSIONS)
    if profile == "postgis":
        expected_extensions.add("postgis")
    checks.append(
        _check(
            "required_extensions",
            expected_extensions.issubset(extensions),
            expected=sorted(expected_extensions),
            actual=sorted(extensions),
            message="TimescaleDB is present in both profiles; PostGIS is PostGIS-only",
        )
    )
    checks.append(
        _check(
            "postgis_extension_exclusivity",
            (profile == "postgis") == ("postgis" in extensions),
            expected=profile == "postgis",
            actual="postgis" in extensions,
            message="PostGIS extension presence exactly matches the immutable profile",
        )
    )

    alembic_revision = manifest.get("alembic_revision")
    checks.append(
        _check(
            "alembic_head",
            alembic_revision == expected_revision,
            expected=expected_revision,
            actual=alembic_revision,
            message="Alembic version table is at the declared head",
        )
    )
    metadata = _as_optional_dict(
        manifest.get("schema_metadata"), name="schema_metadata"
    )
    metadata_profile = metadata.get("value") if metadata else None
    metadata_revision = metadata.get("alembic_revision") if metadata else None
    checks.append(
        _check(
            "immutable_schema_profile",
            metadata_profile == profile and metadata_revision == expected_revision,
            expected={"value": profile, "alembic_revision": expected_revision},
            actual={
                "value": metadata_profile,
                "alembic_revision": metadata_revision,
            },
            message="hydraulic schema metadata matches the requested profile and head",
        )
    )

    hypertable = _as_optional_dict(manifest.get("hypertable"), name="hypertable")
    expected_hypertable = {
        "schema": "hydraulic",
        "table": "simulation_frames",
        "time_column": "frame_time",
        "time_interval": "1 day",
    }
    checks.append(
        _check(
            "simulation_frames_hypertable",
            hypertable == expected_hypertable,
            expected=expected_hypertable,
            actual=hypertable,
            message="simulation_frames is partitioned on frame_time with a one-day chunk",
        )
    )

    retention = _as_optional_dict(manifest.get("retention"), name="retention")
    actual_retention_days = (
        _normalize_interval_days(retention.get("drop_after")) if retention else None
    )
    expected_retention = {
        "table_name": "simulation_frames",
        "retention_days": retention_days,
        "chunk_interval": "1 day",
        "enabled": True,
    }
    actual_retention = (
        {
            "table_name": retention.get("table_name"),
            "retention_days": retention.get("retention_days"),
            "chunk_interval": retention.get("chunk_interval"),
            "enabled": retention.get("enabled"),
        }
        if retention
        else None
    )
    checks.append(
        _check(
            "simulation_frames_retention",
            actual_retention == expected_retention
            and actual_retention_days == retention_days,
            expected={**expected_retention, "drop_after_days": retention_days},
            actual=(
                {**actual_retention, "drop_after_days": actual_retention_days}
                if actual_retention
                else None
            ),
            message="Timescale retention policy matches the declared result-retention days",
        )
    )

    raw_columns = manifest.get("spatial_columns", [])
    raw_indexes = manifest.get("spatial_indexes", [])
    if not isinstance(raw_columns, Sequence) or isinstance(raw_columns, (str, bytes)):
        raise ValueError("manifest field 'spatial_columns' must be an array")
    if not isinstance(raw_indexes, Sequence) or isinstance(raw_indexes, (str, bytes)):
        raise ValueError("manifest field 'spatial_indexes' must be an array")
    columns = [_as_dict(item, name="spatial_columns[]") for item in raw_columns]
    indexes = [_as_dict(item, name="spatial_indexes[]") for item in raw_indexes]

    if profile == "postgis":
        actual_columns = {
            str(column.get("table_name")): {
                "column_name": column.get("column_name"),
                "format_type": column.get("format_type"),
                "generated": column.get("generated"),
            }
            for column in columns
        }
        expected_columns = {
            table: {
                "column_name": "geometry",
                "format_type": geometry_type,
                "generated": "s",
            }
            for table, geometry_type in POSTGIS_COLUMNS.items()
        }
        checks.append(
            _check(
                "postgis_generated_geometry",
                actual_columns == expected_columns,
                expected=expected_columns,
                actual=actual_columns,
                message="PostGIS geometry columns are typed, SRID 4326, and generated",
            )
        )
        actual_indexes = {
            str(index.get("table_name")): {
                "index_name": index.get("index_name"),
                "access_method": index.get("access_method"),
            }
            for index in indexes
        }
        expected_indexes = {
            table: {
                "index_name": f"ix_hydraulic_{table}_geometry_gist",
                "access_method": "gist",
            }
            for table in SPATIAL_TABLES
        }
        checks.append(
            _check(
                "postgis_spatial_indexes",
                actual_indexes == expected_indexes,
                expected=expected_indexes,
                actual=actual_indexes,
                message="all three typed geometry columns have their expected GiST index",
            )
        )
    else:
        checks.append(
            _check(
                "portable_has_no_geometry_columns",
                columns == [],
                expected=[],
                actual=columns,
                message="portable profile persists WKT+bbox only",
            )
        )
        checks.append(
            _check(
                "portable_has_no_spatial_gist",
                indexes == [],
                expected=[],
                actual=indexes,
                message="portable profile has no PostGIS-only spatial index",
            )
        )

    return {
        "ok": all(bool(check["ok"]) for check in checks),
        "profile": profile,
        "expected_revision": expected_revision,
        "retention_days": retention_days,
        "checks": checks,
    }


def _normalize_dsn(dsn: str) -> str:
    normalized = dsn.strip()
    for prefix in ("postgresql+asyncpg://", "postgres+asyncpg://"):
        normalized = normalized.replace(prefix, "postgresql://", 1)
    for prefix in ("postgres://",):
        normalized = normalized.replace(prefix, "postgresql://", 1)
    if not normalized.startswith(("postgresql://", "postgresql+")):
        raise ValueError("DSN must use PostgreSQL")
    return normalized


async def collect_manifest(dsn: str, *, profile: str) -> dict[str, object]:
    """Collect only database state; no DDL or data mutation is performed."""

    try:
        import asyncpg
    except (
        ImportError
    ) as exc:  # pragma: no cover - exercised by deployment environments
        raise RuntimeError(
            "asyncpg is required for live verification; install backend requirements"
        ) from exc

    connection = await asyncpg.connect(
        dsn=_normalize_dsn(dsn),
        server_settings={"hydraulic.spatial_backend": profile},
    )
    try:
        database_record = await connection.fetchrow(
            "SELECT current_database() AS name, "
            "current_setting('server_version') AS server_version"
        )
        extension_records = await connection.fetch(
            "SELECT extname, extversion FROM pg_extension "
            "WHERE extname = ANY($1::text[]) ORDER BY extname",
            sorted(COMMON_EXTENSIONS | {"postgis"}),
        )
        alembic_record = await connection.fetchrow(
            "SELECT version_num FROM alembic_version LIMIT 1"
        )
        metadata_record = await connection.fetchrow(
            "SELECT value, alembic_revision FROM hydraulic.schema_metadata "
            "WHERE key = 'spatial_backend'"
        )
        hypertable_record = await connection.fetchrow(
            "SELECT h.hypertable_schema AS schema, h.hypertable_name AS table, "
            "d.column_name AS time_column, d.time_interval::text AS time_interval "
            "FROM timescaledb_information.hypertables AS h "
            "JOIN timescaledb_information.dimensions AS d "
            "ON d.hypertable_schema = h.hypertable_schema "
            "AND d.hypertable_name = h.hypertable_name "
            "WHERE h.hypertable_schema = 'hydraulic' "
            "AND h.hypertable_name = 'simulation_frames' "
            "AND d.dimension_number = 1"
        )
        retention_record = await connection.fetchrow(
            "SELECT p.table_name, p.retention_days, p.chunk_interval, p.enabled, "
            "j.config->>'drop_after' AS drop_after "
            "FROM hydraulic.result_retention_policies AS p "
            "LEFT JOIN timescaledb_information.jobs AS j "
            "ON j.hypertable_schema = 'hydraulic' "
            "AND j.hypertable_name = p.table_name "
            "AND j.proc_name = 'policy_retention' "
            "WHERE p.table_name = 'simulation_frames'"
        )
        spatial_column_records = await connection.fetch(
            "SELECT c.table_name, c.column_name, "
            "format_type(a.atttypid, a.atttypmod) AS format_type, "
            "a.attgenerated AS generated "
            "FROM information_schema.columns AS c "
            "JOIN pg_namespace AS n ON n.nspname = c.table_schema "
            "JOIN pg_class AS t ON t.relnamespace = n.oid AND t.relname = c.table_name "
            "JOIN pg_attribute AS a ON a.attrelid = t.oid AND a.attname = c.column_name "
            "WHERE c.table_schema = 'hydraulic' "
            "AND c.table_name = ANY($1::text[]) "
            "AND c.column_name = 'geometry' "
            "ORDER BY c.table_name",
            list(SPATIAL_TABLES),
        )
        spatial_index_records = await connection.fetch(
            "SELECT i.tablename AS table_name, i.indexname AS index_name, "
            "am.amname AS access_method "
            "FROM pg_indexes AS i "
            "JOIN pg_class AS idx ON idx.relname = i.indexname "
            "JOIN pg_namespace AS idx_ns ON idx_ns.oid = idx.relnamespace "
            "AND idx_ns.nspname = i.schemaname "
            "JOIN pg_am AS am ON am.oid = idx.relam "
            "WHERE i.schemaname = 'hydraulic' "
            "AND i.tablename = ANY($1::text[]) "
            "AND i.indexdef ILIKE '%geometry%' "
            "ORDER BY i.tablename, i.indexname",
            list(SPATIAL_TABLES),
        )
    finally:
        await connection.close()

    manifest = {
        "database": dict(database_record) if database_record else {},
        "extensions": {
            str(record["extname"]): str(record["extversion"])
            for record in extension_records
        },
        "alembic_revision": (
            str(alembic_record["version_num"]) if alembic_record else None
        ),
        "schema_metadata": dict(metadata_record) if metadata_record else None,
        "hypertable": dict(hypertable_record) if hypertable_record else None,
        "retention": dict(retention_record) if retention_record else None,
        "spatial_columns": [dict(record) for record in spatial_column_records],
        "spatial_indexes": [dict(record) for record in spatial_index_records],
    }
    return cast(
        dict[str, object],
        _normalize_json_value(manifest, name="manifest"),
    )


def _redact_error(message: str, dsn: str) -> str:
    redacted = message.replace(dsn, "<redacted-dsn>")
    try:
        password = urlsplit(_normalize_dsn(dsn)).password
    except ValueError:
        password = None
    if password:
        redacted = redacted.replace(password, "***")
    return redacted


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify extensions, Alembic head, hypertable, retention, and spatial indexes"
    )
    parser.add_argument(
        "--dsn",
        default=os.getenv("DATABASE_URL_SYNC") or os.getenv("DATABASE_URL"),
        help="PostgreSQL DSN; defaults to DATABASE_URL_SYNC then DATABASE_URL",
    )
    parser.add_argument("--profile", required=True, choices=("postgis", "portable"))
    parser.add_argument(
        "--expected-revision",
        default=os.getenv("HYDRAULIC_SCHEMA_REVISION", DEFAULT_REVISION),
    )
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    parser.add_argument("--output", type=Path, help="optional JSON evidence path")
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.dsn:
        parser.error("--dsn or DATABASE_URL_SYNC/DATABASE_URL is required")

    result: dict[str, object]
    try:
        manifest = asyncio.run(collect_manifest(args.dsn, profile=args.profile))
        result = validate_manifest(
            manifest,
            profile=args.profile,
            expected_revision=args.expected_revision,
            retention_days=args.retention_days,
        )
        result["manifest"] = manifest
    except Exception as exc:  # CLI boundary: emit evidence and return a stable code
        result = {
            "ok": False,
            "profile": args.profile,
            "error": {
                "type": type(exc).__name__,
                "message": _redact_error(str(exc), args.dsn),
            },
        }
        exit_code = 2
    else:
        exit_code = 0 if bool(result["ok"]) else 1

    result = cast(
        dict[str, object],
        _normalize_json_value(result, name="result"),
    )
    result["verified_at"] = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(
        result,
        allow_nan=False,
        ensure_ascii=False,
        indent=None if args.compact else 2,
        sort_keys=True,
    )
    print(payload)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
