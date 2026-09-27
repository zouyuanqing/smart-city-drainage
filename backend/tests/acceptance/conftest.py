"""Bind the acceptance matrix to tests that actually ran in this session."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.acceptance.matrix_gate import (
    load_json,
    validate_matrix_structure,
    validate_session_execution,
    validate_stack_report,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
FIXTURE_ROOT = BACKEND_ROOT / "tests" / "fixtures" / "hydraulic"
ACCEPTANCE_ROOT = FIXTURE_ROOT / "acceptance" / "v1"
MATRIX_PATH = ACCEPTANCE_ROOT / "matrix.json"
REQUIREMENTS_PATH = ACCEPTANCE_ROOT / "acceptance_requirements.json"

_CALL_OUTCOMES: dict[str, str] = {}


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.when == "call":
        _CALL_OUTCOMES[report.nodeid] = report.outcome


def _whole_matrix_requested(config: pytest.Config) -> bool:
    if not config.args:
        return True
    for value in config.args:
        normalized = str(value).replace("\\", "/").rstrip("/")
        if normalized in {"tests", "tests/acceptance"} or normalized.endswith(
            ("/tests", "/tests/acceptance")
        ):
            return True
    return False


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    del exitstatus
    matrix = load_json(MATRIX_PATH)
    requirements = load_json(REQUIREMENTS_PATH)
    errors = validate_matrix_structure(
        matrix,
        requirements,
        backend_root=BACKEND_ROOT,
        fixture_root=FIXTURE_ROOT,
    )
    if _whole_matrix_requested(session.config) and not session.config.getoption(
        "collectonly", False
    ):
        errors.extend(validate_session_execution(matrix, _CALL_OUTCOMES))

    if any(
        isinstance(case, dict) and case.get("status") == "PASS"
        for case in matrix.get("cases", [])
    ):
        report_value = os.environ.get("HYDRAULIC_ACCEPTANCE_REPORT")
        if not report_value:
            errors.append(
                "an AC row claims PASS but HYDRAULIC_ACCEPTANCE_REPORT is not set"
            )
        else:
            report_path = Path(report_value)
            if not report_path.is_absolute():
                report_path = REPO_ROOT / report_path
            try:
                report = load_json(report_path)
            except (OSError, ValueError) as exc:
                errors.append(f"acceptance stack report is unreadable: {exc}")
            else:
                errors.extend(
                    validate_stack_report(matrix, report, repo_root=REPO_ROOT)
                )

    if errors:
        for error in errors:
            print(f"ACCEPTANCE_MATRIX_ERROR: {error}")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
