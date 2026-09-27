"""Deterministic SWMM ``.inp`` exporter.

The exporter consumes the immutable model produced by
:mod:`app.services.hydraulic.importers.swmm` (or an equivalent mapping) and
writes a bounded, self-contained INP.  It never uses a user path, resolves a
file, or starts a solver.  A default round-trip check reparses the generated
text and compares the supported semantic records before returning it.
"""

from __future__ import annotations

import math
import shlex
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NoReturn

from app.services.hydraulic.importers.swmm import (
    _PATH_RE,
    FORBIDDEN_SECTION_WORDS,
    RAW_SECTIONS,
    SUPPORTED_SECTIONS,
    SWMM_DEFAULT_OPTIONS,
    SwmmConduit,
    SwmmCoordinate,
    SwmmIssue,
    SwmmJunction,
    SwmmNetwork,
    SwmmOutfall,
    SwmmParseError,
    SwmmRawRow,
    SwmmSlope,
    SwmmSubcatchment,
    _validate_raw_section_tokens,
    parse_swmm,
)

SWMM_EXPORTER_VERSION = "swmm-inp-exporter-v1"
SWMM_EXPORT_ROUNDTRIP_VERSION = "swmm-inp-roundtrip-v1"


class SwmmExportError(ValueError):
    """Raised when a model cannot be represented by a runnable INP."""

    def __init__(self, message: str, *, code: str = "EXPORT_NOT_REPRESENTABLE") -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _error(message: str, code: str = "EXPORT_NOT_REPRESENTABLE") -> NoReturn:
    raise SwmmExportError(message, code=code)


