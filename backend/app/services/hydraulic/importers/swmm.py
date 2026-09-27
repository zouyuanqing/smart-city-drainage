"""Safe, deterministic SWMM ``.inp`` importer.

The importer is intentionally a text parser rather than a process launcher.  It
accepts only the declarative sections used by the hydraulic network contract;
it never resolves an include, opens a file, executes a script, or delegates
parsing to an external executable.  This makes it suitable for an untrusted
import worker and gives the topology/materializer layer a stable, typed
representation to consume.

The public entry point is :func:`parse_swmm`.  A strict parse (the default)
rejects invalid syntax, path-like directives, duplicate identifiers, and
referential/coordinate errors.  Callers that need a report while retaining
partially parsed records can pass ``strict=False`` and inspect
``SwmmNetwork.issues``.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shlex
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, NoReturn

SWMM_PARSER_VERSION = "swmm-inp-parser-v1"
SWMM_MAX_INPUT_BYTES = 20 * 1024 * 1024
SWMM_MAX_LINES = 1_000_000
SWMM_MAX_LINE_LENGTH = 1_000_000
SWMM_MAX_TOKENS_PER_LINE = 32
SWMM_MAX_IDENTIFIER_LENGTH = 256
SWMM_DEFAULT_OPTIONS = (("FLOW_UNITS", "CFS"),)

# These sections are either not needed by the canonical network, or contain
# executable/environmental directives.  The importer deliberately does not
# silently discard a file/include instruction: accepting it would make an
# imported project capable of reaching outside the task sandbox.
FORBIDDEN_SECTION_WORDS = frozenset(
    {
        "DLL",
        "EXE",
        "EXEC",
        "EXECUTE",
        "EXPORT",
        "EXTERNAL",
        "FILE",
        "FILES",
        "IMPORT",
        "INCLUDE",
        "INCLUDES",
        "LOAD",
        "PATH",
        "RUN",
        "SCRIPT",
        "SCRIPTS",
        "SHELL",
        "USE",
        "URL",
    }
)

# Broad enough for ordinary EPA SWMM input files, while making unknown
# sections fail closed.  Raw rows in these sections are preserved for export.
SUPPORTED_SECTIONS = frozenset(
    {
        "CONTROLS",
        "COORDINATES",
        "CONDUITS",
        "CURVES",
        "DWF",
        "DWF_INFLOW",
        "DYES",
        "EVAPORATION",
        "ENERGY",
        "EMISSION",
        "FILES",
        "HYDROGRAPHS",
        "INFLOWS",
        "JUNCTIONS",
        "HYDRAULICS",
        "INFILTRATION",
        "LID_CONTROLS",
        "LID_USAGE",
        "LANDUSES",
        "COVERAGES",
        "LOAD",
        "MAP",
        "MONITORING",
        "OUTFALLS",
        "POLYGONS",
        "RAINGAGES",
        "RDII",
        "RUNOFF",
        "REPORT",
        "STORAGE",
        "STREETS",
        "SOURCES",
        "SUBCATCHMENTS",
        "TAGS",
        "POLLUTANTS",
        "WATER_QUALITY",
        "WASH_OFF",
        "BUILDUP",
        "TEMPERATURE",
        "SNOWPACK",
        "SNOWMELT",
        "TIMESERIES",
        "TITLE",
        "TRANSECTS",
        "TREES",
        "XSECTIONS",
        "VERTICES",
    }
)
# ``FILES`` and ``LOAD`` are listed for diagnostics above but remain forbidden.
SUPPORTED_SECTIONS = SUPPORTED_SECTIONS - FORBIDDEN_SECTION_WORDS

_RAW_SECTIONS = SUPPORTED_SECTIONS - {
    "TITLE",
    "OPTIONS",
    "JUNCTIONS",
    "OUTFALLS",
    "CONDUITS",
    "SUBCATCHMENTS",
    "COORDINATES",
    "SLOPES",
}
# OPTIONS is accepted but not included in the initial set above because it is
# a conventional, non-object section.  SLOPES is explicitly supported.
SUPPORTED_SECTIONS = SUPPORTED_SECTIONS | {"OPTIONS", "SLOPES"}
RAW_SECTIONS = frozenset(
    SUPPORTED_SECTIONS
    - {
        "TITLE",
        "OPTIONS",
        "JUNCTIONS",
        "OUTFALLS",
        "CONDUITS",
        "SUBCATCHMENTS",
        "COORDINATES",
        "SLOPES",
    }
)

_SECTION_RE = re.compile(r"^\[\s*([A-Za-z][A-Za-z0-9_\-\s]*?)\s*\]$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PATH_RE = re.compile(
    r"(?i)(?:"
    r"^[a-z]:"  # drive-absolute and drive-relative Windows paths
    r"|^[\\/]{1,2}"  # absolute POSIX/UNC
    r"|^[a-z][a-z0-9+.-]*://"  # URL
    r"|^(?:file|data):"  # URL/data scheme
    r"|(?:^|[\\/])\.\.(?:[\\/]|$)"
    r"|[\\/]"  # any relative path separator in a data token
    r"|(?:^|[\\/])[^\\/:*?\"<>|]+\.[a-z0-9]{1,8}:[^\\/]+"  # NTFS ADS
    r"|\.(?:inp|dat|txt|csv|bat|cmd|ps1|py|exe|dll)(?:$|[\\/])"
    r")"
)
_FORBIDDEN_DATA_TOKENS = frozenset(
    {
        "BAT",
        "CMD",
        "CMD.EXE",
        "COMMAND",
        "DLL",
        "EXEC",
        "EXE",
        "EXECUTE",
        "FILE",
        "IMPORT",
        "INCLUDE",
        "LOAD",
        "PATH",
        "POWERSHELL",
        "PYTHON",
        "RUN",
        "SCRIPT",
        "SHELL",
        "URL",
        "USE",
    }
)
_VALID_OUTFALL_TYPES = frozenset({"FREE", "NORMAL", "FIXED", "TIDAL", "TIMESERIES"})
_TRUE_VALUES = frozenset({"1", "TRUE", "YES", "Y", "T", "ON"})
_FALSE_VALUES = frozenset({"0", "FALSE", "NO", "N", "F", "OFF"})
_SWMM_COORDINATE_MODES = frozenset({"wgs84", "local"})

# Raw SWMM sections are retained, but their rows are not opaque.  This table
# supplies the minimum row shape needed to reject truncated/unknown records
# before they can participate in an export round trip.
_RAW_SECTION_RULES: Mapping[str, tuple[int, int | None, int | None]] = {
    "BUILDUP": (3, None, 0),
    "CONTROLS": (2, None, None),
    "COVERAGES": (3, 3, 0),
    "CURVES": (3, None, 0),
    "DWF": (3, None, 1),
    "DWF_INFLOW": (3, 3, 0),
    "DYES": (2, 2, 0),
    "ENERGY": (2, 2, None),
    "EMISSION": (2, 2, None),
    "EVAPORATION": (2, 2, None),
    "HYDROGRAPHS": (3, None, 1),
    "INFILTRATION": (2, None, None),
    "LID_CONTROLS": (2, None, 0),
    "LID_USAGE": (5, None, 0),
    "LANDUSES": (1, None, 0),
    "MAP": (2, 2, None),
    "MONITORING": (2, None, None),
    "POLYGONS": (3, None, 0),
    "POLLUTANTS": (1, None, 0),
    "RAINGAGES": (2, None, None),
    "RDII": (2, None, 0),
    "REPORT": (2, 2, None),
    "RUNOFF": (2, None, None),
    "SNOWMELT": (2, None, None),
    "SNOWPACK": (2, None, None),
    "SOURCES": (1, None, None),
    "STORAGE": (2, None, 0),
    "STREETS": (1, None, 0),
    "TEMPERATURE": (1, None, 0),
    "TIMESERIES": (4, None, 0),
    "TRANSECTS": (3, None, None),
    "TREES": (5, None, 0),
    "VERTICES": (3, 3, 0),
    "WASH_OFF": (3, None, 0),
    "WATER_QUALITY": (2, None, 0),
    "XSECTIONS": (3, 7, 0),
}
_RAW_SECTION_ID_RULES = {
    section: index
    for section, (_, _, index) in _RAW_SECTION_RULES.items()
    if index is not None
}
_XSECTION_SHAPES = frozenset(
    {
        "CIRCULAR",
        "FILLED_CIRCULAR",
        "RECT_CLOSED",
        "RECT_OPEN",
        "RECT_TRIANGULAR",
        "RECT_ROUND",
        "MOD_BASKETHANDLE",
        "TRAPEZOIDAL",
        "TRIANGULAR",
        "PARABOLIC",
        "POWERFUNC",
        "IRREGULAR",
        "CUSTOM",
    }
)

_BOOLEAN_OPTIONS = frozenset(
    {
        "ALLOW_PONDING",
        "CHECKFREQ",
        "DEBUG",
        "DT_SYNTH",
        "INFILTRATION",
        "INPUT",
        "NO_CHECKS",
        "NO_EVAPORATION",
        "OUTLAPING",
        "RULE",
        "SKIP_STEADY",
        "SYS_FLOW",
        "TRY_REDUCE_ROUTE",
        "UNIX_EPOCH",
        "USE",
        "WARNINGS",
    }
)
_CHOICE_OPTIONS: Mapping[str, frozenset[str]] = {
    "BACKSLOPE": frozenset({"NONE", "PERCENT", "VALUE"}),
    "FLOW_UNITS": frozenset({"CFS", "GPM", "LPS", "MLD"}),
    "FORCE_MAIN_EQUATION": frozenset({"H-W", "D-W"}),
    "INERTIAL_DAMPING": frozenset({"NONE", "PARTIAL", "FULL"}),
    "NORMAL_FLOW_LIMITED": frozenset({"BOTH", "NO", "ONLY"}),
    "ROUTE_MODEL": frozenset({"NORMAL", "DKW"}),
    "SURCHARGE_METHOD": frozenset({"EXAMINED", "RESIDUAL", "LEVEL"}),
}
_NUMERIC_OPTION_RANGES: Mapping[str, tuple[float, float]] = {
    "DETAIL": (0.0, 100.0),
    "DRY_DAY": (0.0, 365.0),
    "DWF": (0.0, 1.0),
    "DWF_SCALE": (0.0, 1_000_000.0),
    "FRAMEWORK": (0.0, 100.0),
    "LENGTHENING": (0.0, 10.0),
    "MAX_FLOW_CHANGES": (0.0, 100.0),
    "MAX_TRIALS": (0.0, 1000.0),
    "MIN_ROUTE": (0.0, 10_000.0),
    "MIN_SURCHARGE": (0.0, 10_000.0),
    "NUM_THREADS": (0.0, 1.0),
    "SIMULATION_DT": (0.0, 3600.0),
    "SLOPE_WEIGHTING": (0.0, 1.0),
    "START_DRY_DAY": (0.0, 365.0),
    "TIMER": (0.0, 3600.0),
}
_ALLOWED_OPTIONS = (
    _BOOLEAN_OPTIONS
    | set(_CHOICE_OPTIONS)
    | set(_NUMERIC_OPTION_RANGES)
    | frozenset(
        {
            "CONTINUE",
            "END_DATE",
            "END_TIME",
            "FLOW_ROUGHNESS",
            "HEAD_TOL",
            "HYDROGRAPH_STEP",
            "IGNORE_ROUTING",
            "R_COEFFICIENT",
            "RSTART_DATE",
            "RSTART_TIME",
            "START_DATE",
            "START_TIME",
            "STORAGE",
            "VOLUME_CURB",
            "WAVES",
        }
    )
)


class SwmmParseError(ValueError):
    """Stable, line-aware error raised for an invalid or unsafe INP."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "INVALID_INP",
        line: int | None = None,
        section: str | None = None,
        issues: Iterable[SwmmIssue] = (),
    ) -> None:
        self.code = code
        self.message = message
        self.line = line
        self.section = section
        self.issues = tuple(issues)
        location = ""
        if section is not None or line is not None:
            location = f" at {section or '<input>'} line {line or '?'}"
        super().__init__(f"{code}{location}: {message}")


