"""Focused rainfall source, validation, and scenario-copy tests."""

from __future__ import annotations

import math
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.services.hydraulic.rainfall import (
    ALLOWED_RETURN_PERIODS,
    MAX_DURATION_SECONDS,
    CatchmentAllocation,
    RainfallPattern,
    RainfallRequest,
    RainfallScenario,
    RainfallSeries,
    RainfallValidationError,
    SyntheticRainfallSource,
    copy_scenario,
    generate_custom_rainfall,
    generate_design_rainfall,
    generate_random_rainfall,
    materialize_rainfall,
    materialize_scenario,
)

UTC = timezone.utc
START = datetime(2026, 9, 24, tzinfo=UTC)


def test_design_return_periods_are_monotonic_for_the_same_grid() -> None:
    series = [
        generate_design_rainfall(
            period,
            start_at=START,
            duration_seconds=3600,
            step_seconds=300,
        )
        for period in ALLOWED_RETURN_PERIODS
    ]

    assert [item.return_period_years for item in series] == list(ALLOWED_RETURN_PERIODS)
    assert all(
        right.total_depth_mm > left.total_depth_mm
        for left, right in zip(series, series[1:])
    )
    assert all(
        right_value >= left_value
        for left, right in zip(series, series[1:])
        for left_value, right_value in zip(left.intensity_mm_h, right.intensity_mm_h)
    )


@pytest.mark.parametrize("period", [0, 4, 101, -1])
def test_design_rejects_unsupported_return_periods(period: int) -> None:
    with pytest.raises(RainfallValidationError, match="return_period_years"):
        generate_design_rainfall(period, duration_seconds=600, step_seconds=300)


@pytest.mark.parametrize("step", [0, 120, 899, 901])
def test_all_rainfall_steps_are_restricted_to_the_frozen_grid(step: int) -> None:
    with pytest.raises(RainfallValidationError, match="step_seconds"):
        generate_random_rainfall(
            start_at=START,
            duration_seconds=3600,
            step_seconds=step,
            seed=11,
        )


