#!/usr/bin/env python3
"""Create a verifiable backup for the drainage deployment.

Secrets are accepted only through environment variables or files. They are sent
to the container command over stdin and are never written to the manifest,
archive, process arguments, or logs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Iterable, Sequence

from backup_manifest import (
    MANIFEST_SCHEMA,
    canonicalize_pg_dump,
    file_component,
    sha256_bytes,
    sha256_file,
    tree_component,
    validate_sha256,
    write_json,
    write_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "backups"
SUPPORTED_COMPONENTS = ("postgres", "redis", "influx")
PROFILES = ("postgis", "portable")
_BACKUP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ABSOLUTE_CONTAINER_PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]+$")


class BackupError(RuntimeError):
    """Raised when a backup cannot be completed safely."""


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _run_command(
    command: Sequence[str],
    *,
    input_value: str | bytes | None = None,
    text: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess[Any]:
    raw_input = input_value.encode("utf-8") if isinstance(input_value, str) else input_value
    completed = subprocess.run(
        list(command),
        input=raw_input,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        check=False,
    )
    if text:
        completed.stdout = (
            completed.stdout.decode("utf-8", errors="replace")
            if isinstance(completed.stdout, bytes)
            else completed.stdout
        )
        completed.stderr = (
            completed.stderr.decode("utf-8", errors="replace")
            if isinstance(completed.stderr, bytes)
            else completed.stderr
        )
    if check and completed.returncode != 0:
        stderr = completed.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        raise BackupError(
            f"command failed ({completed.returncode}): {shlex.join(command)}: {stderr.strip()}"
        )
    return completed


def _read_secret(
    file_value: str | None,
    env_names: Sequence[str],
) -> str | None:
    if file_value:
        path = Path(file_value).expanduser().resolve(strict=True)
        secret = path.read_text(encoding="utf-8").rstrip("\r\n")
        return secret or None
    for name in env_names:
        value = os.environ.get(name)
        if value is not None:
            return value.rstrip("\r\n") or None
    return None


def _parse_components(raw: str) -> tuple[str, ...]:
    values = [item.strip().lower() for item in raw.split(",") if item.strip()]
    if not values:
        raise BackupError("at least one backup component is required")
    unknown = sorted(set(values) - set(SUPPORTED_COMPONENTS))
    if unknown:
        raise BackupError(f"unsupported backup component(s): {', '.join(unknown)}")
    duplicates = sorted({item for item in values if values.count(item) > 1})
    if duplicates:
        raise BackupError(f"duplicate backup component(s): {', '.join(duplicates)}")
    return tuple(item for item in SUPPORTED_COMPONENTS if item in values)


def _validate_container_path(value: str, *, field: str) -> str:
    normalized = value.rstrip("/")
    if not _ABSOLUTE_CONTAINER_PATH_RE.fullmatch(normalized) or ".." in normalized.split("/"):
        raise BackupError(f"{field} must be a safe absolute container path")
    return normalized


def _validate_container_relative(value: str, *, field: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", value) or value in {".", ".."}:
        raise BackupError(f"{field} must be one safe container path component")
    return value


def _auth_shell(
    command: Sequence[str],
    *,
    secret: str | None,
    variable: str,
    shell_suffix: str = "",
) -> tuple[str, str | None]:
    executable = shlex.join(command)
    if shell_suffix:
        executable = f"{executable} {shell_suffix}"
    if secret is None:
        return (f"exec {executable}" if not shell_suffix else executable), None
    invocation = f"exec {executable}" if not shell_suffix else executable
    return f"IFS= read -r {variable}; export {variable}; {invocation}", secret + "\n"


def _git_metadata() -> dict[str, Any]:
    try:
        commit = _run_command(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            text=True,
        ).stdout.strip()
        status = _run_command(
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--untracked-files=all"],
            text=True,
        ).stdout
    except (BackupError, FileNotFoundError):
        return {"commit": None, "dirty": None, "available": False}
    return {"commit": commit, "dirty": bool(status.strip()), "available": True}


def _parse_images_json(raw: str) -> list[dict[str, Any]]:
    raw = raw.strip()
    if not raw:
        return []
    parsed: Any = None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        rows: list[dict[str, Any]] = []
        for line in raw.splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
        parsed = rows
    if isinstance(parsed, dict):
        parsed = [parsed]
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


class OperationsBackup:
    def __init__(self, args: argparse.Namespace, runner=None) -> None:
        self.args = args
        self.runner = runner or _run_command
        self.compose_file = Path(args.compose_file).expanduser().resolve(strict=True)
        self.compose_base = [args.docker_bin, "compose", "-f", str(self.compose_file)]
        if args.project_name:
            self.compose_base.extend(["-p", args.project_name])
        self.components = _parse_components(args.components)
        self.profile = args.schema_profile
        self.pg_password = _read_secret(args.pg_password_file, ("PGPASSWORD", "POSTGRES_PASSWORD"))
        self.redis_password = _read_secret(
            args.redis_password_file,
            ("REDISCLI_AUTH", "REDIS_PASSWORD"),
        )
        self.influx_token = _read_secret(args.influx_token_file, ("INFLUX_TOKEN",))
        self.redis_data_path = _validate_container_path(
            args.redis_container_data_path,
            field="--redis-container-data-path",
        )
        self.redis_appenddirname = _validate_container_relative(
            args.redis_appenddirname,
            field="--redis-appenddirname",
        )
        self.warnings: list[str] = []

    def _compose(self, *args: str) -> list[str]:
        return [*self.compose_base, *args]

    def _run(
        self,
        command: Sequence[str],
        *,
        input_value: str | bytes | None = None,
        text: bool = True,
        check: bool = True,
    ) -> subprocess.CompletedProcess[Any]:
        return self.runner(
            command,
            input_value=input_value,
            text=text,
            check=check,
        )

    def _exec_authenticated(
        self,
        service: str,
        command: Sequence[str],
        *,
        secret: str | None,
        variable: str,
        input_value: str | bytes | None = None,
        text: bool = True,
        shell_suffix: str = "",
    ) -> subprocess.CompletedProcess[Any]:
        script, secret_input = _auth_shell(
            command,
            secret=secret,
            variable=variable,
            shell_suffix=shell_suffix,
        )
        if input_value is not None:
            if secret_input is not None:
                raise BackupError("cannot combine command input with authenticated stdin")
            secret_input = input_value.decode("utf-8") if isinstance(input_value, bytes) else input_value
        if not text and isinstance(secret_input, str):
            secret_input = secret_input.encode("utf-8")
        return self._run(
            self._compose("exec", "-T", service, "sh", "-ec", script),
            input_value=secret_input,
            text=text,
        )

    def collect_images(self) -> list[dict[str, Any]]:
        result = self._run(self._compose("images", "--format", "json"), check=False)
        if result.returncode != 0:
            self.warnings.append("docker compose images was unavailable")
            return []
        images = _parse_images_json(result.stdout if isinstance(result.stdout, str) else "")
        return sorted(
            images,
            key=lambda item: (
                str(item.get("Service") or item.get("service") or ""),
                str(item.get("Name") or item.get("name") or ""),
            ),
        )

    def _psql(
        self,
        sql: str,
        *,
        database: str | None = None,
        user: str | None = None,
    ) -> str:
        command = [
            "psql",
            "--no-psqlrc",
            "--set=ON_ERROR_STOP=1",
            "--tuples-only",
            "--no-align",
            "--dbname",
            database or self.args.postgres_db,
            "--username",
            user or self.args.postgres_user,
            "--command",
            sql,
        ]
        result = self._exec_authenticated(
            self.args.postgres_service,
            command,
            secret=self.pg_password,
            variable="PGPASSWORD",
        )
        return result.stdout.strip() if isinstance(result.stdout, str) else ""

    def _pg_dump(self, extra: Sequence[str], *, binary: bool) -> bytes:
        command = [
            "pg_dump",
            "--no-owner",
            "--no-privileges",
            "--dbname",
            self.args.postgres_db,
            "--username",
            self.args.postgres_user,
        ]
        if "--data-only" in extra:
            for schema in (
                "_timescaledb_catalog",
                "_timescaledb_internal",
                "_timescaledb_config",
                "_timescaledb_cache",
            ):
                command.extend(["--exclude-schema", schema])
        command.extend(extra)
        result = self._exec_authenticated(
            self.args.postgres_service,
            command,
            secret=self.pg_password,
            variable="PGPASSWORD",
            text=not binary,
            shell_suffix="> /dev/stdout",
        )
        if binary:
            if not isinstance(result.stdout, bytes):
                raise BackupError("pg_dump did not return binary output")
            return result.stdout
        if not isinstance(result.stdout, str):
            raise BackupError("pg_dump did not return text output")
        return canonicalize_pg_dump(
            result.stdout,
            sort_lines="--data-only" in extra,
        )

    def _database_metadata(self) -> dict[str, Any]:
        connectivity = self._psql("SELECT 1;")
        if connectivity != "1":
            raise BackupError("PostgreSQL connectivity check failed")
        extensions: dict[str, str] = {}
        extension_output = self._psql(
            "SELECT extname, extversion FROM pg_extension "
            "WHERE extname IN ('postgis', 'timescaledb', 'uuid-ossp') "
            "ORDER BY extname;"
        )
        for line in extension_output.splitlines():
            parts = line.split("|", 1)
            if len(parts) == 2:
                extensions[parts[0]] = parts[1]
        if "timescaledb" not in extensions:
            raise BackupError("TimescaleDB extension is not installed")
        if self.profile == "postgis" and "postgis" not in extensions:
            raise BackupError("schema profile is postgis but PostGIS is not installed")
        if self.profile == "portable" and "postgis" in extensions:
            raise BackupError("schema profile is portable but PostGIS is installed")

        alembic_table = self._psql("SELECT COALESCE(to_regclass('public.alembic_version')::text, '');")
        alembic_revision = ""
        if alembic_table:
            alembic_revision = self._psql(
                "SELECT COALESCE((SELECT version_num FROM public.alembic_version LIMIT 1), '');"
            )
        row_counts = self._row_counts()
        topology = self._topology_summary()
        return {
            "alembic_revision": alembic_revision or None,
            "extensions": extensions,
            "profile": self.profile,
            "row_counts": row_counts,
            "topology": topology,
        }

    def _row_counts(self) -> dict[str, int]:
        create_function = r"""
