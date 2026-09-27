#!/usr/bin/env python3
"""Verify and restore a smart-city-drainage backup archive.

Verification is the default. Destructive restore requires both ``--apply`` and
``--confirm <backup_id>``. Service names are used through Docker Compose; no
container name is embedded in either script.
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
import time
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Sequence

from backup_manifest import (
    canonicalize_pg_dump,
    load_verified_manifest,
    safe_relative_path,
    sha256_bytes,
    sha256_file,
    write_json,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
SUPPORTED_COMPONENTS = ("postgres", "redis", "influx")
_ABSOLUTE_CONTAINER_PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]+$")


class RestoreError(RuntimeError):
    """Raised when an archive cannot be verified or restored safely."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _run_command(
    command: Sequence[str],
    *,
    input_value: str | bytes | BinaryIO | None = None,
    text: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess[Any]:
    raw_input = input_value.encode("utf-8") if isinstance(input_value, str) else input_value
    run_options: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": False,
        "check": False,
    }
    if hasattr(raw_input, "read"):
        run_options["stdin"] = raw_input
    else:
        run_options["input"] = raw_input
    completed = subprocess.run(list(command), **run_options)
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
        raise RestoreError(
            f"command failed ({completed.returncode}): {shlex.join(command)}: {stderr.strip()}"
        )
    return completed


def _read_secret(file_value: str | None, env_names: Sequence[str]) -> str | None:
    if file_value:
        path = Path(file_value).expanduser().resolve(strict=True)
        return path.read_text(encoding="utf-8").rstrip("\r\n") or None
    for name in env_names:
        value = os.environ.get(name)
        if value is not None:
            return value.rstrip("\r\n") or None
    return None


def _parse_components(raw: str | None, manifest: dict[str, Any]) -> tuple[str, ...]:
    available = tuple(name for name in SUPPORTED_COMPONENTS if name in manifest.get("components", {}))
    if raw is None:
        if not available:
            raise RestoreError("archive contains no restorable components")
        return available
    requested = [item.strip().lower() for item in raw.split(",") if item.strip()]
    unknown = sorted(set(requested) - set(SUPPORTED_COMPONENTS))
    if unknown:
        raise RestoreError(f"unsupported restore component(s): {', '.join(unknown)}")
    missing = sorted(set(requested) - set(available))
    if missing:
        raise RestoreError(f"component(s) not present in archive: {', '.join(missing)}")
    if not requested:
        raise RestoreError("at least one restore component is required")
    return tuple(item for item in SUPPORTED_COMPONENTS if item in requested)


def _validate_container_path(value: str, *, field: str) -> str:
    normalized = value.rstrip("/")
    if not _ABSOLUTE_CONTAINER_PATH_RE.fullmatch(normalized) or ".." in normalized.split("/"):
        raise RestoreError(f"{field} must be a safe absolute container path")
    return normalized


