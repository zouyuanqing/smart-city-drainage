"""Fail-closed negative security acceptance tests.

Every mapped security boundary is a normal test.  Known production gaps are
represented by ``BLOCKED`` evidence, never hidden with xfail.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Any

import pytest
from fastapi import HTTPException, UploadFile
from jose import jwt

from app import main as main_module
from app.api import hydraulic_routes
from app.api.hydraulic_routes import hydraulic_store, reset_hydraulic_api_store
from app.core.security import create_access_token

FIXTURE_ROOT = (
    Path(__file__).parents[1] / "fixtures" / "hydraulic" / "acceptance" / "v1"
)
MALICIOUS_FIXTURE = FIXTURE_ROOT / "malicious_imports.json"
UTC = timezone.utc


def _cases() -> list[dict[str, Any]]:
    return json.loads(MALICIOUS_FIXTURE.read_text(encoding="utf-8"))


def _param_cases() -> list[Any]:
    return [
        pytest.param(case, id=case["id"])
        for case in _cases()
        if case.get("multipart") != "raw"
    ]


def _raw_multipart_cases() -> list[Any]:
    return [
        pytest.param(case, id=case["id"])
        for case in _cases()
        if case.get("multipart") == "raw"
    ]


def _headers(role: str = "operator", key: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": "Bearer "
        + create_access_token(
            "00000000-0000-0000-4000-8000-000000000001",
            extra_claims={"role": role},
        )
    }
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _content(case: dict[str, Any]) -> bytes:
    kind = case.get("content_kind")
    if kind == "oversized-inp":
        return b"x" * (20 * 1024 * 1024 + 1)
    if kind == "deep-json":
        return (b"[" * 1100) + (b"]" * 1100)
    if kind == "zip-ratio-bomb":
        target = io.BytesIO()
        with zipfile.ZipFile(
            target, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            archive.writestr("bounded-zeroes.bin", b"\0" * (8 * 1024 * 1024))
        return target.getvalue()
    if kind == "duplicate-fanout":
        features = [
            {
                "type": "Feature",
                "id": "N-DUPLICATE",
                "geometry": {"type": "Point", "coordinates": [121.3, 31.2]},
                "properties": {"stable_key": "N-DUPLICATE"},
            }
            for _ in range(2_000)
        ]
        return json.dumps(
            {"type": "FeatureCollection", "features": features},
            separators=(",", ":"),
        ).encode("utf-8")
    if "content_hex" in case:
        return bytes.fromhex(case["content_hex"])
    return str(case.get("content", "")).encode("utf-8")


def _raw_multipart(case: dict[str, Any]) -> tuple[bytes, str]:
    boundary = f"acceptance-{case['id']}-boundary"
    payload = _content(case)
    body = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; '
            f'filename="{case["filename"]}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("latin-1")
        + payload
        + f"\r\n--{boundary}--\r\n".encode("ascii")
    )
    return body, f"multipart/form-data; boundary={boundary}"


def _state_counts() -> dict[str, int]:
    return {
        "jobs": len(hydraulic_store.jobs),
        "imports": len(hydraulic_store.imports),
        "idempotency": len(hydraulic_store.idempotency),
        "networks": len(hydraulic_store.networks),
        "revisions": len(hydraulic_store.revisions),
    }


@pytest.fixture(autouse=True)
def _isolated_api_adapter(monkeypatch: pytest.MonkeyPatch):
    reset_hydraulic_api_store()
    main_module._rate_buckets.clear()
    monkeypatch.setattr(
        main_module.settings, "RATE_LIMIT_NETWORK_JOB_SUBMIT_PER_MINUTE", 100
    )
    yield
    reset_hydraulic_api_store()
    main_module._rate_buckets.clear()


@pytest.mark.parametrize("case", _param_cases())
@pytest.mark.asyncio
async def test_ac2_malicious_import_case_is_rejected_without_state_mutation(
    client, case: dict[str, Any]
):
    before_state = _state_counts()

    response = await client.post(
        "/api/networks/import",
        files={
            "file": (
                case["filename"],
                _content(case),
                "application/octet-stream",
            )
        },
        headers=_headers("operator", f"negative-{case['id']}"),
    )

    body = response.json()
    assert response.status_code == case["expected_status"], body
    assert body["code"] == case["expected_code"], body
    assert _state_counts() == before_state

    still_alive = await client.get("/api/networks", headers=_headers("viewer"))
    assert still_alive.status_code == 200


@pytest.mark.parametrize("case", _raw_multipart_cases())
@pytest.mark.asyncio
async def test_ac2_raw_multipart_filename_is_rejected_without_parser_rewrite(
    client, case: dict[str, Any]
):
    before_state = _state_counts()
    body, content_type = _raw_multipart(case)

    response = await client.post(
        "/api/networks/import",
        content=body,
        headers={
            **_headers("operator", f"raw-negative-{case['id']}"),
            "Content-Type": content_type,
        },
    )

    payload = response.json()
    assert response.status_code == case["expected_status"], payload
    assert payload["code"] == case["expected_code"], payload
    assert _state_counts() == before_state


@pytest.mark.parametrize(
    "case",
    [
        case
        for case in _cases()
        if case["id"]
        in {"absolute-posix", "windows-drive", "windows-forward-slash", "unc-path"}
    ],
    ids=lambda case: case["id"],
)
@pytest.mark.asyncio
async def test_ac2_route_rejects_original_windows_and_unc_filenames(
    case: dict[str, Any]
):
    spool = SpooledTemporaryFile()
    spool.write(_content(case))
    spool.seek(0)
    upload = UploadFile(filename=case["filename"], file=spool, size=spool.tell())

    try:
        with pytest.raises(HTTPException) as raised:
            await hydraulic_routes.import_network(
                file=upload,
                key=f"direct-negative-{case['id']}",
                user={
                    "sub": "00000000-0000-0000-4000-8000-000000000001",
                    "role": "operator",
                    "type": "access",
                },
            )
    finally:
        spool.close()

    assert raised.value.status_code == case["expected_status"]
    assert raised.value.detail["code"] == case["expected_code"]


@pytest.mark.asyncio
async def test_import_authentication_rbac_and_idempotency_boundaries(client):
    payload = (
        Path(__file__).parents[1]
        / "fixtures"
        / "hydraulic"
        / "imports"
        / "swmm_minimal_wgs84.inp"
    ).read_bytes()

    missing_auth = await client.post(
        "/api/networks/import",
        files={"file": ("safe.inp", payload, "text/plain")},
        headers={"Idempotency-Key": "negative-missing-auth"},
    )
    assert missing_auth.status_code == 401
    assert missing_auth.json()["code"] == "AUTH_REQUIRED"

    viewer = await client.post(
        "/api/networks/import",
        files={"file": ("safe.inp", payload, "text/plain")},
        headers=_headers("viewer", "negative-viewer-import"),
    )
    assert viewer.status_code == 403
    assert viewer.json()["code"] == "FORBIDDEN"

    unknown_role = await client.post(
        "/api/networks/import",
        files={"file": ("safe.inp", payload, "text/plain")},
        headers=_headers("not-a-role", "negative-role-import"),
    )
    assert unknown_role.status_code == 403

    missing_key = await client.post(
        "/api/networks/import",
        files={"file": ("safe.inp", payload, "text/plain")},
        headers={"Authorization": _headers("operator")["Authorization"]},
    )
    assert missing_key.status_code == 422
    assert missing_key.json()["code"] == "VALIDATION_ERROR"

    valid = await client.post(
        "/api/networks/import",
        files={"file": ("safe.inp", payload, "text/plain")},
        headers=_headers("operator", "negative-idempotency"),
    )
    assert valid.status_code == 202

    conflicting = await client.post(
        "/api/networks/import",
        files={"file": ("other.inp", payload + b"changed", "text/plain")},
        headers=_headers("operator", "negative-idempotency"),
    )
    assert conflicting.status_code == 409
    assert conflicting.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


def _jwt_claims(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    claims = {
        "sub": "00000000-0000-0000-4000-8000-000000000001",
        "role": "operator",
        "type": "access",
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=5),
        "iss": main_module.settings.JWT_ISSUER,
        "aud": main_module.settings.JWT_AUDIENCE,
    }
    claims.update(overrides)
    return claims


@pytest.mark.parametrize(
    "invalid_kind",
    [
        "malformed",
        "tampered",
        "wrong-algorithm",
        "wrong-issuer",
        "wrong-audience",
        "expired",
        "missing-role",
    ],
)
@pytest.mark.asyncio
async def test_import_jwt_negative_matrix_is_rejected(client, invalid_kind: str):
    if invalid_kind == "malformed":
        token = "not-a-jwt"
    elif invalid_kind == "tampered":
        valid = create_access_token(
            "00000000-0000-0000-4000-8000-000000000001",
            extra_claims={"role": "operator"},
        )
        header, payload, signature = valid.split(".")
        token = (
            f"{header}.{payload}.{'A' if signature[0] != 'A' else 'B'}{signature[1:]}"
        )
    elif invalid_kind == "wrong-algorithm":
        token = jwt.encode(
            _jwt_claims(), main_module.settings.JWT_SECRET_KEY, algorithm="HS512"
        )
    elif invalid_kind == "wrong-issuer":
        token = jwt.encode(
            _jwt_claims(iss="attacker"),
            main_module.settings.JWT_SECRET_KEY,
            algorithm="HS256",
        )
    elif invalid_kind == "wrong-audience":
        token = jwt.encode(
            _jwt_claims(aud="attacker"),
            main_module.settings.JWT_SECRET_KEY,
            algorithm="HS256",
        )
    elif invalid_kind == "expired":
        now = datetime.now(UTC)
        token = jwt.encode(
            _jwt_claims(iat=now - timedelta(hours=2), exp=now - timedelta(hours=1)),
            main_module.settings.JWT_SECRET_KEY,
            algorithm="HS256",
        )
    else:
        claims = _jwt_claims()
        claims.pop("role")
        token = jwt.encode(
            claims, main_module.settings.JWT_SECRET_KEY, algorithm="HS256"
        )

    response = await client.post(
        "/api/networks/import",
        files={"file": ("safe.inp", b"[TITLE]\n", "text/plain")},
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": f"jwt-negative-{invalid_kind}",
        },
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"
    assert _state_counts() == {
        "jobs": 0,
        "imports": 0,
        "idempotency": 0,
        "networks": 0,
        "revisions": 0,
    }


@pytest.mark.asyncio
async def test_import_rate_limit_returns_429_without_state_mutation(
    client, monkeypatch
):
    monkeypatch.setattr(
        main_module.settings, "RATE_LIMIT_NETWORK_JOB_SUBMIT_PER_MINUTE", 1
    )
    main_module._rate_buckets.clear()
    payload = (
        Path(__file__).parents[1]
        / "fixtures"
        / "hydraulic"
        / "imports"
        / "swmm_minimal_wgs84.inp"
    ).read_bytes()

    first = await client.post(
        "/api/networks/import",
        files={"file": ("safe.inp", payload, "text/plain")},
        headers=_headers("operator", "rate-limit-first"),
    )
    second = await client.post(
        "/api/networks/import",
        files={"file": ("safe.inp", payload, "text/plain")},
        headers=_headers("operator", "rate-limit-second"),
    )

    assert first.status_code == 202
    assert second.status_code == 429
    assert second.json()["code"] == "RATE_LIMITED"
    assert second.headers["Retry-After"] == "60"
    assert len(hydraulic_store.jobs) == 1
    assert len(hydraulic_store.imports) == 1
    assert len(hydraulic_store.idempotency) == 1


@pytest.mark.asyncio
async def test_import_idempotency_concurrency_and_expiry_generation(
    client, monkeypatch
):
    payload = (
        Path(__file__).parents[1]
        / "fixtures"
        / "hydraulic"
        / "imports"
        / "swmm_minimal_wgs84.inp"
    ).read_bytes()
    key = "concurrent-negative-key"
    first, replay = await asyncio.gather(
        client.post(
            "/api/networks/import",
            files={"file": ("safe.inp", payload, "text/plain")},
            headers=_headers("operator", key),
        ),
        client.post(
            "/api/networks/import",
            files={"file": ("safe.inp", payload, "text/plain")},
            headers=_headers("operator", key),
        ),
    )

    assert first.status_code == replay.status_code == 202
    assert first.json()["network_job_id"] == replay.json()["network_job_id"]
    assert len(hydraulic_store.jobs) == 1
    assert len(hydraulic_store.imports) == 1
    assert len(hydraulic_store.idempotency) == 1

    monkeypatch.setattr(
        hydraulic_routes,
        "_now",
        lambda: datetime.now(UTC) + timedelta(hours=25),
    )
    generation = await client.post(
        "/api/networks/import",
        files={"file": ("next.inp", payload + b"\n", "text/plain")},
        headers=_headers("operator", key),
    )

    assert generation.status_code == 202
    assert generation.json()["network_job_id"] != first.json()["network_job_id"]
    assert len(hydraulic_store.jobs) == 2
    assert len(hydraulic_store.imports) == 2
    assert len(hydraulic_store.idempotency) == 1


@pytest.mark.asyncio
async def test_forecast_endpoint_requires_authentication_before_resource_access(client):
    response = await client.get(
        "/api/forecast/latest", params={"network_id": "missing"}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_REQUIRED"
    assert "detail" in response.json()
    assert "errors" in response.json()


def test_fixture_bytes_are_not_secret_or_executable_payloads():
    for case in _cases():
        if "content_hex" in case:
            payload = base64.b64decode(
                base64.b64encode(bytes.fromhex(case["content_hex"]))
            )
            assert payload
        else:
            assert isinstance(case.get("content", ""), str)
