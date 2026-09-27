"""Regression tests for the acceptance matrix's fail-closed validator."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tests.acceptance.matrix_gate import (
    MATRIX_SCHEMA,
    STACK_REPORT_SCHEMA,
    source_uses_xfail,
    validate_session_execution,
    validate_stack_report,
)


def _matrix_copy(matrix: dict) -> dict:
    return json.loads(json.dumps(matrix))


def test_validator_rejects_unexecuted_and_failed_nodeids():
    matrix = {
        "cases": [
            {
                "id": "AC-1",
                "status": "PASS",
                "tests": ["tests/acceptance/test_matrix_gate.py::diagnostic"],
                "pass_nodeids": ["tests/acceptance/test_matrix_gate.py::diagnostic"],
            }
        ]
    }

    assert validate_session_execution(matrix, {})
    assert "was not executed" in validate_session_execution(matrix, {})[0]
    failed = validate_session_execution(
        matrix,
        {"tests/acceptance/test_matrix_gate.py::diagnostic": "failed"},
    )
    assert failed
    assert "did not pass" in failed[0]


def test_validator_detects_xfail_hidden_in_acceptance_source(tmp_path: Path):
    source = tmp_path / "test_gap.py"
    source.write_text(
        "import pytest\n"
        "@pytest.mark.xfail(strict=True, reason='known gap')\n"
        "def test_contract():\n"
        "    assert False\n",
        encoding="utf-8",
    )

    assert source_uses_xfail(source) is True


def test_validator_binds_pass_status_to_same_run_hashed_artifact(tmp_path: Path):
    result_path = tmp_path / "g6" / "postgis" / "result.json"
    result_path.parent.mkdir(parents=True)
    result = {
        "status": "PASS",
        "exit_code": 0,
        "run_id": "run-1",
        "commit": "commit-1",
    }
    result_path.write_text(json.dumps(result), encoding="utf-8")
    digest = hashlib.sha256(result_path.read_bytes()).hexdigest()
    Path(f"{result_path}.sha256").write_text(
        f"{digest}  result.json\n", encoding="ascii"
    )
    matrix = {
        "schema": MATRIX_SCHEMA,
        "cases": [
            {
                "id": "AC-8",
                "status": "PASS",
                "required_gates": ["g6"],
                "required_artifact_suffixes": ["g6/postgis/result.json"],
            }
        ],
    }
    report = {
        "schema": STACK_REPORT_SCHEMA,
        "status": "PASS",
        "exit_code": 0,
        "run_id": "run-1",
        "commit": "commit-1",
        "acceptance": [{"ac": "ac8", "status": "PASS"}],
        "artifacts": {"g6": [str(result_path)]},
    }

    assert validate_stack_report(matrix, report, repo_root=tmp_path) == []
    report["run_id"] = "cross-run"
    errors = validate_stack_report(matrix, report, repo_root=tmp_path)
    assert errors
    assert any("disagrees" in error or "not same-run PASS" in error for error in errors)