class SwmmSyntaxError(SwmmParseError):
    """Alias retained for callers that distinguish syntax from semantic errors."""


@dataclass(frozen=True, slots=True)
class SwmmIssue:
    """A deterministic issue suitable for an import validation report."""

    code: str
    message: str
    severity: str = "error"
    section: str | None = None
    line: int | None = None
    entity_type: str | None = None
    entity_key: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "section": self.section,
            "line": self.line,
            "entity_type": self.entity_type,
            "entity_key": self.entity_key,
            "details": dict(sorted(self.details.items())),
        }


# Common names used by topology/validation modules.
ValidationIssue = SwmmIssue


def _reject_path_like(
    value: str, *, section: str, line: int, reason: str = "path-like directive"
) -> None:
    stripped = value.strip().strip("'\"")
    if stripped.upper() in _FORBIDDEN_DATA_TOKENS or _PATH_RE.search(stripped):
        raise SwmmParseError(
            f"{reason} is not allowed in an untrusted INP",
            code="UNSAFE_PATH",
            section=section,
            line=line,
        )


def _validate_option_tokens(tokens: Sequence[str]) -> None:
    """Validate a supported ``[OPTIONS]`` key/value row."""

    if len(tokens) < 2 or not tokens[0] or not tokens[1]:
        raise ValueError("OPTIONS rows require a non-empty name and value")
    key = tokens[0].upper()
    value = " ".join(tokens[1:]).upper()
    if key not in _ALLOWED_OPTIONS:
        raise ValueError(f"unsupported option {key}")
    if key in _BOOLEAN_OPTIONS and value not in _TRUE_VALUES | _FALSE_VALUES:
        raise ValueError(f"option {key} requires a boolean value")
    choices = _CHOICE_OPTIONS.get(key)
    if choices is not None and value not in choices:
        raise ValueError(f"option {key} must be one of {sorted(choices)}")
    numeric_range = _NUMERIC_OPTION_RANGES.get(key)
    if numeric_range is not None:
        try:
            number = float(value)
        except ValueError as exc:
            raise ValueError(f"option {key} requires a finite number") from exc
        if (
            not math.isfinite(number)
            or not numeric_range[0] <= number <= numeric_range[1]
        ):
            raise ValueError(
                f"option {key} must be between {numeric_range[0]} "
                f"and {numeric_range[1]}"
            )
    if key in {
        "START_DATE",
        "END_DATE",
        "RSTART_DATE",
        "DWF_START_DATE",
        "DWF_END_DATE",
    }:
        try:
            datetime.strptime(value, "%m/%d/%Y")
        except ValueError as exc:
            raise ValueError(f"option {key} requires MM/DD/YYYY") from exc
    if key in {"START_TIME", "END_TIME", "RSTART_TIME"}:
        for time_format in ("%H:%M:%S", "%H:%M"):
            try:
                datetime.strptime(value, time_format)
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"option {key} requires HH:MM[:SS]")


