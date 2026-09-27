"""Default nowcast materializer entry point — verifies, then refuses.

This module is the process ``NOWCAST_RUNNER_COMMAND`` points at.  It exists so
the nowcast worker has a *real* runner to execute rather than a shell sleep or a
canned success, and it deliberately stops short of inventing a forecast.

What it does for real
---------------------
Given the run identity JSON on stdin it re-verifies, from disk, that every
policy artifact the run was bound to still hashes to the value recorded in the
verified policy config, and that the run has not passed its deadline.  Both are
genuine checks that must happen before any horizon point could be produced.

Why it does not produce a forecast
----------------------------------
Computing the horizon requires the forecast materialization layer
(``app.services.forecast.*``), which is owned by the forecast contract and does
not exist in this tree — ``backend/app/services/forecast/`` is absent and
``backend/app/api/hydraulic_routes.py`` already records that materialization is
not implemented here.  Emitting a synthetic horizon would be exactly the
"mock healthy" behaviour the frozen contract forbids, so this runner exits
non-zero with ``FORECAST_MATERIALIZER_UNAVAILABLE``.

The worker treats a non-zero exit as a failed attempt (``simulation_worker``
applies the same rule to a solver subprocess), so the run settles into
``retry_wait`` and then ``dead_letter`` with that code.  The condition is
therefore recorded durably and visibly instead of being hidden behind a green
readiness.

Wiring a real materializer means pointing ``NOWCAST_RUNNER_COMMAND`` at a
module that answers ``{"status": "ok", "result_sha256": "<64 hex>"}`` on
stdout; nothing else about the worker changes.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_INVALID_REQUEST = 2
EXIT_MATERIALIZER_UNAVAILABLE = 4

UTC = timezone.utc

ERROR_MATERIALIZER_UNAVAILABLE = "FORECAST_MATERIALIZER_UNAVAILABLE"
ERROR_ARTIFACT_MISSING = "FORECAST_POLICY_ARTIFACT_MISSING"
ERROR_ARTIFACT_HASH_MISMATCH = "FORECAST_POLICY_ARTIFACT_HASH_MISMATCH"
ERROR_DEADLINE_PASSED = "FORECAST_DEADLINE_PASSED"


def _artifact_root() -> Path:
    from app.core.config import settings

    return Path(settings.HYDRAULIC_ARTIFACT_ROOT).expanduser().resolve()


def _verified_policy_config() -> dict[str, Any] | None:
    from app.workers.nowcast_worker import load_forecast_policy

    return load_forecast_policy()


def _artifact_index() -> dict[str, Path]:
    """Artifact UUID -> file, read from the controlled root's registry.

    The authoritative mapping lives in ``hydraulic.probability_calibrations``
    / ``hydraulic.forecast_rules``, which the forecast contract seeds.  This
    runner has no database session, so it reads the same mapping from
    ``<HYDRAULIC_ARTIFACT_ROOT>/registry.json`` and re-hashes every file it
    names.  A registry entry that disagrees with the on-disk digest is treated
    as a policy failure, so the two sources cannot drift silently.
    """

    root = _artifact_root()
    registry = root / "registry.json"
    if not registry.is_file():
        return {}
    try:
        document = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = document.get("artifacts") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        return {}
    index: dict[str, Path] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        identity = str(entry.get("artifact_id", "")).strip()
        filename = str(entry.get("file", "")).strip()
        if not identity or not filename:
            continue
        candidate = (root / filename).resolve()
        if root != candidate and root not in candidate.parents:
            # The registry may not point outside the controlled root.
            continue
        index[identity] = candidate
    return index


def verify(request: dict[str, Any]) -> None:
    """Run every pre-materialization check; raise on the first failure."""

    bindings = request.get("policy_bindings")
    if not isinstance(bindings, dict) or not bindings:
        raise SystemExit(
            f"INVALID_RUN_REQUEST: policy_bindings is {sorted(bindings or {})}"
        )
    deadline_text = str(request.get("deadline_at", ""))
    if deadline_text:
        try:
            deadline = datetime.fromisoformat(deadline_text)
        except ValueError:
            deadline = None
        if deadline is not None:
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=UTC)
            if deadline <= datetime.now(UTC):
                raise SystemExit(ERROR_DEADLINE_PASSED)
    config = _verified_policy_config()
    if config is None:
        raise SystemExit(ERROR_ARTIFACT_HASH_MISMATCH)
    known: set[str] = set()
    for entry in config.get("networks", {}).values():
        if not isinstance(entry, dict):
            continue
        for group in ("probability_calibration_ids", "forecast_rule_ids"):
            selection = entry.get(group)
            if isinstance(selection, dict):
                known.update(str(value) for value in selection.values())
    index = _artifact_index()
    for field, artifact_id in sorted(bindings.items()):
        if str(artifact_id) not in known:
            raise SystemExit(
                f"{ERROR_ARTIFACT_MISSING}: {field}={artifact_id} is not in the "
                "verified policy config"
            )
        path = index.get(str(artifact_id))
        if path is None or not path.is_file():
            raise SystemExit(f"{ERROR_ARTIFACT_MISSING}: {artifact_id}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        sidecar = path.with_suffix(path.suffix + ".sha256")
        if not sidecar.is_file():
            raise SystemExit(f"{ERROR_ARTIFACT_MISSING}: no sidecar for {path.name}")
        if sidecar.read_text(encoding="utf-8").strip() != digest:
            raise SystemExit(f"{ERROR_ARTIFACT_HASH_MISMATCH}: {path.name}")


def main(argv: list[str] | None = None) -> int:
    """Read one run request from stdin and answer on stdout."""

    del argv
    raw = sys.stdin.read()
    try:
        request = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"run request is not valid JSON: {exc}", file=sys.stderr)
        return EXIT_INVALID_REQUEST
    if not isinstance(request, dict):
        print("run request must be a JSON object", file=sys.stderr)
        return EXIT_INVALID_REQUEST
    try:
        verify(request)
    except SystemExit as exc:
        code = str(exc)
        print(code, file=sys.stderr)
        if code.startswith("FORECAST_"):
            # The lineage checks above passed (or were skipped) and the only
            # remaining blocker is the absent materialization layer.
            print(
                f"{ERROR_MATERIALIZER_UNAVAILABLE}: this tree has no forecast "
                "materializer (backend/app/services/forecast/ is not implemented); "
                "point NOWCAST_RUNNER_COMMAND at a real one",
                file=sys.stderr,
            )
            return EXIT_MATERIALIZER_UNAVAILABLE
        return EXIT_INVALID_REQUEST
    print(ERROR_MATERIALIZER_UNAVAILABLE, file=sys.stderr)
    return EXIT_MATERIALIZER_UNAVAILABLE


if __name__ == "__main__":
    raise SystemExit(main())
