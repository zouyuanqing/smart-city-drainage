"""Fail-closed validation for the hydraulic acceptance matrix.

The matrix is a requirement map, never a result by itself.  This module binds
its diagnostic nodeids to the current pytest session and only allows ``PASS``
when same-run G0-G6 acceptance artifacts are supplied explicitly.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

MATRIX_SCHEMA = "hydraulic-backend-acceptance-matrix-v2"
REQUIREMENTS_SCHEMA = "hydraulic-backend-acceptance-requirements-v1"
STACK_REPORT_SCHEMA = "hydraulic-acceptance-report-v1"
VALID_STATUSES = {"PASS", "FAIL", "NOT RUN", "BLOCKED"}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize_ac_id(value: object) -> str:
    return str(value).upper().replace("-", "")


def _nodeid_path(nodeid: str) -> str:
    return nodeid.split("::", 1)[0].replace("\\", "/")


def _nodeid_function(nodeid: str) -> str:
    return nodeid.rsplit("::", 1)[-1].split("[", 1)[0]


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def source_uses_xfail(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return any(
        isinstance(node, ast.Call) and _call_name(node.func).endswith(".xfail")
        for node in ast.walk(tree)
    )


def function_has_assertion(path: Path, nodeid: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    wanted = _nodeid_function(nodeid)
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == wanted
    ]
    if not functions:
        return False
    function = functions[0]
    for node in ast.walk(function):
        if isinstance(node, ast.Assert):
            return True
        if isinstance(node, ast.Call) and _call_name(node.func) in {
            "pytest.fail",
            "pytest.raises",
        }:
            return True
    return False


def _validate_nodeid(
    *, nodeid: str, backend_root: Path, errors: list[str], label: str
) -> None:
    if "::" not in nodeid:
        errors.append(f"{label} is not a pytest nodeid: {nodeid}")
        return
    relative = _nodeid_path(nodeid)
    if not relative.startswith(("tests/acceptance/", "tests/security/")):
        errors.append(f"{label} is outside the frozen acceptance namespaces: {nodeid}")
        return
    path = backend_root / relative
    if not path.is_file():
        errors.append(f"{label} does not exist: {nodeid}")
        return
    if not function_has_assertion(path, nodeid):
        errors.append(f"{label} has no executable assertion: {nodeid}")


def validate_matrix_structure(
    matrix: Mapping[str, Any],
    requirements: Mapping[str, Any],
    *,
    backend_root: Path,
    fixture_root: Path,
) -> list[str]:
    """Return all structural defects without trusting declared statuses."""

    errors: list[str] = []
    if matrix.get("schema") != MATRIX_SCHEMA:
        errors.append(f"matrix.schema must be {MATRIX_SCHEMA}")
    if requirements.get("schema") != REQUIREMENTS_SCHEMA:
        errors.append(f"requirements.schema must be {REQUIREMENTS_SCHEMA}")

    expected_requirements = {f"FR-{index}" for index in range(1, 8)}
    if set(matrix.get("requirements", {})) != expected_requirements:
        errors.append("matrix FR ids must be exactly FR-1 through FR-7")

    cases = matrix.get("cases")
    if not isinstance(cases, list):
        return errors + ["matrix.cases must be an array"]
    expected_cases = {f"AC-{index}" for index in range(1, 9)}
    if {case.get("id") for case in cases if isinstance(case, dict)} != expected_cases:
        errors.append("matrix AC ids must be exactly AC-1 through AC-8")

    hashes = matrix.get("fixture_sha256")
    if not isinstance(hashes, dict):
        hashes = {}
        errors.append("matrix.fixture_sha256 must be an object")
    for relative_path, expected_hash in hashes.items():
        path = fixture_root / relative_path
        if not path.is_file():
            errors.append(f"matrix fixture is missing: {relative_path}")
        elif sha256_file(path) != expected_hash:
            errors.append(f"matrix fixture hash mismatch: {relative_path}")

    requirement_cases = requirements.get("cases")
    if not isinstance(requirement_cases, dict):
        requirement_cases = {}
        errors.append("requirements.cases must be an object")

    referenced_sources: set[Path] = set()
    for case in cases:
        if not isinstance(case, dict):
            errors.append("matrix case must be an object")
            continue
        case_id = str(case.get("id", "<missing>"))
        status = case.get("status")
        if status not in VALID_STATUSES:
            errors.append(f"{case_id} has invalid status: {status!r}")
        if status != "PASS" and not str(case.get("reason", "")).strip():
            errors.append(f"{case_id} requires a reason unless status is PASS")

        requirement = requirement_cases.get(case_id)
        if not isinstance(requirement, dict):
            errors.append(f"{case_id} has no frozen evidence requirement")
            requirement = {}
        for field in ("required_gates", "required_artifact_suffixes"):
            expected = requirement.get(field)
            if not isinstance(expected, list) or not expected:
                errors.append(f"{case_id} requirement {field} must be non-empty")
            if case.get(field) != expected:
                errors.append(f"{case_id} {field} does not match frozen requirements")
        observations = requirement.get("required_observations")
        if not isinstance(observations, list) or not observations:
            errors.append(f"{case_id} must freeze required observations")

        tests = case.get("tests")
        if not isinstance(tests, list) or not tests:
            errors.append(f"{case_id} must list at least one executed diagnostic test")
            tests = []
        if len(set(tests)) != len(tests):
            errors.append(f"{case_id} contains duplicate diagnostic nodeids")
        for nodeid in tests:
            if not isinstance(nodeid, str):
                errors.append(f"{case_id} test nodeid must be a string")
                continue
            _validate_nodeid(
                nodeid=nodeid,
                backend_root=backend_root,
                errors=errors,
                label=f"{case_id} diagnostic",
            )
            source = backend_root / _nodeid_path(nodeid)
            if source.is_file():
                referenced_sources.add(source)

        pass_nodeids = case.get("pass_nodeids")
        if not isinstance(pass_nodeids, list):
            errors.append(f"{case_id}.pass_nodeids must be an array")
            pass_nodeids = []
        if status == "PASS" and not pass_nodeids:
            errors.append(f"{case_id} PASS requires concrete pass_nodeids")
        if status != "PASS" and pass_nodeids:
            errors.append(f"{case_id} non-PASS status cannot claim pass_nodeids")
        for nodeid in pass_nodeids:
            if not isinstance(nodeid, str):
                errors.append(f"{case_id} pass nodeid must be a string")
                continue
            _validate_nodeid(
                nodeid=nodeid,
                backend_root=backend_root,
                errors=errors,
                label=f"{case_id} pass",
            )

        for relative_path in case.get("fixtures", []):
            if relative_path not in hashes:
                errors.append(f"{case_id} fixture has no matrix hash: {relative_path}")

    acceptance_test_root = backend_root / "tests" / "acceptance"
    referenced_sources.update(acceptance_test_root.glob("test_*.py"))
    for source in sorted(referenced_sources):
        if source_uses_xfail(source):
            errors.append(
                f"acceptance tests may not hide a known contract gap with xfail: "
                f"{source.relative_to(backend_root).as_posix()}"
            )
    return errors


def _matching_outcomes(base: str, outcomes: Mapping[str, str]) -> list[str]:
    return [
        outcome
        for nodeid, outcome in outcomes.items()
        if nodeid == base or nodeid.startswith(f"{base}[")
    ]


def validate_session_execution(
    matrix: Mapping[str, Any], outcomes: Mapping[str, str]
) -> list[str]:
    """Require every mapped diagnostic to have a call result in this session."""

    errors: list[str] = []
    for case in matrix.get("cases", []):
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("id", "<missing>"))
        for nodeid in case.get("tests", []):
            matched = _matching_outcomes(str(nodeid), outcomes)
            if not matched or any(
                outcome not in {"passed", "failed"} for outcome in matched
            ):
                errors.append(
                    f"{case_id} diagnostic was not executed this session: {nodeid}"
                )
        if case.get("status") != "PASS":
            continue
        for nodeid in case.get("pass_nodeids", []):
            matched = _matching_outcomes(str(nodeid), outcomes)
            if not matched or any(outcome != "passed" for outcome in matched):
                errors.append(
                    f"{case_id} pass nodeid did not pass this session: {nodeid}"
                )
    return errors


def _resolve_artifact(repo_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repo_root / path


def validate_stack_report(
    matrix: Mapping[str, Any], report: Mapping[str, Any], *, repo_root: Path
) -> list[str]:
    """Bind PASS claims to same-run gate artifacts and their SHA-256 sidecars."""

    errors: list[str] = []
    if report.get("schema") != STACK_REPORT_SCHEMA:
        errors.append(f"stack report schema must be {STACK_REPORT_SCHEMA}")
    if report.get("status") != "PASS" or report.get("exit_code") != 0:
        errors.append("stack report is not PASS with exit code 0")
    if not report.get("run_id") or not report.get("commit"):
        errors.append("stack report must bind one non-empty RUN_ID and commit")

    report_cases = {
        _normalize_ac_id(item.get("ac", "")): item
        for item in report.get("acceptance", [])
        if isinstance(item, dict)
    }
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, dict):
        artifacts = {}
        errors.append("stack report artifacts must be an object")
    flat_artifacts = [
        str(path)
        for paths in artifacts.values()
        for path in (paths if isinstance(paths, list) else [])
    ]

    for case in matrix.get("cases", []):
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("id", "<missing>"))
        normalized_case_id = _normalize_ac_id(case_id)
        report_case = report_cases.get(normalized_case_id)
        if report_case is None:
            errors.append(f"stack report has no result for {case_id}")
        elif report_case.get("status") != case.get("status"):
            errors.append(
                f"{case_id} matrix status {case.get('status')!r} disagrees with "
                f"stack report {report_case.get('status')!r}"
            )

        for gate in case.get("required_gates", []):
            paths = artifacts.get(gate)
            if not isinstance(paths, list) or not paths:
                errors.append(f"{case_id} has no same-run artifact for {gate}")
                continue
            for value in paths:
                path = _resolve_artifact(repo_root, str(value)).resolve()
                try:
                    path.relative_to(repo_root.resolve())
                except ValueError:
                    errors.append(
                        f"{case_id} artifact escapes the repository root: {value}"
                    )
                    continue
                if not path.is_file():
                    errors.append(f"{case_id} referenced artifact is missing: {value}")
                    continue
                sidecar = Path(f"{path}.sha256")
                if not sidecar.is_file():
                    errors.append(f"{case_id} artifact has no SHA-256 sidecar: {value}")
                    continue
                try:
                    expected = sidecar.read_text(encoding="ascii").split()[0]
                except (IndexError, OSError, UnicodeError) as exc:
                    errors.append(
                        f"{case_id} artifact hash sidecar is invalid: {value}: {exc}"
                    )
                    continue
                if not _SHA256_RE.fullmatch(expected) or sha256_file(path) != expected:
                    errors.append(f"{case_id} artifact hash mismatch: {value}")
                if path.name == "result.json":
                    try:
                        result = load_json(path)
                    except (OSError, ValueError, json.JSONDecodeError) as exc:
                        errors.append(
                            f"{case_id} gate result is invalid: {value}: {exc}"
                        )
                        continue
                    if (
                        result.get("status") != "PASS"
                        or result.get("exit_code") != 0
                        or result.get("run_id") != report.get("run_id")
                        or result.get("commit") != report.get("commit")
                    ):
                        errors.append(
                            f"{case_id} gate result is not same-run PASS: {value}"
                        )

        for suffix in case.get("required_artifact_suffixes", []):
            normalized = str(suffix).replace("\\", "/").lower()
            if not any(
                value.replace("\\", "/").lower().endswith(normalized)
                for value in flat_artifacts
            ):
                errors.append(f"{case_id} required artifact is absent: {suffix}")
    return errors


def all_cases_pass(matrix: Mapping[str, Any]) -> bool:
    cases = matrix.get("cases", [])
    return (
        isinstance(cases, list)
        and bool(cases)
        and all(
            isinstance(case, dict) and case.get("status") == "PASS" for case in cases
        )
    )
