"""Targeted tests for the read-only R0 and dependency handoff checkers."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from app.core.config import HYDRAULIC_SCHEMA_HEAD as HEAD_REVISION

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str) -> ModuleType:
    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"hydraulic_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration_gate = _load_script("check_migration_gate")
dependency_gate = _load_script("check_dependency_handoff")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _run_checker(name: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-B",
            str(REPO_ROOT / "scripts" / f"{name}.py"),
            *args,
        ],
        cwd=REPO_ROOT.parent,
        check=False,
        capture_output=True,
        text=True,
    )


def _review_fixture(tmp_path: Path) -> tuple[Path, str, Path]:
    repo = tmp_path / "repo"
    for relative in migration_gate.DOCUMENT_PATHS:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"content:{relative}\n", encoding="utf-8")
    versions = repo / "backend/alembic/versions"
    versions.mkdir(parents=True, exist_ok=True)
    (versions / "0001_initial.py").write_text(
        "revision = '0001_initial'\n",
        encoding="utf-8",
    )

    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.email", "gate-tests@example.invalid")
    _git(repo, "config", "user.name", "Gate Tests")
    _git(repo, "add", ".")
    _git(repo, "commit", "--quiet", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")

    documents = [
        {
            "path": relative,
            "sha256": hashlib.sha256((repo / relative).read_bytes()).hexdigest(),
        }
        for relative in migration_gate.DOCUMENT_PATHS
    ]
    bundle = {
        "base_commit": base,
        "documents": documents,
        "schema": "v3-review-bundle-v1",
    }
    bundle_sha256 = hashlib.sha256(
        migration_gate.canonical_json_bytes(bundle)
    ).hexdigest()
    report_relative = "docs/hydraulic-twin/reviews/final.md"
    report = repo / report_relative
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        f"reviewer=test-reviewer\n{base}\nR0_DESIGN_PASS\nPASS\n",
        encoding="utf-8",
    )
    artifact = {
        "base_commit": base,
        "checks": [
            {"evidence": f"test:{name}", "name": name, "status": "PASS"}
            for name in migration_gate.REVIEW_CHECKS
        ],
        "document_bundle_sha256": bundle_sha256,
        "documents": documents,
        "findings": [],
        "review_report": report_relative,
        "review_report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
        "reviewer": "test-reviewer",
        "reviewed_at": "2026-09-24",
        "schema": migration_gate.REVIEW_SCHEMA,
        "verdict": migration_gate.VERDICT,
    }
    artifact_path = repo / "artifacts/contracts/v3-independent-review.json"
    _write_json(artifact_path, artifact)
    return repo, base, artifact_path


def _git_blob_sha(repo: Path, commit: str, relative: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{relative}"],
        check=True,
        capture_output=True,
    )
    return hashlib.sha256(completed.stdout).hexdigest()


def _approved_migration_fixture(
    repo: Path,
    base: str,
    review_artifact: Path,
) -> Path:
    versions = repo / "backend/alembic/versions"
    migration = versions / "0002_hydraulic_schema.py"
    migration.write_text(
        "revision: str = '0002_hydraulic_schema'\n"
        "down_revision: str | None = '0001_initial'\n",
        encoding="utf-8",
    )
    review = json.loads(review_artifact.read_text(encoding="utf-8"))
    approved = {
        "approval": {
            "approved_at": "2026-09-25",
            "basis": "test-approved post-R0 landing",
            "owner": migration_gate.APPROVED_MIGRATION_OWNER,
            "state": migration_gate.APPROVED_MIGRATION_STATUS,
        },
        "head_revision": "0002_hydraulic_schema",
        "migrations": [
            {
                "base_sha256": _git_blob_sha(
                    repo,
                    base,
                    "backend/alembic/versions/0001_initial.py",
                ),
                "base_state": "PRESENT",
                "change": "PRESERVED_BASE",
                "down_revision": None,
                "live_sha256": hashlib.sha256(
                    (versions / "0001_initial.py").read_bytes()
                ).hexdigest(),
                "path": "backend/alembic/versions/0001_initial.py",
                "revision": "0001_initial",
            },
            {
                "base_sha256": None,
                "base_state": "ABSENT",
                "change": "ADDED_AFTER_R0",
                "down_revision": "0001_initial",
                "live_sha256": hashlib.sha256(migration.read_bytes()).hexdigest(),
                "path": "backend/alembic/versions/0002_hydraulic_schema.py",
                "revision": "0002_hydraulic_schema",
            },
        ],
        "owner": migration_gate.APPROVED_MIGRATION_OWNER,
        "phase": "post-migration",
        "r0_review": {
            "artifact": review_artifact.relative_to(repo).as_posix(),
            "artifact_sha256": hashlib.sha256(review_artifact.read_bytes()).hexdigest(),
            "base_commit": base,
            "document_bundle_sha256": review["document_bundle_sha256"],
            "verdict": review["verdict"],
        },
        "schema": migration_gate.APPROVED_MIGRATION_SCHEMA,
        "status": migration_gate.APPROVED_MIGRATION_STATUS,
    }
    path = repo / migration_gate.DEFAULT_APPROVED_MIGRATION_ARTIFACT
    _write_json(path, approved)
    return path


def test_migration_gate_accepts_unchanged_versions(tmp_path: Path) -> None:
    repo, base, artifact = _review_fixture(tmp_path)

    result = migration_gate.validate_migration_gate(
        repo,
        artifact,
        base,
        repo / "backend/alembic/versions",
    )

    assert result["status"] == "PASS"
    assert result["verdict"] == "R0_DESIGN_PASS"
    assert result["versions_changed"] == []


def test_migration_gate_rejects_live_bundle_drift(tmp_path: Path) -> None:
    repo, base, artifact = _review_fixture(tmp_path)
    document = repo / migration_gate.DOCUMENT_PATHS[0]
    document.write_text("changed after review\n", encoding="utf-8")

    with pytest.raises(migration_gate.GateError, match="document_bundle_sha256"):
        migration_gate.validate_migration_gate(
            repo,
            artifact,
            base,
            repo / "backend/alembic/versions",
        )


def test_migration_gate_rejects_versions_change(tmp_path: Path) -> None:
    repo, base, artifact = _review_fixture(tmp_path)
    (repo / "backend/alembic/versions/0001_initial.py").write_text(
        "revision = '0002'\n",
        encoding="utf-8",
    )

    with pytest.raises(migration_gate.GateError, match="versions content changed"):
        migration_gate.validate_migration_gate(
            repo,
            artifact,
            base,
            repo / "backend/alembic/versions",
        )


def test_migration_gate_accepts_explicit_approved_post_migration(
    tmp_path: Path,
) -> None:
    repo, base, artifact = _review_fixture(tmp_path)
    approved = _approved_migration_fixture(repo, base, artifact)

    result = migration_gate.validate_migration_gate(
        repo,
        artifact,
        base,
        repo / "backend/alembic/versions",
        approved,
    )

    assert result["migration_phase"] == migration_gate.APPROVED_MIGRATION_PHASE
    assert result["versions_changed"] == [
        "backend/alembic/versions/0002_hydraulic_schema.py"
    ]
    assert result["approved_migration"]["head_revision"] == "0002_hydraulic_schema"


def test_migration_gate_rejects_approved_migration_content_drift(
    tmp_path: Path,
) -> None:
    repo, base, artifact = _review_fixture(tmp_path)
    approved = _approved_migration_fixture(repo, base, artifact)
    migration = repo / "backend/alembic/versions/0002_hydraulic_schema.py"
    migration.write_text(migration.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(migration_gate.GateError, match="live SHA-256 mismatch"):
        migration_gate.validate_migration_gate(
            repo,
            artifact,
            base,
            repo / "backend/alembic/versions",
            approved,
        )


def test_migration_gate_rejects_unapproved_extra_migration(
    tmp_path: Path,
) -> None:
    repo, base, artifact = _review_fixture(tmp_path)
    approved = _approved_migration_fixture(repo, base, artifact)
    (repo / "backend/alembic/versions/0003_unapproved.py").write_text(
        "revision: str = '0003_unapproved'\n"
        "down_revision: str | None = '0002_hydraulic_schema'\n",
        encoding="utf-8",
    )

    with pytest.raises(migration_gate.GateError, match="live migration set differs"):
        migration_gate.validate_migration_gate(
            repo,
            artifact,
            base,
            repo / "backend/alembic/versions",
            approved,
        )


def test_live_migration_gate_passes_without_running_alembic() -> None:
    result = migration_gate.validate_migration_gate(
        REPO_ROOT,
        REPO_ROOT / "artifacts/contracts/v3-independent-review.json",
        "ca99e0485fb12fbecbd565e79d81f745f8d4a748",
        REPO_ROOT / "backend/alembic/versions",
        REPO_ROOT / migration_gate.DEFAULT_APPROVED_MIGRATION_ARTIFACT,
    )

    assert result["status"] == "PASS"
    assert result["migration_phase"] == migration_gate.APPROVED_MIGRATION_PHASE
    assert result["approved_migration"]["status"] == (
        migration_gate.APPROVED_MIGRATION_STATUS
    )
    assert result["documents"] == [
        {
            "path": "docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md",
            "sha256": result["documents"][0]["sha256"],
        },
        {
            "path": "docs/hydraulic-twin/PRD_SUPPLEMENT.md",
            "sha256": result["documents"][1]["sha256"],
        },
        {
            "path": "docs/hydraulic-twin/SOURCE_PRD.md",
            "sha256": result["documents"][2]["sha256"],
        },
    ]


def test_migration_gate_cli_accepts_workspace_prefixed_paths() -> None:
    workspace_prefix = REPO_ROOT.name
    completed = _run_checker(
        "check_migration_gate",
        "--review-artifact",
        f"{workspace_prefix}/artifacts/contracts/v3-independent-review.json",
        "--base-ref",
        dependency_gate.BASE_COMMIT,
        "--versions-dir",
        f"{workspace_prefix}/backend/alembic/versions",
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "MIGRATION_GATE=PASS" in completed.stdout
    assert (
        f"MIGRATION_PHASE={migration_gate.APPROVED_MIGRATION_PHASE}" in completed.stdout
    )


def test_live_dependency_handoff_matches_fixture_and_settings() -> None:
    result = dependency_gate.validate_dependency_handoff(
        REPO_ROOT,
        REPO_ROOT / "artifacts/contracts/dependency-config-handoff.json",
        REPO_ROOT
        / "backend/tests/fixtures/hydraulic/contracts/dependency-handoff-v1.json",
        REPO_ROOT / ".python-version",
        REPO_ROOT / "frontend/.node-version",
        REPO_ROOT / "frontend/.npmrc",
        REPO_ROOT / "frontend/package.json",
        REPO_ROOT / "frontend/package-lock.json",
        REPO_ROOT / ".env.example",
    )

    assert result["status"] == dependency_gate.HANDOFF_STATUS
    assert result["settings_counts"] == {"authorized": 54, "preserve_only": 51}
    assert result["approved_migration"]["head_revision"] == HEAD_REVISION
    assert result["frontend_performance"]["state"] == "APPROVED_CONTRACT"
    assert result["frontend_performance"]["acceptance_scope"] == "PRD_G5"
    assert result["versions"]["node"] == "18.20.8"
    assert result["versions"]["npm"] == "10.8.2"
    assert result["versions"]["lockfileVersion"] == 3
    assert result["deferred"] == ["frontend-target-runtime-evidence"]
    assert result["g0_g5_blocked_by_deferred"] is True


def test_dependency_handoff_cli_accepts_workspace_prefixed_paths() -> None:
    workspace_prefix = REPO_ROOT.name
    completed = _run_checker(
        "check_dependency_handoff",
        "--handoff",
        f"{workspace_prefix}/artifacts/contracts/dependency-config-handoff.json",
        "--expected",
        (
            f"{workspace_prefix}/backend/tests/fixtures/hydraulic/contracts/"
            "dependency-handoff-v1.json"
        ),
        "--python-version-file",
        f"{workspace_prefix}/.python-version",
        "--node-version-file",
        f"{workspace_prefix}/frontend/.node-version",
        "--npmrc",
        f"{workspace_prefix}/frontend/.npmrc",
        "--package",
        f"{workspace_prefix}/frontend/package.json",
        "--lock",
        f"{workspace_prefix}/frontend/package-lock.json",
        "--env-example",
        f"{workspace_prefix}/.env.example",
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "DEPENDENCY_HANDOFF=PASS_WITH_DEFERRED" in completed.stdout
    assert "G0_G5_BLOCKED_BY_DEFERRED=true" in completed.stdout


@pytest.mark.parametrize("gate", [migration_gate, dependency_gate])
def test_workspace_prefix_does_not_relax_repository_boundary(
    gate: ModuleType,
) -> None:
    with pytest.raises(gate.GateError, match="path escapes repository root"):
        gate._repo_path(REPO_ROOT, f"{REPO_ROOT.name}/../../outside")


def test_dependency_handoff_rejects_authorization_drift() -> None:
    expected = json.loads(
        (
            REPO_ROOT
            / "backend/tests/fixtures/hydraulic/contracts/dependency-handoff-v1.json"
        ).read_text(encoding="utf-8")
    )
    handoff = json.loads(
        (REPO_ROOT / "artifacts/contracts/dependency-config-handoff.json").read_text(
            encoding="utf-8"
        )
    )
    drifted = copy.deepcopy(handoff)
    drifted["auth_ids"] = drifted["auth_ids"][:-1]

    with pytest.raises(dependency_gate.GateError, match="expected fixture"):
        dependency_gate._verify_expected_and_handoff(expected, drifted)


def test_dependency_handoff_rejects_node_version_drift(tmp_path: Path) -> None:
    package = json.loads(
        (REPO_ROOT / "frontend/package.json").read_text(encoding="utf-8")
    )
    package["engines"]["node"] = "18.20.7"
    package_path = tmp_path / "package.json"
    _write_json(package_path, package)
    handoff = json.loads(
        (REPO_ROOT / "artifacts/contracts/dependency-config-handoff.json").read_text(
            encoding="utf-8"
        )
    )

    with pytest.raises(dependency_gate.GateError, match="engines drifted"):
        dependency_gate._verify_versions(
            REPO_ROOT,
            handoff,
            REPO_ROOT / ".python-version",
            REPO_ROOT / "frontend/.node-version",
            REPO_ROOT / "frontend/.npmrc",
            package_path,
            REPO_ROOT / "frontend/package-lock.json",
        )


def test_frontend_performance_contract_is_approved_and_entrypoint_is_active() -> None:
    package = json.loads(
        (REPO_ROOT / "frontend/package.json").read_text(encoding="utf-8")
    )
    handoff = json.loads(
        (REPO_ROOT / "artifacts/contracts/dependency-config-handoff.json").read_text(
            encoding="utf-8"
        )
    )

    assert package["scripts"]["test:perf:twin"] == ("node performance/run-twin-map.mjs")
    assert package["scripts"]["test:perf"] == "node performance/run-twin-map.mjs"
    assert (REPO_ROOT / "frontend/performance/run-twin-map.mjs").is_file()
    fixture = REPO_ROOT / "frontend/performance/fixtures/twin-map-5k-10k-v1.json"
    assert fixture.is_file()
    fixture_value = json.loads(fixture.read_text(encoding="utf-8"))
    assert fixture_value == dependency_gate.FRONTEND_PERFORMANCE_FIXTURE_CONTRACT
    assert fixture_value["acceptance"] == {
        "browser": "production-bundled-headless-chromium",
        "note": (
            "The production browser bundle imports ViewportIndex, "
            "SimulationMapController/Canvas, encodeFrame and FrameCache. API "
            "latency is measured against the frozen local fixture API."
        ),
        "scope": "PRD_G5",
        "target_environment": {
            "gpu": "disabled_software_rasterization",
            "node_version": "18.20.8",
            "physical_cores": 4,
            "ram_bytes": 8589934592,
        },
    }
    verified = dependency_gate._verify_frontend_performance_contract(
        REPO_ROOT,
        handoff["frontend_performance"],
    )
    assert (
        verified["fixture"]["sha256"]
        == hashlib.sha256(fixture.read_bytes()).hexdigest()
    )


def test_frontend_performance_contract_rejects_fixture_drift(tmp_path: Path) -> None:
    source_root = REPO_ROOT / "frontend/performance"
    target_root = tmp_path / "frontend/performance"
    shutil.copytree(source_root, target_root)
    fixture_path = target_root / "fixtures/twin-map-5k-10k-v1.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    fixture["thresholds"]["max_p95_update_ms"] = 51
    _write_json(fixture_path, fixture)
    handoff = json.loads(
        (REPO_ROOT / "artifacts/contracts/dependency-config-handoff.json").read_text(
            encoding="utf-8"
        )
    )

    with pytest.raises(dependency_gate.GateError, match="contract drifted"):
        dependency_gate._verify_frontend_performance_contract(
            tmp_path,
            handoff["frontend_performance"],
        )
