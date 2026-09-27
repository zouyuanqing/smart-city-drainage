#!/usr/bin/env python3
"""Fail-closed, read-only R0 design gate for Alembic migration work.

The checker never imports the application, starts a schema, or runs Alembic. It
binds the immutable final review artifact to the live canonical document bundle
and requested base commit. Before migration it requires the versions directory
to match that base; after migration it validates an independent approval
artifact, the exact linear revision chain, base/live hashes, and the complete
Git-visible versions path set.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

REVIEW_SCHEMA = "v3-independent-review-v1"
VERDICT = "R0_DESIGN_PASS"
APPROVED_MIGRATION_SCHEMA = "approved-migration-v1"
APPROVED_MIGRATION_STATUS = "APPROVED_POST_MIGRATION"
APPROVED_MIGRATION_PHASE = "POST_MIGRATION_APPROVED"
PRE_MIGRATION_PHASE = "PRE_MIGRATION_R0"
APPROVED_MIGRATION_OWNER = "hydraulic-schema"
DEFAULT_APPROVED_MIGRATION_ARTIFACT = "artifacts/contracts/approved-migration-0008.json"
DOCUMENT_PATHS = (
    "docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md",
    "docs/hydraulic-twin/PRD_SUPPLEMENT.md",
    "docs/hydraulic-twin/SOURCE_PRD.md",
)
REVIEW_CHECKS = (
    "schema_semantics",
    "path_ownership",
    "blocked_by",
    "dependency_handoff_predicates",
    "gate_definitions",
)
SHA256_RE = frozenset("0123456789abcdef")


class GateError(RuntimeError):
    """A fail-closed migration-gate violation."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateError(message)


def _git(
    repo_root: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise GateError(f"git {' '.join(args)} failed: {detail}")
    return completed


def _git_text(repo_root: Path, *args: str, check: bool = True) -> str:
    return _git(repo_root, *args, check=check).stdout.decode("utf-8").strip()


def _cli_path(repo_root: Path, value: str) -> Path:
    """Accept both repository-relative and workspace-relative CLI paths."""

    root = repo_root.resolve()
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    workspace_candidate = (root.parent / candidate).resolve()
    try:
        return workspace_candidate.relative_to(root)
    except ValueError:
        return candidate


def _repo_path(repo_root: Path, value: str | Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise GateError(f"path escapes repository root: {value}") from exc
    return resolved


def _is_commit(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in SHA256_RE for character in value)
    )


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in SHA256_RE for character in value)
    )


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GateError(f"{label} is not readable JSON: {path}: {exc}") from exc
    require(isinstance(value, dict), f"{label} must contain a JSON object: {path}")
    return value


def _review_checks(artifact: dict[str, Any]) -> None:
    raw = artifact.get("checks")
    require(isinstance(raw, list), "review artifact checks must be a list")
    by_name: dict[str, dict[str, Any]] = {}
    for item in raw:
        require(isinstance(item, dict), "each review check must be an object")
        name = item.get("name")
        require(isinstance(name, str), "each review check requires a string name")
        require(name not in by_name, f"duplicate review check: {name}")
        by_name[name] = item
    require(
        tuple(sorted(by_name)) == tuple(sorted(REVIEW_CHECKS)),
        f"review checks must be exactly {list(REVIEW_CHECKS)}",
    )
    failed = [name for name, item in by_name.items() if item.get("status") != "PASS"]
    require(not failed, f"review checks are not PASS: {failed}")
    for name, item in by_name.items():
        require(
            isinstance(item.get("evidence"), str) and item["evidence"].strip(),
            f"review check lacks evidence: {name}",
        )


def _review_findings(artifact: dict[str, Any]) -> None:
    findings = artifact.get("findings")
    require(isinstance(findings, list), "review artifact findings must be a list")
    for finding in findings:
        require(isinstance(finding, dict), "each finding must be an object")
        severity = finding.get("severity")
        status = finding.get("status", "OPEN")
        require(
            not (severity in {"BLOCKER", "HIGH"} and str(status).upper() == "OPEN"),
            f"open blocking review finding: {finding.get('id', '<missing id>')}",
        )


