"""Topology validation for immutable hydraulic network revisions.

The validator consumes stable-key records rather than ORM instances.  This
keeps validation usable from import/seed workers and prevents mutable database
rows from leaking into solver-facing logic.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

TOPOLOGY_ALGORITHM_VERSION = "topology-v1"


class IssueSeverity(str, Enum):
    error = "error"
    warning = "warning"
    info = "info"


class IssueCode(str, Enum):
    MISSING_COORDINATES = "MISSING_COORDINATES"
    MISSING_INVERT = "MISSING_INVERT"
    DANGLING_NODE = "DANGLING_NODE"
    DUPLICATE_SEGMENT = "DUPLICATE_SEGMENT"
    SELF_LOOP = "SELF_LOOP"
    REVERSE_SLOPE = "REVERSE_SLOPE"
    DISCONNECTED_COMPONENT = "DISCONNECTED_COMPONENT"
    CYCLE_DETECTED = "CYCLE_DETECTED"


class ValidationStatus(str, Enum):
    valid = "valid"
    valid_with_warnings = "valid_with_warnings"
    invalid = "invalid"


@dataclass(frozen=True, slots=True)
class TopologyNode:
    """Minimum node projection required for topology validation."""

    stable_key: str
    longitude: float | None = None
    latitude: float | None = None
    invert_elevation_m: float | None = None
    kind: str = "junction"


@dataclass(frozen=True, slots=True)
class TopologyPipe:
    """Minimum pipe projection required for topology validation.

    Optional explicit elevations are useful for imported records whose pipe
    elevations differ from the endpoint node records.  The validator falls
    back to node inverts when they are omitted.
    """

    stable_key: str
    upstream_node_key: str
    downstream_node_key: str
    upstream_invert_elevation_m: float | None = None
    downstream_invert_elevation_m: float | None = None
    length_m: float | None = None


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    severity: IssueSeverity
    code: IssueCode
    entity_type: str
    entity_key: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity.value,
            "code": self.code.value,
            "entity_type": self.entity_type,
            "entity_key": self.entity_key,
            "message": self.message,
            "details": dict(sorted(self.details.items())),
        }


@dataclass(frozen=True, slots=True)
class CycleFinding:
    """One deterministic undirected cycle."""

    cycle_index: int
    node_keys: tuple[str, ...]
    pipe_keys: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_index": self.cycle_index,
            "node_keys": list(self.node_keys),
            "pipe_keys": list(self.pipe_keys),
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Deterministic, structured topology validation result."""

    status: ValidationStatus
    simulation_ready: bool
    issues: tuple[ValidationIssue, ...]
    node_count: int
    pipe_count: int
    component_count: int
    components: tuple[tuple[str, ...], ...]
    cycles: tuple[CycleFinding, ...]
    algorithm_version: str = TOPOLOGY_ALGORITHM_VERSION

    @property
    def cycle_count(self) -> int:
        return len(self.cycles)

    @property
    def error_count(self) -> int:
        return sum(issue.severity is IssueSeverity.error for issue in self.issues)

    @property
    def warning_count(self) -> int:
        return sum(issue.severity is IssueSeverity.warning for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithm_version": self.algorithm_version,
            "status": self.status.value,
            "simulation_ready": self.simulation_ready,
            "counts": {
                "nodes": self.node_count,
                "pipes": self.pipe_count,
                "components": self.component_count,
                "cycles": self.cycle_count,
                "errors": self.error_count,
                "warnings": self.warning_count,
            },
            "components": [list(component) for component in self.components],
            "cycles": [cycle.to_dict() for cycle in self.cycles],
            "issues": [issue.to_dict() for issue in self.issues],
        }

    def canonical_json(self) -> str:
        return _canonical_json(self.to_dict())

    @property
    def content_sha256(self) -> str:
        return _sha256(self.canonical_json())


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _issue(
    issues: list[ValidationIssue],
    severity: IssueSeverity,
    code: IssueCode,
    entity_type: str,
    entity_key: str,
    message: str,
    **details: Any,
) -> None:
    issues.append(
        ValidationIssue(
            severity=severity,
            code=code,
            entity_type=entity_type,
            entity_key=entity_key,
            message=message,
            details=details,
        )
    )


def _valid_coordinate(longitude: float | None, latitude: float | None) -> bool:
    if longitude is None or latitude is None:
        return False
    if not math.isfinite(longitude) or not math.isfinite(latitude):
        return False
    return -180.0 <= longitude <= 180.0 and -90.0 <= latitude <= 90.0


