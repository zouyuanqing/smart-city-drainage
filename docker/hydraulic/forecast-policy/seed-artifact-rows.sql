-- Registry rows for the artifacts the shipped forecast policy config binds.
--
-- FORECAST_POLICY_CONFIG_PATH names four immutable artifacts.  The files and
-- their canonical digests live in ../artifacts/ and are re-hashed at load time;
-- these rows are the database half of the same binding, without which
-- /readyz reports forecast_policy = false ("forecast policy references an
-- unknown artifact ID") and the nowcast worker schedules nothing.
--
-- The values below are derived entirely from the shipped files, so the two
-- halves cannot drift: artifact_uri points into HYDRAULIC_ARTIFACT_ROOT and
-- artifact_sha256 is the SHA-256 of that exact file.  Changing a file means
-- re-running this seed, because the contract forbids a policy artifact whose
-- recorded hash and file disagree.
--
-- This is data, not schema: it creates no tables, no extensions, and no
-- hypertable, so it does not compete with Alembic.  Re-running it is a no-op.
--
-- valid_from / valid_until are the artifact validity window the contract
-- requires the scheduler to re-check.  Widen or re-date them deliberately; an
-- expired window is a legitimate way to take the forecast component out of
-- service without deleting the binding.

\set ON_ERROR_STOP on

INSERT INTO hydraulic.probability_calibrations (
    id, artifact_schema_version, method, metric, artifact_uri, artifact_sha256,
    artifact_format, lead_min_minutes, lead_max_minutes, magnitude_min,
    magnitude_max, valid_from, valid_until
) VALUES
    (
        '22222222-2222-4222-8222-222222222222',
        1,
        'calibrated_residual',
        'water_depth_m',
        '/opt/scn/hydraulic/artifacts/flood_forecast_probability_v1.json',
        'eff9d4a3ae96a7c36808316d9e7a6ab56c71d053df644121113f156da3368352',
        'json',
        0, 115, 0.0, 1.0,
        '2026-01-01T00:00:00+00:00', '2036-01-01T00:00:00+00:00'
    ),
    (
        '33333333-3333-4333-8333-333333333333',
        1,
        'calibrated_residual',
        'fill_ratio',
        '/opt/scn/hydraulic/artifacts/pipe_surcharge_probability_v1.json',
        '35f7551fd350f4d883500c4852622cf7d67cb99d6447284c4173e7f0b4494596',
        'json',
        0, 115, 0.0, 1.0,
        '2026-01-01T00:00:00+00:00', '2036-01-01T00:00:00+00:00'
    )
ON CONFLICT (id) DO UPDATE SET
    artifact_schema_version = EXCLUDED.artifact_schema_version,
    method = EXCLUDED.method,
    metric = EXCLUDED.metric,
    artifact_uri = EXCLUDED.artifact_uri,
    artifact_sha256 = EXCLUDED.artifact_sha256,
    artifact_format = EXCLUDED.artifact_format,
    lead_min_minutes = EXCLUDED.lead_min_minutes,
    lead_max_minutes = EXCLUDED.lead_max_minutes,
    magnitude_min = EXCLUDED.magnitude_min,
    magnitude_max = EXCLUDED.magnitude_max,
    valid_from = EXCLUDED.valid_from,
    valid_until = EXCLUDED.valid_until;

INSERT INTO hydraulic.forecast_rules (
    id, rule_schema_version, version, alert_type, metric, operator,
    threshold_value, threshold_unit, clear_value, expiry_grace_minutes,
    no_forecast_confirmations, clear_debounce_minutes,
    refresh_expected_shift_steps, refresh_peak_delta, refresh_confidence_delta,
    refresh_risk_delta, risk_scale_m, risk_scale_ratio, artifact_uri,
    artifact_sha256, valid_from, valid_until
) VALUES
    (
        '44444444-4444-4444-8444-444444444444',
        1,
        'v1',
        'flood_forecast',
        'water_depth_m',
        '>=',
        0.15, 'm', 0.1, 15, 2, 10, 2, 0.1, 0.15, 0.05,
        0.3, NULL,
        '/opt/scn/hydraulic/artifacts/flood_forecast_rule_v1.json',
        '6162d10306c1777ad09c669480928be899430cd3e7fb238be70812ebcbb2adf7',
        '2026-01-01T00:00:00+00:00', '2036-01-01T00:00:00+00:00'
    ),
    (
        '55555555-5555-4555-8555-555555555555',
        1,
        'v1',
        'pipe_surcharge',
        'fill_ratio',
        '>=',
        0.85, 'ratio', 0.6, 15, 2, 10, 2, 0.1, 0.15, 0.05,
        NULL, 0.2,
        '/opt/scn/hydraulic/artifacts/pipe_surcharge_rule_v1.json',
        '30b881c1a78b5eedd61098305e0139dd7b9b0aa0d4b3ae348bb2452a5ee63ba8',
        '2026-01-01T00:00:00+00:00', '2036-01-01T00:00:00+00:00'
    )
ON CONFLICT (id) DO UPDATE SET
    rule_schema_version = EXCLUDED.rule_schema_version,
    version = EXCLUDED.version,
    alert_type = EXCLUDED.alert_type,
    metric = EXCLUDED.metric,
    operator = EXCLUDED.operator,
    threshold_value = EXCLUDED.threshold_value,
    threshold_unit = EXCLUDED.threshold_unit,
    clear_value = EXCLUDED.clear_value,
    expiry_grace_minutes = EXCLUDED.expiry_grace_minutes,
    no_forecast_confirmations = EXCLUDED.no_forecast_confirmations,
    clear_debounce_minutes = EXCLUDED.clear_debounce_minutes,
    refresh_expected_shift_steps = EXCLUDED.refresh_expected_shift_steps,
    refresh_peak_delta = EXCLUDED.refresh_peak_delta,
    refresh_confidence_delta = EXCLUDED.refresh_confidence_delta,
    refresh_risk_delta = EXCLUDED.refresh_risk_delta,
    risk_scale_m = EXCLUDED.risk_scale_m,
    risk_scale_ratio = EXCLUDED.risk_scale_ratio,
    artifact_uri = EXCLUDED.artifact_uri,
    artifact_sha256 = EXCLUDED.artifact_sha256,
    valid_from = EXCLUDED.valid_from,
    valid_until = EXCLUDED.valid_until;