def _verify_review_report(
    repo_root: Path,
    artifact: dict[str, Any],
    base_commit: str,
) -> tuple[Path, str]:
    report_value = artifact.get("review_report")
    require(
        isinstance(report_value, str) and report_value.strip(),
        "review artifact requires review_report",
    )
    report = _repo_path(repo_root, report_value)
    require(report.is_file(), f"review report is missing: {report}")
    try:
        report_relative = report.relative_to(repo_root.resolve()).as_posix()
    except ValueError as exc:
        raise GateError("review report is outside repository root") from exc
    require(
        report_relative.startswith("docs/hydraulic-twin/reviews/"),
        f"review report is not the frozen review path: {report_relative}",
    )
    text = report.read_text(encoding="utf-8")
    reviewer = artifact.get("reviewer")
    require(
        isinstance(reviewer, str) and reviewer in text,
        "reviewer is not evidenced by the review report",
    )
    require(VERDICT in text, f"review report does not contain {VERDICT}")
    require(
        base_commit in text, "review report does not contain the artifact base commit"
    )
    digest = sha256_file(report)
    recorded_digest = artifact.get("review_report_sha256")
    if recorded_digest is not None:
        require(
            _is_sha256(recorded_digest),
            "review_report_sha256 must be lowercase SHA-256",
        )
        require(
            recorded_digest == digest,
            "review_report_sha256 does not match the live review report",
        )
    return report, digest


def _verify_live_bundle(
    repo_root: Path,
    artifact: dict[str, Any],
    base_commit: str,
) -> tuple[str, list[dict[str, str]]]:
    documents: list[dict[str, str]] = []
    for relative in DOCUMENT_PATHS:
        path = repo_root / PurePosixPath(relative)
        require(path.is_file(), f"review document is missing: {relative}")
        documents.append({"path": relative, "sha256": sha256_file(path)})
    bundle = {
        "base_commit": base_commit,
        "documents": documents,
        "schema": "v3-review-bundle-v1",
    }
    bundle_sha256 = sha256_bytes(canonical_json_bytes(bundle))
    require(
        artifact.get("document_bundle_sha256") == bundle_sha256,
        "document_bundle_sha256 does not match the live canonical bundle",
    )
    require(
        artifact.get("documents") == documents,
        "review artifact documents do not match live per-file SHA-256 values",
    )
    return bundle_sha256, documents


def _base_tree_paths(repo_root: Path, base_commit: str, versions_dir: Path) -> set[str]:
    relative = versions_dir.resolve().relative_to(repo_root.resolve()).as_posix()
    output = _git_text(
        repo_root, "ls-tree", "-r", "--name-only", base_commit, "--", relative
    )
    return {line for line in output.splitlines() if line}


def _live_tree_paths(repo_root: Path, versions_dir: Path) -> set[str]:
    if not versions_dir.exists():
        return set()
    relative = versions_dir.resolve().relative_to(repo_root.resolve()).as_posix()
    output = _git_text(
        repo_root,
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "--",
        relative,
    )
    return {
        line
        for line in output.splitlines()
        if line and (repo_root / PurePosixPath(line)).is_file()
    }


def _verify_versions_unchanged(
    repo_root: Path,
    base_commit: str,
    versions_dir: Path,
) -> list[str]:
    require(versions_dir.is_dir(), f"versions directory is missing: {versions_dir}")
    base_paths = _base_tree_paths(repo_root, base_commit, versions_dir)
    live_paths = _live_tree_paths(repo_root, versions_dir)
    differences = sorted(base_paths.symmetric_difference(live_paths))
    require(not differences, f"versions path set changed from base: {differences}")

    relative = versions_dir.resolve().relative_to(repo_root.resolve()).as_posix()
    changed = [
        line
        for line in _git_text(
            repo_root,
            "diff",
            "--name-only",
            base_commit,
            "--",
            relative,
        ).splitlines()
        if line
    ]
    require(not changed, f"versions content changed from base: {changed}")
    return []


