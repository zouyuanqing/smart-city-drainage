"""``python -m app.services.hydraulic.solvers.cli`` — the real solver process.

This is the command that ``SIMULATION_SOLVER_COMMAND`` points at.  The
simulation worker launches it once per attempt with ``shell=False``, with the
attempt workspace as the working directory and ``HYDRAULIC_SANDBOX_PATH`` /
``SIMULATION_ID`` / ``SIMULATION_ATTEMPT`` in its environment
(``backend/app/workers/simulation_worker.py:216-222``).  Exit code ``0`` is
the worker's only "result committed" signal, so this module is deliberately
strict about what may produce a zero.

I/O protocol
============
Everything is a file inside the attempt workspace, resolved in this order:
the ``--workspace`` argument, then ``HYDRAULIC_SANDBOX_PATH``, then the current
working directory.  The workspace must already exist; the solver never creates
or picks a path, so a client-supplied name can never escape the sandbox.

**Input** (read, 20 MiB cap) — ``solver_input.json``

==============  =========================================================
``schema``      exactly ``python-solver-input-v1``
``solver_id``   the frozen identity; must match ``--solver``
``run``         ``simulation_id``/``attempt_id``/``attempt_no``/
                ``fencing_token``/``network_id``/``network_revision_id``
``provenance``  the four independent content hashes plus the rainfall hash,
                the parameter/schema identities, the CRS transform, and
                ``ponding_enabled``
``time_grid``   ``start_at``, ``duration_seconds``,
                ``frame_interval_seconds``, ``expected_frame_count``
``solver_config`` numeric kernel configuration (see
                ``python_mw.SolverConfig``)
``network``     ``nodes``/``pipes``/``catchments`` keyed by stable key, plus
                the ``initial_state`` block (dry or warm)
``rainfall``    ``step_seconds`` + ``intensities_mm_h`` + its own
                ``content_sha256``
==============  =========================================================

**Output** (written, all inside the workspace)

============================  =============================================
``solver_progress.jsonl``     one ``{"percent","phase"}`` object per line,
                              monotonic, inside the §6.2 bands
``frames/frame-NNNNNN.json``  one canonical frame document per frame, written
                              in order as the solver produces them
``solver_quality.json``       mass ledger, stability, and the residual verdict
``solver_metrics.json``       hydraulic rollups for results/calibration/API
``solver_result.json``        the ``SolveSummary`` plus the frame manifest
``solver_error.json``         a structured error, written *only* on failure
============================  =============================================

Exit codes
==========
``0``   the run completed and ``solver_result.json`` was written
``2``   input rejected (missing/invalid/oversized ``solver_input.json``)
``3``   the requested solver identity is not available or not registered
``4``   the solve failed (numerical, regime, cancel, or deadline)
``5``   the run finished but the result is not publishable (quality gate,
        frame-count mismatch, sink failure)

Any non-zero exit writes ``solver_error.json`` and **no** ``solver_result.json``.
There is no path in this module that exits ``0`` without a complete, verified
result on disk: a no-op that merely returns is treated as a failure, because the
worker would record it as a completed attempt.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.hydraulic.solvers.base import (
    RunControl,
    SolverCancelledError,
    SolverContractError,
    SolverErrorCode,
    SolverInputError,
    SolverResultError,
    SolverTimeoutError,
    SolverUnavailableError,
    SolveSummary,
    canonical_json,
)
from app.services.hydraulic.solvers.python_mw import (
    PYTHON_FRAME_SCHEMA,
    load_solver_input_bytes,
    parse_solver_input,
)
from app.services.hydraulic.solvers.registry import describe, get_default_registry

UTC = timezone.utc

SOLVER_INPUT_FILENAME = "solver_input.json"
SOLVER_PROGRESS_FILENAME = "solver_progress.jsonl"
SOLVER_RESULT_FILENAME = "solver_result.json"
SOLVER_QUALITY_FILENAME = "solver_quality.json"
SOLVER_METRICS_FILENAME = "solver_metrics.json"
SOLVER_ERROR_FILENAME = "solver_error.json"
FRAMES_DIRNAME = "frames"
RESULT_SCHEMA = "solver-result-v1"
ERROR_SCHEMA = "solver-error-v1"
PROGRESS_SCHEMA = "solver-progress-v1"

EXIT_OK = 0
EXIT_INPUT_INVALID = 2
EXIT_SOLVER_UNAVAILABLE = 3
EXIT_EXECUTION_FAILED = 4
EXIT_RESULT_INVALID = 5

_USAGE_EXIT_CODE = EXIT_INPUT_INVALID


class WorkspaceSolverSink:
    """The attempt-scoped ``FrameSink`` that writes the protocol's files.

    Frames land in the workspace as they are produced, so a crashed or killed
    attempt leaves a truthful partial staging directory rather than an
    all-or-nothing claim.  The partial set is never published because
    ``solver_result.json`` — the only file that carries the frame manifest and
    summary — is written last, and only after every frame is on disk.
    """

    def __init__(self, workspace: Path, *, max_frame_bytes: int) -> None:
        self._workspace = workspace
        self._max_frame_bytes = max_frame_bytes
        self._frames_dir = workspace / FRAMES_DIRNAME
        self._frames_dir.mkdir(parents=True, exist_ok=True)
        self.frame_hashes: list[dict[str, Any]] = []
        self.finalized_summary: SolveSummary | None = None

    @property
    def frames_dir(self) -> Path:
        return self._frames_dir

    def frame_path(self, frame_index: int) -> Path:
        return self._frames_dir / f"frame-{frame_index:06d}.json"

    async def write(self, frame: Any) -> None:
        payload = _frame_payload_of(frame)
        index = int(_field(payload, "frame_index", "frame_index"))
        if index != len(self.frame_hashes):
            raise SolverResultError(
                f"frame {index} arrived out of order; expected "
                f"{len(self.frame_hashes)}"
            )
        # ``payload_sha256`` covers the canonical payload bytes only, matching
        # ``results.serialize_frame_payload`` (backend/app/services/hydraulic/
        # results.py:236) so the registration row and the staged file agree.
        encoded = canonical_json(payload).encode("utf-8")
        if len(encoded) > self._max_frame_bytes:
            raise SolverResultError(
                f"frame {index} is {len(encoded)} bytes; the frame limit is "
                f"{self._max_frame_bytes}"
            )
        digest = hashlib.sha256(encoded).hexdigest()
        declared = _attribute(frame, "payload_sha256")
        if declared is not None and declared != digest:
            raise SolverResultError(
                f"frame {index} payload hash {digest} does not match the hash "
                f"the solver declared ({declared})"
            )
        path = self.frame_path(index)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_bytes(encoded)
        temporary.replace(path)
        self.frame_hashes.append(
            {
                "frame_index": index,
                "payload_sha256": digest,
                "payload_bytes": len(encoded),
                "file": f"{FRAMES_DIRNAME}/{path.name}",
            }
        )

    async def finalize(self, summary: SolveSummary) -> None:
        if summary.frame_count != len(self.frame_hashes):
            raise SolverResultError(
                f"summary claims {summary.frame_count} frames but "
                f"{len(self.frame_hashes)} were written"
            )
        self.finalized_summary = summary


def _attribute(frame: Any, name: str) -> Any:
    if isinstance(frame, Mapping):
        return frame.get(name)
    return getattr(frame, name, None)


def _field(payload: Mapping[str, Any], name: str, label: str) -> Any:
    if name not in payload:
        raise SolverResultError(f"frame payload has no {label}")
    return payload[name]


def _frame_payload_of(frame: Any) -> Mapping[str, Any]:
    """Extract the canonical frame payload from a DTO or a raw mapping."""

    if isinstance(frame, Mapping):
        if "payload" in frame and isinstance(frame["payload"], Mapping):
            return dict(frame["payload"])
        return dict(frame)
    payload = getattr(frame, "payload", None)
    if isinstance(payload, Mapping):
        return dict(payload)
    to_dict = getattr(frame, "to_dict", None)
    if callable(to_dict):
        document = dict(to_dict())
        nested = document.get("payload")
        if isinstance(nested, Mapping):
            return dict(nested)
        return document
    raise SolverResultError("solver frame is neither a mapping nor a DTO")


class ProgressLogWriter:
    """Appends the §6.2 progress events to ``solver_progress.jsonl``."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._last = -1
        self.events: list[dict[str, Any]] = []

    def __call__(self, percent: int, phase: str) -> None:
        if percent <= self._last:
            return
        self._last = percent
        event = {
            "schema": PROGRESS_SCHEMA,
            "percent": int(percent),
            "phase": str(phase),
            "at": _now_text(),
        }
        self.events.append(event)
        with self._path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(event) + "\n")

    @property
    def last_percent(self) -> int:
        return self._last


