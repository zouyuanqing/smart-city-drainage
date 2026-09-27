"""Deterministic tree-and-loop hydraulic network seed generation.

The generator returns immutable candidate records only.  Device mappings are
inactive candidates and therefore require the explicit operator/admin
confirmation required by the frozen implementation contract.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from app.services.hydraulic.topology import (
    TopologyNode,
    TopologyPipe,
    ValidationReport,
    validate_topology,
)

SEED_ALGORITHM_VERSION = "tree-loop-seed-v1"
DEFAULT_SHANGHAI_BOUNDS = (121.30, 31.15, 121.60, 31.32)
SUPPORTED_OBSERVATION_KINDS = frozenset({"water_level", "flow", "rainfall", "other"})


@dataclass(frozen=True, slots=True)
class SeedConfig:
    """N/M/K controls for the synthetic network.

    ``N`` is the number of upstream branches, ``M`` is the number of junctions
    per branch, and ``K`` is the number of loop-back segments.  The generated
    network has ``N * M + 1`` nodes, ``N * M`` tree segments and ``K`` loop
    segments.  Bounds are EPSG:4326 lon/lat values.
    """

    n: int
    m: int
    k: int
    random_seed: int
    west: float = DEFAULT_SHANGHAI_BOUNDS[0]
    south: float = DEFAULT_SHANGHAI_BOUNDS[1]
    east: float = DEFAULT_SHANGHAI_BOUNDS[2]
    north: float = DEFAULT_SHANGHAI_BOUNDS[3]
    invert_min_m: float = 0.5
    invert_max_m: float = 8.0
    nominal_diameter_min_m: float = 0.3
    nominal_diameter_max_m: float = 1.2
    roughness_manning_n: float = 0.013

    def __post_init__(self) -> None:
        if self.n < 1:
            raise ValueError("N must be at least 1")
        if self.m < 2:
            raise ValueError("M must be at least 2")
        maximum_k = max(0, self.n - 1)
        if self.k < 0 or self.k > maximum_k:
            raise ValueError(f"K must be between 0 and {maximum_k}")
        bounds = (self.west, self.south, self.east, self.north)
        if not all(math.isfinite(value) for value in bounds):
            raise ValueError("seed bounds must be finite")
        if not (-180 <= self.west < self.east <= 180):
            raise ValueError("longitude bounds must be ordered within EPSG:4326")
        if not (-90 <= self.south < self.north <= 90):
            raise ValueError("latitude bounds must be ordered within EPSG:4326")
        if (
            not math.isfinite(self.invert_min_m)
            or not math.isfinite(self.invert_max_m)
            or not 0 < self.invert_min_m < self.invert_max_m
        ):
            raise ValueError("seed invert bounds must be finite, ordered, and positive")
        if (
            not math.isfinite(self.nominal_diameter_min_m)
            or not math.isfinite(self.nominal_diameter_max_m)
            or not 0 < self.nominal_diameter_min_m <= self.nominal_diameter_max_m
        ):
            raise ValueError(
                "seed diameter bounds must be finite, ordered, and positive"
            )
        if not math.isfinite(self.roughness_manning_n) or self.roughness_manning_n <= 0:
            raise ValueError("Manning roughness must be finite and positive")

    @property
    def node_count(self) -> int:
        return self.n * self.m + 1

    @property
    def pipe_count(self) -> int:
        return self.n * self.m + self.k

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "m": self.m,
            "k": self.k,
            "random_seed": self.random_seed,
            "bounds": {
                "west": self.west,
                "south": self.south,
                "east": self.east,
                "north": self.north,
            },
            "invert_min_m": self.invert_min_m,
            "invert_max_m": self.invert_max_m,
            "nominal_diameter_min_m": self.nominal_diameter_min_m,
            "nominal_diameter_max_m": self.nominal_diameter_max_m,
            "roughness_manning_n": self.roughness_manning_n,
        }


@dataclass(frozen=True, slots=True)
class SeedNode:
    stable_key: str
    longitude: float
    latitude: float
    invert_elevation_m: float
    kind: str

    def to_topology_node(self) -> TopologyNode:
        return TopologyNode(
            stable_key=self.stable_key,
            longitude=self.longitude,
            latitude=self.latitude,
            invert_elevation_m=self.invert_elevation_m,
            kind=self.kind,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "stable_key": self.stable_key,
            "kind": self.kind,
            "longitude": self.longitude,
            "latitude": self.latitude,
            "invert_elevation_m": self.invert_elevation_m,
        }


@dataclass(frozen=True, slots=True)
class SeedPipe:
    stable_key: str
    upstream_node_key: str
    downstream_node_key: str
    nominal_diameter_m: float
    length_m: float
    roughness_manning_n: float

    def to_topology_pipe(self) -> TopologyPipe:
        return TopologyPipe(
            stable_key=self.stable_key,
            upstream_node_key=self.upstream_node_key,
            downstream_node_key=self.downstream_node_key,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "stable_key": self.stable_key,
            "upstream_node_key": self.upstream_node_key,
            "downstream_node_key": self.downstream_node_key,
            "nominal_diameter_m": self.nominal_diameter_m,
            "length_m": self.length_m,
            "roughness_manning_n": self.roughness_manning_n,
        }


@dataclass(frozen=True, slots=True)
class SeedDevice:
    """Existing device projection accepted by the seed generator."""

    device_id: str
    device_code: str
    longitude: float
    latitude: float
    observation_kind: str = "water_level"


@dataclass(frozen=True, slots=True)
class CandidateDeviceBinding:
    device_id: str
    device_code: str
    observation_kind: str
    target_type: str
    target_stable_key: str
    mapping_version: int
    active: bool
    distance_m: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "device_code": self.device_code,
            "observation_kind": self.observation_kind,
            "target_type": self.target_type,
            "target_stable_key": self.target_stable_key,
            "mapping_version": self.mapping_version,
            "active": self.active,
            "distance_m": round(self.distance_m, 3),
        }


@dataclass(frozen=True, slots=True)
class SeedNetwork:
    config: SeedConfig
    nodes: tuple[SeedNode, ...]
    pipes: tuple[SeedPipe, ...]
    candidate_bindings: tuple[CandidateDeviceBinding, ...]
    validation: ValidationReport
    algorithm_version: str = SEED_ALGORITHM_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithm_version": self.algorithm_version,
            "config": self.config.to_dict(),
            "nodes": [node.to_dict() for node in self.nodes],
            "pipes": [pipe.to_dict() for pipe in self.pipes],
            "candidate_bindings": [
                binding.to_dict() for binding in self.candidate_bindings
            ],
            "validation": self.validation.to_dict(),
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


def _rounded(value: float, digits: int = 8) -> float:
    return round(value, digits)


def _haversine_m(
    first_lon: float, first_lat: float, second_lon: float, second_lat: float
) -> float:
    radius_m = 6_371_008.8
    lon1, lat1, lon2, lat2 = map(
        math.radians, (first_lon, first_lat, second_lon, second_lat)
    )
    delta_lon = lon2 - lon1
    delta_lat = lat2 - lat1
    hav = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return radius_m * 2 * math.atan2(math.sqrt(hav), math.sqrt(1 - hav))


def _validate_devices(devices: Iterable[SeedDevice]) -> tuple[SeedDevice, ...]:
    result: list[SeedDevice] = []
    seen_ids: set[str] = set()
    for device in sorted(devices, key=lambda item: (item.device_id, item.device_code)):
        if not device.device_id or not device.device_code:
            raise ValueError("seed devices require non-empty id and code")
        if device.device_id in seen_ids:
            raise ValueError(f"duplicate seed device id: {device.device_id}")
        if device.observation_kind not in SUPPORTED_OBSERVATION_KINDS:
            raise ValueError(f"unsupported observation kind: {device.observation_kind}")
        if not (
            math.isfinite(device.longitude)
            and math.isfinite(device.latitude)
            and -180 <= device.longitude <= 180
            and -90 <= device.latitude <= 90
        ):
            raise ValueError(f"invalid coordinates for device {device.device_id}")
        seen_ids.add(device.device_id)
        result.append(device)
    return tuple(result)


def _node_key(branch: int, index: int) -> str:
    return f"SH-N-{branch:03d}-{index:03d}"


def _outfall_key() -> str:
    return "SH-N-OUTFALL"


def _pipe_key(index: int) -> str:
    return f"SH-P-{index:05d}"


def _generate_nodes(config: SeedConfig, rng: random.Random) -> tuple[SeedNode, ...]:
    lon_span = (config.east - config.west) * 0.92
    lat_span = (config.north - config.south) * 0.92
    lon_origin = config.west + (config.east - config.west) * 0.04
    lat_origin = config.south + (config.north - config.south) * 0.04
    invert_span = config.invert_max_m - config.invert_min_m
    nodes: list[SeedNode] = []

    for branch in range(config.n):
        for index in range(config.m):
            progress = index / max(1, config.m - 1)
            branch_fraction = branch / max(1, config.n - 1)
            lon = lon_origin + lon_span * (
                0.08 + 0.82 * progress + 0.06 * branch_fraction
            )
            lat = lat_origin + lat_span * (
                0.08 + 0.84 * (1.0 - branch_fraction) + rng.uniform(-0.025, 0.025)
            )
            # Rank elevations by graph position.  Increasing index always
            # decreases invert, while the loop-back rank is equal, so every
            # valid N/M/K configuration stays inside bounds and slope-safe.
            rank_span = config.m + config.n - 2
            rank = (config.m - 1 - index) - branch
            normalized_rank = (rank + config.n - 1) / rank_span if rank_span else 0.0
            invert = config.invert_min_m + invert_span * normalized_rank
            nodes.append(
                SeedNode(
                    stable_key=_node_key(branch, index),
                    longitude=_rounded(lon),
                    latitude=_rounded(lat),
                    invert_elevation_m=_rounded(invert, 4),
                    kind="manhole",
                )
            )

    nodes.append(
        SeedNode(
            stable_key=_outfall_key(),
            longitude=_rounded(config.east - (config.east - config.west) * 0.04),
            latitude=_rounded(config.south + (config.north - config.south) * 0.5),
            invert_elevation_m=_rounded(config.invert_min_m, 4),
            kind="outfall",
        )
    )
    return tuple(sorted(nodes, key=lambda node: node.stable_key))


def _length_m(first: SeedNode, second: SeedNode) -> float:
    return round(
        max(
            0.001,
            _haversine_m(
                first.longitude, first.latitude, second.longitude, second.latitude
            ),
        ),
        3,
    )


def _pipe(
    stable_key: str,
    first: SeedNode,
    second: SeedNode,
    config: SeedConfig,
    rng: random.Random,
) -> SeedPipe:
    diameter = rng.uniform(config.nominal_diameter_min_m, config.nominal_diameter_max_m)
    return SeedPipe(
        stable_key=stable_key,
        upstream_node_key=first.stable_key,
        downstream_node_key=second.stable_key,
        nominal_diameter_m=_rounded(diameter, 3),
        length_m=_length_m(first, second),
        roughness_manning_n=config.roughness_manning_n,
    )


def _generate_pipes(
    config: SeedConfig, nodes: Sequence[SeedNode], rng: random.Random
) -> tuple[SeedPipe, ...]:
    node_by_key = {node.stable_key: node for node in nodes}
    pipes: list[SeedPipe] = []
    next_index = 1
    for branch in range(config.n):
        for index in range(config.m - 1):
            pipes.append(
                _pipe(
                    _pipe_key(next_index),
                    node_by_key[_node_key(branch, index)],
                    node_by_key[_node_key(branch, index + 1)],
                    config,
                    rng,
                )
            )
            next_index += 1
        pipes.append(
            _pipe(
                _pipe_key(next_index),
                node_by_key[_node_key(branch, config.m - 1)],
                node_by_key[_outfall_key()],
                config,
                rng,
            )
        )
        next_index += 1

    candidates = [
        (branch, (branch * (config.m - 2)) % (config.m - 1))
        for branch in range(config.n - 1)
    ]
    for branch, index in candidates[: config.k]:
        pipes.append(
            _pipe(
                _pipe_key(next_index),
                node_by_key[_node_key(branch, index + 1)],
                node_by_key[_node_key(branch + 1, index)],
                config,
                rng,
            )
        )
        next_index += 1
    return tuple(sorted(pipes, key=lambda pipe: pipe.stable_key))


def _candidate_bindings(
    devices: Sequence[SeedDevice], nodes: Sequence[SeedNode]
) -> tuple[CandidateDeviceBinding, ...]:
    bindings: list[CandidateDeviceBinding] = []
    for device in devices:
        nearest = min(
            nodes,
            key=lambda node: (
                _haversine_m(
                    device.longitude,
                    device.latitude,
                    node.longitude,
                    node.latitude,
                ),
                node.stable_key,
            ),
        )
        distance = _haversine_m(
            device.longitude,
            device.latitude,
            nearest.longitude,
            nearest.latitude,
        )
        bindings.append(
            CandidateDeviceBinding(
                device_id=device.device_id,
                device_code=device.device_code,
                observation_kind=device.observation_kind,
                target_type="node",
                target_stable_key=nearest.stable_key,
                mapping_version=1,
                active=False,
                distance_m=distance,
            )
        )
    return tuple(sorted(bindings, key=lambda binding: binding.device_id))


def generate_tree_loop_seed(
    config: SeedConfig,
    devices: Iterable[SeedDevice] = (),
) -> SeedNetwork:
    """Generate a deterministic N/M/K tree-loop network and device candidates.

    The same config and device input always produce byte-identical canonical
    JSON and content hash.  Device mappings are deliberately inactive; callers
    must persist them as candidates and obtain explicit confirmation before
    setting ``active=true``.
    """

    device_list = _validate_devices(devices)
    rng = random.Random(config.random_seed)
    nodes = _generate_nodes(config, rng)
    pipes = _generate_pipes(config, nodes, rng)
    validation = validate_topology(
        (node.to_topology_node() for node in nodes),
        (pipe.to_topology_pipe() for pipe in pipes),
    )
    if not validation.simulation_ready:
        raise RuntimeError("generated seed failed topology validation")
    if validation.cycle_count != config.k:
        raise RuntimeError(
            "generated seed cycle count "
            f"{validation.cycle_count} does not match K={config.k}"
        )
    return SeedNetwork(
        config=config,
        nodes=nodes,
        pipes=pipes,
        candidate_bindings=_candidate_bindings(device_list, nodes),
        validation=validation,
    )


__all__ = [
    "CandidateDeviceBinding",
    "DEFAULT_SHANGHAI_BOUNDS",
    "SEED_ALGORITHM_VERSION",
    "SeedConfig",
    "SeedDevice",
    "SeedNetwork",
    "SeedNode",
    "SeedPipe",
    "generate_tree_loop_seed",
]