def _migration_identity(path: Path) -> tuple[str, str | None]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise GateError(
            f"approved migration is not readable Python: {path}: {exc}"
        ) from exc
    values: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = [target.id for target in targets if isinstance(target, ast.Name)]
        if "revision" not in names and "down_revision" not in names:
            continue
        value = node.value
        if not isinstance(value, ast.Constant) or not (
            isinstance(value.value, str) or value.value is None
        ):
            raise GateError(f"migration identity must be literal: {path}")
        for name in names:
            if name in {"revision", "down_revision"}:
                values[name] = value.value
    revision = values.get("revision")
    down_revision = values.get("down_revision")
    require(
        isinstance(revision, str) and revision, f"migration revision missing: {path}"
    )
    require(
        down_revision is None or (isinstance(down_revision, str) and down_revision),
        f"migration down_revision is invalid: {path}",
    )
    return revision, down_revision


def _verify_approved_migration(
    repo_root: Path,
    review_artifact_path: Path,
    review_artifact: dict[str, Any],
    approved_artifact_path: Path,
    versions_dir: Path,
) -> tuple[list[str], dict[str, Any]]:
    artifact = _load_json(approved_artifact_path, "approved migration artifact")
    require(
        artifact.get("schema") == APPROVED_MIGRATION_SCHEMA,
        "unexpected approved migration schema",
    )
    require(
        artifact.get("status") == APPROVED_MIGRATION_STATUS,
        f"approved migration status must be {APPROVED_MIGRATION_STATUS}",
    )
    require(
        artifact.get("phase") == "post-migration", "approved migration phase drifted"
    )
    require(
        artifact.get("owner") == APPROVED_MIGRATION_OWNER,
        f"approved migration owner must be {APPROVED_MIGRATION_OWNER}",
    )
    approval = artifact.get("approval")
    require(isinstance(approval, dict), "approved migration requires approval evidence")
    require(
        set(approval) == {"approved_at", "basis", "owner", "state"},
        "invalid approved migration approval shape",
    )
    require(
        approval.get("state") == APPROVED_MIGRATION_STATUS,
        "approved migration approval state drifted",
    )
    require(
        approval.get("owner") == APPROVED_MIGRATION_OWNER,
        "approved migration approval owner drifted",
    )
    for field in ("approved_at", "basis"):
        require(
            isinstance(approval.get(field), str) and approval[field].strip(),
            f"approved migration approval lacks {field}",
        )

    r0_reference = artifact.get("r0_review")
    require(isinstance(r0_reference, dict), "approved migration requires r0_review")
    require(
        set(r0_reference)
        == {
            "artifact",
            "artifact_sha256",
            "base_commit",
            "document_bundle_sha256",
            "verdict",
        },
        "invalid approved migration r0_review shape",
    )
    review_relative = review_artifact_path.relative_to(repo_root).as_posix()
    require(
        r0_reference.get("artifact") == review_relative,
        "approved migration references a different R0 artifact",
    )
    require(
        r0_reference.get("artifact_sha256") == sha256_file(review_artifact_path),
        "approved migration R0 artifact SHA-256 mismatch",
    )
    for field in ("base_commit", "document_bundle_sha256", "verdict"):
        require(
            r0_reference.get(field) == review_artifact.get(field),
            f"approved migration R0 {field} mismatch",
        )

    migrations = artifact.get("migrations")
    require(
        isinstance(migrations, list) and migrations, "approved migration chain is empty"
    )
    require(
        artifact.get("head_revision"),
        "approved migration requires head_revision",
    )
    base_paths = _base_tree_paths(
        repo_root, str(r0_reference["base_commit"]), versions_dir
    )
    live_paths = _live_tree_paths(repo_root, versions_dir)
    versions_relative = versions_dir.relative_to(repo_root).as_posix()
    changed_from_base = {
        line
        for line in _git_text(
            repo_root,
            "diff",
            "--name-only",
            str(r0_reference["base_commit"]),
            "--",
            versions_relative,
        ).splitlines()
        if line
    }
    approved_paths: set[str] = set()
    revisions: set[str] = set()
    previous_revision: str | None = None
    verified_entries: list[dict[str, Any]] = []
    for entry in migrations:
        require(
            isinstance(entry, dict), "each approved migration entry must be an object"
        )
        require(
            set(entry)
            == {
                "base_sha256",
                "base_state",
                "change",
                "down_revision",
                "live_sha256",
                "path",
                "revision",
            },
            "invalid approved migration entry shape",
        )
        relative_value = entry.get("path")
        require(
            isinstance(relative_value, str) and relative_value,
            "approved migration path is required",
        )
        relative = PurePosixPath(relative_value)
        require(
            not relative.is_absolute() and relative.suffix == ".py",
            f"approved migration path must be a repo-relative Python file: {relative_value}",
        )
        path = _repo_path(repo_root, relative)
        try:
            normalized = path.relative_to(repo_root).as_posix()
        except ValueError as exc:
            raise GateError("approved migration path escapes repository root") from exc
        require(
            normalized.startswith(f"{versions_relative}/"),
            f"approved migration is outside versions directory: {normalized}",
        )
        require(
            normalized == relative_value,
            f"non-canonical migration path: {relative_value}",
        )
        require(
            normalized not in approved_paths,
            f"duplicate approved migration path: {normalized}",
        )
        require(path.is_file(), f"approved migration file is missing: {normalized}")
        approved_paths.add(normalized)

        base_blob = _git_blob_text(
            repo_root, str(r0_reference["base_commit"]), normalized
        )
        expected_base_state = "PRESENT" if base_blob is not None else "ABSENT"
        require(
            entry.get("base_state") == expected_base_state,
            f"approved migration base state mismatch: {normalized}",
        )
        expected_base_hash = _git_blob_sha256(
            repo_root, str(r0_reference["base_commit"]), normalized
        )
        require(
            entry.get("base_sha256") == expected_base_hash,
            f"approved migration base SHA-256 mismatch: {normalized}",
        )
        live_hash = sha256_file(path)
        require(
            entry.get("live_sha256") == live_hash,
            f"approved migration live SHA-256 mismatch: {normalized}",
        )
        expected_change = (
            "PRESERVED_BASE" if expected_base_state == "PRESENT" else "ADDED_AFTER_R0"
        )
        require(
            entry.get("change") == expected_change,
            f"approved migration change kind mismatch: {normalized}",
        )
        if expected_base_state == "PRESENT":
            require(
                normalized not in changed_from_base,
                f"approved base migration content changed: {normalized}",
            )
        revision, down_revision = _migration_identity(path)
        require(
            entry.get("revision") == revision,
            f"approved migration revision mismatch: {normalized}",
        )
        require(
            entry.get("down_revision") == down_revision,
            f"approved migration down_revision mismatch: {normalized}",
        )
        require(
            revision not in revisions,
            f"duplicate approved migration revision: {revision}",
        )
        require(
            down_revision == previous_revision,
            f"approved migration chain is not linear at {revision}",
        )
        revisions.add(revision)
        previous_revision = revision
        verified_entries.append(
            {
                "base_sha256": expected_base_hash,
                "base_state": expected_base_state,
                "change": expected_change,
                "down_revision": down_revision,
                "live_sha256": live_hash,
                "path": normalized,
                "revision": revision,
            }
        )

    require(
        previous_revision == artifact.get("head_revision"),
        "approved migration head drifted",
    )
    require(
        approved_paths == live_paths,
        "live migration set differs from approved artifact",
    )
    require(
        base_paths.issubset(approved_paths),
        "approved migration artifact omits a base migration",
    )
    versions_changed = sorted((live_paths - base_paths) | changed_from_base)
    return versions_changed, {
        "artifact": approved_artifact_path.relative_to(repo_root).as_posix(),
        "artifact_sha256": sha256_file(approved_artifact_path),
        "head_revision": previous_revision,
        "migrations": verified_entries,
        "owner": APPROVED_MIGRATION_OWNER,
        "phase": APPROVED_MIGRATION_PHASE,
        "status": APPROVED_MIGRATION_STATUS,
    }