def test_custom_rainfall_accepts_72_hours_and_uses_floor_plus_one_samples() -> None:
    values = [0.0] * (MAX_DURATION_SECONDS // 60 + 1)
    series = generate_custom_rainfall(
        values,
        start_at=START,
        duration_seconds=MAX_DURATION_SECONDS,
        step_seconds=60,
    )

    assert series.sample_count == 4321
    assert series.duration == MAX_DURATION_SECONDS
    assert series.step_seconds == 60
    assert series.timestamps[0] == START
    assert series.timestamps[-1] == START + timedelta(hours=72)
    assert all(math.isfinite(value) and value >= 0 for value in series.intensities)


@pytest.mark.parametrize("duration", [0, -1, MAX_DURATION_SECONDS + 1])
def test_custom_duration_is_bounded(duration: int) -> None:
    with pytest.raises(RainfallValidationError, match="duration_seconds|72 hours"):
        generate_custom_rainfall(
            [1.0, 2.0],
            start_at=START,
            duration_seconds=duration,
            step_seconds=300,
        )


def test_non_aligned_duration_keeps_floor_plus_one_grid_points() -> None:
    series = generate_custom_rainfall(
        [0.0, 4.0, 8.0],
        start_at=START,
        duration_seconds=601,
        step_seconds=300,
    )

    assert series.sample_count == 3
    assert series.end_at == START + timedelta(seconds=601)
    assert series.timestamps == (
        START,
        START + timedelta(seconds=300),
        START + timedelta(seconds=600),
    )


@pytest.mark.parametrize("bad_value", [-0.1, float("nan"), float("inf")])
def test_custom_values_reject_negative_and_non_finite_intensities(
    bad_value: float,
) -> None:
    with pytest.raises(RainfallValidationError, match="finite and non-negative"):
        generate_custom_rainfall(
            [0.0, bad_value],
            start_at=START,
            duration_seconds=300,
            step_seconds=300,
        )


def test_custom_timestamps_must_be_strictly_increasing_on_the_declared_grid() -> None:
    with pytest.raises(RainfallValidationError, match="timestamps"):
        generate_custom_rainfall(
            [0.0, 1.0, 2.0],
            start_at=START,
            duration_seconds=600,
            step_seconds=300,
            timestamps=(
                START,
                START + timedelta(seconds=600),
                START + timedelta(seconds=300),
            ),
        )

    with pytest.raises(RainfallValidationError, match="timestamps"):
        generate_custom_rainfall(
            [0.0, 1.0],
            start_at=START,
            duration_seconds=300,
            step_seconds=300,
            timestamps=(START, START + timedelta(seconds=299)),
        )


def test_random_source_is_byte_reproducible_for_the_same_seed() -> None:
    first = generate_random_rainfall(
        start_at=START,
        duration_seconds=7200,
        step_seconds=300,
        seed=20260924,
    )
    second = generate_random_rainfall(
        start_at=START,
        duration_seconds=7200,
        step_seconds=300,
        seed=20260924,
    )
    different = generate_random_rainfall(
        start_at=START,
        duration_seconds=7200,
        step_seconds=300,
        seed=20260925,
    )

    assert first.canonical_json() == second.canonical_json()
    assert first.content_sha256 == second.content_sha256
    assert first.intensity_mm_h != different.intensity_mm_h
    assert all(math.isfinite(value) and value >= 0 for value in first.intensities)


def test_source_contract_exposes_stable_identity_and_materializes_request() -> None:
    source = SyntheticRainfallSource()
    request = RainfallRequest(
        start_at=START,
        duration_seconds=1800,
        step_seconds=300,
        kind=RainfallPattern.random,
        random_seed=99,
    )

    assert source.source_id == "synthetic"
    assert source.version
    assert source.materialize(request).source_id == source.source_id
    assert source.materialize(request).source_version == source.version


def test_catchment_allocation_is_area_weighted_and_validated() -> None:
    series = generate_random_rainfall(
        start_at=START,
        duration_seconds=1800,
        step_seconds=300,
        seed=5,
        catchment_areas={"north": 75.0, "south": 25.0},
    )

    assert [item.catchment_id for item in series.catchment_allocation] == [
        "north",
        "south",
    ]
    assert series.catchment_allocation[0].weight == pytest.approx(0.75)
    assert series.catchment_allocation[1].weight == pytest.approx(0.25)
    assert sum(item.weight for item in series.catchment_allocation) == pytest.approx(
        1.0
    )
    assert series.catchment_rainfall_mm[0] > series.catchment_rainfall_mm[1]

    with pytest.raises(RainfallValidationError, match="finite and positive"):
        generate_random_rainfall(
            duration_seconds=1800,
            step_seconds=300,
            catchment_areas=[CatchmentAllocation("bad", 0.0, 1.0)],
        )


def test_scenario_copy_creates_new_id_and_preserves_seed_root_and_points() -> None:
    original = materialize_scenario(
        generate_random_rainfall(
            start_at=START,
            duration_seconds=3600,
            step_seconds=300,
            seed=123,
        ),
        network_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        root_scenario_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        name="storm",
        version=4,
    )

    copied = copy_scenario(original)

    assert copied.id != original.id
    assert copied.root_scenario_id == original.root_scenario_id
    assert copied.random_seed == original.random_seed == 123
    assert copied.version == original.version + 1
    assert copied.series is original.series
    assert copied.content_sha256 == original.content_sha256
    assert copied.points == original.points
    assert isinstance(copied, RainfallScenario)


def test_scenario_copy_rejects_reusing_the_source_id() -> None:
    original = materialize_scenario(
        generate_design_rainfall(
            50, start_at=START, duration_seconds=3600, step_seconds=300
        )
    )

    with pytest.raises(RainfallValidationError, match="new id"):
        copy_scenario(original, scenario_id=original.id)


def test_series_hash_and_total_depth_are_stable() -> None:
    series = generate_custom_rainfall(
        [0.0, 12.0, 24.0],
        start_at=START,
        duration_seconds=600,
        step_seconds=300,
    )
    equivalent = generate_custom_rainfall(
        [0.0, 12.0, 24.0],
        start_at=START,
        duration_seconds=600,
        step_seconds=300,
    )

    assert series.content_sha256 == equivalent.content_sha256
    assert len(series.content_sha256) == 64
    # Values are interval-start intensities; the final endpoint has no
    # additional interval, so depth is 0*5min + 12*5min.
    assert series.total_depth_mm == pytest.approx(1.0)


def test_custom_label_is_accepted_for_api_compatibility() -> None:
    series = generate_custom_rainfall(
        [0.0, 1.0],
        start_at=START,
        duration_seconds=300,
        step_seconds=300,
        return_period_years="custom",
    )

    assert series.return_period_years == "custom"
    assert series.kind == "custom"


def test_mapping_request_accepts_minute_and_hour_compatibility_fields() -> None:
    series = SyntheticRainfallSource().materialize(
        {
            "rainfall_type": "custom",
            "return_period": "custom",
            "duration_hours": 1,
            "step_minutes": 5,
            "values": [float(index) for index in range(13)],
            "seed": 8,
        }
    )

    assert series.duration == 3600
    assert series.step_seconds == 300
    assert series.sample_count == 13
    assert series.random_seed == 8


def test_scenario_copy_method_preserves_lineage_by_default() -> None:
    original = materialize_scenario(
        generate_random_rainfall(
            start_at=START,
            duration_seconds=1800,
            step_seconds=300,
            random_seed=17,
        ),
        root_scenario_id=uuid.UUID("00000000-0000-0000-0000-000000000017"),
    )

    copied = original.copy()

    assert copied.root_scenario_id == original.root_scenario_id
    assert copied.network_id == original.network_id
    assert copied.random_seed == original.random_seed
    assert copied.version == original.version + 1


def test_non_aligned_duration_integrates_the_final_partial_interval() -> None:
    series = generate_custom_rainfall(
        [0.0, 4.0, 8.0],
        start_at=START,
        duration_seconds=601,
        step_seconds=300,
    )

    assert series.total_depth_mm == pytest.approx((4.0 * 300 + 8.0) / 3600)


def test_single_interval_design_rainfall_has_positive_strictly_monotonic_depth() -> (
    None
):
    series = [
        generate_design_rainfall(
            period,
            start_at=START,
            duration_seconds=300,
            step_seconds=300,
        )
        for period in ALLOWED_RETURN_PERIODS
    ]

    assert all(item.total_depth_mm > 0 for item in series)
    assert all(
        right.total_depth_mm > left.total_depth_mm
        for left, right in zip(series, series[1:])
    )


def test_custom_return_period_is_accepted_by_schema_and_orm_storage() -> None:
    from app.models.hydraulic_models import RainfallScenario as RainfallScenarioModel
    from app.schemas.hydraulic_schemas import RainfallScenarioCreate

    series = generate_custom_rainfall(
        [0.0, 1.0],
        start_at=START,
        duration_seconds=300,
        step_seconds=300,
        return_period_years="custom",
    )
    payload = {
        "network_id": uuid.UUID("00000000-0000-0000-0000-000000000001"),
        "name": "custom",
        "kind": series.kind,
        "return_period_years": "custom",
        "step_seconds": series.step_seconds,
        "duration_seconds": series.duration,
        "source_type": "synthetic",
        "algorithm": series.algorithm,
        "random_seed": series.random_seed,
        "points": [
            {
                "point_index": point.point_index,
                "valid_at": point.valid_at,
                "intensity_mm_h": point.intensity_mm_h,
            }
            for point in series.samples
        ],
        "content_sha256": series.content_sha256,
    }

    assert (
        RainfallScenarioCreate.model_validate(payload).return_period_years == "custom"
    )
    column_type = RainfallScenarioModel.__table__.c.return_period_years.type
    assert column_type.process_bind_param("custom", None) == "custom"
    assert column_type.process_result_value("50", None) == 50
    assert column_type.process_result_value("custom", None) == "custom"


def test_rainfall_provenance_is_restricted_to_the_builtin_algorithm() -> None:
    with pytest.raises(RainfallValidationError, match="algorithm"):
        RainfallRequest(
            start_at=START,
            duration_seconds=300,
            step_seconds=300,
            kind="custom",
            algorithm="external-algorithm",
            intensities=(0.0, 1.0),
        )

    series = generate_random_rainfall(
        start_at=START,
        duration_seconds=300,
        step_seconds=300,
    )
    with pytest.raises(RainfallValidationError) as source_error:
        replace(series, source_id="external")
    assert source_error.value.issues[0].field == "source_id"
    with pytest.raises(RainfallValidationError, match="algorithm"):
        replace(series, algorithm="external-algorithm")

    class _FalseyExternalSource:
        source_id = "external"
        version = "v1"

        def __bool__(self) -> bool:
            return False

        def materialize(self, request: RainfallRequest) -> RainfallSeries:
            raise AssertionError("external source must not be called")

    with pytest.raises(RainfallValidationError) as source_error:
        materialize_rainfall(
            RainfallRequest(
                start_at=START,
                duration_seconds=300,
                step_seconds=300,
                kind="custom",
                intensities=(0.0, 1.0),
            ),
            source=_FalseyExternalSource(),  # type: ignore[arg-type]
        )
    assert source_error.value.issues[0].field == "source"


def test_series_infers_and_persists_duration_and_scenario_metadata_must_match() -> None:
    original = generate_random_rainfall(
        start_at=START,
        duration_seconds=601,
        step_seconds=300,
    )
    inferred = replace(original, duration_seconds=None)

    assert inferred.duration_seconds == 601
    assert inferred.duration == 601
    assert inferred.content_sha256 == original.content_sha256

    with pytest.raises(RainfallValidationError, match="series"):
        RainfallScenario(
            series=original,
            kind="design",
            return_period_years=1,
        )
    with pytest.raises(RainfallValidationError, match="name"):
        materialize_scenario(original, name="")