def _connected_components(
    node_keys: Sequence[str], adjacency: Mapping[str, set[str]]
) -> tuple[tuple[str, ...], ...]:
    remaining = set(node_keys)
    components: list[tuple[str, ...]] = []
    while remaining:
        start = min(remaining)
        stack = [start]
        remaining.remove(start)
        members: list[str] = []
        while stack:
            current = stack.pop()
            members.append(current)
            for neighbour in sorted(adjacency[current], reverse=True):
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    stack.append(neighbour)
        components.append(tuple(sorted(members)))
    return tuple(components)


def _fundamental_cycles(
    adjacency: Mapping[str, set[str]],
    pipe_by_segment: Mapping[frozenset[str], str],
) -> tuple[CycleFinding, ...]:
    """Return deterministic fundamental cycles with no off-cycle tree trunk."""

    tree_parent: dict[str, str] = {}
    tree_depth: dict[str, int] = {}
    visited: set[str] = set()

    for root in sorted(adjacency):
        if root in visited:
            continue
        visited.add(root)
        tree_depth[root] = 0
        stack = [root]
        while stack:
            current = stack.pop()
            for neighbour in sorted(adjacency[current], reverse=True):
                if neighbour in visited:
                    continue
                visited.add(neighbour)
                tree_parent[neighbour] = current
                tree_depth[neighbour] = tree_depth[current] + 1
                stack.append(neighbour)

    tree_segments = {
        frozenset((child, parent)) for child, parent in tree_parent.items()
    }
    all_segments = {
        frozenset((first, second))
        for first, neighbours in adjacency.items()
        for second in neighbours
        if first < second
    }
    non_tree_segments = sorted(
        all_segments - tree_segments,
        key=lambda item: pipe_by_segment[item],
    )

    def tree_path(first: str, second: str) -> tuple[str, ...]:
        left = [first]
        right = [second]
        while tree_depth[left[-1]] > tree_depth[right[-1]]:
            left.append(tree_parent[left[-1]])
        while tree_depth[right[-1]] > tree_depth[left[-1]]:
            right.append(tree_parent[right[-1]])
        while left[-1] != right[-1]:
            left.append(tree_parent[left[-1]])
            right.append(tree_parent[right[-1]])
        return tuple(left + list(reversed(right[:-1])))

    findings: list[CycleFinding] = []
    for index, non_tree_segment in enumerate(non_tree_segments, start=1):
        first, second = sorted(non_tree_segment)
        node_path = tree_path(first, second)
        cycle_segments = {
            frozenset((left, right)) for left, right in zip(node_path, node_path[1:])
        }
        cycle_segments.add(non_tree_segment)
        findings.append(
            CycleFinding(
                cycle_index=index,
                node_keys=tuple(sorted(node_path)),
                pipe_keys=tuple(
                    sorted(pipe_by_segment[segment] for segment in cycle_segments)
                ),
            )
        )
    return tuple(findings)