@dataclass(frozen=True, slots=True)
class CliOptions:
    workspace: Path
    solver_id: str | None
    input_path: Path | None
    max_frame_bytes: int
    timeout_seconds: float | None
    probe_only: bool
    list_solvers: bool
    emit_frames: bool


def resolve_workspace(explicit: str | None, environ: Mapping[str, str]) -> Path:
    """Resolve the attempt workspace, failing closed when it is unusable."""

    candidate = explicit or environ.get("HYDRAULIC_SANDBOX_PATH") or os.getcwd()
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve(strict=False)
    else:
        path = path.resolve(strict=False)
    if not path.is_dir():
        raise SolverInputError(
            f"solver workspace does not exist: {path}; the solver never creates "
            "its own output directory"
        )
    if not os.access(path, os.W_OK):
        raise SolverInputError(f"solver workspace is not writable: {path}")
    return path


def _now_text() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, document: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(canonical_json(document) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.services.hydraulic.solvers.cli",
        description=(
            "Run one pure-Python hydraulic solve inside a pre-created attempt "
            "workspace. Reads solver_input.json, writes frames/quality/metrics/"
            "result, and exits non-zero on every failure."
        ),
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help=(
            "Attempt workspace. Defaults to $HYDRAULIC_SANDBOX_PATH, then the "
            "current working directory."
        ),
    )
    parser.add_argument(
        "--input",
        default=None,
        help=f"Input document (default: <workspace>/{SOLVER_INPUT_FILENAME}).",
    )
    parser.add_argument(
        "--solver",
        default=None,
        help=(
            "Optional constraint on the frozen solver identity. When omitted the "
            "identity comes from solver_input.json; when given it must agree "
            "with the input, and a disagreement is an input error."
        ),
    )
    parser.add_argument(
        "--max-frame-bytes",
        type=int,
        default=64 * 1024 * 1024,
        help="Hard per-frame payload cap; default 64 MiB (contract §6.5).",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        help="Wall-clock cap for the solve; the default is no local cap.",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Probe the requested identity and print the result, then exit.",
    )
    parser.add_argument(
        "--list-solvers",
        action="store_true",
        help=(
            "Print the registry snapshot (registered identities, EPA status, "
            "capabilities) and exit."
        ),
    )
    return parser


