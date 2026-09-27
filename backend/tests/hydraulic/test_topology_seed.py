"""Focused topology validation and deterministic seed tests."""

from __future__ import annotations

import json

import pytest

from app.services.hydraulic.seed import (
    SEED_ALGORITHM_VERSION,
    SeedConfig,
    SeedDevice,
    generate_tree_loop_seed,
)
from app.services.hydraulic.topology import (
    IssueCode,
    IssueSeverity,
    TopologyNode,
    TopologyPipe,
    ValidationStatus,
    validate_topology,
)


def _node(
    key: str,
    *,
    longitude: float | None = 121.40,
    latitude: float | None = 31.20,
    invert: float | None = 2.0,
    kind: str = "junction",
) -> TopologyNode:
    return TopologyNode(
        stable_key=key,
        longitude=longitude,
        latitude=latitude,
        invert_elevation_m=invert,
        kind=kind,
    )


def _pipe(
    key: str,
    upstream: str,
    downstream: str,
    *,
    length_m: float = 10.0,
    upstream_invert: float | None = None,
    downstream_invert: float | None = None,
) -> TopologyPipe:
    return TopologyPipe(
        stable_key=key,
        upstream_node_key=upstream,
        downstream_node_key=downstream,
        upstream_invert_elevation_m=upstream_invert,
        downstream_invert_elevation_m=downstream_invert,
        length_m=length_m,
    )


def _codes(report: object) -> set[IssueCode]:
    return {issue.code for issue in report.issues}


@pytest.mark.parametrize(
    ("test_id", "nodes", "pipes", "expected_code", "expected_severity"),
    [
        (
            "dangling",
            [_node("A")],
            [_pipe("P1", "A", "MISSING")],
            IssueCode.DANGLING_NODE,
            IssueSeverity.error,
        ),
        (
            "disconnected",
            [_node("A", invert=2.0), _node("B", invert=2.0), _node("C", invert=2.0)],
            [_pipe("P1", "A", "B")],
            IssueCode.DISCONNECTED_COMPONENT,
            IssueSeverity.warning,
        ),
        (
            "reverse-slope",
            [_node("A", invert=1.0), _node("B", invert=2.0)],
            [_pipe("P1", "A", "B")],
            IssueCode.REVERSE_SLOPE,
            IssueSeverity.error,
        ),
        (
            "duplicate",
            [_node("A"), _node("B")],
            [_pipe("P1", "A", "B"), _pipe("P2", "B", "A")],
            IssueCode.DUPLICATE_SEGMENT,
            IssueSeverity.error,
        ),
        (
            "missing-coordinates",
            [_node("A", longitude=None), _node("B")],
            [_pipe("P1", "A", "B")],
            IssueCode.MISSING_COORDINATES,
            IssueSeverity.error,
        ),
        (
            "cycle",
            [_node("A", invert=4.0), _node("B", invert=3.0), _node("C", invert=2.0)],
            [
                _pipe("P1", "A", "B", upstream_invert=4.0, downstream_invert=3.0),
                _pipe("P2", "B", "C", upstream_invert=3.0, downstream_invert=2.0),
                _pipe("P3", "C", "A", upstream_invert=2.0, downstream_invert=1.0),
            ],
            IssueCode.CYCLE_DETECTED,
            IssueSeverity.warning,
        ),
    ],
)
def test_each_topology_error_has_a_focused_case(
    test_id: str,
    nodes: list[TopologyNode],
    pipes: list[TopologyPipe],
    expected_code: IssueCode,
    expected_severity: IssueSeverity,
) -> None:
    report = validate_topology(nodes, pipes)

    matching = [issue for issue in report.issues if issue.code is expected_code]
    assert matching, (test_id, _codes(report))
    assert matching[0].severity is expected_severity
    assert report.status is (
        ValidationStatus.invalid
        if expected_severity is IssueSeverity.error
        else ValidationStatus.valid_with_warnings
    )


def test_topology_report_is_structured_and_deterministic() -> None:
    nodes = [_node("A", invert=4.0), _node("B", invert=3.0), _node("C", invert=2.0)]
    pipes = [
        _pipe("P2", "B", "C", upstream_invert=3.0, downstream_invert=2.0),
        _pipe("P1", "A", "B", upstream_invert=4.0, downstream_invert=3.0),
        _pipe("P3", "C", "A", upstream_invert=2.0, downstream_invert=1.0),
    ]

    first = validate_topology(nodes, pipes)
    second = validate_topology(reversed(nodes), reversed(pipes))

    assert first.canonical_json() == second.canonical_json()
    assert first.content_sha256 == second.content_sha256
    payload = json.loads(first.canonical_json())
    assert payload["counts"] == {
        "nodes": 3,
        "pipes": 3,
        "components": 1,
        "cycles": 1,
        "errors": 0,
        "warnings": 1,
    }
    assert payload["issues"][0]["code"] == IssueCode.CYCLE_DETECTED.value
    assert first.simulation_ready is True


def test_seed_nmk_configuration_builds_tree_loop_mix() -> None:
    seed = generate_tree_loop_seed(SeedConfig(n=4, m=5, k=3, random_seed=20260924))

    assert seed.algorithm_version == SEED_ALGORITHM_VERSION
    assert seed.config.node_count == 4 * 5 + 1
    assert seed.config.pipe_count == 4 * 5 + 3
    assert len(seed.nodes) == seed.config.node_count
    assert len(seed.pipes) == seed.config.pipe_count
    assert seed.validation.cycle_count == 3
    loop_pipe_count = seed.config.pipe_count - seed.config.n * seed.config.m
    assert loop_pipe_count == seed.config.k
    assert seed.validation.component_count == 1
    assert seed.validation.simulation_ready is True
    assert any(node.kind == "outfall" for node in seed.nodes)