def _first(value: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in value:
            return value[name]
    return default


def _finite(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        _error(f"{name} must be numeric, got {value!r}")
    if not math.isfinite(result):
        _error(f"{name} must be finite", code="INVALID_MAPPING")
    return result


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        _error(f"{name} must be a non-empty string", code="INVALID_MAPPING")
    if len(value) > 256 or any(character.isspace() for character in value):
        _error(f"{name} contains whitespace or is too long")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        _error(f"{name} contains a control character")
    if any(marker in value for marker in ("/", "\\", "..", "://", ":")):
        _error(f"{name} looks like a path or executable reference")
    return value


def _record_value(value: Any, names: Sequence[str], default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return _first(value, *names, default=default)
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _coerce_issue(value: Any) -> SwmmIssue:
    if isinstance(value, SwmmIssue):
        return value
    if isinstance(value, Mapping):
        return SwmmIssue(
            code=str(_first(value, "code", default="INVALID_INP")),
            message=str(_first(value, "message", default="invalid import issue")),
            severity=str(_first(value, "severity", default="error")),
            section=_first(value, "section"),
            line=_first(value, "line"),
            entity_type=_first(value, "entity_type"),
            entity_key=_first(value, "entity_key"),
            details=dict(_first(value, "details", default={}) or {}),
        )
    _error(f"unsupported issue record: {value!r}")


def _coerce_junction(value: Any) -> SwmmJunction:
    if isinstance(value, SwmmJunction):
        return value
    if not isinstance(value, Mapping):
        value = {
            "name": _record_value(value, ("name", "id", "stable_key")),
            "elevation": _record_value(value, ("elevation", "elevation_m", "invert")),
            "max_depth": _record_value(value, ("max_depth", "depth"), 0.0),
            "initial_depth": _record_value(value, ("initial_depth", "init_depth"), 0.0),
            "surcharge_depth": _record_value(
                value, ("surcharge_depth", "sur_depth"), 0.0
            ),
            "ponded_area": _record_value(value, ("ponded_area", "aponded"), 0.0),
            "line": _record_value(value, ("line",)),
        }
    name = _identifier(
        _first(value, "name", "id", "stable_key", "node_id"), "junction name"
    )
    elevation = _finite(
        _first(value, "elevation", "elevation_m", "invert", default=0.0),
        "junction elevation",
    )
    values = [
        _finite(_first(value, "max_depth", "depth", default=0.0), "junction max_depth"),
        _finite(
            _first(value, "initial_depth", "init_depth", default=0.0),
            "junction initial_depth",
        ),
        _finite(
            _first(value, "surcharge_depth", "sur_depth", default=0.0),
            "junction surcharge_depth",
        ),
        _finite(
            _first(value, "ponded_area", "aponded", default=0.0), "junction ponded_area"
        ),
    ]
    if any(item < 0 for item in values):
        _error(f"junction {name} has a negative depth or ponded area")
    return SwmmJunction(
        name,
        elevation,
        values[0],
        values[1],
        values[2],
        values[3],
        line=_first(value, "line"),
    )


def _coerce_outfall(value: Any) -> SwmmOutfall:
    if isinstance(value, SwmmOutfall):
        return value
    if not isinstance(value, Mapping):
        value = {
            "name": _record_value(value, ("name", "id", "stable_key", "node_id")),
            "elevation": _record_value(value, ("elevation", "elevation_m", "invert")),
            "type": _record_value(
                value, ("type", "boundary_type", "outfall_type"), "FREE"
            ),
            "stage": _record_value(value, ("stage",), 0.0),
            "gated": _record_value(value, ("gated",), False),
            "line": _record_value(value, ("line",)),
        }
    name = _identifier(
        _first(value, "name", "id", "stable_key", "node_id"), "outfall name"
    )
    elevation = _finite(
        _first(value, "elevation", "elevation_m", "invert", default=0.0),
        "outfall elevation",
    )
    boundary_type = str(
        _first(value, "type", "boundary_type", "outfall_type", default="FREE")
    ).upper()
    if boundary_type not in {"FREE", "NORMAL", "FIXED", "TIDAL", "TIMESERIES"}:
        _error(f"outfall {name} has unsupported type {boundary_type}")
    gated_value = _first(value, "gated", default=False)
    if isinstance(gated_value, str):
        gated = gated_value.upper() in {"YES", "TRUE", "1", "Y", "T"}
        if gated_value.upper() not in {
            "YES",
            "NO",
            "TRUE",
            "FALSE",
            "1",
            "0",
            "Y",
            "N",
            "T",
            "F",
        }:
            _error(f"outfall {name} has invalid gated value")
    else:
        gated = bool(gated_value)
    raw_stage = _first(value, "stage", default=0.0)
    stage: float | str
    if boundary_type == "TIMESERIES":
        stage = _identifier(raw_stage, f"outfall {name} stage series id")
    else:
        stage = _finite(raw_stage, f"outfall {name} stage")
    return SwmmOutfall(
        name,
        elevation,
        boundary_type,
        stage,
        gated,
        line=_first(value, "line"),
    )


def _coerce_conduit(value: Any) -> SwmmConduit:
    if isinstance(value, SwmmConduit):
        return value
    if not isinstance(value, Mapping):
        value = {
            "name": _record_value(value, ("name", "id", "stable_key")),
            "from_node": _record_value(
                value, ("from_node", "upstream_node", "upstream_node_id", "from")
            ),
            "to_node": _record_value(
                value, ("to_node", "downstream_node", "downstream_node_id", "to")
            ),
            "length": _record_value(value, ("length", "length_m")),
            "roughness": _record_value(
                value, ("roughness", "roughness_manning_n", "n")
            ),
            "in_offset": _record_value(value, ("in_offset", "inlet_offset"), 0.0),
            "out_offset": _record_value(value, ("out_offset", "outlet_offset"), 0.0),
            "initial_flow": _record_value(value, ("initial_flow", "init_flow"), 0.0),
            "max_flow": _record_value(value, ("max_flow",), 0.0),
            "line": _record_value(value, ("line",)),
        }
    name = _identifier(_first(value, "name", "id", "stable_key"), "conduit name")
    from_node = _identifier(
        _first(value, "from_node", "upstream_node", "upstream_node_id", "from"),
        f"conduit {name} from_node",
    )
    to_node = _identifier(
        _first(value, "to_node", "downstream_node", "downstream_node_id", "to"),
        f"conduit {name} to_node",
    )
    length = _finite(_first(value, "length", "length_m"), f"conduit {name} length")
    roughness = _finite(
        _first(value, "roughness", "roughness_manning_n", "n"),
        f"conduit {name} roughness",
    )
    if length <= 0 or roughness <= 0:
        _error(f"conduit {name} length and roughness must be positive")
    values = [
        _finite(
            _first(value, "in_offset", "inlet_offset", default=0.0),
            f"conduit {name} in_offset",
        ),
        _finite(
            _first(value, "out_offset", "outlet_offset", default=0.0),
            f"conduit {name} out_offset",
        ),
        _finite(
            _first(value, "initial_flow", "init_flow", default=0.0),
            f"conduit {name} initial_flow",
        ),
        _finite(_first(value, "max_flow", default=0.0), f"conduit {name} max_flow"),
    ]
    if values[3] < 0:
        _error(f"conduit {name} max_flow must be non-negative")
    return SwmmConduit(
        name,
        from_node,
        to_node,
        length,
        roughness,
        values[0],
        values[1],
        values[2],
        values[3],
        line=_first(value, "line"),
    )


def _coerce_subcatchment(value: Any) -> SwmmSubcatchment:
    if isinstance(value, SwmmSubcatchment):
        return value
    if not isinstance(value, Mapping):
        value = {
            "name": _record_value(value, ("name", "id", "stable_key")),
            "outlet": _record_value(value, ("outlet", "outlet_node", "rain_outlet")),
            "area": _record_value(value, ("area", "area_m2")),
            "width": _record_value(value, ("width",), 0.0),
            "slope": _record_value(value, ("slope", "slope_percent"), 0.0),
            "impervious": _record_value(value, ("impervious", "imperv"), 0.0),
            "n_pervious": _record_value(value, ("n_pervious", "n_perv"), 0.1),
            "s_pervious": _record_value(value, ("s_pervious", "s_perv"), 0.05),
            "dstore_impervious": _record_value(
                value, ("dstore_impervious", "dstore_imperv"), 0.0
            ),
            "dstore_pervious": _record_value(
                value, ("dstore_pervious", "dstore_perv"), 0.0
            ),
            "line": _record_value(value, ("line",)),
        }
    name = _identifier(_first(value, "name", "id", "stable_key"), "subcatchment name")
    raw_outlet = _first(value, "outlet", "outlet_node", "rain_outlet")
    outlet = (
        None
        if raw_outlet is None or str(raw_outlet).upper() in {"-1", "NONE", "NULL"}
        else _identifier(raw_outlet, f"subcatchment {name} outlet")
    )
    area = _finite(_first(value, "area", "area_m2"), f"subcatchment {name} area")
    values = [
        _finite(_first(value, "width", default=0.0), f"subcatchment {name} width"),
        _finite(
            _first(value, "slope", "slope_percent", default=0.0),
            f"subcatchment {name} slope",
        ),
        _finite(
            _first(value, "impervious", "imperv", default=0.0),
            f"subcatchment {name} impervious",
        ),
        _finite(
            _first(value, "n_pervious", "n_perv", default=0.1),
            f"subcatchment {name} n_pervious",
        ),
        _finite(
            _first(value, "s_pervious", "s_perv", default=0.05),
            f"subcatchment {name} s_pervious",
        ),
        _finite(
            _first(value, "dstore_impervious", "dstore_imperv", default=0.0),
            f"subcatchment {name} dstore_impervious",
        ),
        _finite(
            _first(value, "dstore_pervious", "dstore_perv", default=0.0),
            f"subcatchment {name} dstore_pervious",
        ),
    ]
    if (
        area <= 0
        or values[0] < 0
        or values[1] < 0
        or not 0 <= values[2] <= 100
        or values[3] <= 0
        or values[4] < 0
        or values[5] < 0
        or values[6] < 0
    ):
        _error(f"subcatchment {name} has out-of-range numeric values")
    return SwmmSubcatchment(
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
        line=_first(value, "line"),
    )


def _coerce_coordinate(value: Any) -> SwmmCoordinate:
    if isinstance(value, SwmmCoordinate):
        return value
    if not isinstance(value, Mapping):
        value = {
            "name": _record_value(value, ("name", "id", "stable_key", "node_id")),
            "x": _record_value(value, ("x", "longitude", "lon")),
            "y": _record_value(value, ("y", "latitude", "lat")),
            "line": _record_value(value, ("line",)),
        }
    name = _identifier(
        _first(value, "name", "id", "stable_key", "node_id"), "coordinate name"
    )
    x = _finite(_first(value, "x", "longitude", "lon"), f"coordinate {name} x")
    y = _finite(_first(value, "y", "latitude", "lat"), f"coordinate {name} y")
    if not -180 <= x <= 180 or not -90 <= y <= 90:
        _error(f"coordinate {name} must be EPSG:4326 lon/lat")
    return SwmmCoordinate(name, x, y, line=_first(value, "line"))


def _coerce_slope(value: Any) -> SwmmSlope:
    if isinstance(value, SwmmSlope):
        return value
    if not isinstance(value, Mapping):
        value = {
            "conduit": _record_value(value, ("conduit", "id", "name", "stable_key")),
            "upstream_node": _record_value(
                value, ("upstream_node", "from_node", "upstream_node_id", "from")
            ),
            "upstream_offset": _record_value(
                value, ("upstream_offset", "up_offset", "offset_up")
            ),
            "downstream_node": _record_value(
                value, ("downstream_node", "to_node", "downstream_node_id", "to")
            ),
            "downstream_offset": _record_value(
                value, ("downstream_offset", "down_offset", "offset_down")
            ),
            "line": _record_value(value, ("line",)),
        }
    conduit = _identifier(
        _first(value, "conduit", "id", "name", "stable_key"), "slope conduit"
    )
    return SwmmSlope(
        conduit,
        _identifier(
            _first(value, "upstream_node", "from_node", "upstream_node_id", "from"),
            f"slope {conduit} upstream_node",
        ),
        _finite(
            _first(value, "upstream_offset", "up_offset", "offset_up"),
            f"slope {conduit} upstream_offset",
        ),
        _identifier(
            _first(value, "downstream_node", "to_node", "downstream_node_id", "to"),
            f"slope {conduit} downstream_node",
        ),
        _finite(
            _first(value, "downstream_offset", "down_offset", "offset_down"),
            f"slope {conduit} downstream_offset",
        ),
        line=_first(value, "line"),
    )


def _coerce_raw_row(value: Any, section: str | None = None) -> SwmmRawRow:
    if isinstance(value, SwmmRawRow):
        return value
    if isinstance(value, Mapping):
        row_section = str(_first(value, "section", default=section or "")).upper()
        tokens = _first(value, "tokens", "values", "row", default=())
        line = _first(value, "line")
    else:
        row_section = str(section or "").upper()
        tokens = value
        line = None
    if not row_section or row_section not in RAW_SECTIONS:
        _error(f"unsupported raw section {row_section!r}")
    if isinstance(tokens, str):
        try:
            tokens = shlex.split(tokens, comments=False, posix=True)
        except ValueError as exc:
            _error(
                f"raw row in [{row_section}] is malformed: {exc}",
                code="INVALID_MAPPING",
            )
    if not isinstance(tokens, Sequence) or isinstance(tokens, (str, bytes)):
        _error(
            f"raw row in [{row_section}] must be a token sequence",
            code="INVALID_MAPPING",
        )
    normalized = tuple(str(token) for token in tokens)
    try:
        _validate_raw_section_tokens(row_section, normalized)
    except ValueError as exc:
        _error(str(exc), code="INVALID_MAPPING")
    return SwmmRawRow(row_section, normalized, line=line)


def _coerce_options(value: Any) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        return tuple((str(key).upper(), str(item)) for key, item in value.items())
    result: list[tuple[str, str]] = []
    for item in value:
        if isinstance(item, Mapping):
            result.append(
                (
                    str(_first(item, "key", "name", default="")).upper(),
                    str(_first(item, "value", "val", default="")),
                )
            )
        elif (
            isinstance(item, Sequence)
            and not isinstance(item, (str, bytes))
            and len(item) >= 2
        ):
            result.append((str(item[0]).upper(), str(item[1])))
        else:
            _error(f"invalid option row: {item!r}", code="INVALID_MAPPING")
    return tuple(result)


def _coerce_model(value: SwmmNetwork | Mapping[str, Any] | Any) -> SwmmNetwork:
    if isinstance(value, SwmmNetwork):
        return value
    if hasattr(value, "to_dict") and callable(value.to_dict):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        _error("export input must be SwmmNetwork or a mapping")
    raw_sections_value = _first(
        value, "raw_sections", "extra_sections", "sections", default=()
    )
    raw_sections: list[SwmmRawRow] = []
    if isinstance(raw_sections_value, Mapping):
        for section, rows in raw_sections_value.items():
            if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
                _error(f"raw section {section!r} must contain rows")
            for row in rows:
                raw_sections.append(_coerce_raw_row(row, str(section)))
    else:
        for row in raw_sections_value:
            raw_sections.append(_coerce_raw_row(row))
    title_value = _first(value, "title", default=())
    if isinstance(title_value, str):
        title = tuple(title_value.splitlines())
    else:
        title = tuple(str(item) for item in title_value)
    issues_value = _first(value, "issues", default=())
    return SwmmNetwork(
        junctions=tuple(
            _coerce_junction(item)
            for item in _first(value, "junctions", "junction", default=())
        ),
        outfalls=tuple(
            _coerce_outfall(item)
            for item in _first(value, "outfalls", "outfall", default=())
        ),
        conduits=tuple(
            _coerce_conduit(item)
            for item in _first(value, "conduits", "pipes", "conduit", default=())
        ),
        subcatchments=tuple(
            _coerce_subcatchment(item)
            for item in _first(
                value, "subcatchments", "catchments", "subcatchment", default=()
            )
        ),
        coordinates=tuple(
            _coerce_coordinate(item)
            for item in _first(value, "coordinates", "coordinate", default=())
        ),
        slopes=tuple(
            _coerce_slope(item) for item in _first(value, "slopes", "slope", default=())
        ),
        title=title,
        options=_coerce_options(_first(value, "options", default=())),
        raw_sections=tuple(raw_sections),
        section_order=tuple(
            str(item).upper() for item in _first(value, "section_order", default=())
        ),
        issues=tuple(_coerce_issue(item) for item in issues_value),
        source_sha256=_first(value, "source_sha256", "source_hash"),
        filename=_first(value, "filename"),
        parser_version=str(
            _first(value, "parser_version", default="swmm-inp-parser-v1")
        ),
    )


def _issue_error(
    issues: Iterable[SwmmIssue], *, code: str = "EXPORT_NOT_REPRESENTABLE"
) -> None:
    for issue in issues:
        if issue.severity == "error":
            # Preserve the parser's stable validation code (for example
            # MISSING_COORDINATES or DANGLING_NODE) instead of flattening it to
            # a generic export error.
            _error(issue.message, code=issue.code or code)


def _validate_for_export(model: SwmmNetwork) -> None:
    _issue_error(model.issues)
    if not model.nodes:
        _error("cannot export an empty network", code="EMPTY_NETWORK")
    node_names: set[str] = set()
    node_by_key: dict[str, SwmmJunction | SwmmOutfall] = {}
    for node in model.nodes:
        name = _identifier(node.name, f"{node.kind} name")
        key = name.casefold()
        if key in node_names:
            _error(f"duplicate node identifier {name}", code="DUPLICATE_ID")
        node_names.add(key)
        node_by_key[key] = node
    entity_ids: dict[str, tuple[str, str]] = {
        node.name.casefold(): (node.kind, node.name) for node in model.nodes
    }
    for conduit in model.conduits:
        key = conduit.name.casefold()
        if key in entity_ids:
            _error(
                f"conduit identifier {conduit.name} is already used by "
                f"{entity_ids[key][0]} {entity_ids[key][1]}",
                code="DUPLICATE_ID",
            )
        entity_ids[key] = ("conduit", conduit.name)
    coordinate_names: set[str] = set()
    for coordinate in model.coordinates:
        name = _identifier(coordinate.name, "coordinate name")
        key = name.casefold()
        if key in coordinate_names:
            _error(f"duplicate coordinate identifier {name}", code="DUPLICATE_ID")
        coordinate_names.add(key)
        _finite(coordinate.x, f"coordinate {name} x")
        _finite(coordinate.y, f"coordinate {name} y")
    missing = sorted(
        (
            node.name
            for node in model.nodes
            if node.name.casefold() not in coordinate_names
        )
    )
    if missing:
        _error(
            f"missing coordinates for: {', '.join(missing)}", code="MISSING_COORDINATES"
        )
    slope_by_conduit = {slope.conduit.casefold(): slope for slope in model.slopes}
    conduits: dict[str, SwmmConduit] = {}
    physical_segments: dict[frozenset[str], str] = {}
    for conduit in model.conduits:
        name = _identifier(conduit.name, "conduit name")
        key = name.casefold()
        if key in conduits:
            _error(f"duplicate conduit identifier {name}", code="DUPLICATE_ID")
        conduits[key] = conduit
        endpoint_keys = [
            _identifier(endpoint, f"conduit {name} endpoint")
            for endpoint in (conduit.from_node, conduit.to_node)
        ]
        missing = [
            endpoint
            for endpoint in endpoint_keys
            if endpoint.casefold() not in node_names
        ]
        if missing:
            _error(
                f"conduit {name} refers to unknown node {missing[0]}",
                code="DANGLING_NODE",
            )
        upstream_key, downstream_key = (
            endpoint.casefold() for endpoint in endpoint_keys
        )
        if upstream_key == downstream_key:
            _error(f"conduit {name} is a self loop", code="SELF_LOOP")
        segment = frozenset((upstream_key, downstream_key))
        first_conduit = physical_segments.get(segment)
        if first_conduit is not None:
            _error(
                f"conduit {name} duplicates physical segment {first_conduit}",
                code="DUPLICATE_SEGMENT",
            )
        physical_segments[segment] = name
        upstream = node_by_key[upstream_key]
        downstream = node_by_key[downstream_key]
        slope = slope_by_conduit.get(key)
        upstream_offset = (
            slope.upstream_offset if slope is not None else conduit.in_offset
        )
        downstream_offset = (
            slope.downstream_offset if slope is not None else conduit.out_offset
        )
        if upstream.elevation - upstream_offset + 1e-9 < (
            downstream.elevation - downstream_offset
        ):
            _error(f"conduit {name} has a reverse slope", code="REVERSE_SLOPE")
    subcatchment_names: set[str] = set()
    for subcatchment in model.subcatchments:
        name = _identifier(subcatchment.name, "subcatchment name")
        key = name.casefold()
        if key in subcatchment_names:
            _error(f"duplicate subcatchment identifier {name}", code="DUPLICATE_ID")
        if key in entity_ids:
            _error(
                f"subcatchment identifier {name} is already used by "
                f"{entity_ids[key][0]} {entity_ids[key][1]}",
                code="DUPLICATE_ID",
            )
        entity_ids[key] = ("subcatchment", name)
        subcatchment_names.add(key)
        if (
            subcatchment.outlet is not None
            and subcatchment.outlet.casefold() not in node_names
        ):
            _error(
                f"subcatchment {name} refers to unknown outlet {subcatchment.outlet}",
                code="DANGLING_NODE",
            )
    seen_slopes: set[str] = set()
    for slope in model.slopes:
        key = slope.conduit.casefold()
        if key in seen_slopes:
            _error(f"duplicate slope for conduit {slope.conduit}", code="DUPLICATE_ID")
        seen_slopes.add(key)
        slope_conduit = conduits.get(key)
        if slope_conduit is None:
            _error(
                f"slope refers to unknown conduit {slope.conduit}", code="DANGLING_NODE"
            )
            continue
        if (
            slope.upstream_node.casefold() != slope_conduit.from_node.casefold()
            or slope.downstream_node.casefold() != slope_conduit.to_node.casefold()
        ):
            _error(
                f"slope endpoints do not match conduit {slope.conduit}",
                code="INVALID_SLOPE",
            )
    cross_sections: dict[str, str] = {}
    for row in model.raw_sections:
        if (
            row.section in FORBIDDEN_SECTION_WORDS
            or row.section not in SUPPORTED_SECTIONS
        ):
            _error(f"raw section [{row.section}] is not exportable", code="UNSAFE_PATH")
        for token in row.tokens:
            _validate_raw_token(token, row.section)
        try:
            _validate_raw_section_tokens(row.section, row.tokens)
        except ValueError as exc:
            _error(str(exc), code="INVALID_MAPPING")
        if row.section == "XSECTIONS":
            key = row.tokens[0].casefold()
            if key in cross_sections:
                _error(
                    f"duplicate XSECTIONS identifier {row.tokens[0]}",
                    code="DUPLICATE_ID",
                )
            if key not in conduits:
                _error(
                    f"XSECTIONS row refers to unknown conduit {row.tokens[0]}",
                    code="DANGLING_NODE",
                )
            cross_sections[key] = row.tokens[0]
    missing_cross_sections = sorted(
        conduit.name for key, conduit in conduits.items() if key not in cross_sections
    )
    if missing_cross_sections:
        _error(
            "missing XSECTIONS for: " + ", ".join(missing_cross_sections),
            code="MISSING_XSECTIONS",
        )


def _validate_raw_token(token: str, section: str) -> None:
    if not token or len(token) > 10_000:
        _error(f"raw token in [{section}] is empty or too long", code="UNSAFE_PATH")
    if any(ord(character) < 32 or ord(character) == 127 for character in token):
        _error(
            f"raw token in [{section}] contains a control character", code="UNSAFE_PATH"
        )
    stripped = token.strip("'\"")
    if stripped.upper() in {
        "FILE",
        "IMPORT",
        "EXPORT",
        "EXEC",
        "EXECUTE",
        "RUN",
        "SCRIPT",
        "URL",
        "USE",
        "LOAD",
        "PATH",
        "INCLUDE",
    }:
        _error(
            f"path or executable directive in [{section}] is forbidden",
            code="UNSAFE_PATH",
        )
    if _PATH_RE.search(stripped):
        _error(f"path-like raw token in [{section}] is forbidden", code="UNSAFE_INP")


def _number(value: float) -> str:
    number = float(value)
    if not math.isfinite(number):
        _error("cannot export a non-finite number")
    if number == 0:
        return "0"
    # ``repr`` is Python's shortest round-trip representation; using a fixed
    # display precision here could silently change a high-precision coordinate
    # when the exported file is parsed again.
    return repr(number)


def _stage_text(value: float | str) -> str:
    if isinstance(value, str):
        return _identifier(value, "outfall stage series id")
    return _number(value)


def _bool(value: bool) -> str:
    return "YES" if value else "NO"


def _raw_token(value: str, section: str) -> str:
    _validate_raw_token(value, section)
    if not value or any(character.isspace() for character in value):
        return shlex.quote(value)
    return value


def _emit_section(lines: list[str], name: str) -> None:
    lines.append(f"[{name}]")


def _emit_rows(lines: list[str], rows: Iterable[Sequence[Any]]) -> None:
    for row in rows:
        tokens = [str(item) for item in row]
        if not tokens:
            _error("cannot export an empty data row", code="EXPORT_NOT_REPRESENTABLE")
        lines.append(" ".join(_raw_token(token, "ROW") for token in tokens))


def _effective_options(model: SwmmNetwork) -> tuple[tuple[str, str], ...]:
    options = [(key.upper(), value) for key, value in model.options]
    if not any(key == "FLOW_UNITS" for key, _ in options):
        options.insert(0, SWMM_DEFAULT_OPTIONS[0])
    return tuple(options)


def _semantic_signature(model: SwmmNetwork) -> tuple[Any, ...]:
    return (
        tuple(model.title),
        _effective_options(model),
        tuple(
            (
                row.name,
                row.elevation,
                row.max_depth,
                row.initial_depth,
                row.surcharge_depth,
                row.ponded_area,
            )
            for row in model.junctions
        ),
        tuple(
            (row.name, row.elevation, row.boundary_type, row.stage, row.gated)
            for row in model.outfalls
        ),
        tuple(
            (
                row.name,
                row.from_node,
                row.to_node,
                row.length,
                row.roughness,
                row.in_offset,
                row.out_offset,
                row.initial_flow,
                row.max_flow,
            )
            for row in model.conduits
        ),
        tuple(
            (
                row.name,
                row.outlet,
                row.area,
                row.width,
                row.slope,
                row.impervious,
                row.n_pervious,
                row.s_pervious,
                row.dstore_impervious,
                row.dstore_pervious,
            )
            for row in model.subcatchments
        ),
        tuple((row.name, row.x, row.y) for row in model.coordinates),
        tuple(
            (
                row.conduit,
                row.upstream_node,
                row.upstream_offset,
                row.downstream_node,
                row.downstream_offset,
            )
            for row in model.slopes
        ),
        tuple((row.section, row.tokens) for row in model.raw_sections),
    )


def _order_sections(model: SwmmNetwork) -> list[str]:
    preferred = [
        "TITLE",
        "OPTIONS",
        "JUNCTIONS",
        "OUTFALLS",
        "CONDUITS",
        "SUBCATCHMENTS",
        "COORDINATES",
        "SLOPES",
    ]
    order = [
        item
        for item in model.section_order
        if item in SUPPORTED_SECTIONS
        and item
        not in {
            "TITLE",
            "OPTIONS",
            "JUNCTIONS",
            "OUTFALLS",
            "CONDUITS",
            "SUBCATCHMENTS",
            "COORDINATES",
            "SLOPES",
        }
    ]
    for section in sorted({row.section for row in model.raw_sections}):
        if section not in order:
            order.append(section)
    return preferred + order


def _export_swmm(
    network: SwmmNetwork | Mapping[str, Any] | Any,
    *,
    validate_roundtrip: bool,
) -> str:
    """Render a validated model as a self-contained, deterministic INP string.

    Set ``validate_roundtrip=False`` only for a diagnostic path; production
    callers should keep the default reparsing check so a file advertised as
    runnable cannot drift from the parsed model.
    """

    model = _coerce_model(network)
    _validate_for_export(model)
    lines: list[str] = []
    section_order = _order_sections(model)
    for section in section_order:
        if section == "TITLE":
            if not model.title:
                continue
            _emit_section(lines, section)
            lines.extend(str(item) for item in model.title)
        elif section == "OPTIONS":
            options = _effective_options(model)
            _emit_section(lines, section)
            for key, value in options:
                _validate_raw_token(key, "OPTIONS")
                _validate_raw_token(value, "OPTIONS")
                lines.append(f"{key} {value}" if value else key)
        elif section == "JUNCTIONS":
            if not model.junctions:
                continue
            _emit_section(lines, section)
            _emit_rows(
                lines,
                (
                    (
                        row.name,
                        _number(row.elevation),
                        _number(row.max_depth),
                        _number(row.initial_depth),
                        _number(row.surcharge_depth),
                        _number(row.ponded_area),
                    )
                    for row in model.junctions
                ),
            )
        elif section == "OUTFALLS":
            if not model.outfalls:
                continue
            _emit_section(lines, section)
            _emit_rows(
                lines,
                (
                    (
                        row.name,
                        _number(row.elevation),
                        row.boundary_type,
                        _stage_text(row.stage),
                        _bool(row.gated),
                    )
                    for row in model.outfalls
                ),
            )
        elif section == "CONDUITS":
            if not model.conduits:
                continue
            _emit_section(lines, section)
            _emit_rows(
                lines,
                (
                    (
                        row.name,
                        row.from_node,
                        row.to_node,
                        _number(row.length),
                        _number(row.roughness),
                        _number(row.in_offset),
                        _number(row.out_offset),
                        _number(row.initial_flow),
                        _number(row.max_flow),
                    )
                    for row in model.conduits
                ),
            )
        elif section == "SUBCATCHMENTS":
            if not model.subcatchments:
                continue
            _emit_section(lines, section)
            _emit_rows(
                lines,
                (
                    (
                        row.name,
                        row.outlet if row.outlet is not None else "-1",
                        _number(row.area),
                        _number(row.width),
                        _number(row.slope),
                        _number(row.impervious),
                        _number(row.n_pervious),
                        _number(row.s_pervious),
                        _number(row.dstore_impervious),
                        _number(row.dstore_pervious),
                    )
                    for row in model.subcatchments
                ),
            )
        elif section == "COORDINATES":
            if not model.coordinates:
                continue
            _emit_section(lines, section)
            _emit_rows(
                lines,
                (
                    (row.name, _number(row.x), _number(row.y))
                    for row in model.coordinates
                ),
            )
        elif section == "SLOPES":
            if not model.slopes:
                continue
            _emit_section(lines, section)
            _emit_rows(
                lines,
                (
                    (
                        row.conduit,
                        row.upstream_node,
                        _number(row.upstream_offset),
                        row.downstream_node,
                        _number(row.downstream_offset),
                    )
                    for row in model.slopes
                ),
            )
        else:
            grouped = [row for row in model.raw_sections if row.section == section]
            if not grouped:
                continue
            _emit_section(lines, section)
            _emit_rows(lines, (row.tokens for row in grouped))
    output = "\n".join(lines).rstrip() + "\n"
    if validate_roundtrip:
        try:
            reparsed = parse_swmm(output, strict=True)
        except SwmmParseError as exc:
            raise SwmmExportError(
                f"generated INP could not be reparsed: {exc}",
                code="ROUNDTRIP_VALIDATION_FAILED",
            ) from exc
        if _semantic_signature(model) != _semantic_signature(reparsed):
            raise SwmmExportError(
                "generated INP changed supported semantic records",
                code="ROUNDTRIP_VALIDATION_FAILED",
            )
    return output


def export_swmm(
    network: SwmmNetwork | Mapping[str, Any] | Any,
    *,
    validate_roundtrip: bool = True,
) -> str:
    """Export a mapping/model while preserving a stable exception contract."""

    try:
        return _export_swmm(network, validate_roundtrip=validate_roundtrip)
    except SwmmExportError:
        raise
    except (
        AssertionError,
        IndexError,
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
    ) as exc:
        raise SwmmExportError(
            f"export mapping is malformed: {exc}", code="INVALID_MAPPING"
        ) from exc


def export_inp(network: SwmmNetwork | Mapping[str, Any] | Any, **kwargs: Any) -> str:
    """Explicit INP spelling for :func:`export_swmm`."""

    return export_swmm(network, **kwargs)


def export_inp_text(
    network: SwmmNetwork | Mapping[str, Any] | Any, **kwargs: Any
) -> str:
    """Text-oriented alias for callers that name the output format explicitly."""

    return export_swmm(network, **kwargs)


class SwmmExporter:
    """Small stateless exporter facade for worker/API integration."""

    exporter_version = SWMM_EXPORTER_VERSION

    def export(
        self, network: SwmmNetwork | Mapping[str, Any] | Any, **kwargs: Any
    ) -> str:
        return export_swmm(network, **kwargs)

    __call__ = export

    def to_bytes(
        self, network: SwmmNetwork | Mapping[str, Any] | Any, **kwargs: Any
    ) -> bytes:
        return self.export(network, **kwargs).encode("utf-8")


SWMMExporter = SwmmExporter
SwmmExportResult = str
SwmmExportValidationError = SwmmExportError
SWMM_EXPORT_ROUNDTRIP_VERSION = SWMM_EXPORT_ROUNDTRIP_VERSION


__all__ = [
    "SWMMExporter",
    "SWMM_EXPORTER_VERSION",
    "SWMM_EXPORT_ROUNDTRIP_VERSION",
    "SwmmExportError",
    "SwmmExportResult",
    "SwmmExportValidationError",
    "SwmmExporter",
    "export_inp",
    "export_inp_text",
    "export_swmm",
]
