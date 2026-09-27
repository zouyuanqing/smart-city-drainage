"""Optional, process-isolated EPA SWMM adapter.

The module never imports an EPA binding and has no import-time filesystem work.
A configured, SHA-256-pinned executable is optional: probing an absent engine
returns a structured unavailable result so a registry can freeze the Python
fallback before a simulation starts.  Once selected, this adapter never changes
solver identity during a run.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import math
import os
import re
import signal
import struct
import subprocess
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

from app.services.hydraulic.sandbox import ProcessTreeJob
from app.services.hydraulic.solvers.base import (
    FrameSink,
    RunControl,
    SimulationFrame,
    SolverCancelledError,
    SolverCapabilities,
    SolverContractError,
    SolverDegradationReport,
    SolveRequest,
    SolverExecutionError,
    SolverInputError,
    SolverInputManifest,
    SolverProbe,
    SolverResultError,
    SolverResultStatus,
    SolverTimeoutError,
    SolverUnavailableError,
    SolverValidationError,
    SolverVersionMismatchError,
    SolveSummary,
    ValidationIssue,
    ValidationReport,
)

UTC = timezone.utc
SWMM_ADAPTER_VERSION = "swmm-adapter-v1"
SWMM_INPUT_MAX_BYTES = 20 * 1024 * 1024
SWMM_DEFAULT_TIMEOUT_SECONDS = 600.0
SWMM_ROUTING_MODES = {
    "swmm_kw": "kinematic_wave",
    "swmm_dw": "dynamic_wave",
}
_SWMM_SOLVER_IDS = frozenset(SWMM_ROUTING_MODES)
_PROCESS_POLL_SECONDS = 0.5
_PROCESS_KILL_TIMEOUT_SECONDS = 5.0
_SWMM_VERSION_PATTERN = re.compile(
    r"^\d+(\.\d+){1,3}([.-]?(alpha|beta|rc)\d*)?$",
)


@dataclass(frozen=True, slots=True)
class SwmmProcessRequest:
    """Exact process request exposed to an injected runner for verification."""

    argv: tuple[str, ...]
    cwd: Path
    shell: bool = False

    def __post_init__(self) -> None:
        if len(self.argv) < 2 or not all(self.argv):
            raise ValueError("SWMM argv must contain non-empty entries")
        if self.shell:
            raise ValueError("SWMM adapter requires shell=False")
        object.__setattr__(self, "cwd", Path(self.cwd).resolve(strict=False))


@runtime_checkable
class SwmmProcess(Protocol):
    """Process handle used by the adapter monitor."""

    pid: int

    async def wait(self) -> int:
        """Return the engine exit code."""

    async def kill_process_tree(self) -> None:
        """Terminate the engine and every descendant."""


@runtime_checkable
class SwmmProcessRunner(Protocol):
    async def start(self, request: SwmmProcessRequest) -> SwmmProcess:
        """Start one pinned engine invocation with shell disabled."""


class SubprocessSwmmProcess:
    """``subprocess.Popen`` handle with cross-platform process-tree cleanup."""

    def __init__(
        self,
        process: subprocess.Popen[Any],
        *,
        job: ProcessTreeJob | None = None,
    ) -> None:
        self._process = process
        self._job = job

    @property
    def pid(self) -> int:
        return int(self._process.pid)

    async def wait(self) -> int:
        return await asyncio.to_thread(self._process.wait)

    async def kill_process_tree(self) -> None:
        job = self._job
        try:
            if job is not None:
                job.terminate()
            if os.name == "nt":
                try:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(
                            subprocess.run,
                            ["taskkill", "/PID", str(self._process.pid), "/T", "/F"],
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                            shell=False,
                        ),
                        timeout=_PROCESS_KILL_TIMEOUT_SECONDS,
                    )
                except (OSError, TimeoutError):
                    result = None
                if result is None or result.returncode != 0:
                    await asyncio.to_thread(self._process.kill)
                return
            killpg = getattr(os, "killpg", None)
            sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
            if killpg is not None:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    killpg(self._process.pid, sigkill)
            kill = getattr(os, "kill", None)
            if kill is not None:
                with contextlib.suppress(ProcessLookupError):
                    kill(self._process.pid, sigkill)
        finally:
            if job is not None:
                job.close()


class SubprocessSwmmProcessRunner:
    """Start SWMM from an attempt-local workspace with fixed file names."""

    async def start(self, request: SwmmProcessRequest) -> SubprocessSwmmProcess:
        stdout_path = request.cwd / "swmm.stdout.log"
        stderr_path = request.cwd / "swmm.stderr.log"
        job = ProcessTreeJob.create()
        stdout = stdout_path.open("xb")
        stderr = stderr_path.open("xb")
        options: dict[str, Any] = {
            "cwd": request.cwd,
            "stdin": subprocess.DEVNULL,
            "stdout": stdout,
            "stderr": stderr,
            "shell": False,
        }
        if os.name == "nt":
            options["creationflags"] = getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0,
            )
        else:
            options["start_new_session"] = True
        try:
            popen = partial(subprocess.Popen, list(request.argv), **options)
            process = cast(
                subprocess.Popen[Any],
                await asyncio.to_thread(popen),
            )
            job.assign(process)
        except Exception:
            with contextlib.suppress(Exception):
                if "process" in locals():
                    process.kill()
            with contextlib.suppress(Exception):
                job.close()
            raise
        finally:
            stdout.close()
            stderr.close()
        return SubprocessSwmmProcess(process, job=job)


@dataclass(frozen=True, slots=True)
class SwmmParseContext:
    """Sandbox-local outputs and immutable result identity for one SWMM run."""

    simulation_id: Any
    attempt_id: Any
    input_path: Path
    report_path: Path
    output_path: Path
    solver_id: str
    solver_version: str
    engine_binary_sha256: str
    input_sha256: str
    network_content_hash: str
    snapshot_content_hash: str
    initial_state_hash: str
    target_manifest_sha256: str
    rainfall_content_hash: str
    solver_config_hash: str
    first_frame_at: datetime
    frame_interval_seconds: int
    expected_frame_count: int


@runtime_checkable
class SwmmOutputParser(Protocol):
    """Streaming parser bound to one verified engine version."""

    parser_version: str
    engine_version: str

    def parse(
        self,
        context: SwmmParseContext,
    ) -> AsyncIterator[SimulationFrame | Mapping[str, Any]] | Any:
        """Return an async iterator of frame DTOs or frame mappings."""


class Swmm5OutputParser:
    """Streaming EPA SWMM 5.1/5.2 binary-output parser.

    The parser reads one reporting period at a time and emits the common
    solver frame schema.  It never materializes the complete time series.
    Parser and engine versions are exact bindings; a different binary layout
    requires a new parser identity.
    """

    parser_version = "swmm5-out-parser-v1"
    _MAGIC = 516_114_522
    _FLOW_UNITS = {
        0: "CFS",
        1: "GPM",
        2: "MGD",
        3: "CMS",
        4: "LPS",
        5: "MLD",
    }
    _FLOW_TO_M3S = {
        0: 0.028316846592,
        1: 0.0000630901964,
        2: 0.3785411764,
        3: 1.0,
        4: 0.001,
        5: 1.1574074074 / 86_400.0,
    }
    _SUBCATCHMENT_VARIABLES = {
        0: "rainfall_mm_h",
        1: "snow_depth_m",
        2: "losses_m3s",
        3: "runoff_m3s",
        4: "groundwater_flow_m3s",
        5: "groundwater_elevation_m",
    }
    _NODE_VARIABLES = {
        0: "depth_m",
        1: "head_m",
        2: "volume_m3",
        3: "lateral_inflow_m3s",
        4: "total_inflow_m3s",
        5: "flooding_m3s",
    }
    _LINK_VARIABLES = {
        0: "flow_m3s",
        1: "depth_m",
        2: "velocity_mps",
        3: "froude_number",
        4: "capacity_ratio",
    }
    _SYSTEM_VARIABLES = {
        0: "air_temperature_c",
        1: "rainfall_mm_h",
        2: "snow_depth_m",
        3: "losses_m3s",
        4: "runoff_m3s",
        5: "dry_weather_inflow_m3s",
        6: "groundwater_inflow_m3s",
        7: "rdii_inflow_m3s",
        8: "direct_inflow_m3s",
        9: "total_inflow_m3s",
        10: "flooding_m3s",
        11: "outflow_m3s",
        12: "storage_m3",
        13: "evaporation_m3s",
        14: "potential_evaporation_m3s",
    }

    def __init__(self, engine_version: str) -> None:
        version = str(engine_version).strip()
        if _SWMM_VERSION_PATTERN.fullmatch(version) is None or not version.startswith(
            ("5.1.", "5.2.")
        ):
            raise ValueError("SWMM5 output parser supports exact engine 5.1.x/5.2.x")
        self.engine_version = version
        self._engine_version_code = (
            int(version.split(".")[0]) * 10_000
            + int(version.split(".")[1]) * 100
            + int(version.split(".")[2])
        )
        self.parser_version = f"{self.parser_version}:{version}"

    @staticmethod
    def _read_exact(stream: Any, size: int) -> bytes:
        data = stream.read(size)
        if len(data) != size:
            raise ValueError("truncated SWMM binary output")
        return cast(bytes, data)

    @classmethod
    def _i(cls, stream: Any) -> int:
        return int(struct.unpack("<i", cls._read_exact(stream, 4))[0])

    @classmethod
    def _f(cls, stream: Any) -> float:
        value = float(struct.unpack("<f", cls._read_exact(stream, 4))[0])
        if not math.isfinite(value):
            raise ValueError("SWMM output contains a non-finite value")
        return value

    @classmethod
    def _d(cls, stream: Any) -> float:
        value = float(struct.unpack("<d", cls._read_exact(stream, 8))[0])
        if not math.isfinite(value):
            raise ValueError("SWMM output contains a non-finite date")
        return value

    @classmethod
    def _names(cls, stream: Any, count: int) -> tuple[str, ...]:
        names: list[str] = []
        for _ in range(count):
            size = cls._i(stream)
            if size < 0 or size > 1024:
                raise ValueError("invalid SWMM object-name length")
            raw = cls._read_exact(stream, size)
            try:
                name = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("SWMM object name is not UTF-8") from exc
            if not name or "\x00" in name:
                raise ValueError("SWMM object name is empty or contains NUL")
            names.append(name)
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError("duplicate SWMM object name")
        return tuple(names)

    @classmethod
    def _skip_properties(cls, stream: Any, object_count: int) -> None:
        property_count = cls._i(stream)
        if property_count < 0 or property_count > 64:
            raise ValueError("invalid SWMM property count")
        property_codes = [cls._i(stream) for _ in range(property_count)]
        if any(code < 0 for code in property_codes):
            raise ValueError("invalid SWMM property code")
        for _ in range(object_count * property_count):
            cls._read_exact(stream, 4)

    @classmethod
    def _variable_codes(cls, stream: Any) -> dict[str, tuple[int, ...]]:
        result: dict[str, tuple[int, ...]] = {}
        for group in ("subcatchments", "nodes", "links", "system"):
            count = cls._i(stream)
            if count < 0 or count > 256:
                raise ValueError("invalid SWMM reporting-variable count")
            codes = tuple(cls._i(stream) for _ in range(count))
            if len(set(codes)) != len(codes):
                raise ValueError("duplicate SWMM reporting-variable code")
            result[group] = codes
        return result

    @classmethod
    def _validate_report(cls, path: Path) -> None:
        if not path.is_file() or path.is_symlink():
            raise ValueError("SWMM report file is missing or unsafe")
        found_content = False
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                found_content = found_content or bool(chunk.strip())
                if re.search(rb"\bERROR\b", chunk, flags=re.IGNORECASE):
                    raise ValueError("SWMM report contains an engine error")
        if not found_content:
            raise ValueError("SWMM report is empty")

    @classmethod
    def _read_layout(cls, path: Path) -> dict[str, Any]:
        file_size = path.stat().st_size
        if file_size < 64:
            raise ValueError("SWMM binary output is too short")
        with path.open("rb") as stream:
            stream.seek(0)
            if cls._i(stream) != cls._MAGIC:
                raise ValueError("invalid SWMM binary-output magic number")
            version_code = cls._i(stream)
            flow_code = cls._i(stream)
            if flow_code not in cls._FLOW_UNITS:
                raise ValueError("unsupported SWMM flow-unit code")
            subcatchment_count = cls._i(stream)
            node_count = cls._i(stream)
            link_count = cls._i(stream)
            pollutant_count = cls._i(stream)
            counts = (subcatchment_count, node_count, link_count, pollutant_count)
            if any(count < 0 or count > 1_000_000 for count in counts):
                raise ValueError("SWMM object count is outside the supported bound")
            stream.seek(-24, 2)
            names_position, properties_position, results_position, period_count = (
                cls._i(stream),
                cls._i(stream),
                cls._i(stream),
                cls._i(stream),
            )
            error_code = cls._i(stream)
            closing_magic = cls._i(stream)
            if closing_magic != cls._MAGIC or error_code != 0:
                raise ValueError("SWMM output is incomplete or reports an error")
            if not (
                0 < names_position < properties_position < results_position < file_size
            ):
                raise ValueError("SWMM output section offsets are invalid")
            stream.seek(names_position)
            names = {
                "subcatchments": cls._names(stream, subcatchment_count),
                "nodes": cls._names(stream, node_count),
                "links": cls._names(stream, link_count),
                "pollutants": cls._names(stream, pollutant_count),
            }
            for _ in names["pollutants"]:
                unit_code = cls._i(stream)
                if unit_code < 0 or unit_code > 2:
                    raise ValueError("unsupported SWMM pollutant-unit code")
            cls._skip_properties(stream, subcatchment_count)
            cls._skip_properties(stream, node_count)
            cls._skip_properties(stream, link_count)
            variable_codes = cls._variable_codes(stream)
            excel_start = cls._d(stream)
            report_step = cls._i(stream)
            expected_codes = {
                "subcatchments": set(cls._SUBCATCHMENT_VARIABLES),
                "nodes": set(cls._NODE_VARIABLES),
                "links": set(cls._LINK_VARIABLES),
                "system": set(range(14)),
            }
            for group, required in expected_codes.items():
                if not required.issubset(variable_codes[group]):
                    raise ValueError(f"SWMM output omits required {group} variables")
            values_per_period = (
                subcatchment_count * len(variable_codes["subcatchments"])
                + node_count * len(variable_codes["nodes"])
                + link_count * len(variable_codes["links"])
                + len(variable_codes["system"])
            )
            record_size = 8 + values_per_period * 4
            expected_size = results_position + period_count * record_size + 24
            if period_count < 0 or expected_size != file_size:
                raise ValueError("SWMM output period count does not match file size")
        return {
            "version_code": version_code,
            "flow_code": flow_code,
            "flow_unit": cls._FLOW_UNITS[flow_code],
            "names": names,
            "variable_codes": variable_codes,
            "excel_start": excel_start,
            "report_step": report_step,
            "period_count": period_count,
            "results_position": results_position,
            "record_size": record_size,
        }

    def _read_layout_bound(self, path: Path) -> dict[str, Any]:
        layout = self._read_layout(path)
        if layout["version_code"] != self._engine_version_code:
            raise ValueError(
                "SWMM binary-output version does not match the bound parser"
            )
        return layout

    def _values_by_code(
        self,
        raw: Sequence[float],
        codes: Sequence[int],
        names: Mapping[int, str],
    ) -> dict[str, float]:
        return {names[code]: raw[index] for index, code in enumerate(codes)}

    def _convert_frame(
        self,
        raw: Sequence[float],
        layout: Mapping[str, Any],
    ) -> dict[str, Any]:
        flow_code = int(layout["flow_code"])
        metric = flow_code in {3, 4, 5}
        flow_factor = self._FLOW_TO_M3S[flow_code]
        length_factor = 1.0 if metric else 0.3048
        volume_factor = 1.0 if metric else 1233.48183754752
        rain_factor = 1.0 if metric else 25.4
        names = cast(Mapping[str, Any], layout["names"])
        codes = cast(Mapping[str, Sequence[int]], layout["variable_codes"])
        cursor = 0

        catchments: dict[str, dict[str, float]] = {}
        sub_width = len(codes["subcatchments"])
        for name in names["subcatchments"]:
            values = self._values_by_code(
                raw[cursor : cursor + sub_width],
                codes["subcatchments"],
                self._SUBCATCHMENT_VARIABLES,
            )
            cursor += sub_width
            converted = dict(values)
            for key in ("losses_m3s", "runoff_m3s", "groundwater_flow_m3s"):
                converted[key] = values[key] * flow_factor
            for key in ("snow_depth_m", "groundwater_elevation_m"):
                converted[key] = values[key] * length_factor
            converted["rainfall_mm_h"] = values["rainfall_mm_h"] * rain_factor
            catchments[name] = converted

        nodes: dict[str, dict[str, float]] = {}
        node_width = len(codes["nodes"])
        for name in names["nodes"]:
            values = self._values_by_code(
                raw[cursor : cursor + node_width],
                codes["nodes"],
                self._NODE_VARIABLES,
            )
            cursor += node_width
            nodes[name] = {
                "depth_m": values["depth_m"] * length_factor,
                "head_m": values["head_m"] * length_factor,
                "volume_m3": values["volume_m3"] * volume_factor,
                "lateral_inflow_m3s": values["lateral_inflow_m3s"] * flow_factor,
                "total_inflow_m3s": values["total_inflow_m3s"] * flow_factor,
                "flooding_m3s": values["flooding_m3s"] * flow_factor,
            }

        pipes: dict[str, dict[str, float]] = {}
        link_width = len(codes["links"])
        for name in names["links"]:
            values = self._values_by_code(
                raw[cursor : cursor + link_width],
                codes["links"],
                self._LINK_VARIABLES,
            )
            cursor += link_width
            pipes[name] = {
                "flow_m3s": values["flow_m3s"] * flow_factor,
                "depth_m": values["depth_m"] * length_factor,
                "velocity_mps": values["velocity_mps"] * length_factor,
                "froude_number": values["froude_number"],
                "capacity_ratio": values["capacity_ratio"],
            }

        system_values = self._values_by_code(
            raw[cursor:], codes["system"], self._SYSTEM_VARIABLES
        )
        system: dict[str, float] = {}
        for key, value in system_values.items():
            if key in {"air_temperature_c"}:
                system[key] = value if metric else (value - 32.0) * 5.0 / 9.0
            elif key in {"snow_depth_m"}:
                system[key] = value * length_factor
            elif key == "rainfall_mm_h":
                system[key] = value * rain_factor
            elif key == "storage_m3":
                system[key] = value * volume_factor
            else:
                system[key] = value * flow_factor
        return {
            "schema_version": "hydraulic-frame-v1",
            "catchments": catchments,
            "nodes": nodes,
            "pipes": pipes,
            "system": system,
            "source": {
                "engine": "epa_swmm",
                "engine_version": self.engine_version,
                "parser_version": self.parser_version,
                "flow_unit": layout["flow_unit"],
            },
        }

    async def parse(
        self,
        context: SwmmParseContext,
    ) -> AsyncIterator[SimulationFrame]:
        self._validate_report(context.report_path)
        layout = self._read_layout_bound(context.output_path)
        if layout["report_step"] != context.frame_interval_seconds:
            raise ValueError("SWMM report interval does not match the frozen grid")
        if layout["period_count"] != context.expected_frame_count:
            raise ValueError(
                "SWMM reporting period count does not match the frame grid"
            )
        excel_epoch = datetime(1899, 12, 30, tzinfo=UTC)
        parsed_start = excel_epoch + timedelta(days=float(layout["excel_start"]))
        if abs((parsed_start - context.first_frame_at).total_seconds()) > 0.001:
            raise ValueError("SWMM start time does not match the frozen grid")
        values_per_period = (int(layout["record_size"]) - 8) // 4
        with context.output_path.open("rb") as stream:
            stream.seek(int(layout["results_position"]))
            for frame_index in range(context.expected_frame_count):
                period_date = self._d(stream)
                expected_seconds = frame_index * context.frame_interval_seconds
                expected_date = (
                    float(layout["excel_start"]) + expected_seconds / 86_400.0
                )
                if abs(period_date - expected_date) > 0.001 / 86_400.0:
                    raise ValueError("SWMM output period timestamps are not uniform")
                raw = [self._f(stream) for _ in range(values_per_period)]
                payload = self._convert_frame(raw, layout)
                yield SimulationFrame(
                    attempt_id=context.attempt_id,
                    simulation_id=context.simulation_id,
                    frame_index=frame_index,
                    valid_at=context.first_frame_at
                    + timedelta(seconds=frame_index * context.frame_interval_seconds),
                    payload=payload,
                    solver_id=context.solver_id,
                    solver_version=context.solver_version,
                    target_manifest_sha256=context.target_manifest_sha256,
                )


@dataclass(frozen=True, slots=True)
class SwmmSolveRequest:
    """Bounded SWMM materialization request accepted by :class:`SwmmAdapter`."""

    simulation_id: Any
    attempt_id: Any
    inp_bytes: bytes
    input_manifest: SolverInputManifest

    def __post_init__(self) -> None:
        if not isinstance(self.inp_bytes, bytes) or not self.inp_bytes:
            raise ValueError("inp_bytes must be non-empty bytes")
        if len(self.inp_bytes) > SWMM_INPUT_MAX_BYTES:
            raise ValueError("inp_bytes exceeds the 20 MiB input budget")
        if not isinstance(self.input_manifest, SolverInputManifest):
            raise TypeError("input_manifest must be SolverInputManifest")


class SwmmAdapter:
    """Uniform solver adapter for the two stable EPA SWMM identities."""

    def __init__(
        self,
        solver_id: str,
        executable: str | os.PathLike[str] | None = None,
        expected_version: str | None = None,
        expected_sha256: str | None = None,
        output_parser: SwmmOutputParser | None = None,
        process_runner: SwmmProcessRunner | None = None,
        probe_timeout_seconds: float = 5.0,
        **aliases: Any,
    ) -> None:
        if solver_id not in _SWMM_SOLVER_IDS:
            raise ValueError("SWMM adapter solver_id must be swmm_kw or swmm_dw")
        executable = executable or aliases.pop("executable_path", None)
        executable = executable or aliases.pop("engine_path", None)
        expected_version = expected_version or aliases.pop("version", None)
        expected_version = expected_version or aliases.pop("engine_version", None)
        expected_sha256 = expected_sha256 or aliases.pop("sha256", None)
        expected_sha256 = expected_sha256 or aliases.pop("engine_sha256", None)
        output_parser = output_parser or aliases.pop("parser", None)
        output_parser = output_parser or aliases.pop("result_parser", None)
        if (
            output_parser is None
            and expected_version
            and expected_version.startswith(("5.1.", "5.2."))
        ):
            output_parser = Swmm5OutputParser(expected_version)
        process_runner = process_runner or aliases.pop("runner", None)
        process_runner = process_runner or aliases.pop("process_launcher", None)
        probe_timeout_seconds = aliases.pop("probe_timeout", probe_timeout_seconds)
        if aliases:
            unknown = ", ".join(sorted(aliases))
            raise TypeError(f"unexpected SWMM adapter options: {unknown}")
        if output_parser is not None and not getattr(
            output_parser, "parser_version", ""
        ):
            raise ValueError("output parser must expose a non-empty parser_version")
        probe_timeout_seconds = float(probe_timeout_seconds)
        if not math.isfinite(probe_timeout_seconds) or probe_timeout_seconds <= 0:
            raise ValueError("probe_timeout_seconds must be a positive finite number")
        self.solver_id = solver_id
        self.expected_version = (expected_version or "").strip()
        self.expected_sha256 = (expected_sha256 or "").strip()
        self.probe_timeout_seconds = probe_timeout_seconds
        self._executable = (
            Path(executable).expanduser() if executable is not None else None
        )
        self._output_parser = output_parser
        self._process_runner = process_runner or SubprocessSwmmProcessRunner()

    @property
    def version(self) -> str:
        return self.expected_version or "unconfigured"

    @property
    def parser_version(self) -> str:
        return str(getattr(self._output_parser, "parser_version", "unbound"))

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        *,
        solver_id: str,
        output_parser: SwmmOutputParser | None = None,
        process_runner: SwmmProcessRunner | None = None,
    ) -> "SwmmAdapter":
        """Build from the three validated EPA settings without importing config."""

        return cls(
            solver_id=solver_id,
            executable=getattr(settings, "EPA_SWMM_EXECUTABLE", None),
            expected_version=getattr(settings, "EPA_SWMM_VERSION", None),
            expected_sha256=getattr(settings, "EPA_SWMM_SHA256", None),
            output_parser=output_parser,
            process_runner=process_runner,
        )

    def capabilities(self) -> SolverCapabilities:
        return SolverCapabilities(
            solver_id=self.solver_id,
            version=self.version,
            model_fidelity="engine_exact",
            supported_features=frozenset({"gravity", "ponding", "routing"}),
            routing_modes=(SWMM_ROUTING_MODES[self.solver_id],),
            supports_ponding=True,
            validated_scale=None,
            max_substeps_per_report=20,
            authoritative=True,
        )

    async def probe(self) -> SolverProbe:
        """Verify path and binary hash without importing or starting SWMM."""

        capabilities = self.capabilities()
        if (
            self._executable is None
            or not self.expected_version
            or not self.expected_sha256
        ):
            return self._unavailable_probe("not_configured", capabilities)
        if _SWMM_VERSION_PATTERN.fullmatch(self.expected_version) is None:
            return self._unavailable_probe("invalid_version", capabilities)
        if self._output_parser is None:
            return self._unavailable_probe("output_parser_not_configured", capabilities)
        parser_engine_version = getattr(
            self._output_parser,
            "engine_version",
            None,
        )
        if (
            not isinstance(parser_engine_version, str)
            or not parser_engine_version.strip()
        ):
            return self._unavailable_probe(
                "parser_engine_version_required", capabilities
            )
        if str(parser_engine_version) != self.expected_version:
            return self._unavailable_probe("parser_version_mismatch", capabilities)
        expected_hash = self.expected_sha256.lower()
        if len(expected_hash) != 64 or any(
            character not in "0123456789abcdef" for character in expected_hash
        ):
            return self._unavailable_probe("invalid_sha256", capabilities)
        executable = self._resolved_executable()
        if not executable.is_file():
            return self._unavailable_probe("executable_not_found", capabilities)
        try:
            observed_hash = await asyncio.wait_for(
                asyncio.to_thread(_sha256_file, executable),
                timeout=self.probe_timeout_seconds,
            )
        except TimeoutError:
            return self._unavailable_probe("probe_timeout", capabilities)
        except OSError:
            return self._unavailable_probe("executable_unreadable", capabilities)
        if observed_hash != expected_hash:
            return SolverProbe(
                solver_id=self.solver_id,
                version=self.expected_version,
                available=False,
                reason="sha256_mismatch",
                capabilities=capabilities,
                engine_binary_sha256=observed_hash,
                executable=str(executable),
                observed_version=self.expected_version,
                details={"expected_sha256": expected_hash},
            )
        return SolverProbe(
            solver_id=self.solver_id,
            version=self.expected_version,
            available=True,
            reason="verified",
            capabilities=capabilities,
            engine_binary_sha256=observed_hash,
            executable=str(executable),
            observed_version=self.expected_version,
            details={"parser_version": self.parser_version},
        )

    async def capability_report(self) -> SolverDegradationReport:
        """Return this adapter's probe without making a fallback decision."""

        probe = await self.probe()
        if probe.available:
            return SolverDegradationReport(
                requested_solver_id=self.solver_id,
                selected_solver_id=self.solver_id,
                available=True,
                degraded=False,
                reason="selected",
                probes=(probe,),
            )
        return SolverDegradationReport(
            requested_solver_id=self.solver_id,
            selected_solver_id=None,
            available=False,
            degraded=False,
            reason=probe.reason,
            fallback_solver_id=None,
            fallback_reason=None,
            probes=(probe,),
        )

    probe_report = capability_report
    degradation_report = capability_report

    def validate(
        self,
        snapshot: Any,
        request: SolveRequest,
    ) -> ValidationReport:
        issues: list[ValidationIssue] = []
        if snapshot is None or hasattr(snapshot, "_sa_instance_state"):
            issues.append(
                ValidationIssue(
                    code="SOLVER_INPUT_INVALID",
                    field="snapshot",
                    message=(
                        "SWMM requires a materialized NetworkSnapshot, "
                        "not an ORM row"
                    ),
                )
            )
        if not isinstance(request, SwmmSolveRequest):
            issues.append(
                ValidationIssue(
                    code="SOLVER_INPUT_INVALID",
                    field="request",
                    message="request must be SwmmSolveRequest",
                )
            )
            return ValidationReport(valid=False, issues=tuple(issues))
        manifest = request.input_manifest
        configured_routing = manifest.solver_config.get("routing")
        if (
            configured_routing is not None
            and configured_routing != SWMM_ROUTING_MODES[self.solver_id]
        ):
            issues.append(
                ValidationIssue(
                    code="UNSUPPORTED_FEATURE",
                    field="request.input_manifest.solver_config.routing",
                    message="solver configuration routing does not match SWMM identity",
                )
            )
        if manifest.solver_id != self.solver_id:
            issues.append(
                ValidationIssue(
                    code="SOLVER_INPUT_INVALID",
                    field="request.input_manifest.solver_id",
                    message=(
                        "request solver identity does not match " "the selected adapter"
                    ),
                )
            )
        if manifest.solver_version != self.version:
            issues.append(
                ValidationIssue(
                    code="SOLVER_VERSION_MISMATCH",
                    field="request.input_manifest.solver_version",
                    message=(
                        "request solver version does not match " "the selected adapter"
                    ),
                )
            )
        if len(request.inp_bytes) > SWMM_INPUT_MAX_BYTES:
            issues.append(
                ValidationIssue(
                    code="INPUT_TOO_LARGE",
                    field="request.inp_bytes",
                    message="materialized INP exceeds the 20 MiB budget",
                )
            )
        return ValidationReport(valid=not issues, issues=tuple(issues))

    async def run(
        self,
        request: SolveRequest,
        snapshot: Any,
        frame_sink: FrameSink,
        control: RunControl,
    ) -> SolveSummary:
        """Run the already-selected engine; never substitute a solver on error."""

        attempt_deadline = time.monotonic() + (
            control.timeout_seconds
            if control.timeout_seconds is not None
            else SWMM_DEFAULT_TIMEOUT_SECONDS
        )

        def check_attempt() -> None:
            control.check()
            if time.monotonic() >= attempt_deadline:
                raise SolverTimeoutError(
                    "SWMM attempt exceeded its deadline",
                    solver_id=self.solver_id,
                )

        check_attempt()
        probe = await self.probe()
        check_attempt()
        if not probe.available:
            error_type = (
                SolverVersionMismatchError
                if probe.reason in {"sha256_mismatch", "invalid_sha256"}
                else SolverUnavailableError
            )
            raise error_type(
                f"SWMM engine is unavailable: {probe.reason}",
                solver_id=self.solver_id,
                reason=probe.reason,
                details=probe.to_dict(),
            )
        validation = self.validate(snapshot, request)
        if not validation.valid:
            raise SolverValidationError(
                "SWMM request validation failed",
                solver_id=self.solver_id,
                details={"issues": [issue.to_dict() for issue in validation.issues]},
            )
        assert isinstance(request, SwmmSolveRequest)
        check_attempt()
        workspace, paths = self._prepare_workspace(request, control)
        await control.report(1, "validation")
        process_request = SwmmProcessRequest(
            argv=(
                str(self._resolved_executable()),
                str(paths["input"]),
                str(paths["report"]),
                str(paths["output"]),
            ),
            cwd=workspace,
            shell=False,
        )
        await control.report(5, "solving")
        check_attempt()
        try:
            process = await self._start_process(process_request)
        except SolverContractError:
            raise
        except Exception as exc:
            raise SolverExecutionError(
                "SWMM engine could not be started",
                solver_id=self.solver_id,
                retryable=False,
                details={"error_type": type(exc).__name__},
            ) from exc
        try:
            return_code = await self._wait_for_process(
                process,
                control,
                deadline=attempt_deadline,
            )
        except (
            SolverTimeoutError,
            SolverCancelledError,
            SolverExecutionError,
            asyncio.CancelledError,
        ) as exc:
            cleanup_error = await self._terminate_process(process)
            if cleanup_error is not None and hasattr(exc, "details"):
                exc.details["cleanup_error"] = cleanup_error
            raise
        cleanup_error = await self._terminate_process(process)
        if cleanup_error is not None:
            raise SolverExecutionError(
                "SWMM process-tree cleanup failed after engine exit",
                solver_id=self.solver_id,
                retryable=False,
                details={
                    "return_code": return_code,
                    "cleanup_error": cleanup_error,
                },
            )
        if return_code != 0:
            raise SolverExecutionError(
                f"SWMM engine exited with code {return_code}",
                solver_id=self.solver_id,
                retryable=False,
                details={"return_code": return_code},
            )
        check_attempt()
        missing_outputs = [
            name
            for name in ("report", "output")
            if not paths[name].is_file() or paths[name].is_symlink()
        ]
        if missing_outputs:
            raise SolverResultError(
                "SWMM completed without its complete report/binary output",
                solver_id=self.solver_id,
                details={"missing_outputs": missing_outputs},
            )
        manifest = request.input_manifest
        engine_binary_sha256 = probe.engine_binary_sha256
        if engine_binary_sha256 is None:
            raise SolverResultError(
                "SWMM probe did not return an engine hash",
                solver_id=self.solver_id,
            )
        context = SwmmParseContext(
            simulation_id=request.simulation_id,
            attempt_id=request.attempt_id,
            input_path=paths["input"],
            report_path=paths["report"],
            output_path=paths["output"],
            solver_id=self.solver_id,
            solver_version=manifest.solver_version,
            engine_binary_sha256=engine_binary_sha256,
            input_sha256=manifest.input_sha256,
            network_content_hash=manifest.network_revision_content_sha256,
            snapshot_content_hash=manifest.snapshot_content_sha256,
            initial_state_hash=manifest.initial_state_content_sha256,
            target_manifest_sha256=manifest.target_manifest_sha256,
            rainfall_content_hash=manifest.rainfall_content_sha256,
            solver_config_hash=manifest.solver_config_hash,
            first_frame_at=manifest.start_at,
            frame_interval_seconds=manifest.frame_interval_seconds,
            expected_frame_count=manifest.expected_frame_count,
        )
        await control.report(80, "parsing")
        check_attempt()
        frame_count = 0
        first_at: datetime | None = None
        last_at: datetime | None = None
        has_failed_frame = False
        has_warning_frame = False
        if self._output_parser is None:
            raise SolverResultError(
                "SWMM output parser is not configured",
                solver_id=self.solver_id,
            )
        try:
            parsed = self._output_parser.parse(context)
            if inspect.isawaitable(parsed):
                parsed = await self._await_with_control(
                    parsed,
                    control,
                    attempt_deadline,
                )
            iterator = parsed.__aiter__()
        except (SolverTimeoutError, SolverCancelledError):
            raise
        except Exception as exc:
            raise SolverResultError(
                "SWMM output parser could not start",
                solver_id=self.solver_id,
                details={"error_type": type(exc).__name__},
            ) from exc
        while True:
            check_attempt()
            try:
                raw_frame = await self._await_with_control(
                    iterator.__anext__(),
                    control,
                    attempt_deadline,
                )
            except StopAsyncIteration:
                break
            except (SolverTimeoutError, SolverCancelledError):
                raise
            except Exception as exc:
                raise SolverResultError(
                    "SWMM output parser failed",
                    solver_id=self.solver_id,
                    details={"error_type": type(exc).__name__},
                ) from exc
            check_attempt()
            frame = self._coerce_frame(
                raw_frame,
                context,
                expected_index=frame_count,
            )
            await self._await_with_control(
                frame_sink.write(frame),
                control,
                attempt_deadline,
            )
            check_attempt()
            if first_at is None:
                first_at = frame.valid_at
            last_at = frame.valid_at
            frame_count += 1
            has_failed_frame = has_failed_frame or (
                frame.quality_status == SolverResultStatus.failed.value
            )
            has_warning_frame = has_warning_frame or (
                frame.quality_status == SolverResultStatus.warning.value
            )
            if frame_count == 1:
                await control.report(94, "parsing")
        if frame_count != context.expected_frame_count:
            raise SolverResultError(
                "SWMM parser returned an incomplete frame grid",
                solver_id=self.solver_id,
                details={
                    "expected_frame_count": context.expected_frame_count,
                    "actual_frame_count": frame_count,
                },
            )
        if has_failed_frame:
            raise SolverResultError(
                "SWMM parser returned a failed-quality frame",
                solver_id=self.solver_id,
            )
        if first_at is None or last_at is None:
            raise SolverResultError(
                "SWMM parser returned an empty frame grid",
                solver_id=self.solver_id,
            )
        result_status = (
            SolverResultStatus.warning.value
            if has_warning_frame
            else SolverResultStatus.verified.value
        )
        summary = SolveSummary(
            solver_id=self.solver_id,
            solver_version=manifest.solver_version,
            model_fidelity="engine_exact",
            engine_binary_sha256=engine_binary_sha256,
            input_sha256=manifest.input_sha256,
            network_content_hash=manifest.network_revision_content_sha256,
            snapshot_content_hash=manifest.snapshot_content_sha256,
            initial_state_hash=manifest.initial_state_content_sha256,
            target_manifest_hash=manifest.target_manifest_sha256,
            rainfall_content_hash=manifest.rainfall_content_sha256,
            solver_config_hash=manifest.solver_config_hash,
            frame_count=frame_count,
            first_at=first_at,
            last_at=last_at,
            result_status=result_status,
            output_artifacts={
                "report": paths["report"].name,
                "binary_output": paths["output"].name,
            },
            diagnostics={
                "adapter_version": SWMM_ADAPTER_VERSION,
                "parser_version": self.parser_version,
                "return_code": return_code,
                "snapshot_content_hash": manifest.snapshot_content_sha256,
                "initial_state_content_sha256": manifest.initial_state_content_sha256,
                "target_manifest_sha256": manifest.target_manifest_sha256,
            },
        )
        await control.report(95, "persisting")
        check_attempt()
        await self._await_with_control(
            frame_sink.finalize(summary),
            control,
            attempt_deadline,
        )
        check_attempt()
        await control.report(100, "completed")
        return summary

    def _unavailable_probe(
        self,
        reason: str,
        capabilities: SolverCapabilities,
    ) -> SolverProbe:
        executable = (
            str(self._resolved_executable()) if self._executable is not None else None
        )
        return SolverProbe(
            solver_id=self.solver_id,
            version=self.version,
            available=False,
            reason=reason,
            capabilities=capabilities,
            executable=executable,
            details={"parser_version": self.parser_version},
        )

    def _resolved_executable(self) -> Path:
        if self._executable is None:
            raise SolverUnavailableError(
                "SWMM executable is not configured",
                solver_id=self.solver_id,
                reason="not_configured",
            )
        executable = self._executable
        if not executable.is_absolute():
            executable = Path.cwd() / executable
        return executable.resolve(strict=False)

    def _prepare_workspace(
        self,
        request: SwmmSolveRequest,
        control: RunControl,
    ) -> tuple[Path, Mapping[str, Path]]:
        if control.workspace_root is None:
            raise SolverInputError(
                "RunControl.workspace_root is required for SWMM",
                solver_id=self.solver_id,
            )
        workspace = Path(control.workspace_root).expanduser().resolve(strict=False)
        if not workspace.is_dir() or workspace.is_symlink():
            raise SolverInputError(
                "SWMM workspace must be an existing non-symlink directory",
                solver_id=self.solver_id,
            )
        existing_bytes = sum(
            path.stat(follow_symlinks=False).st_size
            for path in workspace.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
        if existing_bytes + len(request.inp_bytes) > control.max_workspace_bytes:
            raise SolverInputError(
                "materialized SWMM input exceeds the attempt workspace budget",
                solver_id=self.solver_id,
                details={
                    "existing_bytes": existing_bytes,
                    "input_bytes": len(request.inp_bytes),
                    "max_workspace_bytes": control.max_workspace_bytes,
                },
            )
        paths = {
            "input": workspace / "simulation.inp",
            "report": workspace / "simulation.rpt",
            "output": workspace / "simulation.out",
        }
        for path in paths.values():
            if path.exists() or path.is_symlink():
                raise SolverInputError(
                    "deterministic SWMM sandbox filename already exists",
                    solver_id=self.solver_id,
                    details={"artifact": path.name},
                )
        try:
            with paths["input"].open("xb") as stream:
                stream.write(request.inp_bytes)
        except OSError as exc:
            raise SolverInputError(
                "could not materialize SWMM INP in the attempt workspace",
                solver_id=self.solver_id,
            ) from exc
        return workspace, paths

    async def _start_process(self, request: SwmmProcessRequest) -> SwmmProcess:
        return cast(SwmmProcess, await self._process_runner.start(request))

    async def _await_with_control(
        self,
        awaitable: Any,
        control: RunControl,
        deadline: float,
    ) -> Any:
        """Bound parser/sink awaits by the same cancellation/deadline as the engine."""

        task = asyncio.ensure_future(awaitable)
        try:
            while True:
                control.check()
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    raise SolverTimeoutError(
                        "SWMM attempt exceeded its deadline",
                        solver_id=self.solver_id,
                    )
                done, _ = await asyncio.wait(
                    {task},
                    timeout=min(_PROCESS_POLL_SECONDS, remaining),
                )
                if done:
                    return task.result()
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def _wait_for_process(
        self,
        process: SwmmProcess,
        control: RunControl,
        *,
        deadline: float,
    ) -> int:
        wait_task = asyncio.create_task(process.wait())
        try:
            while True:
                control.check()
                if control.cancelled():
                    raise SolverCancelledError(
                        "SWMM run was cancelled",
                        solver_id=self.solver_id,
                    )
                timeout = _PROCESS_POLL_SECONDS
                if deadline is not None:
                    timeout = min(timeout, max(0.0, deadline - time.monotonic()))
                    if timeout <= 0.0:
                        raise SolverTimeoutError(
                            "SWMM run exceeded its deadline",
                            solver_id=self.solver_id,
                        )
                done, _ = await asyncio.wait({wait_task}, timeout=timeout)
                if done:
                    try:
                        return_code = wait_task.result()
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        raise SolverExecutionError(
                            "SWMM process wait failed",
                            solver_id=self.solver_id,
                            retryable=False,
                            details={"error_type": type(exc).__name__},
                        ) from exc
                    if isinstance(return_code, bool) or not isinstance(
                        return_code, int
                    ):
                        raise SolverExecutionError(
                            "SWMM process returned an invalid exit code",
                            solver_id=self.solver_id,
                        )
                    return return_code
        finally:
            if not wait_task.done():
                wait_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await wait_task

    @staticmethod
    async def _terminate_process(process: SwmmProcess) -> str | None:
        try:
            await process.kill_process_tree()
        except Exception as exc:
            return type(exc).__name__
        return None

    def _coerce_frame(
        self,
        value: SimulationFrame | Mapping[str, Any],
        context: SwmmParseContext,
        *,
        expected_index: int,
    ) -> SimulationFrame:
        if isinstance(value, SimulationFrame):
            frame = value
        elif isinstance(value, Mapping):
            payload = value.get("payload")
            if not isinstance(payload, Mapping):
                raise SolverResultError(
                    "SWMM frame mapping requires a payload mapping",
                    solver_id=self.solver_id,
                )
            raw_index = value.get("frame_index")
            if isinstance(raw_index, bool) or not isinstance(raw_index, int):
                raise SolverResultError(
                    "SWMM frame mapping requires an integer frame_index",
                    solver_id=self.solver_id,
                )
            raw_valid_at = value.get("valid_at", value.get("frame_time"))
            if raw_valid_at is None:
                raise SolverResultError(
                    "SWMM frame mapping requires valid_at",
                    solver_id=self.solver_id,
                )
            frame = SimulationFrame(
                attempt_id=context.attempt_id,
                simulation_id=context.simulation_id,
                frame_index=raw_index,
                valid_at=raw_valid_at,
                payload=payload,
                solver_id=self.solver_id,
                solver_version=context.solver_version,
                target_manifest_sha256=context.target_manifest_sha256,
                quality_status=str(value.get("quality_status", "verified")),
            )
        else:
            raise SolverResultError(
                "SWMM parser yielded an unsupported frame value",
                solver_id=self.solver_id,
            )
        expected_time = context.first_frame_at + timedelta(
            seconds=frame.frame_index * context.frame_interval_seconds
        )
        if frame.frame_index != expected_index:
            raise SolverResultError(
                "SWMM frame indexes must be dense and increasing",
                solver_id=self.solver_id,
                details={
                    "expected_frame_index": expected_index,
                    "actual_frame_index": frame.frame_index,
                },
            )
        if (
            frame.attempt_id != context.attempt_id
            or frame.simulation_id != context.simulation_id
        ):
            raise SolverResultError(
                "SWMM frame belongs to a different simulation attempt",
                solver_id=self.solver_id,
            )
        if (
            frame.solver_id != self.solver_id
            or frame.solver_version != context.solver_version
        ):
            raise SolverResultError(
                "SWMM frame solver identity does not match the selected engine",
                solver_id=self.solver_id,
            )
        if frame.target_manifest_sha256 != context.target_manifest_sha256:
            raise SolverResultError(
                "SWMM frame target manifest does not match the request",
                solver_id=self.solver_id,
            )
        if not isinstance(frame.valid_at, datetime) or frame.valid_at != expected_time:
            raise SolverResultError(
                "SWMM frame does not match the frozen reporting grid",
                solver_id=self.solver_id,
            )
        if frame.quality_status not in {"verified", "warning"}:
            raise SolverResultError(
                "SWMM frame quality status cannot be published",
                solver_id=self.solver_id,
            )
        return frame


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


SWMMAdapter = SwmmAdapter
EpaSwmmAdapter = SwmmAdapter
SwmmSolverAdapter = SwmmAdapter
SwmmUnavailableError = SolverUnavailableError
SwmmTimeoutError = SolverTimeoutError


__all__ = [
    "SWMM_ADAPTER_VERSION",
    "SWMM_DEFAULT_TIMEOUT_SECONDS",
    "SWMM_INPUT_MAX_BYTES",
    "SubprocessSwmmProcess",
    "SubprocessSwmmProcessRunner",
    "EpaSwmmAdapter",
    "SWMMAdapter",
    "Swmm5OutputParser",
    "SwmmAdapter",
    "SwmmOutputParser",
    "SwmmParseContext",
    "SwmmProcess",
    "SwmmProcessRequest",
    "SwmmProcessRunner",
    "SwmmSolveRequest",
    "SwmmSolverAdapter",
    "SwmmTimeoutError",
    "SwmmUnavailableError",
]
