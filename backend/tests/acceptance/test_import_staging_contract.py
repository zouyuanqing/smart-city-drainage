"""The API-created import job must satisfy the worker's staging contract.

``NetworkJobWorker.read_staged_input`` is the only thing that turns a queued
job into work: it reads ``${HYDRAULIC_IMPORT_ROOT}/<job_id><suffix>`` and
fails the attempt closed unless ``sha256(file_bytes) == request_fingerprint``.

Before these tests the API minted that fingerprint from an idempotency
envelope and never wrote the file, so *every* API-created import job was
guaranteed to die with STAGED_INPUT_MISSING.  These tests exercise the real
API -> staging -> worker path rather than asserting that a job is "queued".
"""

from __future__ import annotations

import hashlib
import sys
import uuid
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.api.hydraulic_routes import (  # noqa: E402
    hydraulic_store,
    reset_hydraulic_api_store,
)
from app.core.config import settings  # noqa: E402
from app.workers import network_job_worker  # noqa: E402

FIXTURE_ROOT = BACKEND / "tests" / "fixtures" / "hydraulic" / "imports"
INP_FIXTURE = FIXTURE_ROOT / "swmm_minimal_wgs84.inp"
MAX_BYTES = 1 << 20


def _headers(role: str = "operator", key: str | None = None) -> dict[str, str]:
    from app.core.security import create_access_token

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


@pytest.fixture
def staging_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point HYDRAULIC_IMPORT_ROOT at a per-test directory."""

    root = tmp_path / "hydraulic-import"
    monkeypatch.setattr(settings, "HYDRAULIC_IMPORT_ROOT", str(root))
    reset_hydraulic_api_store()
    yield root
    reset_hydraulic_api_store()


def _claim(job_id: str, fingerprint: str, job_type: str = "import"):
    return network_job_worker.NetworkJobClaim(
        job_id=uuid.UUID(job_id),
        job_type=job_type,
        attempt_no=1,
        fencing_token=1,
        lease_token=job_id,
        lease_until=None,
        request_fingerprint=fingerprint,
        network_id=None,
        created_by=None,
    )


@pytest.mark.asyncio
async def test_an_api_import_job_is_readable_by_the_worker(client, staging_root):
    """The central AC-1 defect: the job the API returns is claimable."""

    payload = INP_FIXTURE.read_bytes()
    response = await client.post(
        "/api/networks/import",
        files={"file": (INP_FIXTURE.name, payload, "text/plain")},
        headers=_headers("operator", "ac1-staging-key"),
    )
    assert response.status_code == 202, response.text
    body = response.json()
    job_id = body["network_job_id"]

    # The worker must be able to find the staged file ...
    staged = network_job_worker.staged_input_path(
        staging_root, uuid.UUID(job_id), "import"
    )
    assert staged.is_file(), f"API did not stage the payload at {staged}"
    assert staged.read_bytes() == payload

    # ... and the job fingerprint must be the bytes' own hash, so
    # read_staged_input accepts it rather than failing the attempt closed.
    fingerprint = hydraulic_store.jobs[job_id]["request_fingerprint"]
    assert fingerprint == hashlib.sha256(payload).hexdigest()
    claim = _claim(job_id, fingerprint)
    read_back = network_job_worker.read_staged_input(
        staging_root, claim, max_bytes=MAX_BYTES
    )
    assert read_back == payload


@pytest.mark.asyncio
async def test_a_staged_job_yields_the_parser_counts_the_api_reported(
    client, staging_root
):
    """AC-1 F4: one authoritative counts definition, API and worker agree."""

    payload = INP_FIXTURE.read_bytes()
    response = await client.post(
        "/api/networks/import",
        files={"file": (INP_FIXTURE.name, payload, "text/plain")},
        headers=_headers("operator", "ac1-counts-key"),
    )
    assert response.status_code == 202, response.text
    body = response.json()
    job_id = body["network_job_id"]

    api_counts = hydraulic_store.jobs[job_id]["preflight"]["counts"]
    outcome = network_job_worker.compute_outcome(
        _claim(
            job_id,
            hydraulic_store.jobs[job_id]["request_fingerprint"],
        ),
        payload,
    )
    assert outcome.kind == "import"
    assert outcome.counts == api_counts
    # Guards the regression this replaced: the worker used to publish a
    # three-key subset while the API published all seven.
    assert set(outcome.counts) == set(api_counts)
    assert len(api_counts) > 3


@pytest.mark.asyncio
async def test_staging_is_per_job_and_never_shared(client, staging_root):
    """Two imports of the same bytes must not overwrite one another."""

    payload = INP_FIXTURE.read_bytes()
    job_ids = []
    for index in (1, 2):
        response = await client.post(
            "/api/networks/import",
            files={"file": (INP_FIXTURE.name, payload, "text/plain")},
            headers=_headers("operator", f"ac1-two-jobs-{index}"),
        )
        assert response.status_code == 202, response.text
        job_ids.append(response.json()["network_job_id"])

    assert len(set(job_ids)) == 2
    for job_id in job_ids:
        staged = network_job_worker.staged_input_path(
            staging_root, uuid.UUID(job_id), "import"
        )
        assert staged.is_file()
        assert staged.read_bytes() == payload


@pytest.mark.asyncio
async def test_a_mismatched_staging_file_fails_the_attempt_closed(client, staging_root):
    """Identity is enforced: swapping the bytes must not be accepted."""

    payload = INP_FIXTURE.read_bytes()
    response = await client.post(
        "/api/networks/import",
        files={"file": (INP_FIXTURE.name, payload, "text/plain")},
        headers=_headers("operator", "ac1-tamper-key"),
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["network_job_id"]

    staged = network_job_worker.staged_input_path(
        staging_root, uuid.UUID(job_id), "import"
    )
    staged.write_bytes(b"[SECTIONS]\n")

    with pytest.raises(network_job_worker.NetworkJobError) as caught:
        network_job_worker.read_staged_input(
            staging_root,
            _claim(job_id, hydraulic_store.jobs[job_id]["request_fingerprint"]),
            max_bytes=MAX_BYTES,
        )
    assert caught.value.error_code == "STAGED_INPUT_HASH_MISMATCH"
    assert not caught.value.retryable
