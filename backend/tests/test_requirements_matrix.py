"""Guard the Python interpreter/dependency matrix.

An unmarked pin without a matching wheel makes `pip install` fall back to a
source build, which is what produced the `pg_config executable not found`
failure for ``psycopg2-binary==2.9.9`` on a Python 3.14 host. These tests pin the
contract interpreter (3.11) and the wheel-backed >= 3.13 companion pins.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

BACKEND = Path(__file__).parents[1]
REPO_ROOT = BACKEND.parent
REQUIREMENT_FILES = (
    "requirements.txt",
    "requirements-cpu.txt",
    "requirements-hydraulic.txt",
)
LOCK_FILES = (
    "requirements-py311.lock",
    "requirements-py311-gpu.lock",
    "requirements-hydraulic-py311.lock",
)

# Packages that are compiled (or need an external toolchain such as pg_config)
# unless a wheel matches the interpreter. Every pin of these must be guarded by
# an environment marker.
COMPILER_BACKED = {
    "psycopg2-binary",
    "asyncpg",
    "hiredis",
    "numpy",
    "pydantic",
    "pydantic-core",
    "pillow",
    "opencv-python-headless",
    "sqlalchemy",
    "torch",
    "torchvision",
    "onnxruntime",
    "onnxruntime-gpu",
}
AI_STACK = {"torch", "torchvision", "ultralytics", "onnxruntime", "onnxruntime-gpu"}
PROBED_VERSIONS = ("3.11", "3.12", "3.13", "3.14")
CONTRACT_PYTHON = "3.11"
HOST_PYTHON = "3.14"

# Pins the contract interpreter must keep (PRD / pyproject requires-python).
CONTRACT_PINS = {
    "sqlalchemy": "2.0.27",
    "asyncpg": "0.29.0",
    "psycopg2-binary": "2.9.9",
    "hiredis": "2.3.2",
    "pydantic": "2.6.1",
    "numpy": "1.26.4",
    "pillow": "10.2.0",
    "opencv-python-headless": "4.9.0.80",
    "torch": "2.2.1",
}
# Wheel-backed companion pins for newer host interpreters.
HOST_PINS = {
    "sqlalchemy": "2.0.45",
    "asyncpg": "0.31.0",
    "psycopg2-binary": "2.9.13",
    "hiredis": "3.3.1",
    "pydantic": "2.12.5",
    "numpy": "2.3.4",
    "pillow": "12.1.0",
    "opencv-python-headless": "4.11.0.86",
}


def _requirements(file_name: str) -> list[Requirement]:
    text = (BACKEND / file_name).read_text(encoding="utf-8")
    parsed: list[Requirement] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        parsed.append(Requirement(line))
    return parsed


def _resolved(file_name: str, python_version: str) -> dict[str, str]:
    env = {
        **default_environment(),
        "python_version": python_version,
        "python_full_version": f"{python_version}.0",
    }
    resolved: dict[str, str] = {}
    for requirement in _requirements(file_name):
        if requirement.marker is not None and not requirement.marker.evaluate(env):
            continue
        versions = [
            spec.version for spec in requirement.specifier if spec.operator == "=="
        ]
        assert len(versions) == 1, f"{requirement} must pin exactly one version"
        resolved[canonicalize_name(requirement.name)] = versions[0]
    return resolved


@pytest.mark.parametrize("file_name", REQUIREMENT_FILES)
def test_every_requirement_is_exactly_pinned(file_name: str) -> None:
    for requirement in _requirements(file_name):
        operators = {spec.operator for spec in requirement.specifier}
        assert operators == {"=="}, f"{file_name}: {requirement} must use =="


@pytest.mark.parametrize("file_name", REQUIREMENT_FILES)
def test_compiler_backed_pins_are_marker_guarded(file_name: str) -> None:
    for requirement in _requirements(file_name):
        if canonicalize_name(requirement.name) not in COMPILER_BACKED:
            continue
        assert requirement.marker is not None, (
            f"{file_name}: {requirement.name} needs a marker so an interpreter "
            "without wheels never falls back to a source build"
        )


@pytest.mark.parametrize("file_name", REQUIREMENT_FILES)
@pytest.mark.parametrize("python_version", PROBED_VERSIONS)
def test_markers_select_exactly_one_pin(file_name: str, python_version: str) -> None:
    env = {
        **default_environment(),
        "python_version": python_version,
        "python_full_version": f"{python_version}.0",
    }
    seen: dict[str, str] = {}
    for requirement in _requirements(file_name):
        if requirement.marker is not None and not requirement.marker.evaluate(env):
            continue
        name = canonicalize_name(requirement.name)
        version = next(
            spec.version for spec in requirement.specifier if spec.operator == "=="
        )
        assert seen.get(name, version) == version, (
            f"{file_name}: {name} resolves to {seen.get(name)} and {version} "
            f"on Python {python_version}"
        )
        seen[name] = version


@pytest.mark.parametrize("file_name", ("requirements.txt", "requirements-cpu.txt"))
def test_contract_interpreter_keeps_the_frozen_pins(file_name: str) -> None:
    resolved = _resolved(file_name, CONTRACT_PYTHON)
    for name, version in CONTRACT_PINS.items():
        assert resolved[name] == version, f"{file_name}: {name} drifted on 3.11"


@pytest.mark.parametrize("file_name", REQUIREMENT_FILES)
def test_newer_host_uses_wheel_backed_variants(file_name: str) -> None:
    resolved = _resolved(file_name, HOST_PYTHON)
    for name, version in HOST_PINS.items():
        if any(canonicalize_name(req.name) == name for req in _requirements(file_name)):
            assert resolved[name] == version, f"{file_name}: {name} on 3.14"
    assert not (AI_STACK & set(resolved)), (
        "the YOLO/CUDA stack has no cp313+ wheels and must stay out of the "
        "newer-interpreter resolution"
    )


@pytest.mark.parametrize("file_name", REQUIREMENT_FILES)
def test_hydraulic_profile_never_pulls_native_engines(file_name: str) -> None:
    if file_name != "requirements-hydraulic.txt":
        pytest.skip("fallback-only profile")
    resolved = _resolved(file_name, HOST_PYTHON)
    for package in ("torch", "opencv-python-headless", "onnxruntime", "hiredis"):
        assert package not in resolved
    assert resolved["pydantic"] and resolved["sqlalchemy"]


@pytest.mark.parametrize("lock_name", LOCK_FILES)
def test_locks_cover_both_marker_branches(lock_name: str) -> None:
    lock_text = (BACKEND / lock_name).read_text(encoding="utf-8")
    assert "--hash=sha256:" in lock_text
    assert "psycopg2-binary==2.9.9 ; python_full_version < '3.13'" in lock_text
    assert "psycopg2-binary==2.9.13 ; python_full_version >= '3.13'" in lock_text
    assert "swmm-toolkit" not in lock_text.lower()
    assert "py-swmm" not in lock_text.lower()


def test_project_metadata_mirrors_the_matrix() -> None:
    pyproject = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["requires-python"] == ">=3.11,<3.12"
    extras = pyproject["project"]["optional-dependencies"]
    assert extras["swmm"] == []
    for extra in ("hydraulic", "ai-cpu", "ai-gpu"):
        for entry in extras[extra]:
            assert canonicalize_name(Requirement(entry).name) in (
                {"numpy", "pillow", "opencv-python-headless"}
                | (AI_STACK if extra.startswith("ai-") else set())
            ), f"{extra}: unexpected dependency {entry}"
        env = {
            **default_environment(),
            "python_version": HOST_PYTHON,
            "python_full_version": f"{HOST_PYTHON}.0",
        }
        active = [
            Requirement(entry)
            for entry in extras[extra]
            if Requirement(entry).marker is None
            or Requirement(entry).marker.evaluate(env)
        ]
        assert not (AI_STACK & {canonicalize_name(r.name) for r in active})


def test_repo_pins_the_contract_interpreter_for_tooling() -> None:
    version_file = REPO_ROOT / ".python-version"
    assert version_file.is_file(), "missing .python-version"
    assert version_file.read_text(encoding="utf-8").strip() == CONTRACT_PYTHON