def _git_blob_text(repo_root: Path, base_commit: str, relative: str) -> bytes | None:
    completed = _git(
        repo_root,
        "show",
        f"{base_commit}:{relative}",
        check=False,
    )
    return completed.stdout if completed.returncode == 0 else None


def _git_blob_sha256(repo_root: Path, base_commit: str, relative: str) -> str | None:
    blob = _git_blob_text(repo_root, base_commit, relative)
    return sha256_bytes(blob) if blob is not None else None


def validate_migration_gate(
    repo_root: Path,
    review_artifact: Path,
    base_ref: str,
    versions_dir: Path,
    approved_migration_artifact: Path | None = None,
) -> dict[str, Any]:
    """Validate the R0 artifact and return a machine-readable PASS summary."""

    root = repo_root.resolve()
    artifact_path = _repo_path(root, review_artifact)
    versions_path = _repo_path(root, versions_dir)
    artifact = _load_json(artifact_path, "review artifact")

    require(
        artifact.get("schema") == REVIEW_SCHEMA, "unexpected review artifact schema"
    )
    require(artifact.get("verdict") == VERDICT, f"review verdict must be {VERDICT}")
    require(
        isinstance(artifact.get("reviewer"), str) and artifact["reviewer"].strip(),
        "review artifact requires reviewer",
    )
    require(
        isinstance(artifact.get("reviewed_at"), str)
        and artifact["reviewed_at"].strip(),
        "review artifact requires reviewed_at",
    )
    recorded_base = artifact.get("base_commit")
    require(
        _is_commit(recorded_base),
        "review artifact base_commit must be a lowercase 40-character commit",
    )
    resolved_base = _git_text(root, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
    require(
        resolved_base == recorded_base,
        f"base ref resolves to {resolved_base}, artifact records {recorded_base}",
    )
    ancestor = _git(
        root, "merge-base", "--is-ancestor", recorded_base, "HEAD", check=False
    )
    require(
        ancestor.returncode == 0,
        f"base commit {recorded_base} is not an ancestor of HEAD",
    )

    _review_checks(artifact)
    _review_findings(artifact)
    report, report_sha256 = _verify_review_report(root, artifact, recorded_base)
    bundle_sha256, documents = _verify_live_bundle(root, artifact, recorded_base)
    approved_migration: dict[str, Any] | None = None
    if approved_migration_artifact is None:
        changed_versions = _verify_versions_unchanged(
            root, recorded_base, versions_path
        )
        migration_phase = PRE_MIGRATION_PHASE
    else:
        approved_path = _repo_path(root, approved_migration_artifact)
        changed_versions, approved_migration = _verify_approved_migration(
            root,
            artifact_path,
            artifact,
            approved_path,
            versions_path,
        )
        migration_phase = APPROVED_MIGRATION_PHASE

    return {
        "status": "PASS",
        "verdict": VERDICT,
        "base_commit": recorded_base,
        "review_report": report.relative_to(root).as_posix(),
        "review_report_sha256": report_sha256,
        "document_bundle_sha256": bundle_sha256,
        "documents": documents,
        "versions_dir": versions_path.relative_to(root).as_posix(),
        "versions_changed": changed_versions,
        "migration_phase": migration_phase,
        "approved_migration": approved_migration,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-artifact", required=True)
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--versions-dir", required=True)
    parser.add_argument(
        "--approved-migration-artifact",
        default=None,
        help=(
            "post-migration approval artifact; when omitted, the canonical "
            "approved-migration-0008.json covering revisions 0001-0008 is used "
            "when present"
        ),
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    approved_value = args.approved_migration_artifact
    if approved_value is None:
        canonical = repo_root / DEFAULT_APPROVED_MIGRATION_ARTIFACT
        approved_value = (
            DEFAULT_APPROVED_MIGRATION_ARTIFACT if canonical.is_file() else None
        )
    try:
        result = validate_migration_gate(
            repo_root,
            _cli_path(repo_root, args.review_artifact),
            args.base_ref,
            _cli_path(repo_root, args.versions_dir),
            _cli_path(repo_root, approved_value) if approved_value else None,
        )
    except (GateError, OSError) as exc:
        print(f"MIGRATION_GATE=FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    print(f"MIGRATION_PHASE={result['migration_phase']}")
    print("MIGRATION_GATE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
