#!/usr/bin/env python3
"""Offline tests for parameterized backup/restore operations."""

from __future__ import annotations

import io
import os
import shlex
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import Any, Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import backup  # noqa: E402
import backup_manifest  # noqa: E402
import restore  # noqa: E402


class FakeRunner:
    """Deterministic Docker/Git process double used by end-to-end CLI tests."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.postgres_service = "database-a"
        self.redis_service = "cache-a"
        self.influx_service = "metrics-a"
        self.profile = "postgis"
        self.redis_db_size = 3
        self.restored = False

    @staticmethod
    def _completed(command: Sequence[str], stdout: Any = "", returncode: int = 0) -> subprocess.CompletedProcess[Any]:
        return subprocess.CompletedProcess(list(command), returncode, stdout, "")

    def __call__(
        self,
        command: Sequence[str],
        *,
        input_value: str | bytes | None = None,
        text: bool = True,
        check: bool = True,
    ) -> subprocess.CompletedProcess[Any]:
        command = [str(item) for item in command]
        self.calls.append(
            {
                "check": check,
                "command": command,
                "input": input_value,
                "text": text,
            }
        )
        if command[0] == "git":
            if "rev-parse" in command:
                return self._completed(command, "a" * 40)
            if "status" in command:
                return self._completed(command, "")
        if "compose" not in command:
            return self._completed(command, b"" if not text else "")
        if "images" in command:
            payload = (
                '[{"Service":"database-a","Name":"postgres-test",'
                '"ID":"sha256:db","Digest":"sha256:db-digest"}]'
            )
            return self._completed(command, payload)
        if "cp" in command:
            source, destination = command[-2], command[-1]
            if source.startswith(f"{self.redis_service}:") and source.endswith(".rdb"):
                target = Path(destination)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"REDIS-RDB")
            if source.startswith(f"{self.influx_service}:"):
                target = Path(destination)
                target.mkdir(parents=True, exist_ok=True)
                (target / "manifest.json").write_text('{"fixture":true}\n', encoding="utf-8")
            return self._completed(command, b"" if not text else "")
        if "exec" in command:
            service = command[command.index("exec") + 2]
            if service == self.postgres_service:
                return self._postgres_exec(command, text)
            if service == self.redis_service:
                return self._redis_exec(command, text)
            if service == self.influx_service:
                return self._completed(command, b"" if not text else "")
            return self._completed(command, b"" if not text else "")
        if "run" in command:
            self.restored = True
            return self._completed(command, b"" if not text else "")
        if "stop" in command or "start" in command:
            return self._completed(command, b"" if not text else "")
        return self._completed(command, b"" if not text else "")

    def _postgres_exec(self, command: Sequence[str], text: bool) -> subprocess.CompletedProcess[Any]:
        rendered = shlex.join(command)
        if "pg_dump" in rendered:
            if "--format=custom" in rendered:
                return self._completed(command, b"PGDMP\x01fake-custom-dump")
            if "--schema-only" in rendered:
                return self._completed(command, "schema-v1" if text else b"schema-v1")
            if "--data-only" in rendered:
                return self._completed(command, "data-v1" if text else b"data-v1")
        if "psql" in rendered:
            sql = shlex.split(command[-1], posix=True)[-1]
            if sql == "SELECT 1;":
                value: Any = "1"
            elif "FROM pg_extension" in sql:
                rows = ["timescaledb|2.17.2", "uuid-ossp|1.1"]
                if self.profile == "postgis":
                    rows.insert(1, "postgis|3.4.2")
                value = "\n".join(rows)
            elif "to_regclass('public.alembic_version')" in sql:
                value = "alembic_version"
            elif "version_num" in sql:
                value = "0002_hydraulic_schema"
            elif "FROM pg_temp.hydraulic_backup_row_counts()" in sql:
                value = "\n".join(
                    [
                        "hydraulic.catchments|4",
                        "hydraulic.network_nodes|10",
                        "hydraulic.networks|1",
                        "hydraulic.pipe_segments|12",
                    ]
                )
            elif "to_regclass('hydraulic." in sql:
                value = "t"
            elif sql.startswith("SELECT count(*)"):
                relation = sql.split("FROM ", 1)[1].split(";", 1)[0]
                value = {"hydraulic.catchments": "4", "hydraulic.network_nodes": "10", "hydraulic.networks": "1", "hydraulic.pipe_segments": "12"}[relation]
            else:
                value = ""
            return self._completed(command, value if text else value.encode())
        return self._completed(command, b"" if not text else "")

    def _redis_exec(self, command: Sequence[str], text: bool) -> subprocess.CompletedProcess[Any]:
        rendered = shlex.join(command)
        if "--rdb" in rendered:
            return self._completed(command, b"" if not text else "")
        command_name = shlex.split(command[-1], posix=True)[-1]
        if command_name == "DBSIZE":
            return self._completed(command, str(self.redis_db_size))
        if command_name == "PING":
            return self._completed(command, "PONG")
        return self._completed(command, b"" if not text else "")


class OperationsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.compose_file = self.root / "compose.yml"
        self.compose_file.write_text("services: {}\n", encoding="utf-8")
        self.password_file = self.root / "redis.password"
        self.password_file.write_text("redis-test-secret\n", encoding="utf-8")
        self.pg_password_file = self.root / "postgres.password"
        self.pg_password_file.write_text("postgres-test-secret\n", encoding="utf-8")
        self.influx_token_file = self.root / "influx.token"
        self.influx_token_file.write_text("influx-test-secret\n", encoding="utf-8")
        self.runner = FakeRunner()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def backup_args(self, output_dir: Path):
        args = backup.build_parser().parse_args(
            [
                "--backup-id",
                "fixture-001",
                "--components",
                "postgres,redis",
                "--compose-file",
                str(self.compose_file),
                "--output-dir",
                str(output_dir),
                "--postgres-service",
                self.runner.postgres_service,
                "--postgres-user",
                "drainage",
                "--postgres-db",
                "smart_drainage",
                "--pg-password-file",
                str(self.pg_password_file),
                "--schema-profile",
                self.runner.profile,
                "--redis-service",
                self.runner.redis_service,
                "--redis-password-file",
                str(self.password_file),
                "--solver-sha256",
                "b" * 64,
                "--writers-paused",
            ]
        )
        backup._validate_args(args)
        return args

    def influx_backup_args(self, output_dir: Path):
        args = backup.build_parser().parse_args(
            [
                "--backup-id",
                "influx-001",
                "--components",
                "influx",
                "--compose-file",
                str(self.compose_file),
                "--output-dir",
                str(output_dir),
                "--influx-service",
                self.runner.influx_service,
                "--influx-org",
                "fixture-org",
                "--influx-token-file",
                str(self.influx_token_file),
                "--solver-sha256",
                "c" * 64,
                "--writers-paused",
            ]
        )
        backup._validate_args(args)
        return args

    def restore_args(
        self,
        archive: Path,
        component: str,
        *,
        confirm: str | None = "fixture-001",
        backup_id: str = "fixture-001",
    ):
        argv = [
            str(archive),
            "--apply",
            "--components",
            component,
            "--compose-file",
            str(self.compose_file),
            "--postgres-service",
            self.runner.postgres_service,
            "--postgres-user",
            "drainage",
            "--postgres-db",
            "smart_drainage",
            "--pg-password-file",
            str(self.pg_password_file),
            "--redis-service",
            self.runner.redis_service,
            "--redis-password-file",
            str(self.password_file),
            "--influx-service",
            self.runner.influx_service,
            "--influx-org",
            "fixture-org",
            "--influx-token-file",
            str(self.influx_token_file),
            "--report",
            str(self.root / f"{component}-restore-report.json"),
        ]
        if confirm is not None:
            argv.extend(["--confirm", confirm])
        return restore.build_parser().parse_args(argv)

    def create_backup(self) -> Path:
        output_dir = self.root / "backups"
        return backup.OperationsBackup(self.backup_args(output_dir), runner=self.runner).run()

    def test_backup_manifest_and_default_restore_verification(self) -> None:
        archive = self.create_backup()
        self.assertTrue(archive.is_file())
        self.assertTrue(archive.with_suffix(archive.suffix + ".sha256").is_file())

        args = restore.build_parser().parse_args(
            [
                str(archive),
                "--compose-file",
                str(self.compose_file),
                "--report",
                str(self.root / "verify-report.json"),
            ]
        )
        report = restore.OperationsRestore(args).run()
        self.assertEqual(report["status"], "VERIFIED")
        self.assertEqual(report["checks"]["manifest"]["status"], "PASS")

    def test_auth_is_stdin_only_and_services_are_parameterized(self) -> None:
        self.create_backup()
        redis_calls = [call for call in self.runner.calls if "redis-cli" in shlex.join(call["command"])]
        self.assertTrue(redis_calls)
        for call in redis_calls:
            rendered = shlex.join(call["command"])
            self.assertNotIn("redis-test-secret", rendered)
            if call["input"] is not None:
                self.assertIn("redis-test-secret", str(call["input"]))
        postgres_calls = [call for call in self.runner.calls if "pg_dump" in shlex.join(call["command"])]
        self.assertTrue(postgres_calls)
        for call in postgres_calls:
            self.assertNotIn("postgres-test-secret", shlex.join(call["command"]))
            self.assertIn("postgres-test-secret", str(call["input"]))
        rendered_calls = "\n".join(shlex.join(call["command"]) for call in self.runner.calls)
        self.assertIn("database-a", rendered_calls)
        self.assertIn("cache-a", rendered_calls)
        self.assertNotIn("scn-postgres", rendered_calls)
        self.assertNotIn("scn-redis", rendered_calls)

    def test_manifest_detects_tampered_payload(self) -> None:
        archive = self.create_backup()
        extracted = self.root / "extracted"
        extracted.mkdir()
        root = restore._safe_extract(archive, extracted)
        payload = root / "redis" / "dump.rdb"
        payload.write_bytes(b"TAMPERED")
        with self.assertRaises(backup_manifest.ManifestError):
            backup_manifest.load_verified_manifest(root)

    def test_archive_sidecar_detects_tampering(self) -> None:
        archive = self.create_backup()
        with archive.open("ab") as handle:
            handle.write(b"tamper")
        with self.assertRaisesRegex(restore.RestoreError, "sidecar does not match"):
            restore._verify_sidecar(archive, allow_missing=False)

    def test_postgres_restore_rechecks_profile_schema_data_rows_and_topology(self) -> None:
        archive = self.create_backup()
        report = restore.OperationsRestore(
            self.restore_args(archive, "postgres"),
            runner=self.runner,
        ).run()
        self.assertEqual(report["status"], "RESTORED")
        self.assertEqual(report["checks"]["postgres"]["status"], "PASS")
        self.assertEqual(report["checks"]["postgres"]["profile"], "postgis")
        rendered = "\n".join(shlex.join(call["command"]) for call in self.runner.calls)
        self.assertIn("pg_restore", rendered)
        self.assertIn("--single-transaction", rendered)
        self.assertIn("database-a", rendered)

    def test_redis_restore_uses_compose_service_and_compares_dbsize(self) -> None:
        archive = self.create_backup()
        report = restore.OperationsRestore(
            self.restore_args(archive, "redis"),
            runner=self.runner,
        ).run()
        self.assertEqual(report["status"], "RESTORED")
        self.assertEqual(report["checks"]["redis"]["db_size"], 3)
        self.assertTrue(self.runner.restored)
        rendered = "\n".join(shlex.join(call["command"]) for call in self.runner.calls)
        self.assertIn(f"stop {self.runner.redis_service}", rendered)
        self.assertIn(f"start {self.runner.redis_service}", rendered)
        self.assertNotIn("scn-redis", rendered)

    def test_apply_requires_exact_backup_id_confirmation(self) -> None:
        archive = self.create_backup()
        with self.assertRaisesRegex(restore.RestoreError, "must exactly match"):
            restore.OperationsRestore(
                self.restore_args(archive, "redis", confirm="wrong-id"),
                runner=self.runner,
            ).run()

    def test_postgis_target_cannot_restore_portable_backup(self) -> None:
        archive = self.create_backup()
        self.runner.profile = "portable"
        with self.assertRaisesRegex(restore.RestoreError, "does not match backup"):
            restore.OperationsRestore(
                self.restore_args(archive, "postgres"),
                runner=self.runner,
            ).run()

    def test_safe_extract_rejects_path_traversal(self) -> None:
        malicious = self.root / "malicious.tar.gz"
        payload = b"bad"
        info = tarfile.TarInfo("../escape")
        info.size = len(payload)
        with tarfile.open(malicious, "w:gz") as archive:
            archive.addfile(info, io.BytesIO(payload))
        with self.assertRaisesRegex(restore.RestoreError, "unsafe archive member"):
            restore._safe_extract(malicious, self.root / "extract")

    def test_influx_tree_manifest_and_authenticated_restore(self) -> None:
        output_dir = self.root / "influx-backups"
        archive = backup.OperationsBackup(
            self.influx_backup_args(output_dir),
            runner=self.runner,
        ).run()
        args = restore.build_parser().parse_args(
            [
                str(archive),
                "--compose-file",
                str(self.compose_file),
                "--report",
                str(self.root / "influx-verify.json"),
            ]
        )
        verified = restore.OperationsRestore(args).run()
        self.assertEqual(verified["status"], "VERIFIED")

        report = restore.OperationsRestore(
            self.restore_args(
                archive,
                "influx",
                confirm="influx-001",
                backup_id="influx-001",
            ),
            runner=self.runner,
        ).run()
        self.assertEqual(report["checks"]["influx"]["status"], "PASS")
        influx_calls = [
            call
            for call in self.runner.calls
            if "influx backup" in shlex.join(call["command"])
            or "influx restore" in shlex.join(call["command"])
        ]
        self.assertTrue(influx_calls)
        for call in influx_calls:
            self.assertNotIn("influx-test-secret", shlex.join(call["command"]))
            self.assertIn("influx-test-secret", str(call["input"]))

    def test_production_entrypoints_do_not_embed_legacy_container_names_or_secrets(self) -> None:
        paths = [
            SCRIPT_DIR / "backup.py",
            SCRIPT_DIR / "restore.py",
            SCRIPT_DIR / "backup_manifest.py",
            SCRIPT_DIR / "backup.sh",
            SCRIPT_DIR / "restore.sh",
            SCRIPT_DIR / "backup.ps1",
            SCRIPT_DIR / "restore.ps1",
        ]
        forbidden = (
            "scn-postgres",
            "scn-redis",
            "scn-influxdb",
            "scn-influx-token-2024-secure",
            "Grafana@2024",
        )
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for marker in forbidden:
                self.assertNotIn(marker, text, f"{path.name} contains hardcoded {marker}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