def _finite_token(value: str, *, field_name: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be a finite number")
    return number


def _validate_raw_section_tokens(section: str, tokens: Sequence[str]) -> None:
    """Validate the frozen minimum shape of one preserved raw-section row."""

    rule = _RAW_SECTION_RULES.get(section)
    if rule is None:
        return
    minimum, maximum, identifier_index = rule
    if len(tokens) < minimum or (maximum is not None and len(tokens) > maximum):
        raise ValueError(f"row in [{section}] has an invalid token count")
    if identifier_index is not None:
        identifier = tokens[identifier_index].strip()
        if not identifier:
            raise ValueError(f"row in [{section}] requires an identifier")
    if section == "XSECTIONS":
        shape = tokens[1].upper()
        if shape not in _XSECTION_SHAPES:
            raise ValueError(f"unsupported XSECTIONS shape {tokens[1]!r}")
        for index, token in enumerate(tokens[2:], start=1):
            _finite_token(token, field_name=f"XSECTIONS parameter {index}")
        if float(tokens[2]) <= 0:
            raise ValueError("the first XSECTIONS parameter must be positive")
    elif section in {"POLYGONS", "VERTICES"}:
        for index, token in enumerate(tokens[1:], start=1):
            _finite_token(token, field_name=f"{section} ordinate {index}")
    elif section == "CURVES":
        for index, token in enumerate(tokens[2:], start=1):
            _finite_token(token, field_name=f"CURVES ordinate {index}")


def _normalise_local_transform(value: Any) -> tuple[float, ...] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        keys = ("a", "b", "c", "d", "e", "f")
        if all(key in value for key in keys):
            raw = tuple(value[key] for key in keys)
        else:
            keys = (
                "scale_x",
                "rotation_x",
                "translate_x",
                "rotation_y",
                "scale_y",
                "translate_y",
            )
            if not all(key in value for key in keys):
                raise ValueError(
                    "local_to_wgs84 mapping requires a,b,c,d,e,f coefficients"
                )
            raw = tuple(value[key] for key in keys)
    else:
        try:
            raw = tuple(value)
        except TypeError as exc:
            raise ValueError(
                "local_to_wgs84 must contain six affine coefficients"
            ) from exc
    if len(raw) != 6:
        raise ValueError("local_to_wgs84 must contain six affine coefficients")
    try:
        result = tuple(float(item) for item in raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("local_to_wgs84 coefficients must be finite numbers") from exc
    if not all(math.isfinite(item) for item in result):
        raise ValueError("local_to_wgs84 coefficients must be finite numbers")
    a, b, _c, d, e, _f = result
    if abs(a * e - b * d) <= 1e-15:
        raise ValueError("local_to_wgs84 affine transform must be invertible")
    return result


def _transform_local_coordinate(
    x: float, y: float, transform: Sequence[float]
) -> tuple[float, float]:
    a, b, c, d, e, f = transform
    return a * x + b * y + c, d * x + e * y + f


def _validate_identifier(value: str, *, section: str, line: int) -> str:
    if not value or len(value) > SWMM_MAX_IDENTIFIER_LENGTH:
        raise SwmmParseError(
            "identifier must be between 1 and 256 characters",
            section=section,
            line=line,
        )
    if any(character.isspace() for character in value) or _CONTROL_RE.search(value):
        raise SwmmParseError(
            "identifier contains whitespace or control characters",
            section=section,
            line=line,
        )
    if any(character in value for character in "`$|<>;&()"):
        raise SwmmParseError(
            "identifier contains shell metacharacters",
            code="UNSAFE_PATH",
            section=section,
            line=line,
        )
    _reject_path_like(value, section=section, line=line, reason="identifier")
    return value


def _number(
    value: str,
    *,
    section: str,
    line: int,
    field_name: str,
    allow_none: bool = False,
) -> float | None:
    if allow_none and value.strip().upper() in {"", "NONE", "NULL", "-"}:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SwmmParseError(
            f"{field_name} must be a finite number, got {value!r}",
            section=section,
            line=line,
        ) from exc
    if not math.isfinite(number):
        raise SwmmParseError(
            f"{field_name} must be finite, got {value!r}",
            section=section,
            line=line,
        )
    return number


def _optional_number(
    tokens: Sequence[str],
    index: int,
    default: float,
    *,
    section: str,
    line: int,
    field_name: str,
) -> float:
    if index >= len(tokens):
        return default
    value = _number(tokens[index], section=section, line=line, field_name=field_name)
    assert value is not None
    return value


def _boolean(value: str, *, section: str, line: int, default: bool = False) -> bool:
    if not value:
        return default
    normalized = value.strip().upper()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise SwmmParseError(
        f"boolean value must be YES/NO, got {value!r}",
        section=section,
        line=line,
    )


@dataclass(frozen=True, slots=True)
class _RecordMixin:
    """Small mapping/alias layer shared by parsed records."""

    def __getitem__(self, key: str) -> Any:
        aliases = {
            "id": "name",
            "stable_key": "name",
            "node_id": "name",
            "upstream_node_id": "upstream_node",
            "downstream_node_id": "downstream_node",
            "longitude": "x",
            "latitude": "y",
        }
        return getattr(self, aliases.get(key, key))

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except AttributeError:
            return default


@dataclass(frozen=True, slots=True)
class SwmmJunction(_RecordMixin):
    name: str
    elevation: float
    max_depth: float = 0.0
    initial_depth: float = 0.0
    surcharge_depth: float = 0.0
    ponded_area: float = 0.0
    line: int | None = None

    @property
    def id(self) -> str:
        return self.name

    @property
    def stable_key(self) -> str:
        return self.name

    @property
    def elevation_m(self) -> float:
        return self.elevation

    @property
    def invert_elevation_m(self) -> float:
        return self.elevation

    @property
    def max_depth_m(self) -> float:
        return self.max_depth

    @property
    def init_depth(self) -> float:
        return self.initial_depth

    @property
    def surcharge_depth_m(self) -> float:
        return self.surcharge_depth

    @property
    def ponded_area_m2(self) -> float:
        return self.ponded_area

    @property
    def kind(self) -> str:
        return "junction"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "elevation": self.elevation,
            "max_depth": self.max_depth,
            "initial_depth": self.initial_depth,
            "surcharge_depth": self.surcharge_depth,
            "ponded_area": self.ponded_area,
            "line": self.line,
        }


@dataclass(frozen=True, slots=True)
class SwmmOutfall(_RecordMixin):
    name: str
    elevation: float
    boundary_type: str = "FREE"
    stage: float | str = 0.0
    gated: bool = False
    line: int | None = None

    @property
    def id(self) -> str:
        return self.name

    @property
    def stable_key(self) -> str:
        return self.name

    @property
    def elevation_m(self) -> float:
        return self.elevation

    @property
    def invert_elevation_m(self) -> float:
        return self.elevation

    @property
    def type(self) -> str:
        return self.boundary_type

    @property
    def kind(self) -> str:
        return "outfall"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "elevation": self.elevation,
            "type": self.boundary_type,
            "stage": self.stage,
            "gated": self.gated,
            "line": self.line,
        }


@dataclass(frozen=True, slots=True)
class SwmmConduit(_RecordMixin):
    name: str
    from_node: str
    to_node: str
    length: float
    roughness: float = 0.01
    in_offset: float = 0.0
    out_offset: float = 0.0
    initial_flow: float = 0.0
    max_flow: float = 0.0
    line: int | None = None

    @property
    def id(self) -> str:
        return self.name

    @property
    def stable_key(self) -> str:
        return self.name

    @property
    def upstream_node(self) -> str:
        return self.from_node

    @property
    def downstream_node(self) -> str:
        return self.to_node

    @property
    def from_node_id(self) -> str:
        return self.from_node

    @property
    def to_node_id(self) -> str:
        return self.to_node

    @property
    def length_m(self) -> float:
        return self.length

    @property
    def roughness_manning_n(self) -> float:
        return self.roughness

    @property
    def in_offset_m(self) -> float:
        return self.in_offset

    @property
    def out_offset_m(self) -> float:
        return self.out_offset

    @property
    def initial_flow_m3s(self) -> float:
        return self.initial_flow

    @property
    def max_flow_m3s(self) -> float:
        return self.max_flow

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "from_node": self.from_node,
            "to_node": self.to_node,
            "length": self.length,
            "roughness": self.roughness,
            "in_offset": self.in_offset,
            "out_offset": self.out_offset,
            "initial_flow": self.initial_flow,
            "max_flow": self.max_flow,
            "line": self.line,
        }


@dataclass(frozen=True, slots=True)
class SwmmSubcatchment(_RecordMixin):
    name: str
    outlet: str | None
    area: float
    width: float = 0.0
    slope: float = 0.0
    impervious: float = 0.0
    n_pervious: float = 0.1
    s_pervious: float = 0.05
    dstore_impervious: float = 0.0
    dstore_pervious: float = 0.0
    line: int | None = None

    @property
    def id(self) -> str:
        return self.name

    @property
    def stable_key(self) -> str:
        return self.name

    @property
    def outlet_node(self) -> str | None:
        return self.outlet

    @property
    def rain_outlet(self) -> str | None:
        return self.outlet

    @property
    def area_m2(self) -> float:
        return self.area

    @property
    def slope_percent(self) -> float:
        return self.slope

    @property
    def impervious_fraction(self) -> float:
        return self.impervious / 100.0

    @property
    def n_perv(self) -> float:
        return self.n_pervious

    @property
    def s_perv(self) -> float:
        return self.s_pervious

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "outlet": self.outlet,
            "area": self.area,
            "width": self.width,
            "slope": self.slope,
            "impervious": self.impervious,
            "n_pervious": self.n_pervious,
            "s_pervious": self.s_pervious,
            "dstore_impervious": self.dstore_impervious,
            "dstore_pervious": self.dstore_pervious,
            "line": self.line,
        }


@dataclass(frozen=True, slots=True)
class SwmmCoordinate(_RecordMixin):
    name: str
    x: float
    y: float
    line: int | None = None

    @property
    def id(self) -> str:
        return self.name

    @property
    def stable_key(self) -> str:
        return self.name

    @property
    def longitude(self) -> float:
        return self.x

    @property
    def latitude(self) -> float:
        return self.y

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "x": self.x, "y": self.y, "line": self.line}


@dataclass(frozen=True, slots=True)
class SwmmSlope(_RecordMixin):
    conduit: str
    upstream_node: str
    upstream_offset: float
    downstream_node: str
    downstream_offset: float
    line: int | None = None

    @property
    def id(self) -> str:
        return self.conduit

    @property
    def stable_key(self) -> str:
        return self.conduit

    @property
    def conduit_id(self) -> str:
        return self.conduit

    @property
    def from_node(self) -> str:
        return self.upstream_node

    @property
    def to_node(self) -> str:
        return self.downstream_node

    def to_dict(self) -> dict[str, Any]:
        return {
            "conduit": self.conduit,
            "upstream_node": self.upstream_node,
            "upstream_offset": self.upstream_offset,
            "downstream_node": self.downstream_node,
            "downstream_offset": self.downstream_offset,
            "line": self.line,
        }


@dataclass(frozen=True, slots=True)
class SwmmRawRow:
    section: str
    tokens: tuple[str, ...]
    line: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"section": self.section, "tokens": list(self.tokens), "line": self.line}


