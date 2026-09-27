#!/bin/sh
# Run the pinned migration command, then bind the shipped forecast policy
# artifacts to their database rows.
#
# Alembic creates hydraulic.probability_calibrations and
# hydraulic.forecast_rules but never inserts into them, while /readyz
# validates the policy config against those rows
# (backend/app/main.py::_validate_forecast_artifacts).  Without the data the
# config references artifact IDs the database does not know, the forecast
# component reports not ready forever, and the nowcast worker schedules
# nothing.
#
# The seed is data, not schema: it creates no table, extension or hypertable,
# so it does not compete with Alembic as the single schema writer, and it is
# ON CONFLICT DO UPDATE throughout so re-running converges instead of
# duplicating.  It runs here rather than in a separate service so the DSN
# stays owned by `migrate` and the deployment keeps exactly the seven
# PostgreSQL clients the contract enumerates.
#
# `command` in docker-compose.yml is still exactly `alembic upgrade head`; this
# wrapper only decides what happens around it.
set -eu

"$@"

exec python -m app.forecast_policy_seed