def _load_input(path: Path) -> Any:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise SolverInputError(
            f"cannot read solver input {path.name}: {type(exc).__name__}"
        ) from exc
    return load_solver_input_bytes(payload)


async def _run(options: CliOptions, environ: Mapping[str, str]) -> int:
    registry = get_default_registry()
    if options.probe_only:
        return await _probe_requested(registry, options.solver_id)

    input_path = options.input_path or (options.workspace / SOLVER_INPUT_FILENAME)
    document = _load_input(input_path)
    # ``simulations.solver_id`` is frozen at submission time, so the input
    # document — not the argv — is the identity of record.  ``--solver`` is an
    # optional constraint that must agree with it; a disagreement is an input
    # error, never a silent switch to whatever the argv happened to name.
    solver_id = str(document.get("solver_id") or options.solver_id or "").strip()
    if not solver_id:
        raise SolverInputError(
            "solver_input.solver_id is missing and no --solver was given"
        )
    if options.solver_id and options.solver_id != solver_id:
        raise SolverInputError(
            f"--solver {options.solver_id!r} contradicts the frozen input "
            f"identity {solver_id!r}"
        )
    if solver_id not in registry:
        raise SolverUnavailableError(
            f"solver {solver_id!r} is not registered; available: "
            f"{list(registry.solver_ids)}",
            solver_id=solver_id,
            reason="not_registered",
        )
    solver = registry.get(solver_id)
    probe = await solver.probe()
    if not probe.available:
        raise SolverUnavailableError(
            f"solver {solver_id!r} is unavailable: {probe.reason}",
            solver_id=solver_id,
            reason=probe.reason,
        )
    request = parse_solver_input(
        document,
        solver_id=solver.solver_id,
        solver_version=solver.version,
        routing_mode=solver.routing_mode,
    )

    progress = ProgressLogWriter(options.workspace / SOLVER_PROGRESS_FILENAME)
    sink = WorkspaceSolverSink(
        options.workspace, max_frame_bytes=options.max_frame_bytes
    )
    started = time.perf_counter()
    control = RunControl(
        progress_cb=progress,
        workspace_root=options.workspace,
        max_substeps_per_report=int(request.config.max_substeps),
        timeout_seconds=options.timeout_seconds,
    )
    try:
        summary = await asyncio.wait_for(
            solver.run(request, request.network, sink, control),
            timeout=options.timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise SolverTimeoutError(
            f"{solver.solver_id} exceeded the local {options.timeout_seconds} s "
            "wall-clock cap"
        ) from exc

    _publish(options, sink, summary, progress, started, request)
    return EXIT_OK


async def _probe_requested(registry: Any, solver_id: str | None) -> int:
    """Probe one identity, or every registered identity when unspecified."""

    if solver_id is None:
        probes = [await registry.probe(item) for item in registry.solver_ids]
        document = {
            "schema": "solver-probe-v1",
            "probes": [probe.to_dict() for probe in probes],
        }
    else:
        if solver_id not in registry:
            raise SolverUnavailableError(
                f"solver {solver_id!r} is not registered; available: "
                f"{list(registry.solver_ids)}",
                solver_id=solver_id,
                reason="not_registered",
            )
        probe = await registry.probe(solver_id)
        document = {
            "schema": "solver-probe-v1",
            "probe": probe.to_dict(),
        }
    print(json.dumps(document, sort_keys=True))
    return EXIT_OK


def _publish(
    options: CliOptions,
    sink: WorkspaceSolverSink,
    summary: SolveSummary,
    progress: ProgressLogWriter,
    started: float,
    request: Any,
) -> None:
    """Write the publishable artefacts; the last write is the result file."""

    diagnostics = dict(summary.diagnostics)
    quality = diagnostics.get("quality")
    metrics = diagnostics.get("metrics")
    if not isinstance(quality, Mapping) or not isinstance(metrics, Mapping):
        raise SolverResultError(
            "solver summary does not carry the quality and metrics documents"
        )
    if len(sink.frame_hashes) != summary.frame_count:
        raise SolverResultError(
            f"{summary.frame_count} frames were summarised but "
            f"{len(sink.frame_hashes)} exist on disk"
        )
    manifest = diagnostics.get("frame_manifest")
    if not isinstance(manifest, Sequence) or len(manifest) != summary.frame_count:
        raise SolverResultError(
            "solver summary frame manifest does not match the frame count"
        )
    for entry, written in zip(manifest, sink.frame_hashes):
        if entry.get("payload_sha256") != written["payload_sha256"]:
            raise SolverResultError(
                f"frame {written['frame_index']} hash differs between the "
                "summary manifest and the written frame"
            )
    if progress.last_percent < 99:
        progress(99, "persisting")

    _write_json(options.workspace / SOLVER_QUALITY_FILENAME, dict(quality))
    _write_json(options.workspace / SOLVER_METRICS_FILENAME, dict(metrics))
    # The frozen input is the identity of record; the worker's environment
    # variables are only cross-checked, never preferred.
    run = dict(getattr(request, "run", {}) or {})
    result = {
        "schema": RESULT_SCHEMA,
        "simulation_id": str(run.get("simulation_id", "")),
        "attempt_id": str(run.get("attempt_id", "")),
        "attempt_no": int(run.get("attempt_no", 0) or 0),
        "fencing_token": int(run.get("fencing_token", 0) or 0),
        "network_id": str(run.get("network_id", "")),
        "network_revision_id": str(run.get("network_revision_id", "")),
        "input_sha256": getattr(request, "input_sha256", lambda: "")(),
        "frame_schema": PYTHON_FRAME_SCHEMA,
        "quality_schema": str(quality.get("schema", "")),
        "metrics_schema": str(metrics.get("schema", "")),
        "progress_events": len(progress.events),
        "wall_time_seconds": round(time.perf_counter() - started, 6),
        "frames": list(sink.frame_hashes),
        "summary": summary.to_dict(),
    }
    _write_json(options.workspace / SOLVER_RESULT_FILENAME, result)


def _write_error(
    workspace: Path | None,
    exc: BaseException,
    *,
    solver_id: str,
) -> None:
    if workspace is None:
        return
    code = getattr(exc, "code", None) or SolverErrorCode.SOLVER_EXECUTION_FAILED.value
    # The failing identity is the one the error names; the argv label is only a
    # fallback for failures raised before an identity was resolved.
    identity = getattr(exc, "solver_id", None) or solver_id
    document = {
        "schema": ERROR_SCHEMA,
        "solver_id": str(identity or ""),
        "error_code": str(code),
        "error_type": type(exc).__name__,
        "message": str(getattr(exc, "message", exc)),
        "retryable": bool(getattr(exc, "retryable", False)),
        "details": dict(getattr(exc, "details", {}) or {}),
        "at": _now_text(),
    }
    try:
        _write_json(workspace / SOLVER_ERROR_FILENAME, document)
    except OSError:
        # The workspace may already have been removed by a cancel path; the
        # non-zero exit code remains the authoritative signal.
        print("solver-error.json could not be written", file=sys.stderr)


def _exit_code_for(exc: BaseException) -> int:
    if isinstance(exc, SolverUnavailableError):
        return EXIT_SOLVER_UNAVAILABLE
    if isinstance(exc, (SolverInputError, json.JSONDecodeError, ValueError)):
        return EXIT_INPUT_INVALID
    if isinstance(exc, SolverContractError):
        if str(getattr(exc, "code", "")) == SolverErrorCode.SOLVER_RESULT_INVALID.value:
            return EXIT_RESULT_INVALID
        return EXIT_EXECUTION_FAILED
    if isinstance(exc, (SolverCancelledError, SolverTimeoutError)):
        return EXIT_EXECUTION_FAILED
    return EXIT_EXECUTION_FAILED


async def amain(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    environ = os.environ
    workspace: Path | None = None
    # ``None`` means "take the identity from the frozen input"; the empty
    # string is only ever used as the label on an error written before the
    # input could be read.
    requested_solver = args.solver or None
    solver_id = str(requested_solver or "")
    try:
        if args.list_solvers:
            print(json.dumps(describe(), sort_keys=True))
            return EXIT_OK
        if args.probe and not requested_solver:
            workspace = None
        if args.max_frame_bytes <= 0:
            raise SolverInputError("--max-frame-bytes must be positive")
        if args.timeout_seconds is not None and args.timeout_seconds <= 0:
            raise SolverInputError("--timeout-seconds must be positive")
        workspace = resolve_workspace(args.workspace, environ)
        options = CliOptions(
            workspace=workspace,
            solver_id=requested_solver,
            input_path=Path(args.input) if args.input else None,
            max_frame_bytes=int(args.max_frame_bytes),
            timeout_seconds=args.timeout_seconds,
            probe_only=bool(args.probe),
            list_solvers=bool(args.list_solvers),
            emit_frames=True,
        )
        # A previous run may have left a result file; remove it up front so a
        # later crash cannot leave a stale "success" on disk.
        (workspace / SOLVER_RESULT_FILENAME).unlink(missing_ok=True)
        (workspace / SOLVER_ERROR_FILENAME).unlink(missing_ok=True)
        return await _run(options, environ)
    except SystemExit as exc:  # argparse rejected the arguments
        return int(exc.code or _USAGE_EXIT_CODE)
    except BaseException as exc:  # noqa: BLE001 - the exit code is the contract
        if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
            _write_error(workspace, exc, solver_id=solver_id)
            return EXIT_EXECUTION_FAILED
        _write_error(workspace, exc, solver_id=solver_id)
        print(
            f"solver failed solver_id={solver_id} "
            f"error_code={getattr(exc, 'code', type(exc).__name__)}: {exc}",
            file=sys.stderr,
        )
        return _exit_code_for(exc)


def main(argv: Sequence[str] | None = None) -> int:
    """Process entry point for ``python -m app.services.hydraulic.solvers.cli``."""

    try:
        return asyncio.run(amain(argv))
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        return EXIT_EXECUTION_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