def test_same_random_seed_is_byte_identical() -> None:
    config = SeedConfig(n=3, m=4, k=2, random_seed=123456)
    devices = [
        SeedDevice("device-b", "MH-002", 121.4750, 31.2350, "water_level"),
        SeedDevice("device-a", "MH-001", 121.4737, 31.2304, "water_level"),
    ]

    first = generate_tree_loop_seed(config, devices)
    second = generate_tree_loop_seed(config, list(reversed(devices)))

    assert first.canonical_json() == second.canonical_json()
    assert first.content_sha256 == second.content_sha256


def test_seed_binds_existing_devices_only_as_inactive_candidates() -> None:
    device = SeedDevice(
        device_id="a0000001-0000-0000-0000-000000000001",
        device_code="MH-001",
        longitude=121.4737,
        latitude=31.2304,
    )

    seed = generate_tree_loop_seed(SeedConfig(n=2, m=3, k=1, random_seed=7), [device])

    assert len(seed.candidate_bindings) == 1
    binding = seed.candidate_bindings[0]
    assert binding.device_id == device.device_id
    assert binding.target_type == "node"
    assert binding.target_stable_key in {node.stable_key for node in seed.nodes}
    assert binding.active is False
    assert binding.distance_m >= 0


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"n": 0, "m": 3, "k": 0}, "N must be at least 1"),
        ({"n": 2, "m": 1, "k": 0}, "M must be at least 2"),
        ({"n": 2, "m": 3, "k": 2}, "K must be between 0 and 1"),
    ],
)
def test_seed_config_rejects_invalid_nmk(kwargs: dict[str, int], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        SeedConfig(random_seed=1, **kwargs)


def test_cycle_finding_contains_only_the_actual_cycle_not_its_shared_trunk() -> None:
    nodes = [
        _node("0-TRUNK"),
        _node("A"),
        _node("B"),
        _node("C"),
        _node("D"),
    ]
    pipes = [
        _pipe("0-TRUNK", "0-TRUNK", "A"),
        _pipe("P1", "A", "B"),
        _pipe("P2", "B", "C"),
        _pipe("P3", "C", "D"),
        _pipe("P4", "D", "A"),
    ]

    report = validate_topology(nodes, pipes)

    assert report.cycle_count == 1
    assert set(report.cycles[0].node_keys) == {"A", "B", "C", "D"}
    assert set(report.cycles[0].pipe_keys) == {"P1", "P2", "P3", "P4"}
    assert (
        "0-TRUNK" not in json.loads(report.canonical_json())["cycles"][0]["node_keys"]
    )


def test_missing_invert_and_disconnected_components_block_simulation_readiness() -> (
    None
):
    missing_invert = validate_topology(
        [_node("A", invert=None), _node("B", invert=1.0)],
        [_pipe("P1", "A", "B")],
    )
    assert IssueCode.MISSING_INVERT in _codes(missing_invert)
    assert missing_invert.status is ValidationStatus.invalid
    assert not missing_invert.simulation_ready

    disconnected = validate_topology(
        [
            _node("A", invert=2.0),
            _node("B", invert=1.0),
            _node("ISOLATED", invert=1.0),
        ],
        [_pipe("P1", "A", "B")],
    )
    assert IssueCode.DISCONNECTED_COMPONENT in _codes(disconnected)
    assert disconnected.status is ValidationStatus.valid_with_warnings
    assert not disconnected.simulation_ready


def test_seed_generation_is_total_and_honors_invert_bounds() -> None:
    config = SeedConfig(
        n=4,
        m=5,
        k=3,
        random_seed=20260924,
        invert_min_m=3.9,
        invert_max_m=4.0,
    )
    seed = generate_tree_loop_seed(config)
    elevations = {node.stable_key: node.invert_elevation_m for node in seed.nodes}

    assert seed.validation.simulation_ready is True
    assert all(
        config.invert_min_m <= elevation <= config.invert_max_m
        for elevation in elevations.values()
    )
    assert all(
        elevations[pipe.upstream_node_key] + 1e-9
        >= elevations[pipe.downstream_node_key]
        for pipe in seed.pipes
    )

    for n in range(1, 6):
        for m in range(2, 7):
            for k in range(max(0, n - 1) + 1):
                exhaustive = generate_tree_loop_seed(
                    SeedConfig(
                        n=n,
                        m=m,
                        k=k,
                        random_seed=1_000 * n + 10 * m + k,
                        invert_min_m=7.999,
                        invert_max_m=8.0,
                    )
                )
                exhaustive_elevations = {
                    node.stable_key: node.invert_elevation_m
                    for node in exhaustive.nodes
                }
                assert exhaustive.validation.simulation_ready is True
                assert all(
                    7.999 <= elevation <= 8.0
                    for elevation in exhaustive_elevations.values()
                )
                assert all(
                    exhaustive_elevations[pipe.upstream_node_key] + 1e-9
                    >= exhaustive_elevations[pipe.downstream_node_key]
                    for pipe in exhaustive.pipes
                )
