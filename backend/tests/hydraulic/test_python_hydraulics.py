"""Focused tests for the pure-Python hydraulic fallback solver."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from app.services.hydraulic.solvers.python_hydraulics import (
    Catchment,
    HydraulicNetwork,
    HydraulicNode,
    HydraulicPipe,
    NetworkValidationError,
    NumericalStabilityError,
    PipeState,
    QualityStatus,
    RainfallForcing,
    RoutingMode,
    SolverConfig,
    UnsupportedRegimeError,
    courant_number,
    solve_network,
    solve_network_to_sink,
    stable_time_step,
)

G = 9.80665
DEPTH_FLOOR_M = 1.0e-4


def _node(
    key: str,
    invert: float,
    *,
    max_depth: float = 2.0,
    initial_depth: float = 0.0,
    area: float = 0.1,
    kind: str = "junction",
) -> HydraulicNode:
    return HydraulicNode(
        node_id=key,
        invert_elevation_m=invert,
        max_depth_m=max_depth,
        initial_depth_m=initial_depth,
        surface_area_m2=area,
        kind=kind,
    )


def _pipe(
    key: str,
    upstream: str,
    downstream: str,
    *,
    length: float = 20.0,
    diameter: float = 0.3,
    upstream_invert: float,
    downstream_invert: float,
    manning: float = 0.013,
) -> HydraulicPipe:
    return HydraulicPipe(
        pipe_id=key,
        upstream_node_id=upstream,
        downstream_node_id=downstream,
        length_m=length,
        diameter_m=diameter,
        manning_n=manning,
        upstream_invert_m=upstream_invert,
        downstream_invert_m=downstream_invert,
    )


def _catchment(key: str, outlet: str, area: float = 100.0) -> Catchment:
    return Catchment(
        catchment_id=key,
        outlet_node_id=outlet,
        area_m2=area,
        runoff_coefficient=1.0,
    )


def test_dry_trunk_has_zero_flow_and_closed_zero_balance() -> None:
    network = HydraulicNetwork(
        nodes=(_node("A", 2.0), _node("OUT", 1.8, kind="outfall")),
        pipes=(_pipe("P1", "A", "OUT", upstream_invert=2.0, downstream_invert=1.8),),
        catchments=(),
    )
    forcing = RainfallForcing(intensities_mm_h=(0.0, 0.0), step_seconds=60.0)

    result = solve_network(
        network,
        forcing,
        routing_mode=RoutingMode.MOTION_WAVE,
        config=SolverConfig(
            duration_s=180.0,
            reporting_interval_s=60.0,
            rain_step_s=60.0,
        ),
    )

    assert result.solver_id == "py_mw"
    assert result.quality_status is QualityStatus.VERIFIED
    assert result.mass_balance.rainfall_input_m3 == 0.0
    assert result.mass_balance.outfall_discharge_m3 == 0.0
    assert result.mass_balance.final_storage_m3 == 0.0
    assert result.mass_balance.residual_m3 == pytest.approx(0.0, abs=1.0e-12)
    assert all(frame.pipes[0].flow_m3s == 0.0 for frame in result.frames)
    assert all(frame.pipes[0].fill_ratio == 0.0 for frame in result.frames)
    assert result.stability.max_courant <= 0.5


def test_serrain_network_transports_rainfall_pulse_and_conserves_mass() -> None:
    network = HydraulicNetwork(
        nodes=(
            _node("A", 3.0, area=0.02),
            _node("B", 2.5, area=0.02),
            _node("OUT", 2.0, area=0.02, kind="outfall"),
        ),
        pipes=(
            _pipe("P1", "A", "B", upstream_invert=3.0, downstream_invert=2.5),
            _pipe("P2", "B", "OUT", upstream_invert=2.5, downstream_invert=2.0),
        ),
        catchments=(_catchment("C1", "A", area=200.0),),
    )
    forcing = RainfallForcing(intensities_mm_h=(100.0, 100.0), step_seconds=60.0)

    result = solve_network(
        network,
        forcing,
        config=SolverConfig(
            duration_s=120.0,
            reporting_interval_s=30.0,
            rain_step_s=60.0,
        ),
    )

    assert max(frame.pipes[0].flow_m3s for frame in result.frames) > 0.0
    assert max(frame.pipes[1].flow_m3s for frame in result.frames) > 0.0
    assert result.mass_balance.outfall_discharge_m3 > 0.0
    assert result.mass_balance.final_storage_m3 >= 0.0
    assert result.mass_balance.residual_relative <= 1.0e-12
    assert (
        result.mass_balance.rainfall_input_m3
        - result.mass_balance.infiltration_loss_m3
        - result.mass_balance.outfall_discharge_m3
        - result.mass_balance.overflow_m3
        - result.mass_balance.final_storage_m3
    ) == pytest.approx(0.0, abs=1.0e-12)


def test_parallel_loop_pipes_are_routed_without_mass_gain() -> None:
    network = HydraulicNetwork(
        nodes=(
            _node("A", 4.0, area=0.05),
            _node("B", 3.7, area=0.05),
            _node("OUT", 3.4, area=0.05, kind="outfall"),
        ),
        pipes=(
            _pipe("LOOP-1", "A", "B", upstream_invert=4.0, downstream_invert=3.7),
            _pipe("LOOP-2", "A", "B", upstream_invert=4.0, downstream_invert=3.7),
            _pipe("OUT-PIPE", "B", "OUT", upstream_invert=3.7, downstream_invert=3.4),
        ),
        catchments=(_catchment("C1", "A", area=300.0),),
    )
    forcing = RainfallForcing(intensities_mm_h=(80.0, 80.0), step_seconds=30.0)

    result = solve_network(
        network,
        forcing,
        config=SolverConfig(
            duration_s=90.0,
            reporting_interval_s=30.0,
            rain_step_s=30.0,
        ),
    )

    peak = max(
        (frame.pipes[0].flow_m3s, frame.pipes[1].flow_m3s) for frame in result.frames
    )
    assert peak[0] > 0.0
    assert peak[1] > 0.0
    assert result.mass_balance.residual_m3 == pytest.approx(0.0, abs=1.0e-12)
    assert all(
        0.0 <= frame.pipes[index].fill_ratio <= 1.0
        for frame in result.frames
        for index in range(3)
    )


def test_overloaded_pipe_reports_fill_surcharge_ratio_and_surcharged_state() -> None:
    network = HydraulicNetwork(
        nodes=(
            _node("A", 2.0, max_depth=0.5, area=0.01),
            _node("OUT", 1.8, max_depth=0.5, area=0.01, kind="outfall"),
        ),
        pipes=(
            _pipe(
                "P1",
                "A",
                "OUT",
                length=20.0,
                diameter=0.2,
                upstream_invert=2.0,
                downstream_invert=1.8,
            ),
        ),
        catchments=(_catchment("C1", "A", area=1000.0),),
    )
    forcing = RainfallForcing(intensities_mm_h=(1000.0,), step_seconds=20.0)

    result = solve_network(
        network,
        forcing,
        config=SolverConfig(
            duration_s=20.0,
            reporting_interval_s=20.0,
            rain_step_s=20.0,
        ),
    )

    pipe = result.frames[-1].pipes[0]
    assert pipe.state is PipeState.SURCHARGED
    assert pipe.surcharge_ratio > 1.0
    assert 0.0 <= pipe.fill_ratio <= 1.0
    assert result.frames[-1].nodes[0].surcharged is True


def test_diffusion_wave_allows_backflow_but_motion_wave_does_not() -> None:
    network = HydraulicNetwork(
        nodes=(
            _node("A", 1.0, max_depth=3.0, initial_depth=0.0),
            _node("B", 0.9, max_depth=3.0, initial_depth=0.15),
            _node("OUT", 0.8, max_depth=3.0, kind="outfall"),
        ),
        pipes=(
            _pipe(
                "P1",
                "A",
                "B",
                length=100.0,
                diameter=1.0,
                upstream_invert=1.0,
                downstream_invert=0.9,
            ),
            _pipe(
                "OUT-PIPE",
                "B",
                "OUT",
                length=100.0,
                diameter=1.0,
                upstream_invert=0.9,
                downstream_invert=0.8,
            ),
        ),
        catchments=(),
    )
    common = dict(
        duration_s=1.0,
        reporting_interval_s=1.0,
        rain_step_s=1.0,
    )

    diffusion = solve_network(
        network,
        RainfallForcing(intensities_mm_h=(), step_seconds=20.0),
        routing_mode=RoutingMode.DIFFUSION_WAVE,
        config=SolverConfig(**common),
    )
    motion = solve_network(
        network,
        RainfallForcing(intensities_mm_h=(), step_seconds=20.0),
        routing_mode=RoutingMode.MOTION_WAVE,
        config=SolverConfig(**common),
    )

    reverse_frames = [
        frame for frame in diffusion.frames if frame.pipes[0].flow_m3s < 0.0
    ]
    assert reverse_frames
    assert reverse_frames[0].pipes[0].direction.value == "upstream"
    assert all(frame.pipes[0].flow_m3s >= 0.0 for frame in motion.frames)
    assert diffusion.mass_balance.residual_m3 == pytest.approx(0.0, abs=1.0e-12)


def test_diffusion_wave_rejects_supercritical_request() -> None:
    network = HydraulicNetwork(
        nodes=(
            _node("A", 0.2, max_depth=2.0, initial_depth=0.05, area=1.0),
            _node("B", 0.0, max_depth=2.0, initial_depth=0.1, area=1.0),
            _node("OUT", -0.1, max_depth=2.0, area=1.0, kind="outfall"),
        ),
        pipes=(
            HydraulicPipe(
                pipe_id="P1",
                upstream_node_id="A",
                downstream_node_id="B",
                length_m=20.0,
                manning_n=0.001,
                upstream_invert_m=0.2,
                downstream_invert_m=0.0,
                width_m=0.01,
                height_m=1.0,
                shape="rectangular",
            ),
            _pipe(
                "OUT-PIPE",
                "B",
                "OUT",
                length=20.0,
                diameter=1.0,
                upstream_invert=0.0,
                downstream_invert=-0.1,
            ),
        ),
        catchments=(_catchment("C1", "A", area=1000.0),),
    )

    with pytest.raises(UnsupportedRegimeError, match="Froude"):
        solve_network(
            network,
            RainfallForcing(intensities_mm_h=(), step_seconds=1.0),
            routing_mode=RoutingMode.DIFFUSION_WAVE,
            config=SolverConfig(
                duration_s=1.0,
                reporting_interval_s=1.0,
                rain_step_s=1.0,
            ),
        )


def test_extreme_short_pipe_hits_frozen_twenty_substep_limit() -> None:
    network = HydraulicNetwork(
        nodes=(
            _node("A", 0.01, area=0.1),
            _node("B", 0.0, area=0.1, kind="outfall"),
        ),
        pipes=(
            _pipe(
                "P1",
                "A",
                "B",
                length=1.0e-6,
                diameter=0.01,
                upstream_invert=0.01,
                downstream_invert=0.0,
            ),
        ),
        catchments=(_catchment("C1", "A", area=1000.0),),
    )

    with pytest.raises(NumericalStabilityError, match="20 substeps"):
        solve_network(
            network,
            RainfallForcing(intensities_mm_h=(1000.0,), step_seconds=1.0),
            config=SolverConfig(
                duration_s=1.0,
                reporting_interval_s=1.0,
                rain_step_s=1.0,
            ),
        )


def test_public_cfl_helpers_follow_frozen_formula() -> None:
    speed = 2.0
    depth = 0.75
    control_length = 10.0
    courant_limit = 0.5

    expected = (
        courant_limit
        * control_length
        / (speed + math.sqrt(G * max(depth, DEPTH_FLOOR_M)))
    )
    actual = stable_time_step(
        velocity_mps=speed,
        depth_m=depth,
        control_length_m=control_length,
        courant_limit=courant_limit,
    )

    assert courant_number(speed, depth, control_length, 1.0) == pytest.approx(
        (speed + math.sqrt(G * max(depth, DEPTH_FLOOR_M))) / control_length
    )
    assert actual == pytest.approx(expected)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"duration_s": 0.0},
        {"duration_s": 61.0, "reporting_interval_s": 30.0},
        {"duration_s": 60.0, "max_courant": 0.51},
        {"duration_s": 60.0, "max_substeps": 21},
        {"duration_s": float("inf")},
    ],
)
def test_extreme_solver_configuration_is_rejected(
    kwargs: dict[str, float | int],
) -> None:
    values: dict[str, float | int] = {
        "duration_s": 60.0,
        "reporting_interval_s": 30.0,
        "rain_step_s": 30.0,
        "max_courant": 0.5,
        "max_substeps": 20,
    }
    values.update(kwargs)

    with pytest.raises(ValueError):
        SolverConfig(**values)


@pytest.mark.parametrize("bad_value", [0.0, -1.0, float("nan"), float("inf")])
def test_non_finite_or_non_positive_pipe_dimensions_are_rejected(
    bad_value: float,
) -> None:
    with pytest.raises(NetworkValidationError, match="diameter|finite"):
        HydraulicNetwork(
            nodes=(_node("A", 1.0), _node("B", 0.9)),
            pipes=(
                _pipe(
                    "P1",
                    "A",
                    "B",
                    diameter=bad_value,
                    upstream_invert=1.0,
                    downstream_invert=0.9,
                ),
            ),
            catchments=(),
        )


def test_same_input_is_deterministic() -> None:
    network = HydraulicNetwork(
        nodes=(_node("A", 2.0), _node("OUT", 1.8, kind="outfall")),
        pipes=(_pipe("P1", "A", "OUT", upstream_invert=2.0, downstream_invert=1.8),),
        catchments=(_catchment("C1", "A"),),
    )
    forcing = RainfallForcing(intensities_mm_h=(25.0, 50.0, 0.0), step_seconds=60.0)
    config = SolverConfig(
        duration_s=180.0,
        reporting_interval_s=60.0,
        rain_step_s=60.0,
    )

    first = solve_network(network, forcing, config=config)
    second = solve_network(network, forcing, config=config)

    assert first.canonical_json() == second.canonical_json()


def test_finite_rainfall_pulse_does_not_repeat_last_nonzero_intensity() -> None:
    network = HydraulicNetwork(
        nodes=(_node("A", 2.0), _node("OUT", 1.8, kind="outfall")),
        pipes=(_pipe("P1", "A", "OUT", upstream_invert=2.0, downstream_invert=1.8),),
        catchments=(_catchment("C1", "A", area=100.0),),
    )

    result = solve_network(
        network,
        RainfallForcing(intensities_mm_h=(100.0,), step_seconds=60.0),
        config=SolverConfig(
            duration_s=120.0,
            reporting_interval_s=60.0,
            rain_step_s=60.0,
        ),
    )

    expected_pulse_m3 = 100.0 * 60.0 / 3_600_000.0 * 100.0
    assert result.mass_balance.rainfall_input_m3 == pytest.approx(expected_pulse_m3)


@pytest.mark.parametrize(
    "network",
    [
        HydraulicNetwork(nodes=(), pipes=(), catchments=()),
        HydraulicNetwork(
            nodes=(_node("A", 2.0), _node("ISLAND", 1.0)),
            pipes=(),
            catchments=(),
        ),
        HydraulicNetwork(
            nodes=(_node("A", 2.0), _node("OUT", 1.0, kind="outfall")),
            pipes=(),
            catchments=(),
        ),
    ],
)
def test_network_requires_connected_objects_and_explicit_boundary(
    network: HydraulicNetwork,
) -> None:
    with pytest.raises(NetworkValidationError, match="empty|connected|outfall"):
        solve_network(
            network,
            RainfallForcing(intensities_mm_h=(), step_seconds=60.0),
            config=SolverConfig(
                duration_s=60.0,
                reporting_interval_s=60.0,
                rain_step_s=60.0,
            ),
        )


def test_diffusion_wave_steep_dry_trunk_stays_dry_instead_of_pressure_flow() -> None:
    network = HydraulicNetwork(
        nodes=(_node("A", 100.0), _node("OUT", 0.0, kind="outfall")),
        pipes=(
            _pipe(
                "P1",
                "A",
                "OUT",
                length=100.0,
                diameter=1.0,
                upstream_invert=100.0,
                downstream_invert=0.0,
            ),
        ),
        catchments=(),
    )

    result = solve_network(
        network,
        RainfallForcing(intensities_mm_h=(), step_seconds=60.0),
        routing_mode=RoutingMode.DIFFUSION_WAVE,
        config=SolverConfig(
            duration_s=1.0,
            reporting_interval_s=1.0,
            rain_step_s=1.0,
        ),
    )

    assert all(frame.pipes[0].flow_m3s == 0.0 for frame in result.frames)
    assert all(frame.pipes[0].state is PipeState.DRY for frame in result.frames)


def test_initial_pipe_storage_is_included_in_the_conservation_ledger() -> None:
    network = HydraulicNetwork(
        nodes=(_node("A", 2.0), _node("OUT", 1.8, kind="outfall")),
        pipes=(
            replace(
                _pipe(
                    "P1",
                    "A",
                    "OUT",
                    upstream_invert=2.0,
                    downstream_invert=1.8,
                ),
                initial_flow_m3s=0.01,
            ),
        ),
        catchments=(),
    )

    result = solve_network(
        network,
        RainfallForcing(intensities_mm_h=(), step_seconds=60.0),
        config=SolverConfig(
            duration_s=1.0,
            reporting_interval_s=1.0,
            rain_step_s=1.0,
        ),
    )

    assert result.frames[0].pipes[0].flow_m3s == pytest.approx(0.01)
    assert result.mass_balance.initial_pipe_storage_m3 > 0.0
    assert result.mass_balance.to_dict()["initial_pipe_storage_m3"] > 0.0


@pytest.mark.asyncio
async def test_streaming_solver_does_not_retain_all_frames() -> None:
    network = HydraulicNetwork(
        nodes=(_node("A", 2.0), _node("OUT", 1.8, kind="outfall")),
        pipes=(_pipe("P1", "A", "OUT", upstream_invert=2.0, downstream_invert=1.8),),
        catchments=(),
    )

    class Sink:
        def __init__(self) -> None:
            self.frames = []

        async def write(self, frame) -> None:
            self.frames.append(frame)

    sink = Sink()
    result = await solve_network_to_sink(
        network,
        RainfallForcing(intensities_mm_h=(), step_seconds=30.0),
        sink,
        config=SolverConfig(
            duration_s=60.0,
            reporting_interval_s=30.0,
            rain_step_s=30.0,
        ),
    )

    assert len(sink.frames) == 3
    assert result.frames == ()


def test_extreme_but_finite_pipe_dimensions_fail_with_structured_domain_error() -> None:
    network = HydraulicNetwork(
        nodes=(_node("A", 1.0), _node("OUT", 0.0, kind="outfall")),
        pipes=(
            _pipe(
                "P1",
                "A",
                "OUT",
                length=1.0,
                diameter=1.0e308,
                upstream_invert=1.0,
                downstream_invert=0.0,
            ),
        ),
        catchments=(),
    )

    with pytest.raises(NetworkValidationError, match="capacity|geometry|finite"):
        solve_network(
            network,
            RainfallForcing(intensities_mm_h=(), step_seconds=1.0),
            config=SolverConfig(
                duration_s=1.0,
                reporting_interval_s=1.0,
                rain_step_s=1.0,
            ),
        )
