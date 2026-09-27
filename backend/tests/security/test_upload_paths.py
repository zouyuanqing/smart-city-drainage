"""Upload and model-repository path hardening tests."""

from __future__ import annotations

import uuid

import pytest

from app.api import routes
from app.core.model_manager import (
    ModelManager,
    UnsafeModelPath,
    safe_model_path,
    validate_model_version,
)
from app.core.security import create_access_token
from app.models.db_models import RoleEnum


class _Manager:
    def scan_repository(self):
        return {}


def _admin_headers() -> dict[str, str]:
    token = create_access_token(
        subject=str(uuid.uuid4()), extra_claims={"role": RoleEnum.admin.value}
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_model_upload_rejects_path_traversal(client, tmp_path, monkeypatch):
    monkeypatch.setattr(routes.settings, "MODEL_STORAGE_PATH", str(tmp_path / "models"))
    response = await client.post(
        "/api/models/upload",
        params={"version": "../escape"},
        files={"file": ("model.pt", b"not-a-model", "application/octet-stream")},
        headers=_admin_headers(),
    )

    assert response.status_code == 422
    assert not (tmp_path / "escape.pt").exists()


@pytest.mark.asyncio
async def test_model_upload_rejects_windows_path_separator(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(routes.settings, "MODEL_STORAGE_PATH", str(tmp_path / "models"))
    response = await client.post(
        "/api/models/upload",
        params={"version": r"..\\escape"},
        files={"file": ("model.onnx", b"not-a-model", "application/octet-stream")},
        headers=_admin_headers(),
    )

    assert response.status_code == 422
    assert not (tmp_path / "escape.onnx").exists()


@pytest.mark.asyncio
async def test_model_upload_enforces_streaming_size_limit(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(routes.settings, "MODEL_STORAGE_PATH", str(tmp_path / "models"))
    monkeypatch.setattr(routes.settings, "MAX_UPLOAD_SIZE_MB", 1)
    monkeypatch.setattr(routes, "get_model_manager", lambda: _Manager())

    response = await client.post(
        "/api/models/upload",
        params={"version": "too-large"},
        files={
            "file": (
                "model.pt",
                b"x" * (1024 * 1024 + 1),
                "application/octet-stream",
            )
        },
        headers=_admin_headers(),
    )

    assert response.status_code == 413
    model_root = tmp_path / "models"
    assert not (model_root / "too-large.pt").exists()
    assert not list(model_root.glob("*.part"))


@pytest.mark.asyncio
async def test_model_upload_is_atomic_and_does_not_return_absolute_path(
    client, tmp_path, monkeypatch
):
    monkeypatch.setattr(routes.settings, "MODEL_STORAGE_PATH", str(tmp_path / "models"))
    monkeypatch.setattr(routes.settings, "MAX_UPLOAD_SIZE_MB", 1)
    monkeypatch.setattr(routes, "get_model_manager", lambda: _Manager())

    response = await client.post(
        "/api/models/upload",
        params={"version": "safe"},
        files={"file": ("safe.onnx", b"model-bytes", "application/octet-stream")},
        headers=_admin_headers(),
    )

    assert response.status_code == 200
    assert response.json()["file_path"] == "safe.onnx"
    assert (tmp_path / "models" / "safe.onnx").read_bytes() == b"model-bytes"
    assert not list((tmp_path / "models").glob("*.part"))


def test_model_manager_rejects_traversal_and_symlink(tmp_path, monkeypatch):
    model_root = tmp_path / "models"
    model_root.mkdir()
    monkeypatch.setattr(routes.settings, "MODEL_STORAGE_PATH", str(model_root))

    manager = ModelManager.__new__(ModelManager)
    assert manager._resolve_model_path("../outside") is None
    assert manager._resolve_model_path(r"..\\outside") is None

    outside = tmp_path / "outside.onnx"
    outside.write_bytes(b"outside")
    link = model_root / "linked.onnx"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not available on this host")
    assert manager._resolve_model_path("linked") is None


def test_safe_model_path_never_returns_outside_root(tmp_path):
    with pytest.raises(UnsafeModelPath):
        safe_model_path("../escape", ".pt", root=tmp_path / "models")
    with pytest.raises(UnsafeModelPath):
        safe_model_path("v1", ".zip", root=tmp_path / "models")


@pytest.mark.parametrize("version", ["CON", "nul.onnx", "LPT9", "model."])
def test_model_version_rejects_windows_device_names_and_trailing_dot(version):
    with pytest.raises(UnsafeModelPath):
        validate_model_version(version)