def validate_topology(
    nodes: Iterable[TopologyNode],
    pipes: Iterable[TopologyPipe],
) -> ValidationReport:
    """Validate a network revision and return a deterministic structured report.

    Coordinates are checked for presence/range only.  Segment uniqueness and
    cycle detection intentionally use the undirected hydraulic graph because
    a reversed duplicate remains the same physical segment.  Elevation checks
    use the explicitly directed upstream/downstream inverts.
    """

    node_list = sorted(nodes, key=lambda node: node.stable_key)
    pipe_list = sorted(pipes, key=lambda pipe: pipe.stable_key)
    node_by_key = {node.stable_key: node for node in node_list}
    issues: list[ValidationIssue] = []

    for node in node_list:
        if not _valid_coordinate(node.longitude, node.latitude):
            _issue(
                issues,
                IssueSeverity.error,
                IssueCode.MISSING_COORDINATES,
                "node",
                node.stable_key,
                "Node coordinates are missing, non-finite, or outside EPSG:4326.",
                longitude=node.longitude,
                latitude=node.latitude,
            )
        if node.invert_elevation_m is None or not math.isfinite(
            node.invert_elevation_m
        ):
            _issue(
                issues,
                IssueSeverity.error,
                IssueCode.MISSING_INVERT,
                "node",
                node.stable_key,
                "Node invert elevation is required for simulation.",
                invert_elevation_m=node.invert_elevation_m,
            )

    seen_segments: dict[frozenset[str], str] = {}
    pipe_by_segment: dict[frozenset[str], str] = {}
    adjacency: dict[str, set[str]] = {key: set() for key in node_by_key}
    for pipe in pipe_list:
        upstream = pipe.upstream_node_key
        downstream = pipe.downstream_node_key
        missing = [key for key in (upstream, downstream) if key not in node_by_key]
        if missing:
            _issue(
                issues,
                IssueSeverity.error,
                IssueCode.DANGLING_NODE,
                "pipe_segment",
                pipe.stable_key,
                "Pipe references a node that does not exist in this revision.",
                missing_node_keys=sorted(set(missing)),
            )
        if upstream == downstream:
            _issue(
                issues,
                IssueSeverity.error,
                IssueCode.SELF_LOOP,
                "pipe_segment",
                pipe.stable_key,
                "Pipe upstream and downstream node are identical.",
                node_key=upstream,
            )

        if not missing:
            segment_key = frozenset((upstream, downstream))
            first_key = seen_segments.get(segment_key)
            if first_key is not None:
                _issue(
                    issues,
                    IssueSeverity.error,
                    IssueCode.DUPLICATE_SEGMENT,
                    "pipe_segment",
                    pipe.stable_key,
                    "Pipe duplicates an existing physical segment.",
                    duplicate_of=first_key,
                    node_keys=sorted((upstream, downstream)),
                )
            else:
                seen_segments[segment_key] = pipe.stable_key
                pipe_by_segment[segment_key] = pipe.stable_key
                adjacency[upstream].add(downstream)
                adjacency[downstream].add(upstream)

        upstream_node = node_by_key.get(upstream)
        downstream_node = node_by_key.get(downstream)
        upstream_invert = pipe.upstream_invert_elevation_m
        if upstream_invert is None and upstream_node is not None:
            upstream_invert = upstream_node.invert_elevation_m
        downstream_invert = pipe.downstream_invert_elevation_m
        if downstream_invert is None and downstream_node is not None:
            downstream_invert = downstream_node.invert_elevation_m
        if (
            upstream_invert is None
            or downstream_invert is None
            or not math.isfinite(upstream_invert)
            or not math.isfinite(downstream_invert)
        ):
            _issue(
                issues,
                IssueSeverity.error,
                IssueCode.MISSING_INVERT,
                "pipe_segment",
                pipe.stable_key,
                "Pipe endpoint inverts are required for simulation.",
                upstream_node_key=upstream,
                downstream_node_key=downstream,
                upstream_invert_elevation_m=upstream_invert,
                downstream_invert_elevation_m=downstream_invert,
            )
        elif upstream_invert + 1e-9 < downstream_invert:
            _issue(
                issues,
                IssueSeverity.error,
                IssueCode.REVERSE_SLOPE,
                "pipe_segment",
                pipe.stable_key,
                "Pipe invert rises from upstream to downstream.",
                upstream_node_key=upstream,
                downstream_node_key=downstream,
                upstream_invert_elevation_m=upstream_invert,
                downstream_invert_elevation_m=downstream_invert,
            )

    node_keys = tuple(node_by_key)
    components = _connected_components(node_keys, adjacency)
    has_disconnected_components = len(components) > 1
    if has_disconnected_components:
        largest_size = max(len(component) for component in components)
        primary = next(
            component for component in components if len(component) == largest_size
        )
        for component in components:
            if component == primary:
                continue
            _issue(
                issues,
                IssueSeverity.warning,
                IssueCode.DISCONNECTED_COMPONENT,
                "network",
                f"component:{component[0]}",
                "Network contains a component disconnected from the primary component.",
                node_count=len(component),
                node_keys=list(component),
            )

    cycles = _fundamental_cycles(adjacency, pipe_by_segment)
    for cycle in cycles:
        _issue(
            issues,
            IssueSeverity.warning,
            IssueCode.CYCLE_DETECTED,
            "network",
            f"cycle:{cycle.cycle_index:04d}",
            "Network contains an undirected cycle.",
            node_count=len(cycle.node_keys),
            node_keys=list(cycle.node_keys),
            pipe_keys=list(cycle.pipe_keys),
        )

    issues.sort(
        key=lambda issue: (
            issue.severity.value,
            issue.code.value,
            issue.entity_type,
            issue.entity_key,
            _canonical_json(dict(issue.details)),
        )
    )
    error_count = sum(issue.severity is IssueSeverity.error for issue in issues)
    warning_count = sum(issue.severity is IssueSeverity.warning for issue in issues)
    if error_count:
        status = ValidationStatus.invalid
    elif warning_count:
        status = ValidationStatus.valid_with_warnings
    else:
        status = ValidationStatus.valid

    return ValidationReport(
        status=status,
        simulation_ready=error_count == 0 and not has_disconnected_components,
        issues=tuple(issues),
        node_count=len(node_list),
        pipe_count=len(pipe_list),
        component_count=len(components),
        components=components,
        cycles=cycles,
    )


__all__ = [
    "CycleFinding",
    "IssueCode",
    "IssueSeverity",
    "TOPOLOGY_ALGORITHM_VERSION",
    "TopologyNode",
    "TopologyPipe",
    "ValidationIssue",
    "ValidationReport",
    "ValidationStatus",
    "validate_topology",
]