def _validate_container_relative(value: str, *, field: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", value) or value in {".", ".."}:
        raise RestoreError(f"{field} must be one safe container path component")
    return value


def _auth_shell(command: Sequence[str], *, secret: str | None, variable: str) -> tuple[str, str | None]:
    executable = shlex.join(command)
    if secret is None:
        return f"exec {executable}", None
    return f"IFS= read -r {variable}; export {variable}; exec {executable}", secret + "\n"


def _verify_sidecar(archive: Path, *, allow_missing: bool) -> str | None:
    sidecar = archive.with_suffix(archive.suffix + ".sha256")
    if not sidecar.is_file():
        if allow_missing:
            return None
        raise RestoreError(f"archive SHA-256 sidecar is missing: {sidecar}")
    fields = sidecar.read_text(encoding="ascii").strip().split()
    if len(fields) < 1 or not re.fullmatch(r"[0-9a-f]{64}", fields[0]):
        raise RestoreError(f"invalid archive SHA-256 sidecar: {sidecar}")
    actual = sha256_file(archive)
    if actual != fields[0]:
        raise RestoreError("archive SHA-256 sidecar does not match archive")
    return actual


def _safe_extract(archive: Path, destination: Path) -> Path:
    seen: set[str] = set()
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle.getmembers():
                member_path = PurePosixPath(member.name)
                if (
                    not member.name
                    or member_path.is_absolute()
                    or any(part in {"", ".", ".."} for part in member_path.parts)
                    or member.issym()
                    or member.islnk()
                    or not (member.isdir() or member.isfile())
                ):
                    raise RestoreError(f"unsafe archive member: {member.name!r}")
                normalized = member_path.as_posix()
                if normalized in seen:
                    raise RestoreError(f"duplicate archive member: {normalized}")
                seen.add(normalized)
                target = destination.joinpath(*member_path.parts)
                try:
                    target.resolve(strict=False).relative_to(destination.resolve())
                except ValueError as exc:
                    raise RestoreError(f"archive member escapes destination: {member.name!r}") from exc
            bundle.extractall(destination)
    except tarfile.TarError as exc:
        raise RestoreError(f"invalid tar.gz archive: {exc}") from exc
    roots = [path for path in destination.iterdir() if path.is_dir()]
    if len(roots) != 1:
        raise RestoreError("archive must contain exactly one backup directory")
    return roots[0]


def _parse_header(values: Sequence[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise RestoreError(f"header must use NAME=VALUE syntax: {value!r}")
        name, header_value = value.split("=", 1)
        if not name.strip():
            raise RestoreError("header name cannot be empty")
        headers[name.strip()] = header_value
    return headers


class OperationsRestore:
    def __init__(self, args: argparse.Namespace, runner=None) -> None:
        self.args = args
        self.runner = runner or _run_command
        self.compose_file = Path(args.compose_file).expanduser().resolve(strict=True)
        self.compose_base = [args.docker_bin, "compose", "-f", str(self.compose_file)]
        if args.project_name:
            self.compose_base.extend(["-p", args.project_name])
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
        self.manifest: dict[str, Any] | None = None
        self.manifest_root: Path | None = None
        self.checks: dict[str, Any] = {}
        self.warnings: list[str] = []

    def _compose(self, *args: str) -> list[str]:
        return [*self.compose_base, *args]

    def _run(
        self,
        command: Sequence[str],
        *,
        input_value: str | bytes | BinaryIO | None = None,
        text: bool = True,
        check: bool = True,
    ) -> subprocess.CompletedProcess[Any]:
        return self.runner(command, input_value=input_value, text=text, check=check)

    def _exec_authenticated(
        self,
        service: str,
        command: Sequence[str],
        *,
        secret: str | None,
        variable: str,
        text: bool = True,
    ) -> subprocess.CompletedProcess[Any]:
        script, secret_input = _auth_shell(command, secret=secret, variable=variable)
        if not text and isinstance(secret_input, str):
            secret_input = secret_input.encode("utf-8")
        return self._run(
            self._compose("exec", "-T", service, "sh", "-ec", script),
            input_value=secret_input,
            text=text,
        )

    def _component_path(self, component: str) -> Path:
        if self.manifest is None or self.manifest_root is None:
            raise RestoreError("manifest has not been loaded")
        entry = self.manifest["components"][component]
        relative = safe_relative_path(entry["path"])
        path = self.manifest_root.joinpath(*relative.parts)
        if not path.exists():
            raise RestoreError(f"component payload is missing: {component}")
        return path

    def _psql(self, sql: str) -> str:
        if self.manifest is None:
            raise RestoreError("manifest has not been loaded")
        database = self.args.postgres_db or self.manifest.get("database", {}).get("database")
        user = self.args.postgres_user or self.manifest.get("database", {}).get("user")
        if not database or not user:
            raise RestoreError("PostgreSQL restore requires database and user")
        command = [
            "psql",
            "--no-psqlrc",
            "--set=ON_ERROR_STOP=1",
            "--tuples-only",
            "--no-align",
            "--dbname",
            database,
            "--username",
            user,
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

    def _pg_dump_hash(self, mode: str) -> str:
        if self.manifest is None:
            raise RestoreError("manifest has not been loaded")
        database = self.args.postgres_db or self.manifest.get("database", {}).get("database")
        user = self.args.postgres_user or self.manifest.get("database", {}).get("user")
        if not database or not user:
            raise RestoreError("PostgreSQL restore requires database and user")
        command = [
            "pg_dump",
            "--no-owner",
            "--no-privileges",
            f"--{mode}",
            "--dbname",
            database,
            "--username",
            user,
        ]
        if mode == "data-only":
            for schema in (
                "_timescaledb_catalog",
                "_timescaledb_internal",
                "_timescaledb_config",
                "_timescaledb_cache",
            ):
                command.extend(["--exclude-schema", schema])
        result = self._exec_authenticated(
            self.args.postgres_service,
            command,
            secret=self.pg_password,
            variable="PGPASSWORD",
        )
        if not isinstance(result.stdout, str):
            raise RestoreError("pg_dump verification returned non-text output")
        return sha256_bytes(
            canonicalize_pg_dump(
                result.stdout,
                sort_lines=mode == "data-only",
            )
        )

    def _extensions(self) -> dict[str, str]:
        output = self._psql(
            "SELECT extname, extversion FROM pg_extension "
            "WHERE extname IN ('postgis', 'timescaledb', 'uuid-ossp') "
            "ORDER BY extname;"
        )
        extensions: dict[str, str] = {}
        for line in output.splitlines():
            parts = line.split("|", 1)
            if len(parts) == 2:
                extensions[parts[0]] = parts[1]
        return extensions

    def _current_profile(self) -> str | None:
        extensions = self._extensions()
        if "postgis" in extensions:
            return "postgis"
        if "timescaledb" in extensions:
            return "portable"
        return None

    def _alembic_revision(self) -> str | None:
        if not self._psql("SELECT to_regclass('public.alembic_version') IS NOT NULL;"):
            return None
        value = self._psql(
            "SELECT COALESCE((SELECT version_num FROM public.alembic_version LIMIT 1), '');"
        )
        return value or None

    def _row_counts(self) -> dict[str, int]:
        if self.manifest is None:
            raise RestoreError("manifest has not been loaded")
        database = self.args.postgres_db or self.manifest.get("database", {}).get("database")
        user = self.args.postgres_user or self.manifest.get("database", {}).get("user")
        if not database or not user:
            raise RestoreError("PostgreSQL restore requires database and user")
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
            database,
            "--username",
            user,
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
            if len(parts) == 2:
                try:
                    counts[parts[0]] = int(parts[1])
                except ValueError:
                    continue
        if not counts:
            raise RestoreError("restored database row-count verification produced no tables")
        return counts

    def _topology_summary(self) -> dict[str, Any]:
        relations = (
            "hydraulic.networks",
            "hydraulic.network_nodes",
            "hydraulic.pipe_segments",
            "hydraulic.catchments",
        )
        summary: dict[str, Any] = {"relations_present": {}}
        for relation in relations:
            present = bool(self._psql(f"SELECT to_regclass('{relation}') IS NOT NULL;"))
            summary["relations_present"][relation] = present
            if present:
                value = self._psql(f"SELECT count(*)::bigint FROM {relation};")
                try:
                    summary[relation] = int(value)
                except ValueError as exc:
                    raise RestoreError(f"invalid restored topology count for {relation}") from exc
        return summary

    def _preflight_database(self, expected_profile: str) -> None:
        connectivity = self._psql("SELECT 1;")
        if connectivity != "1":
            raise RestoreError("PostgreSQL target connectivity check failed")
        current_profile = self._current_profile()
        if current_profile is not None and current_profile != expected_profile:
            raise RestoreError(
                f"target database profile {current_profile!r} does not match backup {expected_profile!r}"
            )

    def _restore_postgres(self) -> None:
        if self.manifest is None:
            raise RestoreError("manifest has not been loaded")
        expected = self.manifest["database"]
        expected_profile = expected.get("profile")
        if expected_profile not in {"postgis", "portable"}:
            raise RestoreError("manifest has an invalid PostgreSQL profile")
        self._preflight_database(expected_profile)
        local_dump = self._component_path("postgres")
        remote_dump = f"/tmp/scn-restore-{self.manifest['backup_id']}.dump"
        self._run(self._compose("cp", str(local_dump), f"{self.args.postgres_service}:{remote_dump}"))
        database = self.args.postgres_db or expected.get("database")
        user = self.args.postgres_user or expected.get("user")
        if not database or not user:
            raise RestoreError("manifest does not contain PostgreSQL database/user")
        command = [
            "pg_restore",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            "--single-transaction",
            "--exit-on-error",
            "--dbname",
            database,
            "--username",
            user,
            remote_dump,
        ]
        try:
            self._exec_authenticated(
                self.args.postgres_service,
                command,
                secret=self.pg_password,
                variable="PGPASSWORD",
            )
        finally:
            self._run(
                self._compose(
                    "exec",
                    "-T",
                    self.args.postgres_service,
                    "rm",
                    "-f",
                    "--",
                    remote_dump,
                ),
                check=False,
            )
        actual_extensions = self._extensions()
        if "timescaledb" not in actual_extensions:
            raise RestoreError("restored database is missing TimescaleDB")
        if expected_profile == "postgis" and "postgis" not in actual_extensions:
            raise RestoreError("restored postgis database is missing PostGIS")
        if expected_profile == "portable" and "postgis" in actual_extensions:
            raise RestoreError("restored portable database contains PostGIS")
        actual_revision = self._alembic_revision()
        if actual_revision != expected.get("alembic_revision"):
            raise RestoreError(
                f"restored Alembic revision {actual_revision!r} does not match "
                f"manifest {expected.get('alembic_revision')!r}"
            )
        schema_hash = self._pg_dump_hash("schema-only")
        if schema_hash != expected.get("schema_sha256"):
            raise RestoreError("restored PostgreSQL schema hash does not match manifest")
        data_hash = self._pg_dump_hash("data-only")
        if data_hash != expected.get("data_sha256"):
            raise RestoreError("restored PostgreSQL data hash does not match manifest")
        row_counts = self._row_counts()
        expected_row_counts = expected.get("row_counts", {})
        if row_counts != expected_row_counts:
            missing = sorted(set(expected_row_counts) - set(row_counts))
            extra = sorted(set(row_counts) - set(expected_row_counts))
            changed = sorted(
                name
                for name in set(expected_row_counts) & set(row_counts)
                if expected_row_counts[name] != row_counts[name]
            )
            raise RestoreError(
                "restored PostgreSQL row counts do not match manifest "
                f"(missing={missing[:10]}, extra={extra[:10]}, changed={changed[:10]})"
            )
        topology = self._topology_summary()
        if topology != expected.get("topology"):
            raise RestoreError("restored topology summary does not match manifest")
        self.checks["postgres"] = {
            "alembic_revision": actual_revision,
            "data_sha256": data_hash,
            "extensions": actual_extensions,
            "profile": expected_profile,
            "row_counts": row_counts,
            "schema_sha256": schema_hash,
            "status": "PASS",
            "topology": topology,
        }

    def _restore_influx(self) -> None:
        if self.manifest is None:
            raise RestoreError("manifest has not been loaded")
        local_backup = self._component_path("influx")
        remote_path = f"/tmp/drainage-influx-restore-{self.manifest['backup_id']}"
        org = self.args.influx_org or self.manifest.get("influx", {}).get("org")
        host = self.args.influx_url or self.manifest.get("influx", {}).get("host")
        if not org or not host:
            raise RestoreError("InfluxDB restore requires organization and host")
        self._run(self._compose("cp", str(local_backup), f"{self.args.influx_service}:{remote_path}"))
        command = [
            "influx",
            "restore",
            remote_path,
            "--host",
            host,
            "--org",
            org,
        ]
        try:
            self._exec_authenticated(
                self.args.influx_service,
                command,
                secret=self.influx_token,
                variable="INFLUX_TOKEN",
            )
        finally:
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
        self.checks["influx"] = {"status": "PASS"}

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

    def _restore_redis(self) -> None:
        if self.manifest is None:
            raise RestoreError("manifest has not been loaded")
        local_dump = self._component_path("redis")
        appenddirname = _validate_container_relative(
            self.args.redis_appenddirname
            or self.manifest.get("redis", {}).get("appenddirname")
            or "appendonlydir",
            field="Redis appenddirname",
        )
        self._run(self._compose("stop", self.args.redis_service))
        started = False
        try:
            data_dir = shlex.quote(self.redis_data_path)
            script = (
                "set -eu; "
                f"mkdir -p {data_dir}; "
                f"find {data_dir} -mindepth 1 -maxdepth 1 -exec rm -rf -- '{{}}' +; "
                f"cat > {data_dir}/dump.rdb; "
                f"if getent passwd redis >/dev/null 2>&1; then chown -R redis:redis {data_dir}; fi"
            )
            with local_dump.open("rb") as payload:
                self._run(
                    self._compose(
                        "run",
                        "--rm",
                        "--no-deps",
                        "-T",
                        "--entrypoint",
                        "sh",
                        self.args.redis_service,
                        "-ec",
                        script,
                    ),
                    input_value=payload,
                    text=False,
                )
            aof_manifest = shlex.quote(f"{self.redis_data_path}/{appenddirname}/appendonly.aof.manifest")
            conversion_script = (
                "set -eu; "
                f"redis-server --port 0 --unixsocket /tmp/drainage-restore.sock "
                f"--unixsocketperm 700 --dir {data_dir} --dbfilename dump.rdb "
                f"--pidfile /tmp/drainage-restore.pid --save '' --appendonly no --daemonize yes; "
                "i=0; while [ \"$i\" -lt 30 ]; do "
                "redis-cli -s /tmp/drainage-restore.sock PING >/dev/null 2>&1 && break; "
                "i=$((i + 1)); sleep 1; done; "
                "redis-cli -s /tmp/drainage-restore.sock PING >/dev/null; "
                "redis-cli -s /tmp/drainage-restore.sock CONFIG SET appendonly yes >/dev/null; "
                "i=0; while [ \"$i\" -lt 30 ]; do "
                f"[ -s {aof_manifest} ] && break; "
                "i=$((i + 1)); sleep 1; done; "
                f"[ -s {aof_manifest} ]; "
                "redis-cli -s /tmp/drainage-restore.sock SHUTDOWN SAVE >/dev/null 2>&1 || true"
            )
            self._run(
                self._compose(
                    "run",
                    "--rm",
                    "--no-deps",
                    "-T",
                    "--entrypoint",
                    "sh",
                    self.args.redis_service,
                    "-ec",
                    conversion_script,
                )
            )
        finally:
            self._run(self._compose("start", self.args.redis_service), check=False)
            started = True
        if not started:
            raise RestoreError("Redis service was not restarted")
        deadline = time.monotonic() + self.args.redis_timeout_seconds
        last_error = "not ready"
        while time.monotonic() < deadline:
            try:
                if self._redis_query("PING") == "PONG":
                    break
            except RestoreError as exc:
                last_error = str(exc)
            time.sleep(1)
        else:
            raise RestoreError(f"Redis did not become ready: {last_error}")
        expected_size = int(self.manifest.get("redis", {}).get("db_size", -1))
        actual_size = int(self._redis_query("DBSIZE"))
        if actual_size != expected_size:
            raise RestoreError(
                f"restored Redis DBSIZE {actual_size} does not match manifest {expected_size}"
            )
        self.checks["redis"] = {"db_size": actual_size, "status": "PASS"}

    def _http_smoke(self, url: str, headers: dict[str, str], *, sse: bool) -> dict[str, Any]:
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.args.smoke_timeout_seconds) as response:
                content_type = response.headers.get("Content-Type", "")
                if sse:
                    response.read(256)
                status = int(response.status)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RestoreError(f"HTTP smoke failed for {url}: {exc}") from exc
        if status < 200 or status >= 300:
            raise RestoreError(f"HTTP smoke returned status {status} for {url}")
        if sse and "text/event-stream" not in content_type.lower():
            raise RestoreError(f"SSE smoke did not return text/event-stream for {url}")
        return {"content_type": content_type, "status": status, "url": url}

    def _smoke(self) -> dict[str, Any]:
        headers = _parse_header(self.args.smoke_header)
        for env_name in self.args.smoke_header_env:
            value = os.environ.get(env_name)
            if value is None:
                raise RestoreError(f"smoke header environment variable is not set: {env_name}")
            headers[env_name] = value
        api_urls = self.args.api_smoke_url or []
        sse_urls = self.args.sse_smoke_url or []
        if not api_urls and not sse_urls:
            self.warnings.append("API/SSE smoke was not requested")
            return {"status": "NOT_RUN"}
        results = {
            "api": [self._http_smoke(url, headers, sse=False) for url in api_urls],
            "sse": [self._http_smoke(url, headers, sse=True) for url in sse_urls],
        }
        if not results["api"] or not results["sse"]:
            self.warnings.append("API/SSE smoke is partial; provide both --api-smoke-url and --sse-smoke-url")
            results["status"] = "PARTIAL"
        else:
            results["status"] = "PASS"
        return results

    def run(self) -> dict[str, Any]:
        archive = Path(self.args.archive).expanduser().resolve(strict=True)
        archive_hash = _verify_sidecar(
            archive,
            allow_missing=self.args.allow_missing_sidecar,
        )
        temp_parent = Path(tempfile.mkdtemp(prefix="scn-restore-verify-"))
        try:
            manifest_root = _safe_extract(archive, temp_parent)
            self.manifest_root = manifest_root
            self.manifest = load_verified_manifest(manifest_root)
            if manifest_root.name != self.manifest.get("backup_id"):
                raise RestoreError("archive directory does not match manifest backup_id")
            components = _parse_components(self.args.components, self.manifest)
            self.checks["manifest"] = {
                "backup_id": self.manifest["backup_id"],
                "components": list(components),
                "metadata_complete": self.manifest.get("metadata_complete", False),
                "status": "PASS",
            }
            if not self.args.apply:
                self.checks["restore"] = {"status": "NOT_RUN"}
                return self._report(archive, archive_hash, components)
            if self.args.confirm != self.manifest["backup_id"]:
                raise RestoreError(
                    "--confirm must exactly match manifest backup_id to authorize a destructive restore"
                )
            for service in self.args.stop_service:
                self._run(self._compose("stop", service))
            if "postgres" in components:
                self._restore_postgres()
            if "influx" in components:
                self._restore_influx()
            if "redis" in components:
                self._restore_redis()
            for service in self.args.start_service:
                self._run(self._compose("start", service))
            smoke = self._smoke()
            self.checks["api_sse_smoke"] = smoke
            self.checks["restore"] = {"status": "PASS"}
            return self._report(archive, archive_hash, components)
        finally:
            shutil.rmtree(temp_parent, ignore_errors=True)

    def _report(
        self,
        archive: Path,
        archive_hash: str | None,
        components: Sequence[str],
    ) -> dict[str, Any]:
        if self.manifest is None:
            raise RestoreError("manifest has not been loaded")
        status = "VERIFIED" if not self.args.apply else "RESTORED"
        report = {
            "archive": str(archive),
            "archive_sha256": archive_hash,
            "backup_id": self.manifest["backup_id"],
            "checked_at": utc_now(),
            "checks": self.checks,
            "components": list(components),
            "status": status,
            "warnings": sorted(set(self.warnings)),
        }
        report_path = Path(self.args.report).expanduser().resolve() if self.args.report else archive.with_suffix(
            archive.suffix + ".restore-report.json"
        )
        write_json(report_path, report)
        print(f"RESTORE_STATUS={status}")
        print(f"RESTORE_ARCHIVE={archive}")
        print(f"RESTORE_REPORT={report_path}")
        print(f"MANIFEST_BACKUP_ID={self.manifest['backup_id']}")
        print(f"RESTORE_COMPONENTS={','.join(components)}")
        smoke = self.checks.get("api_sse_smoke")
        if smoke:
            print(f"API_SSE_SMOKE={smoke['status']}")
        for warning in report["warnings"]:
            print(f"RESTORE_WARNING={warning}")
        return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify or restore a smart-city-drainage backup archive.",
    )
    parser.add_argument("archive")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--verify-only", action="store_true", help="Verify hashes only (default)")
    mode.add_argument("--apply", action="store_true", help="Perform destructive component restore")
    parser.add_argument("--confirm", help="Must exactly equal manifest backup_id when --apply is used")
    parser.add_argument("--components", help="Comma-separated subset of manifest components")
    parser.add_argument("--allow-missing-sidecar", action="store_true")
    parser.add_argument("--compose-file", default=str(DEFAULT_COMPOSE_FILE))
    parser.add_argument("--project-name")
    parser.add_argument("--docker-bin", default="docker")
    parser.add_argument("--postgres-service", default="postgres")
    parser.add_argument("--postgres-user", default=os.environ.get("POSTGRES_USER"))
    parser.add_argument("--postgres-db", default=os.environ.get("POSTGRES_DB"))
    parser.add_argument("--pg-password-file")
    parser.add_argument("--redis-service", default="redis")
    parser.add_argument("--redis-username", default=os.environ.get("REDIS_USERNAME"))
    parser.add_argument("--redis-password-file")
    parser.add_argument("--redis-container-data-path", default="/data")
    parser.add_argument("--redis-appenddirname")
    parser.add_argument("--redis-timeout-seconds", type=int, default=60)
    parser.add_argument("--influx-service", default="influxdb")
    parser.add_argument("--influx-url")
    parser.add_argument("--influx-org", default=os.environ.get("INFLUX_ORG"))
    parser.add_argument("--influx-token-file")
    parser.add_argument("--stop-service", action="append", default=[])
    parser.add_argument("--start-service", action="append", default=[])
    parser.add_argument("--api-smoke-url", action="append", default=[])
    parser.add_argument("--sse-smoke-url", action="append", default=[])
    parser.add_argument("--smoke-header", action="append", default=[])
    parser.add_argument(
        "--smoke-header-env",
        action="append",
        default=[],
        help="Read a smoke header value from this environment variable",
    )
    parser.add_argument("--smoke-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.redis_timeout_seconds <= 0 or args.smoke_timeout_seconds <= 0:
            raise RestoreError("timeout values must be positive")
        report = OperationsRestore(args).run()
    except (RestoreError, OSError, ValueError, KeyError) as exc:
        print(f"RESTORE_STATUS=FAILED: {exc}", file=sys.stderr)
        return 1
    return 0 if report["status"] in {"VERIFIED", "RESTORED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