CREATE OR REPLACE FUNCTION pg_temp.hydraulic_backup_row_counts()
RETURNS TABLE(table_name text, row_count bigint)
LANGUAGE plpgsql
AS $function$
DECLARE
  item record;
  count_value bigint;
BEGIN
  FOR item IN
    SELECT schemaname, tablename
    FROM pg_tables
    WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
      AND left(schemaname, 1) <> '_'
      AND left(schemaname, 8) <> 'pg_temp_'
      AND left(tablename, 1) <> '_'
      AND left(tablename, 8) <> 'pg_temp_'
    ORDER BY schemaname, tablename
  LOOP
    EXECUTE format('SELECT count(*) FROM %I.%I', item.schemaname, item.tablename)
      INTO count_value;
    table_name := format('%s.%s', item.schemaname, item.tablename);
    row_count := count_value;
    RETURN NEXT;
  END LOOP;
END
$function$
"""
        command = [
            "psql",
            "--no-psqlrc",
            "--quiet",
            "--set=ON_ERROR_STOP=1",
            "--tuples-only",
            "--no-align",
            "--dbname",
            self.args.postgres_db,
            "--username",
            self.args.postgres_user,
            "--command",
            create_function,
            "--command",
            "SELECT table_name, row_count FROM pg_temp.hydraulic_backup_row_counts();",
        ]
        result = self._exec_authenticated(
            self.args.postgres_service,
            command,
            secret=self.pg_password,
            variable="PGPASSWORD",
        )
        output = result.stdout.strip() if isinstance(result.stdout, str) else ""
        counts: dict[str, int] = {}
        for line in output.splitlines():
            parts = line.split("|", 1)
            if len(parts) != 2:
                continue
            try:
                counts[parts[0]] = int(parts[1])
            except ValueError:
                continue
        if not counts:
            raise BackupError("database row-count verification produced no tables")
        return counts

    def _topology_summary(self) -> dict[str, Any]:
        relations = (
            "hydraulic.networks",
            "hydraulic.network_nodes",
            "hydraulic.pipe_segments",
            "hydraulic.catchments",
        )
        present: dict[str, bool] = {}
        for relation in relations:
            present[relation] = bool(
                self._psql(f"SELECT to_regclass('{relation}') IS NOT NULL;")
            )
        summary: dict[str, Any] = {"relations_present": present}
        for relation, is_present in present.items():
            if is_present:
                value = self._psql(f"SELECT count(*)::bigint FROM {relation};")
                try:
                    summary[relation] = int(value)
                except ValueError as exc:
                    raise BackupError(f"invalid topology count for {relation}") from exc
        return summary

    def _backup_postgres(self, root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
        postgres_dir = root / "postgres"
        postgres_dir.mkdir(parents=True, exist_ok=True)
        dump_path = postgres_dir / "database.dump"
        dump_payload = self._pg_dump(
            ("--format=custom",),
            binary=True,
        )
        if not dump_payload:
            raise BackupError("PostgreSQL custom dump is empty")
        dump_path.write_bytes(dump_payload)
        schema_hash = sha256_bytes(self._pg_dump(("--schema-only",), binary=False))
        data_hash = sha256_bytes(self._pg_dump(("--data-only",), binary=False))
        database = self._database_metadata()
        database.update(
            {
                "data_sha256": data_hash,
                "database": self.args.postgres_db,
                "schema_sha256": schema_hash,
                "user": self.args.postgres_user,
            }
        )
        return file_component("postgres/database.dump", dump_path), database

    def _redis_command(self, *arguments: str) -> list[str]:
        command = ["redis-cli"]
        if self.args.redis_username:
            command.extend(["--user", self.args.redis_username])
        command.extend(arguments)
        return command

    def _redis_query(self, *arguments: str) -> str:
        result = self._exec_authenticated(
            self.args.redis_service,
            self._redis_command(*arguments),
            secret=self.redis_password,
            variable="REDISCLI_AUTH",
        )
        return result.stdout.strip() if isinstance(result.stdout, str) else ""

    def _backup_redis(self, root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
        redis_dir = root / "redis"
        redis_dir.mkdir(parents=True, exist_ok=True)
        remote_path = f"/tmp/drainage-redis-{self.args.backup_id}.rdb"
        local_path = redis_dir / "dump.rdb"
        configured_output = self._redis_query("CONFIG", "GET", "appenddirname")
        configured_lines = [line.strip() for line in configured_output.splitlines() if line.strip()]
        configured_appenddirname = configured_lines[-1] if configured_lines else ""
        if configured_appenddirname and configured_appenddirname != self.redis_appenddirname:
            raise BackupError(
                f"Redis appenddirname is {configured_appenddirname!r}, not "
                f"{self.redis_appenddirname!r}"
            )
        db_size_before_raw = self._redis_query("DBSIZE")
        try:
            db_size_before = int(db_size_before_raw)
        except ValueError as exc:
            raise BackupError(f"invalid Redis DBSIZE response: {db_size_before_raw!r}") from exc
        self._exec_authenticated(
            self.args.redis_service,
            self._redis_command("--rdb", remote_path),
            secret=self.redis_password,
            variable="REDISCLI_AUTH",
        )
        self._run(self._compose("cp", f"{self.args.redis_service}:{remote_path}", str(local_path)))
        self._run(
            self._compose(
                "exec",
                "-T",
                self.args.redis_service,
                "rm",
                "-f",
                "--",
                remote_path,
            ),
            check=False,
        )
        if not local_path.is_file() or local_path.stat().st_size == 0:
            raise BackupError("Redis RDB snapshot is missing or empty")
        db_size_raw = self._redis_query("DBSIZE")
        try:
            db_size = int(db_size_raw)
        except ValueError as exc:
            raise BackupError(f"invalid Redis DBSIZE response: {db_size_raw!r}") from exc
        if db_size != db_size_before:
            raise BackupError(
                "Redis key count changed while the RDB snapshot was being copied; "
                "pause writers or retry the backup"
            )
        return file_component("redis/dump.rdb", local_path), {
            "appenddirname": self.redis_appenddirname,
            "db_size": db_size,
        }

    def _backup_influx(self, root: Path) -> dict[str, Any]:
        influx_dir = root / "influx"
        influx_dir.mkdir(parents=True, exist_ok=True)
        remote_path = f"/tmp/drainage-influx-{self.args.backup_id}"
        local_path = influx_dir / "backup"
        command = [
            "influx",
            "backup",
            remote_path,
            "--host",
            self.args.influx_url,
            "--org",
            self.args.influx_org,
        ]
        self._exec_authenticated(
            self.args.influx_service,
            command,
            secret=self.influx_token,
            variable="INFLUX_TOKEN",
        )
        self._run(self._compose("cp", f"{self.args.influx_service}:{remote_path}", str(local_path)))
        self._run(
            self._compose(
                "exec",
                "-T",
                self.args.influx_service,
                "rm",
                "-rf",
                "--",
                remote_path,
            ),
            check=False,
        )
        if not local_path.is_dir():
            raise BackupError("InfluxDB backup directory is missing")
        return tree_component("influx/backup", local_path)

    def _solver_metadata(self) -> dict[str, Any]:
        supplied = self.args.solver_sha256
        path_hash: str | None = None
        if self.args.solver_path:
            path_hash = sha256_file(Path(self.args.solver_path).expanduser().resolve(strict=True))
        if supplied:
            supplied = validate_sha256(supplied, field="--solver-sha256")
        if supplied and path_hash and supplied != path_hash:
            raise BackupError("--solver-sha256 does not match --solver-path")
        engine_sha256 = supplied or path_hash
        if not engine_sha256:
            self.warnings.append("solver engine SHA-256 was not provided")
        return {
            "engine_sha256": engine_sha256 or "unavailable",
            "path": str(Path(self.args.solver_path).expanduser().resolve()) if self.args.solver_path else None,
        }

    def run(self) -> Path:
        output_dir = Path(self.args.output_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        archive_path = output_dir / f"{self.args.backup_id}.tar.gz"
        if archive_path.exists() or archive_path.with_suffix(".tar.gz.sha256").exists():
            raise BackupError(f"backup archive already exists: {archive_path}")
        work_parent = Path(tempfile.mkdtemp(prefix="scn-backup-", dir=output_dir))
        work_root = work_parent / self.args.backup_id
        work_root.mkdir()
        archive_tmp = output_dir / f".{self.args.backup_id}.tar.gz.tmp"
        try:
            components: dict[str, Any] = {}
            database: dict[str, Any] = {}
            redis: dict[str, Any] = {}
            images = self.collect_images()
            repo = _git_metadata()
            solver = self._solver_metadata()
            if "postgres" in self.components:
                components["postgres"], database = self._backup_postgres(work_root)
            if "redis" in self.components:
                components["redis"], redis = self._backup_redis(work_root)
            if "influx" in self.components:
                components["influx"] = self._backup_influx(work_root)

            evidence_dir = work_root / "evidence"
            evidence_dir.mkdir()
            evidence_values = {
                "images": images,
                "repo": repo,
                "solver": solver,
            }
            artifact_entries: dict[str, Any] = {}
            for name, value in evidence_values.items():
                relative = f"evidence/{name}.json"
                write_json(evidence_dir / f"{name}.json", value)
                artifact_entries[name] = file_component(relative, evidence_dir / f"{name}.json")

            metadata_reasons: list[str] = []
            if set(self.components) != set(SUPPORTED_COMPONENTS):
                metadata_reasons.append("not all backup components were selected")
            if not images:
                metadata_reasons.append("image digests are unavailable")
            if solver["engine_sha256"] == "unavailable":
                metadata_reasons.append("solver engine SHA-256 is unavailable")
            if not self.args.writers_paused:
                metadata_reasons.append("writer pause was not explicitly confirmed")
            if not repo.get("commit"):
                metadata_reasons.append("git commit metadata is unavailable")

            manifest: dict[str, Any] = {
                "artifacts": artifact_entries,
                "backup_id": self.args.backup_id,
                "components": components,
                "created_at": utc_now().isoformat().replace("+00:00", "Z"),
                "database": database,
                "influx": {
                    "host": self.args.influx_url,
                    "org": self.args.influx_org,
                }
                if "influx" in self.components
                else None,
                "metadata_complete": not metadata_reasons,
                "metadata_gaps": metadata_reasons,
                "redis": redis or None,
                "repo": repo,
                "runtime": {
                    "images": images,
                    "solver": solver,
                },
                "schema": MANIFEST_SCHEMA,
                "services": {
                    "influx": self.args.influx_service,
                    "postgres": self.args.postgres_service,
                    "redis": self.args.redis_service,
                },
                "writers_paused": bool(self.args.writers_paused),
            }
            if self.warnings:
                manifest["warnings"] = sorted(set(self.warnings))
            _, manifest_hash = write_manifest(work_root, manifest)

            with tarfile.open(archive_tmp, "w:gz") as archive:
                archive.add(work_root, arcname=self.args.backup_id, recursive=True)
            os.replace(archive_tmp, archive_path)
            archive_hash = sha256_file(archive_path)
            sidecar = archive_path.with_suffix(archive_path.suffix + ".sha256")
            sidecar_tmp = sidecar.with_suffix(sidecar.suffix + ".tmp")
            sidecar_tmp.write_text(
                f"{archive_hash}  {archive_path.name}\n",
                encoding="ascii",
            )
            os.replace(sidecar_tmp, sidecar)
            self._print_summary(archive_path, sidecar, manifest_hash, manifest)
            return archive_path
        except Exception:
            shutil.rmtree(work_parent, ignore_errors=True)
            archive_tmp.unlink(missing_ok=True)
            archive_path.with_suffix(archive_path.suffix + ".sha256").unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(work_parent, ignore_errors=True)

    def _print_summary(
        self,
        archive_path: Path,
        sidecar: Path,
        manifest_hash: str,
        manifest: dict[str, Any],
    ) -> None:
        print("BACKUP_STATUS=COMPLETE")
        print(f"BACKUP_ARCHIVE={archive_path}")
        print(f"BACKUP_SHA256_FILE={sidecar}")
        print(f"MANIFEST_SHA256={manifest_hash}")
        print(f"BACKUP_COMPONENTS={','.join(manifest['components'])}")
        print(f"METADATA_COMPLETE={str(manifest['metadata_complete']).lower()}")
        for gap in manifest["metadata_gaps"]:
            print(f"METADATA_GAP={gap}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a hash-verified PostgreSQL/Timescale/PostGIS, Redis, and InfluxDB backup.",
    )
    parser.add_argument("--backup-id", help="Archive basename; defaults to UTC timestamp")
    parser.add_argument(
        "--components",
        default="postgres,redis,influx",
        help="Comma-separated subset of postgres,redis,influx",
    )
    parser.add_argument("--compose-file", default=str(DEFAULT_COMPOSE_FILE))
    parser.add_argument("--project-name", help="Docker Compose project name override")
    parser.add_argument("--docker-bin", default="docker")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--postgres-service", default="postgres")
    parser.add_argument("--postgres-user", default=os.environ.get("POSTGRES_USER"))
    parser.add_argument("--postgres-db", default=os.environ.get("POSTGRES_DB"))
    parser.add_argument("--pg-password-file", help="File containing PGPASSWORD/POSTGRES_PASSWORD")
    parser.add_argument(
        "--schema-profile",
        choices=PROFILES,
        default=os.environ.get("HYDRAULIC_SPATIAL_BACKEND"),
    )
    parser.add_argument("--redis-service", default="redis")
    parser.add_argument("--redis-username", default=os.environ.get("REDIS_USERNAME"))
    parser.add_argument("--redis-password-file", help="File containing REDISCLI_AUTH/REDIS_PASSWORD")
    parser.add_argument("--redis-container-data-path", default="/data")
    parser.add_argument("--redis-appenddirname", default="appendonlydir")
    parser.add_argument("--influx-service", default="influxdb")
    parser.add_argument("--influx-url", default="http://127.0.0.1:8086")
    parser.add_argument("--influx-org", default=os.environ.get("INFLUX_ORG"))
    parser.add_argument("--influx-token-file", help="File containing INFLUX_TOKEN")
    parser.add_argument("--solver-path", help="Optional solver binary to hash")
    parser.add_argument("--solver-sha256", default=os.environ.get("SOLVER_ENGINE_SHA256"))
    parser.add_argument(
        "--writers-paused",
        action="store_true",
        help="Confirm API/workers/schedulers are paused for a consistent database snapshot",
    )
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if args.backup_id is None:
        args.backup_id = utc_now().strftime("%Y%m%dT%H%M%SZ")
    if not _BACKUP_ID_RE.fullmatch(args.backup_id):
        raise BackupError("--backup-id must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}")
    if "postgres" in _parse_components(args.components):
        if not args.postgres_user or not args.postgres_db:
            raise BackupError("PostgreSQL backup requires --postgres-user/--postgres-db or env")
        if not args.schema_profile:
            raise BackupError("PostgreSQL backup requires --schema-profile")
    if "influx" in _parse_components(args.components) and not args.influx_org:
        raise BackupError("InfluxDB backup requires --influx-org or INFLUX_ORG")
    if args.solver_sha256 and not _SHA256_RE.fullmatch(args.solver_sha256):
        raise BackupError("--solver-sha256 must be 64 lowercase hexadecimal characters")
    if args.solver_sha256:
        validate_sha256(args.solver_sha256, field="--solver-sha256")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        _validate_args(args)
        archive = OperationsBackup(args).run()
    except (BackupError, OSError, ValueError) as exc:
        print(f"BACKUP_STATUS=FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"BACKUP_CREATED={archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