@dataclass(frozen=True, slots=True)
class SwmmNetwork:
    """Canonical, immutable representation of the supported INP sections."""

    junctions: tuple[SwmmJunction, ...] = ()
    outfalls: tuple[SwmmOutfall, ...] = ()
    conduits: tuple[SwmmConduit, ...] = ()
    subcatchments: tuple[SwmmSubcatchment, ...] = ()
    coordinates: tuple[SwmmCoordinate, ...] = ()
    slopes: tuple[SwmmSlope, ...] = ()
    title: tuple[str, ...] = ()
    options: tuple[tuple[str, str], ...] = ()
    raw_sections: tuple[SwmmRawRow, ...] = ()
    section_order: tuple[str, ...] = ()
    issues: tuple[SwmmIssue, ...] = ()
    source_sha256: str | None = None
    filename: str | None = None
    parser_version: str = SWMM_PARSER_VERSION

    def __post_init__(self) -> None:
        # Accept lists/tuples and mappings at the boundary without weakening
        # the immutable representation used by the worker.
        for field_name in (
            "junctions",
            "outfalls",
            "conduits",
            "subcatchments",
            "coordinates",
            "slopes",
            "title",
            "raw_sections",
            "section_order",
            "issues",
        ):
            value = getattr(self, field_name)
            object.__setattr__(self, field_name, tuple(value))
        normalized_options: tuple[tuple[str, str], ...]
        if isinstance(self.options, Mapping):
            normalized_options = tuple(
                (str(key).upper(), str(value)) for key, value in self.options.items()
            )
        else:
            normalized_options = tuple(
                (str(item[0]).upper(), str(item[1])) for item in self.options
            )
        object.__setattr__(self, "options", normalized_options)

    @property
    def nodes(self) -> tuple[SwmmJunction | SwmmOutfall, ...]:
        return self.junctions + self.outfalls

    @property
    def pipes(self) -> tuple[SwmmConduit, ...]:
        """Alias used by generic pipe-oriented callers."""

        return self.conduits

    @property
    def catchments(self) -> tuple[SwmmSubcatchment, ...]:
        """Alias for the SWMM subcatchment collection."""

        return self.subcatchments

    def _structural_issue_codes(self) -> frozenset[str]:
        codes: set[str] = set()
        if not self.nodes:
            codes.add("EMPTY_NETWORK")
        node_by_key: dict[str, SwmmJunction | SwmmOutfall] = {}
        for node in self.nodes:
            key = node.name.casefold()
            if key in node_by_key:
                codes.add("DUPLICATE_ID")
            node_by_key[key] = node
        coordinate_keys = {row.name.casefold() for row in self.coordinates}
        if any(node.name.casefold() not in coordinate_keys for node in self.nodes):
            codes.add("MISSING_COORDINATES")
        slope_by_conduit = {slope.conduit.casefold(): slope for slope in self.slopes}
        physical_segments: dict[frozenset[str], str] = {}
        conduit_keys: set[str] = set()
        for conduit in self.conduits:
            conduit_key = conduit.name.casefold()
            if conduit_key in conduit_keys:
                codes.add("DUPLICATE_ID")
            conduit_keys.add(conduit_key)
            upstream_key = conduit.from_node.casefold()
            downstream_key = conduit.to_node.casefold()
            if upstream_key not in node_by_key or downstream_key not in node_by_key:
                codes.add("DANGLING_NODE")
                continue
            if upstream_key == downstream_key:
                codes.add("SELF_LOOP")
            segment = frozenset((upstream_key, downstream_key))
            if segment in physical_segments:
                codes.add("DUPLICATE_SEGMENT")
            physical_segments[segment] = conduit.name
            slope = slope_by_conduit.get(conduit_key)
            upstream_offset = (
                slope.upstream_offset if slope is not None else conduit.in_offset
            )
            downstream_offset = (
                slope.downstream_offset if slope is not None else conduit.out_offset
            )
            upstream_invert = node_by_key[upstream_key].elevation - upstream_offset
            downstream_invert = (
                node_by_key[downstream_key].elevation - downstream_offset
            )
            if upstream_invert + 1e-9 < downstream_invert:
                codes.add("REVERSE_SLOPE")
        cross_section_ids = {
            row.tokens[0].casefold()
            for row in self.raw_sections
            if row.section == "XSECTIONS" and row.tokens
        }
        if any(key not in cross_section_ids for key in conduit_keys):
            codes.add("MISSING_XSECTIONS")
        return frozenset(codes)

    @property
    def valid(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues) and not (
            self._structural_issue_codes()
        )

    @property
    def exportable(self) -> bool:
        return self.valid

    @property
    def counts(self) -> dict[str, int]:
        return {
            "junctions": len(self.junctions),
            "outfalls": len(self.outfalls),
            "nodes": len(self.nodes),
            "conduits": len(self.conduits),
            "subcatchments": len(self.subcatchments),
            "coordinates": len(self.coordinates),
            "slopes": len(self.slopes),
        }

    @property
    def source_hash(self) -> str | None:
        return self.source_sha256

    @property
    def manifest(self) -> dict[str, Any]:
        return {
            "parser_version": self.parser_version,
            "source_sha256": self.source_sha256,
            "content_sha256": self.content_sha256,
            "counts": self.counts,
            "exportable": self.exportable,
        }

    @property
    def errors(self) -> tuple[SwmmIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> tuple[SwmmIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "warning")

    @property
    def options_map(self) -> dict[str, str]:
        return {key: value for key, value in self.options}

    def _effective_options_map(self) -> dict[str, str]:
        options = dict(self.options_map)
        if "FLOW_UNITS" not in options:
            options.update(SWMM_DEFAULT_OPTIONS)
        return options

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "parser_version": self.parser_version,
            "source_sha256": self.source_sha256,
            "filename": self.filename,
            "title": list(self.title),
            "options": dict(self.options_map),
            "junctions": [row.to_dict() for row in self.junctions],
            "outfalls": [row.to_dict() for row in self.outfalls],
            "conduits": [row.to_dict() for row in self.conduits],
            "subcatchments": [row.to_dict() for row in self.subcatchments],
            "coordinates": [row.to_dict() for row in self.coordinates],
            "slopes": [row.to_dict() for row in self.slopes],
            "raw_sections": [row.to_dict() for row in self.raw_sections],
            "section_order": list(self.section_order),
            "issues": [issue.to_dict() for issue in self.issues],
        }

    as_dict = to_dict

    def canonical_json(self) -> str:
        # ``source_sha256`` and source line numbers describe the upload, not
        # the network.  Excluding them keeps content_sha256 stable across a
        # canonical export/re-import while retaining the source hash above.
        semantic = {
            "title": list(self.title),
            "options": self._effective_options_map(),
            "junctions": sorted(
                (
                    {
                        key: value
                        for key, value in row.to_dict().items()
                        if key != "line"
                    }
                    for row in self.junctions
                ),
                key=lambda row: row["name"].casefold(),
            ),
            "outfalls": sorted(
                (
                    {
                        key: value
                        for key, value in row.to_dict().items()
                        if key != "line"
                    }
                    for row in self.outfalls
                ),
                key=lambda row: row["name"].casefold(),
            ),
            "conduits": sorted(
                (
                    {
                        key: value
                        for key, value in row.to_dict().items()
                        if key != "line"
                    }
                    for row in self.conduits
                ),
                key=lambda row: row["name"].casefold(),
            ),
            "subcatchments": sorted(
                (
                    {
                        key: value
                        for key, value in row.to_dict().items()
                        if key != "line"
                    }
                    for row in self.subcatchments
                ),
                key=lambda row: row["name"].casefold(),
            ),
            "coordinates": sorted(
                (
                    {
                        key: value
                        for key, value in row.to_dict().items()
                        if key != "line"
                    }
                    for row in self.coordinates
                ),
                key=lambda row: row["name"].casefold(),
            ),
            "slopes": sorted(
                (
                    {
                        key: value
                        for key, value in row.to_dict().items()
                        if key != "line"
                    }
                    for row in self.slopes
                ),
                key=lambda row: row["conduit"].casefold(),
            ),
            "raw_sections": [
                {"section": row.section, "tokens": list(row.tokens)}
                for row in self.raw_sections
            ],
        }
        return json.dumps(
            semantic,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    def __getitem__(self, key: str) -> Any:
        aliases = {
            "junctions": "junctions",
            "junction": "junctions",
            "outfalls": "outfalls",
            "outfall": "outfalls",
            "conduits": "conduits",
            "pipes": "conduits",
            "conduit": "conduits",
            "subcatchments": "subcatchments",
            "subcatchment": "subcatchments",
            "coordinates": "coordinates",
            "coordinate": "coordinates",
            "slopes": "slopes",
            "slope": "slopes",
        }
        if key in aliases:
            return getattr(self, aliases[key])
        if key in {"title", "options", "raw_sections", "section_order", "issues"}:
            return getattr(self, key)
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def to_topology_nodes(self) -> tuple[Any, ...]:
        """Return nodes in the shape consumed by ``topology.validate_topology``."""

        coordinates = {item.name.casefold(): item for item in self.coordinates}
        result = []
        for node in self.nodes:
            coordinate = coordinates.get(node.name.casefold())
            result.append(
                _TopologyNode(
                    stable_key=node.name,
                    longitude=coordinate.x if coordinate else None,
                    latitude=coordinate.y if coordinate else None,
                    invert_elevation_m=node.elevation,
                    kind=node.kind,
                )
            )
        return tuple(result)

    def to_topology_pipes(self) -> tuple[Any, ...]:
        """Return conduits in the shape consumed by topology validation."""

        return tuple(
            _TopologyPipe(
                stable_key=pipe.name,
                upstream_node_key=pipe.from_node,
                downstream_node_key=pipe.to_node,
                length_m=pipe.length,
            )
            for pipe in self.conduits
        )


# Avoid importing the topology module at module import time.  These tiny local
# projections preserve compatibility with the existing topology service while
# keeping this parser usable in isolation and free of a circular dependency.
@dataclass(frozen=True, slots=True)
class _TopologyNode:
    stable_key: str
    longitude: float | None
    latitude: float | None
    invert_elevation_m: float
    kind: str


@dataclass(frozen=True, slots=True)
class _TopologyPipe:
    stable_key: str
    upstream_node_key: str
    downstream_node_key: str
    length_m: float


class _Parser:
    def __init__(
        self,
        *,
        strict: bool,
        filename: str | None,
        coordinate_mode: str = "wgs84",
        local_to_wgs84: Any = None,
    ) -> None:
        self.strict = strict
        self.filename = filename
        normalized_mode = str(coordinate_mode).strip().lower()
        if normalized_mode not in _SWMM_COORDINATE_MODES:
            raise SwmmParseError(
                "coordinate_mode must be 'wgs84' or 'local'",
                code="INVALID_COORDINATE_MODE",
            )
        if normalized_mode == "wgs84" and local_to_wgs84 is not None:
            raise SwmmParseError(
                "local_to_wgs84 is only valid in local coordinate mode",
                code="INVALID_COORDINATE_MODE",
            )
        try:
            transform = _normalise_local_transform(local_to_wgs84)
        except ValueError as exc:
            raise SwmmParseError(str(exc), code="INVALID_COORDINATE_MODE") from exc
        self.coordinate_mode = normalized_mode
        self.local_to_wgs84 = transform
        self.issues: list[SwmmIssue] = []

    def fail(
        self,
        message: str,
        *,
        code: str = "INVALID_INP",
        section: str | None = None,
        line: int | None = None,
    ) -> NoReturn:
        raise SwmmParseError(message, code=code, section=section, line=line)

    def issue(
        self,
        code: str,
        message: str,
        *,
        section: str | None = None,
        line: int | None = None,
        entity_type: str | None = None,
        entity_key: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.issues.append(
            SwmmIssue(
                code=code,
                message=message,
                severity="error",
                section=section,
                line=line,
                entity_type=entity_type,
                entity_key=entity_key,
                details=dict(details or {}),
            )
        )

    def parse(
        self,
        source: str | bytes | bytearray,
        *,
        max_bytes: int,
        max_lines: int,
        max_line_length: int,
    ) -> SwmmNetwork:
        if isinstance(source, str):
            try:
                raw = source.encode("utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                self.fail(
                    f"INP is not valid Unicode text: {exc}", code="INVALID_ENCODING"
                )
            if raw.startswith(b"\xef\xbb\xbf"):
                text = source.lstrip("\ufeff")
            else:
                text = source
        elif isinstance(source, (bytes, bytearray)):
            raw = bytes(source)
            if len(raw) > max_bytes:
                self.fail(
                    f"input exceeds {max_bytes} bytes",
                    code="INPUT_TOO_LARGE",
                )
            try:
                text = raw.decode("utf-8-sig", errors="strict")
            except UnicodeDecodeError as exc:
                self.fail(f"INP is not valid UTF-8: {exc}", code="INVALID_ENCODING")
        else:
            raise TypeError("SWMM input must be str, bytes, or bytearray")

        if len(raw) > max_bytes:
            self.fail(f"input exceeds {max_bytes} bytes", code="INPUT_TOO_LARGE")
        if "\x00" in text or _CONTROL_RE.search(text):
            self.fail(
                "INP contains NUL or forbidden control characters", code="INVALID_INP"
            )
        lines = text.splitlines()
        if len(lines) > max_lines:
            self.fail(f"input exceeds {max_lines} lines", code="INPUT_TOO_LARGE")

        rows: dict[str, list[tuple[tuple[str, ...], int]]] = {
            section: [] for section in SUPPORTED_SECTIONS
        }
        order: list[str] = []
        current: str | None = None
        title: list[str] = []
        options: list[tuple[str, str]] = []
        seen_sections: set[str] = set()
        raw_ids: dict[str, set[str]] = {}

        for line_number, original in enumerate(lines, start=1):
            if len(original) > max_line_length:
                self.fail(
                    f"line exceeds {max_line_length} characters",
                    code="INPUT_TOO_LARGE",
                    line=line_number,
                )
            line = self._content_line(original, line_number)
            if not line:
                continue
            header = _SECTION_RE.fullmatch(line)
            if header:
                section = " ".join(header.group(1).upper().split())
                if section in FORBIDDEN_SECTION_WORDS or any(
                    word in section for word in FORBIDDEN_SECTION_WORDS
                ):
                    self.fail(
                        f"path or executable section [{section}] is forbidden",
                        code="UNSAFE_PATH",
                        section=section,
                        line=line_number,
                    )
                if section not in SUPPORTED_SECTIONS:
                    self.fail(
                        f"unsupported section [{section}]",
                        code="UNSUPPORTED_SECTION",
                        section=section,
                        line=line_number,
                    )
                if section in seen_sections:
                    self.fail(
                        f"duplicate section [{section}]",
                        section=section,
                        line=line_number,
                    )
                seen_sections.add(section)
                current = section
                order.append(section)
                continue
            if current is None:
                self.fail(
                    "data appears before the first [SECTION]",
                    line=line_number,
                )
            assert current is not None
            if current == "TITLE":
                title.append(line)
                continue
            tokens = self._tokens(line, current, line_number)
            if not tokens:
                continue
            for token in tokens:
                _reject_path_like(token, section=current, line=line_number)
            if current == "OPTIONS":
                if len(tokens) < 2:
                    self.fail(
                        "OPTIONS rows require a name and value",
                        code="INVALID_OPTION",
                        section=current,
                        line=line_number,
                    )
                try:
                    _validate_option_tokens(tokens)
                except ValueError as exc:
                    self.fail(
                        str(exc),
                        code="INVALID_OPTION",
                        section=current,
                        line=line_number,
                    )
                key = tokens[0].upper()
                if any(existing == key for existing, _ in options):
                    self.issue(
                        "DUPLICATE_OPTION",
                        f"duplicate option {key}",
                        section=current,
                        line=line_number,
                        entity_type="option",
                        entity_key=key,
                    )
                    continue
                options.append((key, " ".join(tokens[1:])))
            elif current in RAW_SECTIONS:
                try:
                    _validate_raw_section_tokens(current, tokens)
                except ValueError as exc:
                    self.fail(
                        str(exc),
                        code="INVALID_ROW",
                        section=current,
                        line=line_number,
                    )
                identifier_index = _RAW_SECTION_ID_RULES.get(current)
                if identifier_index is not None:
                    identifier = tokens[identifier_index].casefold()
                    section_ids = raw_ids.setdefault(current, set())
                    if identifier in section_ids:
                        self.issue(
                            "DUPLICATE_ID",
                            f"duplicate {current} identifier",
                            section=current,
                            line=line_number,
                            entity_type=current.lower(),
                            entity_key=tokens[identifier_index],
                        )
                        continue
                    section_ids.add(identifier)
                rows[current].append((tokens, line_number))
            elif len(tokens) > SWMM_MAX_TOKENS_PER_LINE:
                self.fail(
                    f"row has more than {SWMM_MAX_TOKENS_PER_LINE} tokens",
                    section=current,
                    line=line_number,
                )
            else:
                rows[current].append((tokens, line_number))

        if not order:
            self.fail("INP contains no sections", code="EMPTY_INP")
        network = self._build(
            rows,
            order=order,
            title=tuple(title),
            options=tuple(options),
            source_sha256=hashlib.sha256(raw).hexdigest(),
        )
        if self.strict and network.issues:
            first = network.issues[0]
            raise SwmmParseError(
                first.message,
                code=first.code,
                line=first.line,
                section=first.section,
                issues=network.issues,
            )
        return network

    @staticmethod
    def _content_line(raw_line: str, line_number: int) -> str:
        stripped = raw_line.strip()
        if stripped.startswith("//") or stripped.startswith("#"):
            return ""
        # SWMM comments start with a semicolon.  Stripping the first semicolon
        # also correctly handles a header followed by ``; comment``.
        semicolon = stripped.find(";")
        if semicolon >= 0:
            stripped = stripped[:semicolon].strip()
        return stripped

    def _tokens(self, line: str, section: str, line_number: int) -> tuple[str, ...]:
        if any(character in line for character in "`$|<>&"):
            self.fail(
                "shell metacharacters are not allowed in an INP row",
                code="UNSAFE_INP",
                section=section,
                line=line_number,
            )
        # Inspect the unquoted line as well: shlex can consume a Windows
        # backslash and hide a path before token-level validation runs.
        _reject_path_like(line, section=section, line=line_number)
        if len(line.split()) > SWMM_MAX_TOKENS_PER_LINE:
            self.fail(
                f"row has more than {SWMM_MAX_TOKENS_PER_LINE} tokens",
                section=section,
                line=line_number,
            )
        try:
            tokens = tuple(shlex.split(line, comments=False, posix=True))
        except ValueError as exc:
            self.fail(
                f"malformed quoted value: {exc}",
                section=section,
                line=line_number,
            )
        if len(tokens) > SWMM_MAX_TOKENS_PER_LINE:
            self.fail(
                f"row has more than {SWMM_MAX_TOKENS_PER_LINE} tokens",
                section=section,
                line=line_number,
            )
        return tokens

    def _build(
        self,
        rows: Mapping[str, Sequence[tuple[tuple[str, ...], int]]],
        *,
        order: Sequence[str],
        title: tuple[str, ...],
        options: tuple[tuple[str, str], ...],
        source_sha256: str,
    ) -> SwmmNetwork:
        junctions = self._parse_junctions(rows.get("JUNCTIONS", ()))
        outfalls = self._parse_outfalls(rows.get("OUTFALLS", ()))
        conduits = self._parse_conduits(rows.get("CONDUITS", ()))
        subcatchments = self._parse_subcatchments(rows.get("SUBCATCHMENTS", ()))
        coordinates = self._parse_coordinates(rows.get("COORDINATES", ()))
        slopes = self._parse_slopes(rows.get("SLOPES", ()))
        raw_sections = tuple(
            SwmmRawRow(section, tuple(tokens), line)
            for section in order
            if section in RAW_SECTIONS
            for tokens, line in rows.get(section, ())
        )

        self._validate_references(
            junctions,
            outfalls,
            conduits,
            subcatchments,
            coordinates,
            slopes,
            raw_sections,
        )
        issues = tuple(
            sorted(
                self.issues,
                key=lambda issue: (
                    issue.severity,
                    issue.code,
                    issue.section or "",
                    issue.line or 0,
                    issue.entity_type or "",
                    issue.entity_key or "",
                ),
            )
        )
        return SwmmNetwork(
            junctions=junctions,
            outfalls=outfalls,
            conduits=conduits,
            subcatchments=subcatchments,
            coordinates=coordinates,
            slopes=slopes,
            title=title,
            options=options,
            raw_sections=raw_sections,
            section_order=tuple(order),
            issues=issues,
            source_sha256=source_sha256,
            filename=self.filename,
        )

    def _parse_junctions(
        self, rows: Sequence[tuple[tuple[str, ...], int]]
    ) -> tuple[SwmmJunction, ...]:
        result: list[SwmmJunction] = []
        seen: set[str] = set()
        for tokens, line in rows:
            if len(tokens) < 2:
                self.fail(
                    "JUNCTIONS row requires name and elevation",
                    section="JUNCTIONS",
                    line=line,
                )
            name = _validate_identifier(tokens[0], section="JUNCTIONS", line=line)
            key = name.casefold()
            if key in seen:
                self.issue(
                    "DUPLICATE_ID",
                    "duplicate junction identifier",
                    section="JUNCTIONS",
                    line=line,
                    entity_type="junction",
                    entity_key=name,
                )
                continue
            seen.add(key)
            values = [
                _optional_number(
                    tokens,
                    index,
                    default,
                    section="JUNCTIONS",
                    line=line,
                    field_name=field,
                )
                for index, default, field in (
                    (2, 0.0, "max_depth"),
                    (3, 0.0, "initial_depth"),
                    (4, 0.0, "surcharge_depth"),
                    (5, 0.0, "ponded_area"),
                )
            ]
            if any(value < 0 for value in values):
                self.fail(
                    "JUNCTIONS depths and ponded area must be non-negative",
                    section="JUNCTIONS",
                    line=line,
                )
            elevation = _number(
                tokens[1],
                section="JUNCTIONS",
                line=line,
                field_name="elevation",
            )
            assert elevation is not None
            result.append(
                SwmmJunction(
                    name,
                    elevation,
                    values[0],
                    values[1],
                    values[2],
                    values[3],
                    line=line,
                )
            )
        return tuple(result)

    def _parse_outfalls(
        self, rows: Sequence[tuple[tuple[str, ...], int]]
    ) -> tuple[SwmmOutfall, ...]:
        result: list[SwmmOutfall] = []
        seen: set[str] = set()
        for tokens, line in rows:
            if len(tokens) < 2:
                self.fail(
                    "OUTFALLS row requires name and elevation",
                    section="OUTFALLS",
                    line=line,
                )
            name = _validate_identifier(tokens[0], section="OUTFALLS", line=line)
            key = name.casefold()
            if key in seen:
                self.issue(
                    "DUPLICATE_ID",
                    "duplicate outfall identifier",
                    section="OUTFALLS",
                    line=line,
                    entity_type="outfall",
                    entity_key=name,
                )
                continue
            seen.add(key)
            boundary_type = tokens[2].upper() if len(tokens) > 2 else "FREE"
            if boundary_type not in _VALID_OUTFALL_TYPES:
                self.fail(
                    f"unsupported OUTFALL type {boundary_type!r}",
                    section="OUTFALLS",
                    line=line,
                )
            elevation = (
                _number(
                    tokens[1], section="OUTFALLS", line=line, field_name="elevation"
                )
                or 0.0
            )
            # SWMM permits the optional Stage column to be omitted for a
            # FREE/NORMAL outfall, in which case the fourth token is Gated.
            # TIMESERIES is different: its Stage value is a time-series ID.
            stage_index: int | None = 3
            gated_index = 4
            if len(tokens) == 4 and tokens[3].upper() in _TRUE_VALUES | _FALSE_VALUES:
                if boundary_type == "TIMESERIES":
                    self.fail(
                        "TIMESERIES outfall requires a time-series identifier",
                        section="OUTFALLS",
                        line=line,
                    )
                stage_index = None
                gated_index = 3
            stage: float | str
            if boundary_type == "TIMESERIES":
                if stage_index is None or stage_index >= len(tokens):
                    self.fail(
                        "TIMESERIES outfall requires a time-series identifier",
                        section="OUTFALLS",
                        line=line,
                    )
                stage = _validate_identifier(
                    tokens[stage_index], section="OUTFALLS", line=line
                )
            else:
                stage = (
                    0.0
                    if stage_index is None
                    else _optional_number(
                        tokens,
                        stage_index,
                        0.0,
                        section="OUTFALLS",
                        line=line,
                        field_name="stage",
                    )
                )
            gated = (
                _boolean(tokens[gated_index], section="OUTFALLS", line=line)
                if len(tokens) > gated_index
                else False
            )
            result.append(
                SwmmOutfall(name, elevation, boundary_type, stage, gated, line=line)
            )
        return tuple(result)

    def _parse_conduits(
        self, rows: Sequence[tuple[tuple[str, ...], int]]
    ) -> tuple[SwmmConduit, ...]:
        result: list[SwmmConduit] = []
        seen: set[str] = set()
        for tokens, line in rows:
            if len(tokens) < 5:
                self.fail(
                    "CONDUITS row requires name, from, to, length, and roughness",
                    section="CONDUITS",
                    line=line,
                )
            name = _validate_identifier(tokens[0], section="CONDUITS", line=line)
            if name.casefold() in seen:
                self.issue(
                    "DUPLICATE_ID",
                    "duplicate conduit identifier",
                    section="CONDUITS",
                    line=line,
                    entity_type="conduit",
                    entity_key=name,
                )
                continue
            seen.add(name.casefold())
            from_node = _validate_identifier(tokens[1], section="CONDUITS", line=line)
            to_node = _validate_identifier(tokens[2], section="CONDUITS", line=line)
            length = (
                _number(tokens[3], section="CONDUITS", line=line, field_name="length")
                or 0.0
            )
            roughness = (
                _number(
                    tokens[4], section="CONDUITS", line=line, field_name="roughness"
                )
                or 0.0
            )
            if length <= 0 or roughness <= 0:
                self.fail(
                    "CONDUITS length and roughness must be positive",
                    section="CONDUITS",
                    line=line,
                )
            values = [
                _optional_number(
                    tokens,
                    index,
                    default,
                    section="CONDUITS",
                    line=line,
                    field_name=field,
                )
                for index, default, field in (
                    (5, 0.0, "in_offset"),
                    (6, 0.0, "out_offset"),
                    (7, 0.0, "initial_flow"),
                    (8, 0.0, "max_flow"),
                )
            ]
            if values[3] < 0:
                self.fail(
                    "CONDUITS max_flow must be non-negative",
                    section="CONDUITS",
                    line=line,
                )
            result.append(
                SwmmConduit(
                    name,
                    from_node,
                    to_node,
                    length,
                    roughness,
                    values[0],
                    values[1],
                    values[2],
                    values[3],
                    line=line,
                )
            )
        return tuple(result)

    def _parse_subcatchments(
        self, rows: Sequence[tuple[tuple[str, ...], int]]
    ) -> tuple[SwmmSubcatchment, ...]:
        result: list[SwmmSubcatchment] = []
        seen: set[str] = set()
        for tokens, line in rows:
            if len(tokens) < 3:
                self.fail(
                    "SUBCATCHMENTS row requires name, outlet, and area",
                    section="SUBCATCHMENTS",
                    line=line,
                )
            name = _validate_identifier(tokens[0], section="SUBCATCHMENTS", line=line)
            if name.casefold() in seen:
                self.issue(
                    "DUPLICATE_ID",
                    "duplicate subcatchment identifier",
                    section="SUBCATCHMENTS",
                    line=line,
                    entity_type="subcatchment",
                    entity_key=name,
                )
                continue
            seen.add(name.casefold())
            outlet: str | None = (
                None
                if tokens[1].upper() in {"-1", "NONE", "NULL"}
                else _validate_identifier(tokens[1], section="SUBCATCHMENTS", line=line)
            )
            area = (
                _number(
                    tokens[2], section="SUBCATCHMENTS", line=line, field_name="area"
                )
                or 0.0
            )
            if area <= 0:
                self.fail(
                    "SUBCATCHMENTS area must be positive",
                    section="SUBCATCHMENTS",
                    line=line,
                )
            values = [
                _optional_number(
                    tokens,
                    index,
                    default,
                    section="SUBCATCHMENTS",
                    line=line,
                    field_name=field,
                )
                for index, default, field in (
                    (3, 0.0, "width"),
                    (4, 0.0, "slope"),
                    (5, 0.0, "impervious"),
                    (6, 0.1, "n_pervious"),
                    (7, 0.05, "s_pervious"),
                    (8, 0.0, "dstore_impervious"),
                    (9, 0.0, "dstore_pervious"),
                )
            ]
            if (
                values[0] < 0
                or values[1] < 0
                or not 0 <= values[2] <= 100
                or values[3] <= 0
                or values[4] < 0
                or values[5] < 0
                or values[6] < 0
            ):
                self.fail(
                    "SUBCATCHMENTS numeric values are out of range",
                    section="SUBCATCHMENTS",
                    line=line,
                )
            result.append(
                SwmmSubcatchment(
                    name,
                    outlet,
                    area,
                    values[0],
                    values[1],
                    values[2],
                    values[3],
                    values[4],
                    values[5],
                    values[6],
                    line=line,
                )
            )
        return tuple(result)

    def _parse_coordinates(
        self, rows: Sequence[tuple[tuple[str, ...], int]]
    ) -> tuple[SwmmCoordinate, ...]:
        result: list[SwmmCoordinate] = []
        seen: set[str] = set()
        for tokens, line in rows:
            if len(tokens) != 3:
                self.fail(
                    "COORDINATES row requires name, x, and y",
                    section="COORDINATES",
                    line=line,
                )
            name = _validate_identifier(tokens[0], section="COORDINATES", line=line)
            if name.casefold() in seen:
                self.issue(
                    "DUPLICATE_ID",
                    "duplicate coordinate identifier",
                    section="COORDINATES",
                    line=line,
                    entity_type="coordinate",
                    entity_key=name,
                )
                continue
            seen.add(name.casefold())
            x = (
                _number(tokens[1], section="COORDINATES", line=line, field_name="x")
                or 0.0
            )
            y = (
                _number(tokens[2], section="COORDINATES", line=line, field_name="y")
                or 0.0
            )
            if self.coordinate_mode == "local":
                if self.local_to_wgs84 is None:
                    self.issue(
                        "CRS_UNKNOWN",
                        "local coordinates require an explicit "
                        "local_to_wgs84 transform",
                        section="COORDINATES",
                        line=line,
                        entity_type="coordinate",
                        entity_key=name,
                    )
                else:
                    x, y = _transform_local_coordinate(x, y, self.local_to_wgs84)
            if not (
                self.coordinate_mode == "local" and self.local_to_wgs84 is None
            ) and (not -180 <= x <= 180 or not -90 <= y <= 90):
                self.fail(
                    "COORDINATES must be EPSG:4326 lon/lat "
                    "after the declared transform",
                    section="COORDINATES",
                    line=line,
                )
            result.append(SwmmCoordinate(name, x, y, line=line))
        return tuple(result)

    def _parse_slopes(
        self, rows: Sequence[tuple[tuple[str, ...], int]]
    ) -> tuple[SwmmSlope, ...]:
        result: list[SwmmSlope] = []
        seen: set[str] = set()
        for tokens, line in rows:
            if len(tokens) != 5:
                self.fail(
                    "SLOPES row requires conduit and four endpoint/offset fields",
                    section="SLOPES",
                    line=line,
                )
            conduit = _validate_identifier(tokens[0], section="SLOPES", line=line)
            if conduit.casefold() in seen:
                self.issue(
                    "DUPLICATE_ID",
                    "duplicate slope for conduit",
                    section="SLOPES",
                    line=line,
                    entity_type="slope",
                    entity_key=conduit,
                )
                continue
            seen.add(conduit.casefold())
            upstream_offset = _number(
                tokens[2],
                section="SLOPES",
                line=line,
                field_name="upstream_offset",
            )
            downstream_offset = _number(
                tokens[4],
                section="SLOPES",
                line=line,
                field_name="downstream_offset",
            )
            assert upstream_offset is not None
            assert downstream_offset is not None
            result.append(
                SwmmSlope(
                    conduit,
                    _validate_identifier(tokens[1], section="SLOPES", line=line),
                    upstream_offset,
                    _validate_identifier(tokens[3], section="SLOPES", line=line),
                    downstream_offset,
                    line=line,
                )
            )
        return tuple(result)

    def _validate_references(
        self,
        junctions: Sequence[SwmmJunction],
        outfalls: Sequence[SwmmOutfall],
        conduits: Sequence[SwmmConduit],
        subcatchments: Sequence[SwmmSubcatchment],
        coordinates: Sequence[SwmmCoordinate],
        slopes: Sequence[SwmmSlope],
        raw_sections: Sequence[SwmmRawRow],
    ) -> None:
        nodes: dict[str, SwmmJunction | SwmmOutfall] = {}

        def register_node(node: SwmmJunction | SwmmOutfall, kind: str) -> None:
            key = node.name.casefold()
            if key in nodes:
                self.issue(
                    "DUPLICATE_ID",
                    "identifier is used by both a junction and an outfall",
                    section=kind.upper(),
                    entity_type=kind,
                    entity_key=node.name,
                )
            else:
                nodes[key] = node

        for junction in junctions:
            register_node(junction, "junction")
        for outfall in outfalls:
            register_node(outfall, "outfall")
        slope_by_conduit = {slope.conduit.casefold(): slope for slope in slopes}
        physical_segments: dict[frozenset[str], str] = {}
        for conduit in conduits:
            upstream_key = conduit.from_node.casefold()
            downstream_key = conduit.to_node.casefold()
            if upstream_key in nodes and downstream_key in nodes:
                segment = frozenset((upstream_key, downstream_key))
                first_conduit = physical_segments.get(segment)
                if first_conduit is not None:
                    self.issue(
                        "DUPLICATE_SEGMENT",
                        "conduit duplicates an existing physical segment",
                        section="CONDUITS",
                        line=conduit.line,
                        entity_type="conduit",
                        entity_key=conduit.name,
                        details={
                            "duplicate_of": first_conduit,
                            "node_keys": sorted((conduit.from_node, conduit.to_node)),
                        },
                    )
                else:
                    physical_segments[segment] = conduit.name
                upstream = nodes[upstream_key]
                downstream = nodes[downstream_key]
                slope = slope_by_conduit.get(conduit.name.casefold())
                upstream_offset = (
                    slope.upstream_offset if slope is not None else conduit.in_offset
                )
                downstream_offset = (
                    slope.downstream_offset if slope is not None else conduit.out_offset
                )
                upstream_invert = upstream.elevation - upstream_offset
                downstream_invert = downstream.elevation - downstream_offset
                if upstream_invert + 1e-9 < downstream_invert:
                    self.issue(
                        "REVERSE_SLOPE",
                        "conduit invert rises from upstream to downstream",
                        section="CONDUITS",
                        line=conduit.line,
                        entity_type="conduit",
                        entity_key=conduit.name,
                        details={
                            "upstream_node_key": upstream.name,
                            "downstream_node_key": downstream.name,
                            "upstream_invert_elevation_m": upstream_invert,
                            "downstream_invert_elevation_m": downstream_invert,
                        },
                    )
        entity_ids: dict[str, tuple[str, str]] = {
            key: ("node", node.name) for key, node in nodes.items()
        }
        for conduit in conduits:
            key = conduit.name.casefold()
            if key in entity_ids:
                self.issue(
                    "DUPLICATE_ID",
                    "conduit identifier is already used by another SWMM object",
                    section="CONDUITS",
                    entity_type="conduit",
                    entity_key=conduit.name,
                    details={
                        "existing_type": entity_ids[key][0],
                        "existing_key": entity_ids[key][1],
                    },
                )
            else:
                entity_ids[key] = ("conduit", conduit.name)
        for subcatchment in subcatchments:
            key = subcatchment.name.casefold()
            if key in entity_ids:
                self.issue(
                    "DUPLICATE_ID",
                    "subcatchment identifier is already used by another SWMM object",
                    section="SUBCATCHMENTS",
                    entity_type="subcatchment",
                    entity_key=subcatchment.name,
                    details={
                        "existing_type": entity_ids[key][0],
                        "existing_key": entity_ids[key][1],
                    },
                )
            else:
                entity_ids[key] = ("subcatchment", subcatchment.name)
        coordinate_by_id: dict[str, SwmmCoordinate] = {}
        for coordinate in coordinates:
            key = coordinate.name.casefold()
            if key in coordinate_by_id:
                self.issue(
                    "DUPLICATE_ID",
                    "duplicate coordinate identifier",
                    section="COORDINATES",
                    entity_type="coordinate",
                    entity_key=coordinate.name,
                )
            else:
                coordinate_by_id[key] = coordinate
            if key not in nodes:
                self.issue(
                    "DANGLING_NODE",
                    "coordinate refers to an unknown node",
                    line=coordinate.line,
                    section="COORDINATES",
                    entity_type="coordinate",
                    entity_key=coordinate.name,
                )
        for key, stored_node in nodes.items():
            if key not in coordinate_by_id:
                self.issue(
                    "MISSING_COORDINATES",
                    "node has no COORDINATES row",
                    line=stored_node.line,
                    section=stored_node.kind.upper(),
                    entity_type=stored_node.kind,
                    entity_key=stored_node.name,
                )
        if not nodes:
            self.issue(
                "EMPTY_NETWORK",
                "INP contains no junction or outfall records",
                section="JUNCTIONS",
            )

        for conduit in conduits:
            missing = [
                node
                for node in (conduit.from_node, conduit.to_node)
                if node.casefold() not in nodes
            ]
            if missing:
                self.issue(
                    "DANGLING_NODE",
                    "conduit refers to an unknown node",
                    section="CONDUITS",
                    entity_type="conduit",
                    entity_key=conduit.name,
                    details={"missing_node_keys": missing},
                )
            if conduit.from_node.casefold() == conduit.to_node.casefold():
                self.issue(
                    "SELF_LOOP",
                    "conduit endpoints are identical",
                    section="CONDUITS",
                    entity_type="conduit",
                    entity_key=conduit.name,
                )
        for subcatchment in subcatchments:
            if (
                subcatchment.outlet is not None
                and subcatchment.outlet.casefold() not in nodes
            ):
                self.issue(
                    "DANGLING_NODE",
                    "subcatchment refers to an unknown outlet",
                    section="SUBCATCHMENTS",
                    entity_type="subcatchment",
                    entity_key=subcatchment.name,
                    details={"missing_node_keys": [subcatchment.outlet]},
                )
        conduits_by_id = {pipe.name.casefold(): pipe for pipe in conduits}
        cross_section_ids: set[str] = set()
        for row in raw_sections:
            if row.section != "XSECTIONS":
                continue
            cross_section_id = row.tokens[0].casefold()
            cross_section_ids.add(cross_section_id)
            if cross_section_id not in conduits_by_id:
                self.issue(
                    "DANGLING_NODE",
                    "XSECTIONS row refers to an unknown conduit",
                    section="XSECTIONS",
                    line=row.line,
                    entity_type="xsections",
                    entity_key=row.tokens[0],
                )
        for conduit in conduits:
            if conduit.name.casefold() not in cross_section_ids:
                self.issue(
                    "MISSING_XSECTIONS",
                    f"conduit {conduit.name} has no XSECTIONS row",
                    section="XSECTIONS",
                    line=conduit.line,
                    entity_type="conduit",
                    entity_key=conduit.name,
                )
        for slope in slopes:
            slope_conduit = conduits_by_id.get(slope.conduit.casefold())
            if slope_conduit is None:
                self.issue(
                    "DANGLING_NODE",
                    "slope refers to an unknown conduit",
                    section="SLOPES",
                    entity_type="slope",
                    entity_key=slope.conduit,
                )
                continue
            if (
                slope.upstream_node.casefold() != slope_conduit.from_node.casefold()
                or slope.downstream_node.casefold() != slope_conduit.to_node.casefold()
            ):
                self.issue(
                    "INVALID_SLOPE",
                    "slope endpoints do not match conduit direction",
                    section="SLOPES",
                    entity_type="slope",
                    entity_key=slope.conduit,
                )


def parse_swmm(
    source: str | bytes | bytearray,
    *,
    strict: bool = True,
    filename: str | None = None,
    max_bytes: int = SWMM_MAX_INPUT_BYTES,
    max_lines: int = SWMM_MAX_LINES,
    max_line_length: int = SWMM_MAX_LINE_LENGTH,
    coordinate_mode: str = "wgs84",
    local_to_wgs84: Any = None,
) -> SwmmNetwork:
    """Parse a bounded SWMM INP document into a canonical immutable model.

    ``strict=True`` (the default) rejects all error issues, including missing
    coordinates and dangling references.  ``strict=False`` is useful for a
    validation worker that needs to return all issues in one report; the
    resulting model is intentionally marked invalid and must not be exported.
    """

    if max_bytes < 1 or max_lines < 1 or max_line_length < 1:
        raise ValueError("parser limits must be positive")
    return _Parser(
        strict=strict,
        filename=filename,
        coordinate_mode=coordinate_mode,
        local_to_wgs84=local_to_wgs84,
    ).parse(
        source,
        max_bytes=max_bytes,
        max_lines=max_lines,
        max_line_length=max_line_length,
    )


def parse_swmm_inp(source: str | bytes | bytearray, **kwargs: Any) -> SwmmNetwork:
    """Explicit INP spelling for :func:`parse_swmm`."""

    return parse_swmm(source, **kwargs)


def parse_inp(source: str | bytes | bytearray, **kwargs: Any) -> SwmmNetwork:
    """Short compatibility alias used by import workers."""

    return parse_swmm(source, **kwargs)


def parse_inp_text(source: str | bytes | bytearray, **kwargs: Any) -> SwmmNetwork:
    """Text-oriented alias retained for callers that name the input format."""

    return parse_swmm(source, **kwargs)


class SwmmParser:
    """Small state-free parser facade for dependency injection."""

    parser_version = SWMM_PARSER_VERSION

    def parse(self, source: str | bytes | bytearray, **kwargs: Any) -> SwmmNetwork:
        return parse_swmm(source, **kwargs)

    __call__ = parse


class SwmmImporter(SwmmParser):
    """Named importer facade; it performs no file or subprocess I/O."""


SWMMParser = SwmmParser
SWMMImporter = SwmmImporter
SwmmImportResult = SwmmNetwork
SwmmModel = SwmmNetwork
SwmmImportError = SwmmParseError
SwmmValidationError = SwmmParseError


__all__ = [
    "FORBIDDEN_SECTION_WORDS",
    "RAW_SECTIONS",
    "SUPPORTED_SECTIONS",
    "SWMM_DEFAULT_OPTIONS",
    "SWMMImporter",
    "SWMMParser",
    "SWMM_MAX_INPUT_BYTES",
    "SWMM_MAX_LINES",
    "SWMM_MAX_LINE_LENGTH",
    "SWMM_MAX_TOKENS_PER_LINE",
    "SWMM_PARSER_VERSION",
    "SwmmConduit",
    "SwmmCoordinate",
    "SwmmImportResult",
    "SwmmImportError",
    "SwmmImporter",
    "SwmmIssue",
    "SwmmJunction",
    "SwmmModel",
    "SwmmNetwork",
    "SwmmOutfall",
    "SwmmParseError",
    "SwmmParser",
    "SwmmRawRow",
    "SwmmSlope",
    "SwmmSubcatchment",
    "SwmmSyntaxError",
    "SwmmValidationError",
    "ValidationIssue",
    "parse_inp",
    "parse_inp_text",
    "parse_swmm",
    "parse_swmm_inp",
]
